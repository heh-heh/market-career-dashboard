#!/usr/bin/env python3
"""Isolated IR_SPEC_V1 historical backtester. Never submits orders.

Reviewed v4_data_manifest.json is preferred. When --provisional is explicit and
the reviewed manifest is unavailable, raw START labels are mechanically audited
and the run is permanently marked PROVISIONAL_UNREVIEWED_DATA.
Full runs are manual. --from-date/--to-date permit bounded smoke evaluation
while earlier rows are read as past-only warmup. No parameter search interface.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import heapq
import json
import math
import os
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research"))
if __package__:
    from .backtest_v4_strategies import (Bar,MINUTE,NY,exchange_sessions,file_sha256,
            manifest_input_path,normalize_minute_timestamp,validate_data_manifest)
    from .intraday_v4_engine import (BASELINE,STOCKS,SEMI,Book,Event,History,Session,benchmark,context,cross_ranks,evaluate)
    from .intraday_v4_metrics import summarize,grouped,time_bucket,chronological_folds,daily_consistency
else:
    from backtest_v4_strategies import (Bar,MINUTE,NY,exchange_sessions,file_sha256,
            manifest_input_path,normalize_minute_timestamp,validate_data_manifest)
    from intraday_v4_engine import (BASELINE,STOCKS,SEMI,Book,Event,History,Session,benchmark,context,cross_ranks,evaluate)
    from intraday_v4_metrics import summarize,grouped,time_bucket,chronological_folds,daily_consistency
from build_v4_data_manifest import inspect_file,identity,source_file

VERSION = "IR_SPEC_V1_IMPLEMENTATION_2"


def atomic_json(path,value):
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp = path.with_name(path.name+f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


class Reporter:
    def __init__(self,args):
        self.args = args
        self.state = dict(engine="v4-"+args.strategy,strategy=args.strategy,runId=uuid.uuid4().hex,pid=os.getpid(),phase="starting",
            running=True,progress=0,completedDays=0,processedRows=0,trades=0,signals=0,entries=0,error=None,summary={})

    def update(self,**values):
        self.state.update(values,updatedAt=datetime.now(timezone.utc).isoformat())
        atomic_json(self.args.state,self.state)

    def log(self,text):
        self.args.log.parent.mkdir(parents=True,exist_ok=True)
        with self.args.log.open("a",encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat()+" "+text+"\n")


def provisional_inspect_file(path,symbol,timestamp_kind="start",naive_timezone=None):
    """Bounded-memory mechanical audit for provisional runs.

    Unlike the reviewed-manifest validator this never builds a temporary SQLite
    index. Because provisional inputs must already be monotonic, any duplicate
    timestamp is necessarily adjacent; an out-of-order transition fails closed.
    This keeps the audit O(1) memory and avoids exhausting /tmp/root storage.
    """
    before=path.stat()
    digest=file_sha256(path)
    opener=gzip.open if path.suffix==".gz" else open
    meta=dict(
        symbol=symbol,path=path.name,sha256=digest,rows=0,
        firstTimestamp=None,lastTimestamp=None,duplicates=0,exactDuplicates=0,
        conflictingDuplicates=0,ohlcViolations=0,quoteViolations=0,
        timestampViolations=0,symbolViolations=0,outOfOrderTransitions=0,
        monotonicTimestampOrdering=True,strictlyIncreasingTimestamps=True,
        timestamp_kind=timestamp_kind,
        timestamp_semantics="minute_start" if timestamp_kind=="start" else "exclusive_minute_end",
        naive_timezone=naive_timezone,validationErrors=[],errorExamples=[],
        auditMode="streaming_bounded_memory",coverageCheck="deferred_to_causal_runner",
    )
    previous_start=None
    previous_fingerprint=None
    first=last=None
    with opener(path,"rt",encoding="utf-8",newline="") as stream:
        reader=csv.DictReader(stream)
        columns=reader.fieldnames or []
        required={"timestamp","open","high","low","close","volume"}
        if not required.issubset(columns) or len(columns)!=len(set(columns)):
            raise ValueError(f"{symbol}: missing/duplicate OHLCV columns; got {columns}")
        identity_columns=[x for x in ("symbol","ticker") if x in columns]
        for line,row in enumerate(reader,2):
            meta["rows"]+=1
            if any((row.get(col) or "").strip().upper()!=symbol for col in identity_columns):
                meta["symbolViolations"]+=1
            try:
                start=normalize_minute_timestamp(row["timestamp"],timestamp_kind,naive_timezone)
            except (ValueError,TypeError,AttributeError) as exc:
                meta["timestampViolations"]+=1
                if len(meta["errorExamples"])<5:
                    meta["errorExamples"].append(f"line {line}: timestamp: {exc}")
                continue
            if previous_start is not None and start<previous_start:
                meta["outOfOrderTransitions"]+=1
                if len(meta["errorExamples"])<5:
                    meta["errorExamples"].append(f"line {line}: out-of-order timestamp")
            if first is None or start<first: first=start
            if last is None or start>last: last=start
            try:
                vals=tuple(float(row[k]) for k in ("open","high","low","close","volume"))
                o,h,l,close,volume=vals
                if not all(math.isfinite(x) for x in vals) or min(o,h,l,close)<=0 or volume<0 or h<max(o,close) or l>min(o,close) or h<l:
                    raise ValueError("invalid/nonfinite OHLCV")
            except (ValueError,TypeError) as exc:
                meta["ohlcViolations"]+=1
                vals=tuple(row.get(k) for k in ("open","high","low","close","volume"))
                if len(meta["errorExamples"])<5:
                    meta["errorExamples"].append(f"line {line}: {exc}")
            try:
                bid=float(row["bid"]) if row.get("bid") else None
                ask=float(row["ask"]) if row.get("ask") else None
                if (bid is None)!=(ask is None) or (bid is not None and (not math.isfinite(bid) or not math.isfinite(ask) or not 0<bid<=ask)):
                    raise ValueError("invalid/partial quote")
            except (ValueError,TypeError):
                meta["quoteViolations"]+=1
                bid=ask=None
            fingerprint=(vals,bid,ask)
            if previous_start is not None and start==previous_start:
                meta["duplicates"]+=1
                meta["strictlyIncreasingTimestamps"]=False
                if fingerprint==previous_fingerprint:
                    meta["exactDuplicates"]+=1
                else:
                    meta["conflictingDuplicates"]+=1
            previous_start=start
            previous_fingerprint=fingerprint
    meta["monotonicTimestampOrdering"]=meta["outOfOrderTransitions"]==0
    if first is not None:
        meta["firstTimestamp"]=(first if timestamp_kind=="start" else first+MINUTE).isoformat()
        meta["lastTimestamp"]=(last if timestamp_kind=="start" else last+MINUTE).isoformat()
    for field in ("conflictingDuplicates","ohlcViolations","timestampViolations","symbolViolations","quoteViolations","outOfOrderTransitions"):
        if meta[field]:
            meta["validationErrors"].append(f"{field}={meta[field]}")
    if first is None:
        meta["validationErrors"].append("No valid timestamped rows")
    after=path.stat()
    if file_sha256(path)!=digest or identity(after)!=identity(before):
        meta["validationErrors"].append("Input changed during validation; wait for a finalized export and retry")
    return meta


def read_stream(path,symbol,meta):
    """Bounded memory. Reject disorder/conflicts instead of sorting future rows."""
    previous = None
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path,"rt",encoding="utf-8",newline="") as f:
        for row in csv.DictReader(f):
            start = normalize_minute_timestamp(row["timestamp"],"start",meta["naive_timezone"])
            vals = [float(row[k]) for k in ("open","high","low","close","volume")]
            bar = Bar(start,start+MINUTE,*vals)
            if not all(math.isfinite(x) for x in vals) or min(vals[:4])<=0 or vals[4]<0 or bar.h<max(bar.o,bar.c) or bar.l>min(bar.o,bar.c) or bar.h<bar.l:
                raise ValueError(f"{symbol}: invalid OHLCV at {start}")
            if previous and start < previous.start: raise ValueError(f"{symbol}: out-of-order input")
            if previous and start == previous.start:
                if bar != previous: raise ValueError(f"{symbol}: conflicting duplicate")
                continue
            previous = bar
            yield bar


def day_stream(paths,manifest):
    """K-way merge, retaining only one session's rows across instruments."""
    heap=[]; streams={}
    for symbol,path in paths.items():
        streams[symbol]=iter(read_stream(path,symbol,manifest["symbols"][symbol]))
        first=next(streams[symbol],None)
        if first: heapq.heappush(heap,(first.start,symbol,first))
    day=None; rows={}
    try:
        while heap:
            _,symbol,bar=heapq.heappop(heap)
            newday=bar.start.date().isoformat()
            if day is not None and newday != day:
                yield day,rows
                rows={}
            day=newday
            rows.setdefault(symbol,{})[bar.start]=bar
            nxt=next(streams[symbol],None)
            if nxt: heapq.heappush(heap,(nxt.start,symbol,nxt))
        if day: yield day,rows
    finally:
        for stream in streams.values(): stream.close()


