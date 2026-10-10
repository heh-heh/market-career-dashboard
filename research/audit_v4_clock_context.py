#!/usr/bin/env python3
"""Read-only, bounded-memory V4 clock/context coverage audit; no trades/manifest.

Explicit --timestamp-kind is required, never inferred. Per-session rows are
streamed to JSONL; aggregate JSON retains <=20 examples/reason. Any future
neighbor timestamp is post-hoc coverage only, never input to context(). Earlier
calendar sessions are past-only warmup. No provenance is certified by this tool.
"""
from __future__ import annotations
import argparse
import csv
import gzip
import json
import math
import sys
import sqlite3
import tempfile
import zipfile
from zoneinfo import ZoneInfo
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research"))
try:
    from .backtest_intraday_v4 import atomic_json,day_stream
    from .backtest_v4_strategies import MINUTE,NY,exchange_sessions,normalize_minute_timestamp,file_sha256
    from .intraday_v4_engine import History,Session,Event,context
    from .intraday_v4_diagnostics import idle_gate_reason,snapshot_evidence
except ImportError:
    from backtest_intraday_v4 import atomic_json,day_stream
    from backtest_v4_strategies import MINUTE,NY,exchange_sessions,normalize_minute_timestamp,file_sha256
    from intraday_v4_engine import History,Session,Event,context
    from intraday_v4_diagnostics import idle_gate_reason,snapshot_evidence
from build_v4_data_manifest import source_file,identity

CLOCK_LABELS=("15:28","15:29","15:30","15:31","15:50","15:59")


