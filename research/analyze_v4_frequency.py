#!/usr/bin/env python3
"""Read a completed campaign WITHOUT market data or a backtest rerun.

Creates a fresh UUID directory. Terminal decisions are streamed, aggregate
facets are marginal, and missing gate measurements remain NOT_MEASURED.
This tool does not choose, implement, or tune a candidate.
"""
import argparse
import csv
import json
import math
import os
import statistics
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    from .compare_v4_research import analyze, comparable, input_hash, MAX_JSON_BYTES
    from .backtest_intraday_v4 import atomic_json
    from .intraday_v4_funnel import bucket
    from .intraday_v4_engine import benchmark, SEMI
except ImportError:
    from compare_v4_research import analyze, comparable, input_hash, MAX_JSON_BYTES
    from backtest_intraday_v4 import atomic_json
    from intraday_v4_funnel import bucket
    from intraday_v4_engine import benchmark, SEMI


def read_json(path):
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError(f"JSON size limit exceeded: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def top_reasons(counts):
    total=sum(counts.values())
    return [dict(reason=r,count=n,pct=100*n/total if total else None)
            for r,n in sorted(counts.items(),key=lambda x:(-x[1],x[0]))[:20]]


def decision_breakdown(path, wanted=()):
    """Terminal event counts, not minute gate attempts. Bounded per-symbol/day details."""
    if not path.is_file():
        return dict(status="UNAVAILABLE",reason="decisions.jsonl missing"),{}
    before=input_hash(path)
    facets={}; totals=Counter(); stages=Counter(); matched={}; wanted=set(wanted)
    with path.open(encoding="utf-8") as stream:
        while True:
            line=stream.readline(2*1024*1024)
            if not line: break
            if len(line)>=2*1024*1024:
                raise ValueError("Decision record exceeds bounded line limit")
            event=json.loads(line)
            k,s,day=event["strategy"],event["symbol"],event["date"]
            d=event.get("diagnostics",{})
            if (s,day) in wanted: matched[(s,day)]=event
            # One event exists per strategy/symbol/day, and states can transition
            # multiple times inside a clock decision. Count each attained state once.
            for state in {t["toState"] for t in event.get("transitions",[])}:
                stages[k+":"+state]+=1
            if event["state"]!="CANCELLED": continue
            reason=d.get("reason") or "UNCLASSIFIED_TERMINAL_CANCELLATION"
            totals[k+":"+reason]+=1
            rc=d.get("rejectionContext",{})
            ctx=rc.get("marketContext") or d.get("signalContext") or {}
            ts=rc.get("decisionTime") or next((t.get("timestamp") for t in reversed(event.get("transitions",[])) if t.get("timestamp")),None)
            if ts:
                T=datetime.fromisoformat(ts)
                if T.tzinfo is None: raise ValueError("Naive decision timestamp")
                time=bucket(T)
            else: time="UNAVAILABLE"
            refs=rc.get("references",{})
            sector=refs.get("SOXX",{}).get("snapshot")
            sector_state=("NOT_REQUIRED" if s not in SEMI else "UNAVAILABLE" if not sector
                          else "MISSING_FEATURES" if sector.get("u20") is None or sector.get("vwap") is None
                          else "CONFIRMED" if sector["u20"]>=0 and sector["close"]>sector["vwap"] else "NOT_CONFIRMED")
            values=dict(strategy=k,year=day[:4],symbol=s,regime=ctx.get("direction","UNAVAILABLE")+"/"+ctx.get("volatility","UNAVAILABLE"),
                        timeOfDay=time,benchmark=benchmark(s),marketConfirmation=str(ctx.get("qqq"))+"/"+str(ctx.get("spy")),sectorContext=sector_state)
            for facet,label in values.items():
                facets.setdefault(facet,{}).setdefault(label,Counter())[k+":"+reason]+=1
    if input_hash(path)!=before: raise ValueError(f"Decisions changed during analysis: {path}")
    return dict(status="AVAILABLE",sha256=before,terminalReasons=totals,top20=top_reasons(totals),byFacet=facets,
                attainedStates=stages,unit="one terminal cancellation per strategy/symbol/session",
                coverage="IDLE sessions without setup are absent; unavailable context is not reconstructed"),matched


def frequency_metrics(result):
    a=analyze(result)
    trades=result["trades"]
    years={t["date"][:4] for t in trades}
    # Include zero-trade years from the declared evaluated range, not just traded years.
    cfg=result["configuration"]; data=result.get("data",{})
    lo=cfg.get("fromDate") or data.get("firstSession")
    hi=cfg.get("toDate") or data.get("lastSession")
    if lo and hi: years.update(str(y) for y in range(int(lo[:4]),int(hi[:4])+1))
    a.update(medianR=statistics.median(t["netR"] for t in trades) if trades else None,
             tradesByYearIncludingEmpty={y:sum(t["date"][:4]==y for t in trades) for y in sorted(years)},
             calendarYearsTouched=len(years),tradesPerCalendarYearTouched=len(trades)/len(years) if years else None,
             annualFrequencyNote="Partial first/last years included; not a 252-session annualization",
             productionReady=False)
    return a


def pnl_semantics(result):
    notional=result["configuration"].get("notionalUSD")
    if not isinstance(notional,(int,float)) or not math.isfinite(notional) or notional<=0:
        return dict(status="UNVERIFIED",reason="Fixed notional not declared")
    violations=[]
    for t in result["trades"]:
        if not math.isclose(t["quantity"]*t["entryPrice"],notional,rel_tol=1e-9,abs_tol=1e-8) or not math.isclose(t["pnlUsd"],notional*t["returnPct"]/100,rel_tol=1e-9,abs_tol=1e-8):
            violations.append(dict(symbol=t["symbol"],date=t["date"],pnlUsd=t["pnlUsd"]))
    return dict(status="MISMATCH" if violations else "PASS_FIXED_NOTIONAL_IDENTITY" if result["trades"] else "NO_TRADES_TO_VERIFY",notionalUSD=notional,checkedTrades=len(result["trades"]),
        formula="quantity=N/entryFill; pnlUsd=N*(exitFill/entryFill-1)=N*returnPct/100",
        numericEqualityAt100USD=notional==100,violations=violations,
        warning="Sum trade returns is not portfolio return; account return uses actual declared initial capital")


def compare_admissions(baseline,candidate,baseline_decisions,candidate_decisions):
    def key(t): return (t["symbol"],t["date"],t["triggerTimestamp"])
    old={key(t):t for t in baseline["trades"]}; new={key(t):t for t in candidate["trades"]}
    def detail(t,other):
        event=other.get((t["symbol"],t["date"]))
        fields=(event or {}).get("diagnostics",{})
        return dict(trade=t,otherVariantEvent=event,
            otherVariantTerminalReason=fields.get("reason"),
            exactSameSetup=bool(event and fields.get("setupTimestamp")==t.get("setupTimestamp")),
            maintenanceAdmissions=t.get("diagnostics",{}).get("maintenanceAdmissions"),
            note="Same-day event is descriptive, not proof of one causal gate; missing maintenance observations are not inferred")
    admitted=[new[k] for k in sorted(new.keys()-old.keys())]
    lost=[old[k] for k in sorted(old.keys()-new.keys())]
    return dict(identity="symbol + trading date + actual trigger timestamp; shifted triggers are separate",
        sharedTriggers=len(old.keys() & new.keys()),newlyAdmitted=[detail(t,baseline_decisions) for t in admitted],
        noLongerAdmitted=[detail(t,candidate_decisions) for t in lost],
        newlyAdmittedMetrics=frequency_metrics(dict(candidate,trades=admitted)),
        noLongerAdmittedMetrics=frequency_metrics(dict(baseline,trades=lost)),
        interpretation="Do not assume four versus two means two extra identical-baseline trades; inspect trigger identities and lost trades")


def analyze_campaign(path,progress=None):
    path=Path(path)
    campaign_sha=input_hash(path/"campaign.json")
    campaign=read_json(path/"campaign.json")
    if campaign.get("phase")!="completed": raise ValueError("Campaign must be completed; no partial performance claim")
    wanted_names={"baseline-ir1-2bps","baseline-ir2-2bps","baseline-ir3-2bps","baseline-all-2bps","ir1-r1-ir1-2bps"}
    jobs=campaign.get("jobs",[])
    if {j["name"] for j in jobs}!=wanted_names or len(jobs)!=5:
        raise ValueError("Expected exactly the five baseline/IR1-R1 campaign jobs")
    if {j["name"] for j in campaign.get("completed",[])}!=wanted_names:
        raise ValueError("Campaign completed list does not contain all five jobs")
    results={}; audits={}; sources={}; report={}; matched={}; shared_hashes={}
    for job in jobs:
        name=job["name"]; result_path=path/name/"result.json"
        before=input_hash(result_path); result=read_json(result_path);audit=None;audit_sha=None
        if not isinstance(result.get("trades"),list): raise ValueError("Result has no trade records")
        audit_path=path/name/"audit.json"
        if audit_path.is_file():
            audit_sha=input_hash(audit_path);audit=read_json(audit_path)
            if input_hash(audit_path)!=audit_sha: raise ValueError("Audit mutated during read: "+name)
        if audit and audit.get("runId")!=result.get("runId"): raise ValueError(f"Audit/result runId mismatch: {name}")
        if audit:
            if not audit.get("validationPassed"): raise ValueError(f"Mechanical audit failed: {name}")
            for symbol,item in audit.get("symbols",{}).items():
                digest=item.get("sha256")
                if not digest: raise ValueError(f"Source hash missing: {name}/{symbol}")
                if shared_hashes.setdefault(symbol,digest)!=digest:
                    raise ValueError(f"Source hash changed across campaign jobs: {symbol}")
                declared=campaign.get("sourceHashes",{}).get(symbol)
                if declared is not None and declared!=digest:
                    raise ValueError(f"Campaign/audit source hash mismatch: {symbol}")
        if result["configuration"].get("contract")!="IR_SPEC_V1": raise ValueError("Unexpected strategy contract")
        expected_variant="ir1-r1" if name.startswith("ir1-r1") else "baseline"
        expected_strategy=["ir1","ir2","ir3"] if name=="baseline-all-2bps" else ["ir1" if expected_variant!="baseline" else name.split("-")[1]]
        if result["configuration"].get("strategy")!=expected_strategy or result["configuration"].get("researchVariant","baseline")!=expected_variant:
            raise ValueError(f"Job/result variant or strategy mismatch: {name}")
        if result.get("execution",{}).get("timestampKind") not in {"start","end"}: raise ValueError("Unknown timestamp semantics")
        if input_hash(result_path)!=before: raise ValueError(f"Result changed during read: {name}")
        results[name]=result; audits[name]=audit
        sources[name]=dict(resultPath=str(result_path),sha256=before,runId=result.get("runId"),
            configuration=result["configuration"],execution=result.get("execution"),data=result.get("data"),
            auditPath=str(audit_path) if audit_path.is_file() else None,
            auditSha256=audit_sha)
    for name,result in results.items():
        if progress: progress(name,len(report),len(jobs))
        other=results["ir1-r1-ir1-2bps"] if name=="baseline-ir1-2bps" else results["baseline-ir1-2bps"]
        keys={(t["symbol"],t["date"]) for t in other["trades"]} if name in {"baseline-ir1-2bps","ir1-r1-ir1-2bps"} else set()
        decisions,matched[name]=decision_breakdown(path/name/"decisions.jsonl",keys)
        sources[name].update(decisionsPath=str(path/name/"decisions.jsonl") if decisions.get("sha256") else None,
                             decisionsSha256=decisions.get("sha256"))
        diagnostics=result.get("diagnostics",{})
        measurement=result.get("frequencyDiagnostics",dict(status="NOT_MEASURED"))
        per_strategy={}
        for strategy,d in diagnostics.items():
            stages=d.get("stageFunnel",[])
            setup=d.get("stages",{}).get("setupValid",d.get("stages",{}).get("conditionPassed",0))
            signals=d.get("stages",{}).get("signalEmitted",0)
            entries=d.get("stages",{}).get("tradeEntered",0)
            sessions=d.get("observationCounts",{}).get("sessionsTotal",0)
            lifecycle_rows=[];previous=sessions
            for row in stages:
                n=row["count"]
                nested=n<=previous
                lifecycle_rows.append(dict(stage=row["stage"],unit="unique symbol/strategy/session",input=previous,
                    passed=n,rejected=previous-n if nested else None,rejectPct=100*(previous-n)/previous if nested and previous else None,
                    survivalFromAllSymbolStrategySessions=n/sessions if sessions else None,
                    denominatorWarning=None if nested else "Non-nested historical stage; do not infer rejection count"))
                previous=n
            if stages:
                completed=sum(t["strategy"]==strategy for t in result["trades"])
                lifecycle_rows.append(dict(stage="completedTrade",unit="unique symbol/strategy/session",input=entries,
                    passed=completed,rejected=entries-completed if entries>=completed else None,
                    rejectPct=100*(entries-completed)/entries if entries and entries>=completed else None,
                    survivalFromAllSymbolStrategySessions=completed/sessions if sessions else None,
                    denominatorWarning="Unresolved positions are not completed trades"))
            per_strategy[strategy]=dict(stages=stages,lifecycleRows=lifecycle_rows,stageCounts=d.get("stages",{}),
                observationCounts=d.get("observationCounts",{}),clockCoverage=d.get("clockCoverage",{}),
                terminalTop20=top_reasons(d.get("rejectReasons",{})),
                observationTop20=top_reasons(d.get("observationRejectReasons",{})),
                sessionExclusions=d.get("sessionExclusionReasons",{}),
                directionFailureBreakdown=d.get("directionFailureBreakdown"),samples=d.get("samplesByReason",{}),
                observationsAndTerminalEventsAreSeparate=True,
                entryPerSetup=entries/setup if setup else None,entryPerSignal=entries/signals if signals else None,
                entryPerSymbolStrategySession=entries/sessions if sessions else None,
                lifecycleWarning="stageFunnel ratios retained as logged; branches are not one nested minute waterfall")
        report[name]=dict(metrics=frequency_metrics(result),pnlSemantics=pnl_semantics(result),
            lifecycle=per_strategy,conditionalGates=measurement,terminalDecisionBreakdown=decisions,
            gateMeasurementStatus="AVAILABLE" if measurement.get("strategies") else "NOT_MEASURED",
            noInventedCounts="Absent gate input/pass/reject counts require an instrumented replay; setup/trade totals do not identify them")
    baseline=results["baseline-ir1-2bps"]; candidate=results["ir1-r1-ir1-2bps"]
    comparison=comparable(baseline,candidate,(audits["baseline-ir1-2bps"],audits["ir1-r1-ir1-2bps"]))
    admissions=compare_admissions(baseline,candidate,matched["baseline-ir1-2bps"],matched["ir1-r1-ir1-2bps"]) if comparison["comparable"] else dict(status="BLOCKED_NONCOMPARABLE")
    # Recheck source files, including audit, after all analysis; never write inputs.
    for name,source in sources.items():
        if input_hash(Path(source["resultPath"]))!=source["sha256"]:
            raise ValueError("Result mutated during analysis: "+name)
        if source["auditPath"] and input_hash(Path(source["auditPath"]))!=source["auditSha256"]:
            raise ValueError("Audit mutated during analysis: "+name)
        if source["decisionsPath"] and input_hash(Path(source["decisionsPath"]))!=source["decisionsSha256"]:
            raise ValueError("Decisions mutated during analysis: "+name)
    if input_hash(path/"campaign.json")!=campaign_sha: raise ValueError("Campaign changed during analysis")
    return dict(version=1,generatedAt=datetime.now(timezone.utc).isoformat(),sourceCampaign=str(path),
        sourceCampaignSha256=campaign_sha,sources=sources,jobs=report,comparison=comparison,
        ir1R1Admissions=admissions,verdict="INCONCLUSIVE",candidateSelection="BLOCKED_PENDING_QUANTIFIED_BOTTLENECK_REVIEW",
        dataModes=sorted({r["configuration"].get("dataMode","UNKNOWN") for r in results.values()}),
        classification={name:"KEEP_BASELINE" if name.startswith("baseline") else "CONTINUE_RESEARCH" for name in results},
        warnings=["Keeper label preserves controls; no version is production-ready",
                  "Mechanical audit does not certify provenance, executable quotes, fees, or edge",
                  "Present-day universe retains survivorship bias; no historical membership claim",
                  "No new candidate, tuning, market-data access, or simulation occurred in this analysis"])


def write_report(directory,report):
    atomic_json(directory/"v4_funnel_diagnostics.json",report)
    lines=["# V4 frequency diagnostics", "", "Actual completed artifacts; no market-data rerun. Verdict: **INCONCLUSIVE**.","",
           "| Job | Trades | Expectancy R | Median R | PF R | MDD R | Nonempty OOS / all OOS | Last OOS trades | Gates |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    def fmt(v): return "—" if v is None else f"{v:.4f}"
    for name,item in report["jobs"].items():
        m=item["metrics"]; overall=m["overall"]; f=m["chronologicalEvaluation"]; last=m["finalCompleteOOSFold"]
        lines.append(f"| {name} | {overall['trades']} | {fmt(overall['expectancyR'])} | {fmt(m['medianR'])} | {fmt(overall['profitFactorR'])} | {fmt(overall['maxDrawdownR'])} | {f['nonemptyOOSFolds']} / {f['totalOOSFolds']} | {last['oos']['trades'] if last else '—'} | {item['gateMeasurementStatus']} |")
        with (directory/(name+"_funnel.csv")).open("x",newline="") as stream:
            w=csv.writer(stream);w.writerow(("strategy","scope","unit","gate","input","pass","reject","rejectPct","survivalFromChainInput"))
            for strategy,chains in item["conditionalGates"].get("strategies",{}).items():
                for scope,chain in chains.items():
                    for g in chain["gates"]:
                        w.writerow((strategy,scope,chain["unit"],g["gate"],g["input"],g["passed"],g["rejected"],g["rejectPct"],g["survivalFromChainInput"]))
        with (directory/(name+"_lifecycle.csv")).open("x",newline="") as stream:
            w=csv.writer(stream);w.writerow(("strategy","stage","unit","input","pass","reject","rejectPct","survivalFromAllSymbolStrategySessions"))
            for strategy,d in item["lifecycle"].items():
                for row in d["lifecycleRows"]:
                    w.writerow((strategy,row["stage"],row["unit"],row["input"],row["passed"],row["rejected"],row["rejectPct"],row["survivalFromAllSymbolStrategySessions"]))
        lines.extend(["",f"## {name}","", "Terminal rejection reasons (not repeated minute waits):",""])
        for strategy,d in item["lifecycle"].items():
            lines.append(f"{strategy}: entry/setup={d['entryPerSetup']}; entry/signal={d['entryPerSignal']}; entry/symbol-strategy-session={d['entryPerSymbolStrategySession']}")
            lines.extend(f"- {r['reason']}: {r['count']} ({r['pct']:.2f}%)" for r in d["terminalTop20"])
        if item["gateMeasurementStatus"]=="NOT_MEASURED":
            lines.extend(["", "Exact nested gate counts were not logged. CSV is header-only; no inferred counts. Use --funnel-diagnostics on a new campaign."])
    lines.extend(["", "## IR1-R1 admissions", "", "Comparable: "+str(report["comparison"]["comparable"]),
                  "Inspect JSON ir1R1Admissions for full trade records, lost/replaced triggers, same-day baseline cancellations, MFE/MAE and latest OOS.",
                  "", "## Accounting", "", "With fixed N=$100, pnlUsd=N*returnPct/100 is numerically returnPct. Portfolio percentage uses $10,000 per strategy. No sizing or accounting change.",
                  "", "## Next decision", "", "New variants remain blocked until the quantified dominant gate is reviewed. At most two hypotheses may then be preregistered; no search, threshold tuning, or winner chosen here."])
    (directory/"v4_funnel_diagnostics.md").write_text("\n".join(lines)+"\n")
    (directory/"candidate_hypotheses.md").write_text("# Candidate preregistration\n\nStatus: BLOCKED_PENDING_QUANTIFIED_BOTTLENECK_REVIEW\n\nNo new variant selected. First inspect measured gate counts, source identity, historical availability, IR3 clock conjunctions and newly admitted IR1-R1 trades. Then preregister at most two mechanisms, changed gate, causal rationale, expected frequency, failure modes and rejection criteria before one chronological evaluation. Baseline, costs and exits stay frozen.\n")


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument("--campaign",type=Path,help="Directory containing completed campaign.json")
    source.add_argument("--research-root",type=Path,help="Select latest completed */full/*/campaign.json (no raw-data scan)")
    p.add_argument("--timestamp-kind",choices=("start","end"),help="Assert expected semantics of every existing result; never reinterpret data")
    p.add_argument("--results-dir",type=Path,help="Fresh analysis subdirectory is created here; default campaign run root/funnel")
    args=p.parse_args(argv)
    if args.campaign:
        campaign=args.campaign.resolve()
    else:
        files=sorted(args.research_root.glob("*/full/*/campaign.json"),key=lambda f:f.stat().st_mtime,reverse=True)
        selected=next((f for f in files if read_json(f).get("phase")=="completed"),None)
        if selected is None: p.error("No completed full campaign under research root")
        campaign=selected.parent.resolve()
    root=(args.results_dir or campaign.parent.parent/"funnel").resolve()
    if {"toss_1m","toss_ticks"} & set(root.parts): p.error("Analysis output cannot be under raw-data directories")
    if root==campaign or root.is_relative_to(campaign): p.error("Outputs cannot be inside source campaign")
    directory=root/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True,exist_ok=False)
    def state(**kw): atomic_json(directory/"state.json",dict(pid=os.getpid(),updatedAt=datetime.now(timezone.utc).isoformat(),**kw))
    state(phase="running",stage="funnel",progress=0,sourceCampaign=str(campaign),running=True)
    try:
        def progress(name,done,total):
            state(phase="running",stage="funnel",progress=100*done/total,currentJob=name,completedJobs=done,totalJobs=total,running=True)
        report=analyze_campaign(campaign,progress)
        if args.timestamp_kind and any(s["execution"]["timestampKind"]!=args.timestamp_kind for s in report["sources"].values()):
            raise ValueError("Campaign timestampKind does not match explicit analysis assertion")
        write_report(directory,report)
        state(phase="completed",stage="funnel",progress=100,completedJobs=5,totalJobs=5,running=False,verdict=report["verdict"],
              candidateSelection=report["candidateSelection"],sourceCampaign=str(campaign),resultPath=str(directory/"v4_funnel_diagnostics.json"))
        print(json.dumps(dict(directory=str(directory),verdict=report["verdict"],comparison=report["comparison"])))
        return 0
    except Exception as exc:
        state(phase="error",stage="funnel",running=False,error=str(exc))
        raise


if __name__=="__main__": raise SystemExit(main())