def candidate_queue(sessions,T,slip):
    eligible={}
    for symbol,s in sessions.items():
        x=s.snapshots.get(T)
        if symbol not in STOCKS or not s.eligibility or not x: continue
        e=s.eligibility
        priority=abs(math.log(x["close"]/e["prevClose"]))/(e["dailyATR"]/e["prevClose"])
        eligible[symbol]=(priority,e["adv20"])
    shortlist=sorted(eligible,key=lambda s:(-eligible[s][0],-eligible[s][1],s))[:15]
    valid=[s for s in shortlist if sessions[s].snapshots[T]["rvol"] is not None]
    rv=cross_ranks({s:sessions[s].snapshots[T]["rvol"] for s in valid})
    hd=cross_ranks({s:(sessions[s].eligibility["dailyATR"]/sessions[s].eligibility["prevClose"])/(2*(slip+.0001)) for s in valid})
    scores={s:50*rv[s]+50*hd[s] for s in valid}
    return scores,shortlist


def run_sessions(day_rows,schedules,symbols,strategies,slip=2,from_date=None,to_date=None,reporter=None,decisions_path=None,entry_symbols=None):
    entry_symbols=set(symbols) if entry_symbols is None else set(entry_symbols)
    histories={s:History() for s in symbols}
    books={k:Book(k,slip) for k in strategies}
    funnel=Counter(); evaluation_days=[]; overlap=[]; processed=0
    iterator=iter(day_rows); item=next(iterator,None)
    evaluated_count=sum((not from_date or d>=from_date) and (not to_date or d<=to_date) for d in schedules)
    if reporter: reporter.update(phase="warmup",totalDays=evaluated_count)
    completed=0
    for day,(opening,closing) in sorted(schedules.items()):
        if to_date and day>to_date: break
        while item and item[0]<day: item=next(iterator,None)
        rows=item[1] if item and item[0]==day else {}
        if item and item[0]==day: item=next(iterator,None)
        # Histories include even missing calendar sessions as missing values.
        sessions={s:Session(s,opening,closing,histories[s]) for s in symbols}
        active=(not from_date or day>=from_date)
        if active:
            funnel["evaluatedSessions"]+=1
            for s in entry_symbols:
                if sessions[s].eligibility:
                    funnel["dailyLiquidityPass"]+=1
                else:
                    h=list(histories[s].daily)[-61:]
                    funnel["DAILY_HISTORY_UNAVAILABLE" if len(h)<61 or None in h else "DAILY_LIQUIDITY_REJECTED"]+=1
        events={(k,s):Event(k,s) for k in strategies for s in entry_symbols if (k=="ir3" and s in {"SPY","QQQ"}) or (k!="ir3" and s in STOCKS)}
        cutoff=min(closing-10*MINUTE,opening.replace(hour=15,minute=50))
        T=opening+MINUTE
        while T<=closing:
            for s,session in sessions.items():
                bar=rows.get(s,{}).get(T-MINUTE)
                if bar: session.observe(bar); processed+=1
            if active:
                # Manage prior positions before resolving pending/new signals.
                for book in books.values():
                    if book.position:
                        symbol=book.position["symbol"]
                        done=book.manage(T,sessions[symbol].minutes.get(T),cutoff)
                        if done:
                            event=events.get((book.strategy,symbol))
                            if event and event.state=="MANAGING": event.move("EXIT",T,done["exitReason"])
                ctx=context(sessions,T)
                for kind,book in books.items():
                    pending=[e for (k,s),e in events.items() if k==kind and e.state=="TRIGGERED"]
                    pending.sort(key=lambda e:(-e.fields["priority"],0 if kind=="ir3" else -e.fields.get("adv20",0),e.fields["signalTimestamp"],e.symbol))
                    for event in pending:
                        bar=sessions[event.symbol].minutes.get(T)
                        if bar:
                            book.enter(event,bar,T,ctx,sessions[event.symbol],sessions)
                        else:
                            event.move("CANCELLED",T,"ENTRY_GAP_OR_LATENCY")
                            book.funnel["ENTRY_GAP_OR_LATENCY"]+=1
                scores,shortlist=candidate_queue({s:v for s,v in sessions.items() if s in entry_symbols},T,slip/10000)
                funnel["universeObservations"]+=len([s for s in sessions if s in STOCKS])
                funnel["thresholdCandidates"]+=len(shortlist)
                funnel["QUEUE_UNAVAILABLE"]+=len(shortlist)-len(scores)
                for (kind,symbol),event in events.items():
                    if kind=="ir3" and T.time().isoformat()!="15:30:00": continue
                    before=event.state
                    score=scores.get(symbol)
                    if kind=="ir3": score=0
                    evaluate(event,sessions[symbol],sessions,T,ctx,score is not None)
                    if kind=="ir3" and event.state=="IDLE":
                        event.move("CANCELLED",T,"CLOCK_ELIGIBILITY_CONTEXT_UNAVAILABLE")
                    if before!=event.state:
                        funnel["candidateEvaluations"]+=1
                        if before=="IDLE" and event.state!="CANCELLED": funnel["setups"]+=1
                        if event.state=="TRIGGERED":
                            funnel["signals"]+=1
                            event.fields["priority"]=event.fields.get("openingZ",score)
                            event.fields["adv20"]=sessions[symbol].eligibility["adv20"]
                        if event.state=="CANCELLED": funnel[event.fields["reason"]]+=1
                if any(sessions[s].snapshots.get(T) is None for s in ("QQQ","SPY")):
                    funnel["MISSING_BENCHMARK_DATA"]+=1
                elif ctx["direction"]=="UNKNOWN": funnel["MARKET_FEATURES_UNAVAILABLE"]+=1
                elif ctx["volatility"]=="UNKNOWN": funnel["REGIME_HISTORY_UNAVAILABLE"]+=1
            T+=MINUTE
        if active:
            evaluation_days.append(day); completed+=1
            for s in symbols:
                a,b=events.get(("ir1",s)),events.get(("ir3",s))
                # IR1 stocks / IR3 indices cannot share a symbol; exposure-day
                # co-occurrence is reported separately, never duplicate alpha.
                if a and b and a.fields.get("signalTimestamp") and b.fields.get("signalTimestamp"):
                    overlap.append(dict(date=day,symbol=s,ir1=a.fields["signalTimestamp"],ir3=b.fields["signalTimestamp"]))
            if decisions_path:
                with Path(decisions_path).open("a",encoding="utf-8") as f:
                    for e in events.values():
                        if e.state!="IDLE":
                            f.write(json.dumps(dict(strategy=e.strategy,symbol=e.symbol,date=day,state=e.state,
                                transitions=e.transitions,diagnostics=e.fields),allow_nan=False)+"\n")
            if reporter:
                trades=[t for b in books.values() for t in b.trades]
                reporter.update(phase="running",running=True,progress=10+85*completed/max(1,evaluated_count),
                    currentDay=day,currentTimestamp=closing.isoformat(),completedDays=completed,processedRows=processed,trades=len(trades),
                    signals=funnel["signals"],entries=sum(b.funnel["entries"] for b in books.values()),
                    summary=summarize(trades,10000*len(books)),funnel=dict(funnel))
        elif reporter:
            reporter.update(phase="warmup",currentDay=day,currentTimestamp=closing.isoformat(),processedRows=processed)
        for session in sessions.values(): session.finish()
    for b in books.values(): funnel.update(b.funnel)
    trades=sorted([t for b in books.values() for t in b.trades],key=lambda t:(t["exitTimestamp"],t["strategy"],t["symbol"]))
    exposure_overlap=[]
    for day in evaluation_days:
        kinds={t["strategy"] for t in trades if t["date"]==day}
        if "ir1" in kinds and "ir3" in kinds: exposure_overlap.append(day)
    return trades,dict(funnel),[dict(b.position,status="UNRESOLVED",pnlUsd=None) for b in books.values() if b.position],evaluation_days,dict(sameSymbolSignals=overlap,ir1AndIr3TradeDays=exposure_overlap)


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--strategy",choices=("ir1","ir2","ir3","all"),default="all")
    p.add_argument("--data-dir","--data",dest="data_dir",type=Path,default=ROOT/"data/toss_1m")
    p.add_argument("--manifest",type=Path,default=ROOT/"research/v4_data_manifest.json")
    p.add_argument("--provisional",action="store_true",
                   help="Allow an explicitly unreviewed research run when the reviewed manifest is unavailable; mechanical audits still apply")
    p.add_argument("--symbols",help="Selected entry symbols; required references automatically added")
    p.add_argument("--out",type=Path)
    p.add_argument("--state",type=Path)
    p.add_argument("--log",type=Path)
    p.add_argument("--trades-csv",type=Path)
    p.add_argument("--data-audit",type=Path)
    p.add_argument("--decisions",type=Path)
    p.add_argument("--slippage-bps",type=float,default=2)
    p.add_argument("--from-date")
    p.add_argument("--to-date")
    a=p.parse_args(argv)
    if not math.isfinite(a.slippage_bps) or not 0<=a.slippage_bps<=100: p.error("slippage must be finite, 0..100 bps")
    for v in (a.from_date,a.to_date):
        if v: datetime.strptime(v,"%Y-%m-%d")
    if a.from_date and a.to_date and a.from_date>a.to_date: p.error("from-date > to-date")
    prefix=ROOT/"research"/f"backtest_v4_{a.strategy}"
    for name,suffix in (("out","_result.json"),("state","_state.json"),("log",".log"),
                         ("trades_csv","_trades.csv"),("data_audit","_data_audit.json"),("decisions","_decisions.jsonl")):
        if getattr(a,name) is None: setattr(a,name,Path(str(prefix)+suffix))
    outputs=[getattr(a,n).resolve() for n in ("out","state","log","trades_csv","data_audit","decisions")]
    if len(set(outputs))!=len(outputs) or any(x.is_relative_to(a.data_dir.resolve()) for x in outputs) or a.manifest.resolve() in outputs:
        p.error("Outputs must be distinct, outside raw data, and cannot overwrite manifest")
    return a