def scan_file(path,symbol,kind,naive_zone=None,to_date=None,session_sink=None):
    """One-day duplicate keys only. Disorder fails; never sort/repair inputs.

    Cross-day disorder makes the duplicate count a lower bound, explicitly
    recorded. Same-day nonadjacent duplicates are counted exactly. No session
    replay is attempted on invalid inputs.
    """
    meta=dict(symbol=symbol,path=str(path),rows=0,duplicates=0,conflictingDuplicates=0,
              outOfOrder=0,badTimezone=0,invalidOHLCV=0,sourceUTCOffsets=Counter(),
              firstTradingDate=None,lastTradingDate=None,errors=[],samples=[],
              timestamp_kind=kind,naive_timezone=naive_zone,duplicateCountIsLowerBound=False,
              symbolViolations=0,negativeVolume=0,zeroVolume=0,exactDuplicates=0,
              firstTimestamp=None,lastTimestamp=None,schema=None,timestampExamples={},sha256=file_sha256(path),
              sourceTimezoneInterpretation="Explicit per-row offset -> America/New_York; naive requires declaration")
    opener=gzip.open if path.suffix==".gz" else open
    previous=None;day=None;seen={}; day_counts=Counter(); before=identity(path.stat());currency=Counter()
    with opener(path,"rt",encoding="utf-8",newline="") as f:
        reader=csv.DictReader(f)
        if not {"timestamp","open","high","low","close","volume"}<=set(reader.fieldnames or []):
            raise ValueError(f"{symbol}: OHLCV schema missing")
        meta["schema"]=reader.fieldnames
        if len(reader.fieldnames)!=len(set(reader.fieldnames)):
            raise ValueError(f"{symbol}: duplicate schema columns")
        for line,row in enumerate(reader,2):
            try:
                raw=datetime.fromisoformat(row["timestamp"].replace("Z","+00:00"))
                start=normalize_minute_timestamp(row["timestamp"],kind,naive_zone)
            except (ValueError,TypeError,AttributeError) as exc:
                meta["badTimezone"]+=1
                if len(meta["samples"])<5: meta["samples"].append(dict(line=line,sourceTimestamp=row.get("timestamp"),reason=str(exc)))
                continue
            date=start.date().isoformat()
            if to_date and date>to_date:
                # Only a monotonic prefix is trusted; no statement about unread tail.
                break
            meta["firstTimestamp"]=meta["firstTimestamp"] or row["timestamp"]
            meta["lastTimestamp"]=row["timestamp"]
            offset=str(raw.utcoffset()) if raw.tzinfo else "naive:"+str(naive_zone)
            ny_offset=str(start.utcoffset())
            key=offset+" -> ET "+ny_offset
            if key not in meta["timestampExamples"] and len(meta["timestampExamples"])<10:
                meta["timestampExamples"][key]=dict(source=row["timestamp"],normalizedStartET=start.isoformat(),
                    utc=start.astimezone(timezone.utc).isoformat(),kst=start.astimezone(ZoneInfo("Asia/Seoul")).isoformat(),
                    observableET=(start+MINUTE).isoformat())
            meta["rows"]+=1
            if any((row.get(col) or "").strip().upper()!=symbol for col in ("symbol","ticker") if col in reader.fieldnames):
                meta["symbolViolations"]+=1
            meta["sourceUTCOffsets"][str(raw.utcoffset()) if raw.tzinfo else "naive:"+str(naive_zone)]+=1
            if row.get("currency"): currency[row["currency"]]+=1
            try:
                vals=tuple(float(row[k]) for k in ("open","high","low","close","volume"))
            except (ValueError,TypeError):
                vals=(float("nan"),)*5
            o,h,l,c,v=vals
            meta["negativeVolume"]+=int(v<0)
            meta["zeroVolume"]+=int(v==0)
            if not all(math.isfinite(x) for x in vals) or min(o,h,l,c)<=0 or v<0 or h<max(o,c) or l>min(o,c) or h<l:
                meta["invalidOHLCV"]+=1
            if date!=day:
                if day is not None and session_sink: session_sink(symbol,day,dict(day_counts))
                day=date;seen={};day_counts=Counter()
            if previous is not None and start<previous:
                meta["outOfOrder"]+=1
                day_counts["outOfOrderCount"]+=1
                if date!=previous.date().isoformat(): meta["duplicateCountIsLowerBound"]=True
            previous=start
            if start in seen:
                meta["duplicates"]+=1
                day_counts["duplicateTimestampCount"]+=1
                if seen[start]!=vals: meta["conflictingDuplicates"]+=1
                else: meta["exactDuplicates"]+=1
            else: seen[start]=vals
            meta["firstTradingDate"]=min(meta["firstTradingDate"] or date,date)
            meta["lastTradingDate"]=max(meta["lastTradingDate"] or date,date)
    if day is not None and session_sink: session_sink(symbol,day,dict(day_counts))
    meta["currencies"]=dict(currency)
    meta["currencyEvidence"]="COLUMN_PRESENT" if currency else "UNAVAILABLE_NOT_ASSUMED_USD"
    for key in ("badTimezone","outOfOrder","conflictingDuplicates","invalidOHLCV","symbolViolations"):
        if meta[key]: meta["errors"].append(f"{key}={meta[key]}")
    if currency and set(currency)!={"USD"}: meta["errors"].append("non-USD currency")
    if identity(path.stat())!=before or file_sha256(path)!=meta["sha256"]: meta["errors"].append("Source changed during audit")
    if not meta["rows"]: meta["errors"].append("No rows in requested prefix")
    meta["sourceIdentity"]=list(before)
    meta["scope"]="file prefix through to-date" if to_date else "entire file"
    return meta


