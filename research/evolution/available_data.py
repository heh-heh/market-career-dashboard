"""Availability evidence and immutable selection plans, separate from V07 baselines."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from research.backtest_intraday_v4 import atomic_json
from research.backtest_v4_strategies import MINUTE, exchange_sessions, file_sha256
from research.build_v4_data_manifest import source_file
from research.evolution.data_access import check_dates, inspect_window, scheduled_days
from research.evolution.features import MinuteFeatureProvider, benchmark_for_symbol
from research.evolution.config import Config

ROOT=Path(__file__).resolve().parents[2]
REFERENCES=("SPY","QQQ","SOXX")
POLICY="V07_CAUSAL_AVAILABLE_PREFIX_V1"
BIAS=["EX_POST_SESSION_COMPLETENESS_SELECTION: failed reference days are excluded after inspection",
      "DYNAMIC_UNIVERSE: eligible symbols vary by observed prefix; not a fixed-universe annual estimate",
      "POLICY_DEPENDENT_WINDOW_REJECTION: a held-symbol gap discards the entire population window",
      "MISSINGNESS_MAY_CORRELATE_WITH_VOLATILITY_HALTS_IPO_HISTORY_OR_PROVIDER_COVERAGE",
      "CURRENT_UNIVERSE_SURVIVORSHIP_UNRESOLVED: no point-in-time universe claim"]


def new_output(path, data_root=None):
    path=Path(path).resolve()
    protected=[(ROOT/"data").resolve()]
    if data_root: protected.append(Path(data_root).resolve())
    if any(path==p or p in path.parents for p in protected):
        raise ValueError("Output must be outside raw data")
    path.mkdir(parents=True,exist_ok=False)
    return path


def append(path,value):
    with Path(path).open("a",encoding="utf-8") as f:
        f.write(json.dumps(value,sort_keys=True,allow_nan=False)+"\n")


def dependencies(symbol):
    benchmark,sector=benchmark_for_symbol(symbol)
    return sorted(set(REFERENCES)|{benchmark,sector})


def minute_ranges(missing):
    """Exact END-label ranges, inclusive, one-minute spacing. No missing-time loss."""
    result=[]
    for t in missing:
        if result and t==datetime.fromisoformat(result[-1]["lastEnd"])+MINUTE:
            result[-1]["lastEnd"]=t.isoformat();result[-1]["count"]+=1
        else:
            result.append(dict(firstEnd=t.isoformat(),lastEnd=t.isoformat(),count=1))
    return result


def day_evidence(day,rows,opening,closing,symbols,source_errors):
    expected=int((closing-opening)/MINUTE)
    facts={}
    for s in symbols:
        minutes=rows.get(s,{})
        observed=[opening+i*MINUTE for i in range(expected) if opening+i*MINUTE in minutes]
        absent=[opening+(i+1)*MINUTE for i in range(expected) if opening+i*MINUTE not in minutes]
        prefix=0;volume=0.
        for i in range(expected):
            bar=minutes.get(opening+i*MINUTE)
            if bar is None:break
            prefix+=1;volume+=bar.v
        # Existing provider needs 31 contiguous closes, a full session VWAP prefix
        # and positive cumulative volume. This is availability, not a new signal.
        usable=prefix>=31 and volume>0
        reason=source_errors.get(s) or ("SESSION_MISSING" if not observed else
                  "REGULAR_MINUTES_MISSING" if absent else None)
        facts[s]=dict(symbol=s,sessionDate=day,expectedMinutes=expected,observedMinutes=len(observed),
            missingMinutes=len(absent),complete=not absent and s not in source_errors,
            firstRegularEnd=(observed[0]+MINUTE).isoformat() if observed else None,
            lastRegularEnd=(observed[-1]+MINUTE).isoformat() if observed else None,
            contiguousPrefixMinutes=prefix,causalPrefixUsable=usable and s not in source_errors,
            missingMinuteRanges=minute_ranges(absent),reason=reason,benchmarkDependencies=dependencies(s))
    return facts


def audit_all(data_root,output,first="2022-01-01",last="2025-12-31",symbols=None,naive_timezone=None):
    check_dates(first,last)
    if first < "2022-01-01":raise ValueError("Audit starts no earlier than 2022")
    data_root=Path(data_root).resolve()
    if not data_root.is_dir():raise ValueError("Data root does not exist")
    allowed=set(json.loads((ROOT/"research/universe_v3.json").read_text())["symbols"])
    symbols=sorted(allowed if symbols is None else set(symbols))
    if not symbols or not set(symbols)<=allowed:raise ValueError("Use universe_v3 symbols")
    needed=sorted(set(symbols)|set(REFERENCES))
    output=new_output(output,data_root)
    paths={};meta={};errors={}
    for s in needed:
        present=[p for p in (data_root/(s+".csv"),data_root/(s+".csv.gz")) if p.exists() or p.is_symlink()]
        if not present:
            errors[s]="SOURCE_FILE_MISSING";continue
        # Ambiguous sources, invalid schema, conflicts and timezone errors block
        # the audit. Never silently drop a corrupted/escaped source from research.
        path=source_file(data_root,s)
        meta[s]=inspect_window(path,s,first,last,naive_timezone,allow_empty=True)
        paths[s]=path
    schedules=exchange_sessions(first,last)
    if not schedules:raise ValueError("No XNYS sessions in audit range")
    totals={s:dict(completeSessions=0,partialSessions=0,missingSessions=0,expectedMinutes=0,
                  observedMinutes=0,causalUsableSessions=0,completeDates=[],missingDates=[],partialDates=[]) for s in needed}
    regime_totals=Counter();eligible=0
    # Classification uses only SPY/QQQ, not per-bot/per-symbol feature copies.
    cfg=Config(**json.loads((ROOT/"research/evolution/v07_5d_regime_timing_config.json").read_text()))
    for day_index,(day,rows) in enumerate(scheduled_days(paths,{"symbols":meta},schedules),1):
        if day_index%25==1:
            from research.evolution.audit_v07_data import resource_preflight
            resource_preflight(output)
        opening,closing=schedules[day]
        facts=day_evidence(day,rows,opening,closing,needed,errors)
        for s,f in facts.items():
            append(output/"symbol_sessions.jsonl",f)
            x=totals[s];x["expectedMinutes"]+=f["expectedMinutes"];x["observedMinutes"]+=f["observedMinutes"]
            state="complete" if f["complete"] else "missing" if f["observedMinutes"]==0 else "partial"
            x[state+"Sessions"]+=1;x[state+"Dates"].append(day)
            x["causalUsableSessions"]+=f["causalPrefixUsable"]
        reference_failures=[dict(symbol=s,reason=facts[s]["reason"]) for s in REFERENCES if not facts[s]["complete"]]
        usable=[s for s in symbols if facts[s]["causalPrefixUsable"]]
        accepted=not reference_failures and bool(usable)
        reason="BENCHMARK_SESSION_INCOMPLETE" if reference_failures else "NO_CAUSAL_SYMBOL_PREFIX" if not usable else None
        provider=MinuteFeatureProvider(cfg).prepare({s:rows.get(s,{}) for s in ("SPY","QQQ")},opening,closing,[])
        regimes=Counter(x["regime"] if x["valid"] else "CONTEXT_UNAVAILABLE" for x in provider.market.values())
        regime_totals.update(regimes);eligible+=accepted
        append(output/"sessions.jsonl",dict(sessionDate=day,expectedMinutes=int((closing-opening)/MINUTE),
            eligible=accepted,reason=reason,referenceFailures=reference_failures,
            usablePrefixSymbols=usable,partialPrefixSymbols=[s for s in usable if not facts[s]["complete"]],completeSymbols=[s for s in symbols if facts[s]["complete"]],
            unavailableSymbols={s:facts[s]["reason"] or "INSUFFICIENT_PREFIX_OR_VOLUME" for s in symbols if s not in usable},
            rawUniverseComplete=all(f["complete"] for f in facts.values()),regimeMinutes=dict(regimes)))
    for s,p in paths.items():
        if file_sha256(p)!=meta[s]["sha256"]:raise ValueError(f"{s}: source mutated during audit")
    for s,x in totals.items():
        x["minuteCompletenessPct"]=100*x["observedMinutes"]/x["expectedMinutes"] if x["expectedMinutes"] else 0
        x["sessionCompletenessPct"]=100*x["completeSessions"]/len(schedules)
        x["sourceError"]=errors.get(s)
    git=subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,text=True,capture_output=True)
    result=dict(version="V07_AVAILABLE_DATA_AUDIT_1",researchOnly=True,createdAt=datetime.now(timezone.utc).isoformat(),
        gitCommit=git.stdout.strip(),dataRoot=str(data_root),fromDate=first,toDate=last,timestampKind="end",calendar="XNYS",
        finalOosGate="CLOSED",symbols=symbols,sourceManifest=meta,missingSourceSymbols=errors,
        dataMode="PROVISIONAL_UNREVIEWED_DATA",priceAdjustmentConfirmed=False,
        mechanicalAuditCompleted=True,rawUniverseComplete=all(x["completeSessions"]==len(schedules) for x in totals.values()),
        sessions=len(schedules),eligibleSessions=eligible,perSymbol=totals,regimeMinutes=dict(regime_totals),
        selectionBiasWarnings=BIAS,
        files={n:dict(sha256=file_sha256(output/n),bytes=(output/n).stat().st_size) for n in ("sessions.jsonl","symbol_sessions.jsonl")})
    atomic_json(output/"audit.json",result)
    return output


def load_audit(directory):
    directory=Path(directory).resolve()
    info=json.loads((directory/"audit.json").read_text())
    if info["version"]!="V07_AVAILABLE_DATA_AUDIT_1" or info.get("finalOosGate")!="CLOSED":raise ValueError("Invalid audit version/gate")
    check_dates(info["fromDate"],info["toDate"])
    if set(info["files"])!={"sessions.jsonl","symbol_sessions.jsonl"}:raise ValueError("Audit evidence hashes required")
    for name,expected in info["files"].items():
        if name not in ("sessions.jsonl","symbol_sessions.jsonl") or file_sha256(directory/name)!=expected["sha256"]:
            raise ValueError("AUDIT_HASH_MISMATCH")
    return info


def window_facts(sessions):
    windows=[];pending=[];streak=[];rolling=0;interrupted=0;rejected=Counter();smoke=None
    for s in sessions:
        if not "2022-01-01"<=s["sessionDate"]<="2023-12-31":continue
        if not s["eligible"]:
            interrupted+=bool(pending);rejected[s["reason"]]+=1;pending=[];streak=[];continue
        pending.append(s["sessionDate"]);streak.append(s["sessionDate"])
        if len(streak)>=5:rolling+=1
        if len(streak)>=10 and smoke is None:smoke=dict(fromDate=streak[-10],toDate=streak[-1],sessions=10)
        if len(pending)==5:windows.append(pending);pending=[]
    return dict(trainingWindows=windows,usableNonoverlappingWindows=len(windows),potentialRollingWindows=rolling,
                interruptedPartialWindows=interrupted,excludedTrainingSessions=sum(rejected.values()),
                excludedReasons=dict(rejected),trailingUnselectedDays=pending,smokeRange=smoke)


def select_windows(audit_dir,output):
    audit_dir=Path(audit_dir).resolve();audit=load_audit(audit_dir)
    output=new_output(output,audit["dataRoot"])
    sessions=[]
    with (audit_dir/"sessions.jsonl").open() as stream:
        for line in stream:sessions.append(json.loads(line))
    expected=list(exchange_sessions(audit["fromDate"],audit["toDate"]))
    if [s["sessionDate"] for s in sessions]!=expected:raise ValueError("Audit calendar order/count mismatch")
    windows_info=window_facts(sessions)
    proportions={}
    for split,lo,hi in (("train","2022-01-01","2023-12-31"),("internal_holdout","2024-01-01","2024-12-31"),("development_validation","2025-01-01","2025-12-31")):
        subset=[s for s in sessions if lo<=s["sessionDate"]<=hi];counts=Counter();accepted=Counter()
        for s in subset:
            counts.update(s["regimeMinutes"])
            if s["eligible"]:accepted.update(s["regimeMinutes"])
        denominator=sum(counts.values());selected_total=sum(accepted.values())
        proportions[split]=dict(requestedSessions=len(subset),eligibleSessions=sum(s["eligible"] for s in subset),
            allRegimeMinutes=dict(counts),eligibleRegimeMinutes=dict(accepted),
            allRegimeProportions={r:n/denominator for r,n in counts.items()} if denominator else {},
            eligibleRegimeProportions={r:n/selected_total for r,n in accepted.items()} if selected_total else {},
            fullCalendarCovered=len(subset)==len(exchange_sessions(lo,hi)),
            rawUniverseComplete=bool(subset) and all(s["rawUniverseComplete"] for s in subset))
    plan=dict(version="V07_AVAILABLE_PLAN_1",researchOnly=True,dataPolicy=POLICY,finalOosGate="CLOSED",
        dataRoot=audit["dataRoot"],auditDir=str(audit_dir),auditSha256=file_sha256(audit_dir/"audit.json"),
        sourceManifest=audit["sourceManifest"],missingSourceSymbols=audit["missingSourceSymbols"],symbols=audit["symbols"],
        fromDate=audit["fromDate"],toDate=audit["toDate"],timestampKind="end",evaluationWindowSessions=5,
        sessions=sessions,**windows_info,splitCoverage=proportions,
        baselineComparability=dict(dataSelectionComparable=not windows_info["excludedTrainingSessions"] and all(s["rawUniverseComplete"] for s in sessions if s["sessionDate"]<"2024-01-01"),
            reason="Only identical dates, hashes, population, seed, config and no effective missing-data masks permit paired comparison"),
        selectionBiasWarnings=BIAS,dataMode="PROVISIONAL_UNREVIEWED_DATA",priceAdjustmentConfirmed=False)
    atomic_json(output/"selection_plan.json",plan)
    return output/"selection_plan.json"


def verify_plan(path):
    path=Path(path).resolve();plan=json.loads(path.read_text())
    if plan.get("version")!="V07_AVAILABLE_PLAN_1" or plan.get("dataPolicy")!=POLICY or plan.get("timestampKind")!="end" or plan.get("evaluationWindowSessions")!=5:
        raise ValueError("Unsupported selection plan")
    check_dates(plan["fromDate"],plan["toDate"])
    audit_path=Path(plan["auditDir"])/"audit.json"
    if file_sha256(audit_path)!=plan["auditSha256"]:raise ValueError("AUDIT_HASH_MISMATCH")
    audit=load_audit(plan["auditDir"])
    if plan.get("finalOosGate")!="CLOSED" or (audit["fromDate"],audit["toDate"])!=(plan["fromDate"],plan["toDate"]):
        raise ValueError("PLAN_DATE_OR_GATE_MISMATCH")
    if audit["sourceManifest"]!=plan["sourceManifest"] or audit["dataRoot"]!=plan["dataRoot"] or audit["symbols"]!=plan["symbols"] or audit["missingSourceSymbols"]!=plan["missingSourceSymbols"]:
        raise ValueError("PLAN_SOURCE_MISMATCH")
    evidence=[]
    with (Path(plan["auditDir"])/"sessions.jsonl").open() as f:
        for line in f:evidence.append(json.loads(line))
    if [s["sessionDate"] for s in evidence]!=list(exchange_sessions(plan["fromDate"],plan["toDate"])):
        raise ValueError("PLAN_CALENDAR_ALIGNMENT_MISMATCH")
    if evidence!=plan["sessions"]:raise ValueError("PLAN_SESSION_EVIDENCE_MISMATCH")
    for key,value in window_facts(evidence).items():
        if plan.get(key)!=value:raise ValueError("PLAN_DERIVED_WINDOWS_MISMATCH: "+key)
    root=Path(plan["dataRoot"]);paths={}
    for s,m in plan["sourceManifest"].items():
        p=source_file(root,s)
        if p.name!=m["path"] or file_sha256(p)!=m["sha256"]:raise ValueError(f"{s}: INPUT_HASH_CHANGED")
        paths[s]=p
    for s in plan["missingSourceSymbols"]:
        if any((root/(s+suffix)).exists() for suffix in (".csv",".csv.gz")):
            raise ValueError(f"{s}: previously missing source appeared; regenerate audit/plan")
    return plan,paths


def compare_conditions(info, baseline_path, mask_comparable):
    differences=[]
    if not mask_comparable:differences.append("DATA_SELECTION_OR_EVALUATION_DATES_DIFFER")
    if baseline_path is None:
        return dict(sameConditions=None,dataSelectionComparable=mask_comparable,pairedBaselineProvided=False,
                    status="NEEDS_PAIRED_BASELINE" if mask_comparable else "NOT_COMPARABLE",differences=differences)
    baseline_path=Path(baseline_path).resolve()
    baseline=json.loads((baseline_path/"run.json").read_text())
    check_dates(baseline["fromDate"],baseline["toDate"])
    for key in ("seed","symbols","fromDate","toDate","executionModel","dataOrigin","dataMode"):
        if baseline.get(key)!=info.get(key):differences.append(key)
    if Config(**baseline["config"]).snapshot()!=Config(**info["config"]).snapshot():differences.append("config")
    old_hashes={s:m["sha256"] for s,m in baseline.get("inputManifest",{}).get("symbols",{}).items()}
    new_hashes={s:m["sha256"] for s,m in info["inputManifest"]["symbols"].items()}
    if old_hashes!=new_hashes:differences.append("sourceHashes")
    if baseline.get("phase")!="completed" or baseline.get("resultsValid") is False or baseline.get("missingSessions"):
        differences.append("baselineDataInvalidOrIncomplete")
    if info.get("sourcePopulationHash") and baseline.get("sourcePopulationHash")!=info["sourcePopulationHash"]:
        differences.append("frozenPopulationHash")
    return dict(sameConditions=not differences,dataSelectionComparable=mask_comparable,pairedBaselineProvided=True,
                baselineRun=str(baseline_path),differences=differences,
                status="PAIRED_CONDITIONS_MATCH" if not differences else "NOT_COMPARABLE",
                caveat="Metadata pairing plus source hashes; no claim of profitability or equality across arbitrary code versions")