def main(argv=None):
    args=parse_args(argv); reporter=Reporter(args); reporter.update()
    reporter.log(f"START {VERSION} strategy={args.strategy}; thresholds frozen; no optimization")
    audit_generated=False
    try:
        strategies=["ir1","ir2","ir3"] if args.strategy=="all" else [args.strategy]
        universe=json.loads((ROOT/"research/universe_v3.json").read_text())["symbols"]
        allowed=(STOCKS if any(k!="ir3" for k in strategies) else frozenset()) | ({"SPY","QQQ"} if "ir3" in strategies else set())
        selected=set(s.strip().upper() for s in args.symbols.split(",")) if args.symbols else set(universe)&allowed
        if not selected or not selected<=allowed: raise ValueError("Selected symbols are unmapped/excluded for these IR strategies")
        required=selected|{"SPY","QQQ"}
        if "ir1" in strategies and selected&SEMI: required.add("SOXX")
        reviewed_manifest=args.manifest.is_file()
        provisional=bool(args.provisional and not reviewed_manifest)
        if not reviewed_manifest and not provisional:
            raise ValueError("Reviewed V4 data manifest is missing; rerun with --provisional for an explicitly unreviewed research run")
        audits={}
        if reviewed_manifest:
            manifest=validate_data_manifest(args.manifest,required,"start",data_dir=args.data_dir)
            paths={s:manifest_input_path(args.data_dir,s,manifest["symbols"][s]) for s in sorted(required)}
        else:
            # Provisional mode never fabricates provenance. It binds the exact
            # current bytes and performs the same mechanical audit used below.
            manifest=dict(version=1,timestampKind="start",calendar="XNYS",validationPassed=False,
                          reviewStatus="PROVISIONAL_UNREVIEWED_DATA",symbols={})
            paths={}
            for s in sorted(required):
                paths[s]=source_file(args.data_dir,s)
                reporter.update(phase="data_audit",progress=5*len(paths)/len(required),currentTimestamp=s)
                meta=provisional_inspect_file(paths[s],s,"start",None)
                meta["path"]=paths[s].name
                manifest["symbols"][s]=meta
                audits[s]=meta
        for i,(s,path) in enumerate(paths.items()):
            reporter.update(phase="data_audit",progress=5+5*i/len(paths),currentTimestamp=s)
            if s not in audits:
                audits[s]=inspect_file(path,s,"start",manifest["symbols"][s]["naive_timezone"])
            if not audits[s]["monotonicTimestampOrdering"]:
                audits[s]["validationErrors"].append("Input ordering is non-monotonic; raw files were not rewritten")
            if reviewed_manifest and audits[s]["sha256"]!=manifest["symbols"][s]["sha256"]:
                audits[s]["validationErrors"].append("Manifest hash changed")
        manifest_sha=file_sha256(args.manifest) if reviewed_manifest else None
        audit=dict(version=1,runId=reporter.state["runId"],generatedAt=datetime.now(timezone.utc).isoformat(),timestampKind="start",
                   reviewStatus="REVIEWED_MANIFEST" if reviewed_manifest else "PROVISIONAL_UNREVIEWED_DATA",
                   provisional=provisional,manifestPath=str(args.manifest) if reviewed_manifest else None,manifestSha256=manifest_sha,symbols=audits,
                   dataRoot=str(args.data_dir.resolve()),requiredSymbols=sorted(required),
                   availableSymbols=sorted({p.name[:-7] for p in args.data_dir.glob("*.csv.gz")}|
                                           {p.stem for p in args.data_dir.glob("*.csv")}),
                   validationPassed=all(not x["validationErrors"] for x in audits.values()))
        atomic_json(args.data_audit,audit)
        audit_generated=True
        if not audit["validationPassed"]: raise ValueError("DATA_AUDIT_FAILED: "+json.dumps({s:x["validationErrors"] for s,x in audits.items() if x["validationErrors"]}))
        first=min(x["firstTimestamp"][:10] for x in audits.values()); last=max(x["lastTimestamp"][:10] for x in audits.values())
        schedules=exchange_sessions(first,last)
        args.decisions.parent.mkdir(parents=True,exist_ok=True); args.decisions.write_text("")
        before={s:identity(p.stat()) for s,p in paths.items()}
        trades,funnel,unresolved,days,overlap=run_sessions(day_stream(paths,manifest),schedules,required,strategies,args.slippage_bps,
                     args.from_date,args.to_date,reporter,args.decisions,selected)
        for s,p in paths.items():
            if identity(p.stat())!=before[s] or file_sha256(p)!=audits[s]["sha256"]:
                raise ValueError(f"{s}: source changed during run; results refused")
        data_mode="REVIEWED_MANIFEST" if reviewed_manifest else "PROVISIONAL_UNREVIEWED_DATA"
        result=dict(engine="v4-"+args.strategy,strategy=args.strategy,runId=reporter.state["runId"],generatedAt=datetime.now(timezone.utc).isoformat(),
            configuration=dict(contract="IR_SPEC_V1",implementationVersion=VERSION,strategy=strategies,selectedSymbols=sorted(selected),
                dataMode=data_mode,provisional=provisional,
                parameters={k:BASELINE[k] for k in strategies+["common"]},oneAttemptPerSymbolStrategyDay=True,
                specificationSha256=file_sha256(ROOT/"research/intraday_strategy_formulas_v1.md"),
                codeHashes={p.name:file_sha256(p) for p in (Path(__file__),ROOT/"research/intraday_v4_engine.py",ROOT/"research/intraday_v4_metrics.py")},
                maxPositionsPerStrategy=1,initialCashPerStrategy=10000,notionalUSD=100,fromDate=args.from_date,toDate=args.to_date),
            execution=dict(model="COMPLETED_CLOSE_OBSERVATION_PROXY_60S",timestampKind="start",slippageBpsPerSide=args.slippage_bps,
                halfSpreadProxyBpsPerSide=1,feesUSD=0,feeVerified=False,quoteAgeVerified=False,executabilityConfirmed=False,
                entry="First strictly subsequent completed minute close; missing minute cancels",stop="Observable close, never theoretical stop",
                intrabar="OHLC extremes not executable; sampled-close model only",sourceQuoteTimestamps=None),
            data=dict(auditPath=str(args.data_audit),manifestSha256=audit["manifestSha256"],symbols=sorted(required),
                reviewStatus=data_mode,firstSession=first,lastSession=last,
                pointInTimeEligibility=("Externally reviewed manifest; seed instrument taxonomy; survivor bias remains"
                    if reviewed_manifest else "UNREVIEWED in provisional mode; historical $5/ADV20 eligibility and corporate-action basis may be biased"),
                warnings=[] if reviewed_manifest else [
                    "PROVISIONAL_UNREVIEWED_DATA",
                    "Price-adjustment/corporate-action basis is not externally verified",
                    "Point-in-time eligibility and symbol identity are not externally reviewed",
                    "Do not use this run for final performance claims",
                ]),
            funnel=funnel,overall=summarize(trades,10000*len(strategies)),
            byStrategy=grouped(trades,lambda t:t["strategy"]),bySymbol=grouped(trades,lambda t:t["symbol"]),
            byYear=grouped(trades,lambda t:t["date"][:4]),byMonth=grouped(trades,lambda t:t["date"][:7]),
            bySession=grouped(trades,lambda t:t["session"]),byExitReason=grouped(trades,lambda t:t["exitReason"]),
            byRegime=grouped(trades,lambda t:t["marketRegime"]["direction"]+"/"+t["marketRegime"]["volatility"]),
            byTimeOfDay=grouped(trades,time_bucket),chronologicalEvaluation=chronological_folds(trades,days),
            unresolvedPositions=unresolved,strategyOverlap=dict(**overlap,note="IR1 stocks and IR3 indices have disjoint entry universes; trade-day co-occurrence is not simultaneous trigger overlap"),
            researchVerdict=("PROVISIONAL_UNREVIEWED_DATA" if provisional else "UNVALIDATED_DESCRIPTIVE_ONLY"),specBlocked=[],trades=trades)
        result["overall"]["dailySharpeLike"]=daily_consistency(trades,days,10000*len(strategies))
        for kind in strategies:
            result["byStrategy"].setdefault(kind,summarize([]))
        atomic_json(args.out,result)
        args.trades_csv.parent.mkdir(parents=True,exist_ok=True)
        fields=sorted(set().union(*(t.keys() for t in trades))) if trades else ["strategy","symbol","entryTimestamp","exitTimestamp","returnPct","netR"]
        with args.trades_csv.open("w",encoding="utf-8",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
            for t in trades: w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in t.items()})
        reporter.update(phase="completed",running=False,progress=100,summary=result["overall"],funnel=funnel,
                        dataMode=data_mode,provisional=provisional,unresolvedPositions=len(unresolved),error=None)
        reporter.log(f"COMPLETED trades={len(trades)} unresolved={len(unresolved)}; no profitability claim")
        print(json.dumps(dict(engine=result["engine"],overall=result["overall"],out=str(args.out)),ensure_ascii=False))
        return 0
    except Exception as exc:
        if not audit_generated: atomic_json(args.data_audit,dict(runId=reporter.state["runId"],validationPassed=False,error=str(exc)))
        reporter.update(phase="error",running=False,error=str(exc))
        reporter.log("ERROR "+str(exc))
        raise


if __name__=="__main__":
    raise SystemExit(main())