def coverage_record(symbol,day,rows,opening,closing,kind,benchmark_available,clock_reason=None):
    regular=sorted(t for t in rows if opening<=t<closing)
    expected=int((closing-opening).total_seconds()/60)
    presence={label:opening.replace(hour=int(label[:2]),minute=int(label[3:])) in rows for label in CLOCK_LABELS}
    target=opening.replace(hour=15,minute=30)
    # Explicitly distinct: decision at 15:30 uses START 15:29 / END 15:30.
    decision_target=target-MINUTE
    prev=max((t for t in regular if t<=decision_target),default=None)
    nxt=min((t for t in regular if t>decision_target),default=None)
    return dict(tradingDate=day,symbol=symbol,timezone="America/New_York",timestampKind=kind,
                firstRegularTimestamp=regular[0].isoformat() if regular else None,
                lastRegularTimestamp=regular[-1].isoformat() if regular else None,
                expectedRegularMinuteCount=expected,actualRegularMinuteCount=len(regular),
                missingMinuteCount=expected-len(regular),earlyClose=expected!=390,
                barsByNormalizedStart=presence,
                sourceLabels={label:(opening.replace(hour=int(label[:2]),minute=int(label[3:]))+(MINUTE if kind=="end" else 0*MINUTE)).isoformat() for label in CLOCK_LABELS},
                decisionTime=target.isoformat(),expectedBarStart=decision_target.isoformat(),
                expectedSourceTimestamp=(target if kind=="end" else decision_target).isoformat(),
                decisionTargetPresent=decision_target in rows,benchmarkContextAvailable=benchmark_available,
                clockGateReason=clock_reason,
                sessionCountsClockProxy=dict(PRE=sum(opening.replace(hour=4,minute=0)<=t<opening for t in rows),
                    REGULAR=len(regular),AFTER=sum(closing<=t<closing.replace(hour=20,minute=0) for t in rows),
                    OTHER=sum(t<opening.replace(hour=4,minute=0) or t>=closing.replace(hour=20,minute=0) for t in rows)),
                sessionClassificationNote="ET clock diagnostic only; outside RTH is not certified Toss DAY/PRE/AFTER classification",
                nearestPreviousTimestamp=prev.isoformat() if prev else None,
                nearestNextTimestamp=nxt.isoformat() if nxt else None,
                neighborSemantics="Post-hoc coverage; next timestamp is never used for a signal/context")


def artifact_summary(path,sample_limit=5):
    """Summarize existing logs without fabricating atomic context values."""
    counts={};examples={};bounds_proofs=[];bounds_count=0
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        result=json.loads(archive.read(next(n for n in names if n.endswith("_result.json"))))
        audit=json.loads(archive.read(next(n for n in names if n.endswith("_data_audit.json"))))
        with archive.open(next(n for n in names if n.endswith("_decisions.jsonl"))) as stream:
            for line in stream:
                e=json.loads(line);kind=e["strategy"]
                reason=e["diagnostics"].get("reason") or e["state"]
                counts.setdefault(kind,Counter())[reason]+=1
                if reason=="CLOCK_ELIGIBILITY_CONTEXT_UNAVAILABLE":
                    meta=audit.get("symbols",{}).get(e["symbol"],{})
                    decision=datetime.fromisoformat(e["diagnostics"]["cancelledTimestamp"])
                    if meta.get("lastTimestamp") and meta.get("timestamp_kind") in {"start","end"}:
                        last_start=normalize_minute_timestamp(meta["lastTimestamp"],meta["timestamp_kind"],meta.get("naive_timezone"))
                        if last_start < decision-MINUTE:
                            bounds_count+=1
                            if len(bounds_proofs)<sample_limit:
                                bounds_proofs.append(dict(reason="CLOCK_TARGET_BAR_MISSING",symbol=e["symbol"],sessionDate=e["date"],decisionTime=decision.isoformat(),lastInputTimestamp=meta["lastTimestamp"],expectedBarStart=(decision-MINUTE).isoformat(),evidence="Supplied audit EOF precedes required own bar"))
                key=kind+":"+reason
                ex=examples.setdefault(key,[])
                if len(ex)<sample_limit: ex.append(e)
    breakdown={k:{r:dict(count=n,pct=100*n/sum(c.values())) for r,n in c.items()} for k,c in counts.items()}
    return dict(source=str(path),mode=result["configuration"].get("dataMode"),
                overall=result["overall"],funnel=result["funnel"],terminalBreakdown=breakdown,
                samplesByReason=examples,dataAudit=audit,
                provableAtomicFailuresFromFileBounds=dict(CLOCK_TARGET_BAR_MISSING=bounds_count),
                fileBoundEvidence=bounds_proofs,
                limitation="Legacy decisions lack cancel-time context; atomic historical counts cannot be reconstructed without minute files")


def audit_data(paths,kind,out,sessions_out,from_date=None,to_date=None,naive_zone=None,sample_limit=5,calendar_provider=exchange_sessions):
    # Disk index has one row per symbol/session, never one row per minute.
    with tempfile.TemporaryDirectory(prefix="v4-clock-audit-") as tmp:
        with sqlite3.connect(str(Path(tmp)/"session_counts.sqlite")) as conn:
            conn.execute("CREATE TABLE counts (symbol TEXT, day TEXT, stats TEXT, PRIMARY KEY(symbol,day))")
            return _audit_data(paths,kind,out,sessions_out,from_date,to_date,naive_zone,sample_limit,calendar_provider,conn)


def _audit_data(paths,kind,out,sessions_out,from_date,to_date,naive_zone,sample_limit,calendar_provider,conn):
    if not 0<=sample_limit<=20: raise ValueError("sample_limit must be 0..20")
    if not {"SPY","QQQ"}<=set(paths): raise ValueError("SPY and QQQ are required; no benchmark substitution")
    def sink(s,d,stats):
        conn.execute("INSERT OR REPLACE INTO counts VALUES (?,?,?)",(s,d,json.dumps(stats)))
    sources={s:scan_file(p,s,kind,naive_zone,to_date,sink) for s,p in paths.items()}
    conn.commit()
    result=dict(version=1,generatedAt=datetime.now(timezone.utc).isoformat(),
                reviewStatus="PROVISIONAL_UNREVIEWED_DATA",auditOnly=True,
                timestampKind=kind,timezone="America/New_York",naiveTimezoneDeclaration=naive_zone,
                clockRule="Decision 15:30 ET uses completed START 15:29 / exclusive END 15:30. START 15:30 is not available until 15:31.",
                sources=sources,aggregate=Counter(),bySymbol={},reasonCounts=Counter(),samplesByReason={})
    if any(m["errors"] for m in sources.values()):
        result["aggregate"]["rows_bad_timezone"]=sum(m["badTimezone"] for m in sources.values())
        result["status"]="FAIL";atomic_json(out,result);return result
    first=min(m["firstTradingDate"] for m in sources.values());last=max(m["lastTradingDate"] for m in sources.values())
    schedule=calendar_provider(first,last)
    if from_date:
        prior=[d for d in schedule if d<from_date]
        warmup_start=prior[-61] if len(prior)>=61 else first
        schedule={d:times for d,times in schedule.items() if d>=warmup_start}
    hist={s:History() for s in paths};manifest=dict(symbols=sources)
    before={s:identity(p.stat()) for s,p in paths.items()}
    if any(list(before[s])!=sources[s]["sourceIdentity"] for s in paths):
        raise ValueError("Source changed between mechanical scan and context replay")
    stream=iter(day_stream(paths,manifest));item=next(stream,None)
    sessions_out.parent.mkdir(parents=True,exist_ok=True)
    with sessions_out.open("w",encoding="utf-8") as output:
        for day,(opening,closing) in sorted(schedule.items()):
            if to_date and day>to_date: break
            while item and item[0]<day: item=next(stream,None)
            rows=item[1] if item and item[0]==day else {}
            if item and item[0]==day: item=next(stream,None)
            sessions={s:Session(s,opening,closing,hist[s]) for s in paths}
            target=opening.replace(hour=15,minute=30);T=opening+MINUTE;clock_ctx=None;clock_reasons={};clock_evidence={}
            while T<=closing:
                for s,session in sessions.items():
                    b=rows.get(s,{}).get(T-MINUTE)
                    if b: session.observe(b)
                if T==target:
                    clock_ctx=context(sessions,T)
                    for symbol in {"SPY","QQQ"}:
                        event=Event("ir3",symbol)
                        reason,ref=idle_gate_reason(event,sessions[symbol],sessions,target,clock_ctx,True)
                        clock_reasons[symbol]=reason
                        if reason:
                            clock_evidence[symbol]=snapshot_evidence(event,sessions[symbol],sessions,target,clock_ctx,ref)
                T+=MINUTE
            available=clock_ctx is not None and clock_ctx["direction"]!="UNKNOWN" and clock_ctx["volatility"]!="UNKNOWN"
            if not from_date or day>=from_date:
                for s in paths:
                    reason=ref=None
                    if target>closing: reason="CLOCK_SESSION_EARLY_CLOSE"
                    elif s in {"SPY","QQQ"}:
                        reason=clock_reasons[s]
                    r=coverage_record(s,day,rows.get(s,{}),opening,closing,kind,available,reason)
                    stored=conn.execute("SELECT stats FROM counts WHERE symbol=? AND day=?",(s,day)).fetchone()
                    stats=json.loads(stored[0]) if stored else {}
                    daily=list(hist[s].daily)[-61:]
                    r["historyCoverage"]=dict(priorSessionCount=len(daily),completePriorSessions=sum(x is not None for x in daily),
                        requiredDailySessions=61,dailyEligible=sessions[s].eligibility is not None,
                        priorOpeningReturns=sum(x is not None for x in hist[s].opening_returns),
                        requiredOpeningReturnsIR3=40,priorOpeningVolumes=sum(x is not None for x in hist[s].opening_volumes))
                    r.update(duplicateTimestampCount=stats.get("duplicateTimestampCount",0),outOfOrderCount=stats.get("outOfOrderCount",0))
                    output.write(json.dumps(r)+"\n")
                    totals=result["bySymbol"].setdefault(s,Counter())
                    for dest in (totals,result["aggregate"]):
                        dest["sessions_total"]+=1
                        dest["sessions_with_1530" if r["barsByNormalizedStart"]["15:30"] else "sessions_missing_1530"]+=1
                        dest["sessions_with_decision_target" if r["decisionTargetPresent"] else "sessions_missing_decision_target"]+=1
                        dest["sessions_with_complete_regular_close"]+=int(r["missingMinuteCount"]==0)
                        dest["sessions_early_close"]+=int(r["earlyClose"])
                        dest["sessions_with_benchmark_context" if available else "sessions_without_benchmark_context"]+=1
                        dest["expected_regular_minutes"]+=r["expectedRegularMinuteCount"]
                        dest["actual_regular_minutes"]+=r["actualRegularMinuteCount"]
                        dest["missing_regular_minutes"]+=r["missingMinuteCount"]
                        dest["daily_eligible_sessions"]+=int(r["historyCoverage"]["dailyEligible"])
                        dest["history_61_available_sessions"]+=int(r["historyCoverage"]["completePriorSessions"]==61)
                        for label,n in r["sessionCountsClockProxy"].items(): dest[label+"_rows_clock_proxy"]+=n
                    if reason:
                        result["reasonCounts"][reason]+=1
                        samples=result["samplesByReason"].setdefault(reason,[])
                        if len(samples)<sample_limit:
                            sample=clock_evidence.get(s,dict(symbol=s,sessionDate=day,sessionClose=closing.isoformat(),decisionTime=None))
                            sample.update(reason=reason,nearestNextTimestamp=r["nearestNextTimestamp"],nextTimestampNote=r["neighborSemantics"])
                            samples.append(sample)
            for session in sessions.values(): session.finish()
    stream.close()
    if any(identity(p.stat())!=before[s] or file_sha256(p)!=sources[s]["sha256"] for s,p in paths.items()):
        raise ValueError("Source changed during audit; discard diagnostics and wait for stable files")
    for totals in [result["aggregate"],*result["bySymbol"].values()]:
        totals["regularCoveragePct"]=100*totals["actual_regular_minutes"]/totals["expected_regular_minutes"] if totals["expected_regular_minutes"] else None
    result["aggregate"]["sessions_bad_timezone"]=0
    result["status"]="PASS_MECHANICAL_AUDIT_ONLY"
    result["sessionsPath"]=str(sessions_out)
    atomic_json(out,result)
    return result


def write_report(path,result):
    lines=["# V4 read-only data audit", "", "PROVISIONAL_UNREVIEWED_DATA · mechanical diagnostics only; no review flags or strategy results.",
        "", "Status: `"+result["status"]+"`. Timestamp kind is an explicit declaration, not inferred.",
        "", "| Symbol | Rows | First | Last | Dup / conflict | OHLC invalid | Volume negative / zero |", "|---|---:|---|---|---:|---:|---:|"]
    for symbol,m in sorted(result["sources"].items()):
        lines.append(f"| {symbol} | {m['rows']} | {m.get('firstTimestamp')} | {m.get('lastTimestamp')} | {m['duplicates']} / {m['conflictingDuplicates']} | {m['invalidOHLCV']} | {m.get('negativeVolume',0)} / {m.get('zeroVolume',0)} |")
    for symbol,m in sorted(result["sources"].items()):
        if m["errors"]: lines += ["",symbol+": "+"; ".join(m["errors"])]
    lines += ["", "Per-symbol coverage/history counts:", "", "```json", json.dumps(result.get("bySymbol",{}),indent=2), "```",
        "", "Out-of-order/timezone/OHLC errors prevent causal replay. Missing regular minutes are reported, never filled.",
        "Clock counts include early-close exclusions. PRE/AFTER counts are ET clock proxies, not certified Toss extended-session labels.",
        "Price adjustment, symbol provenance and currency when absent remain UNREVIEWED. This is not a reviewed manifest.",
        "Per-file hashes, source/UTC/KST/ET/observable timestamp samples, errors and clock reasons are in JSON; session detail is streamed to JSONL."]
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text("\n".join(lines)+"\n")


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir",type=Path,default=ROOT/"data/toss_1m")
    p.add_argument("--symbols",default="SPY,QQQ",help="SPY/QQQ required; selected symbols only")
    p.add_argument("--timestamp-kind",choices=("start","end"))
    p.add_argument("--naive-timezone",help="Explicit source declaration; never assumed")
    p.add_argument("--from-date");p.add_argument("--to-date")
    p.add_argument("--out",type=Path,default=ROOT/"research/v4_clock_context_audit.json")
    p.add_argument("--report",type=Path,help="Optional Markdown mechanical audit report")
    p.add_argument("--sessions-out",type=Path,help="Default: OUT stem + _sessions.jsonl")
    p.add_argument("--sample-limit",type=int,choices=range(21),default=5)
    p.add_argument("--result-zip",type=Path,help="Summarize existing artifact only; does not read minute data")
    args=p.parse_args(argv)
    if args.result_zip:
        if args.out.resolve()==args.result_zip.resolve(): p.error("Cannot overwrite input ZIP")
        summary=artifact_summary(args.result_zip,args.sample_limit);atomic_json(args.out,summary)
        print(json.dumps(summary["terminalBreakdown"],indent=2));return 0
    if args.timestamp_kind is None: p.error("--timestamp-kind start|end must be explicitly declared")
    for d in (args.from_date,args.to_date):
        if d: datetime.strptime(d,"%Y-%m-%d")
    if args.from_date and args.to_date and args.from_date>args.to_date: p.error("from-date > to-date")
    symbols={s.strip().upper() for s in args.symbols.split(",") if s.strip()}|{"SPY","QQQ"}
    supported=set(json.loads((ROOT/"research/universe_v3.json").read_text())["symbols"])
    if not symbols<=supported: p.error("Symbols must be in universe_v3.json")
    paths={s:source_file(args.data_dir,s) for s in sorted(symbols)}
    sessions=args.sessions_out or args.out.with_name(args.out.stem+"_sessions.jsonl")
    if args.data_dir.resolve() in args.out.resolve().parents or args.data_dir.resolve() in sessions.resolve().parents:
        p.error("Audit outputs must be outside raw-data directory")
    if args.out.resolve()==sessions.resolve() or any(x.resolve() in {args.out.resolve(),sessions.resolve()} for x in paths.values()):
        p.error("Output paths must be distinct and cannot overwrite inputs")
    if args.report and (args.report.resolve() in {args.out.resolve(),sessions.resolve()} or args.data_dir.resolve() in args.report.resolve().parents):
        p.error("Markdown report must be distinct and outside raw-data directory")
    result=audit_data(paths,args.timestamp_kind,args.out,sessions,args.from_date,args.to_date,args.naive_timezone,args.sample_limit)
    result["inventory"]=dict(selectedSymbols=len(paths),selectedFiles=len(paths),
        availableCSVFiles=len(list(args.data_dir.glob("*.csv"))),availableGzipFiles=len(list(args.data_dir.glob("*.csv.gz"))),
        priceAdjustment="UNREVIEWED_NOT_INFERRED",provenanceCertified=False)
    atomic_json(args.out,result)
    if args.report: write_report(args.report,result)
    print(json.dumps(dict(status=result["status"],aggregate=result["aggregate"],reasonCounts=result["reasonCounts"],errors={s:m["errors"] for s,m in result["sources"].items() if m["errors"]},out=str(args.out)),indent=2))
    return 0 if not result["status"]=="FAIL" else 1


if __name__=="__main__":
    raise SystemExit(main())
