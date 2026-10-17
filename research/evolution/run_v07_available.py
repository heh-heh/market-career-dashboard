#!/usr/bin/env python3
"""Isolated available-data experiment: unchanged V07-R1 policy/fitness/costs.

Full-day reference completeness is an ex-post research quality screen, not a
claim of executable forward session selection. Symbols use only causal prefixes.
"""
import argparse
from dataclasses import replace
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
import uuid
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"research"))
from research.backtest_intraday_v4 import atomic_json
from research.backtest_v4_strategies import exchange_sessions,file_sha256
from research.evolution.available_data import POLICY,BIAS,new_output,append,verify_plan,compare_conditions
from research.evolution.audit_v07_data import resource_preflight
from research.evolution.config import Config
from research.evolution.data_access import check_dates,scheduled_days
from research.evolution.engine import evaluate_day
from research.evolution.population import initial_population,load_population_records,daily_evaluation,aggregate_window_evaluations,reproduce
from research.evolution.resource import sample_resources
from research.evolution.run_evolution_v0 import save_day,population_hash


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--selection-plan",type=Path,required=True)
    p.add_argument("--config",type=Path,default=ROOT/"research/evolution/v07_5d_regime_timing_config.json")
    p.add_argument("--results-dir",type=Path,default=Path("/mnt/research/evolution-v07-available"))
    p.add_argument("--population",type=int,default=20);p.add_argument("--seed",type=int,default=42)
    p.add_argument("--timestamp-kind",choices=["end"],default="end")
    p.add_argument("--from-date");p.add_argument("--to-date")
    p.add_argument("--smoke",action="store_true",help="Exactly ten planned consecutive TRAIN sessions, max32 legacy / max100 V08 bots; runtime gaps may discard windows")
    p.add_argument("--baseline-run",type=Path,help="Optional same-condition legacy run for metadata/hash comparison only")
    p.add_argument("--source-run",type=Path,help="Frozen new available-data population; 2024 internal holdout or 2025 dev validation only")
    p.add_argument("--require-ebs",action="store_true")
    p.add_argument("--research-mount",choices=["/mnt/research","/mnt/research-extra"],default="/mnt/research")
    p.add_argument("--min-evaluation-trades",type=int,default=30,help="Per-bot adequacy floor; never a fitness reward or significance claim")
    p.add_argument("--data-origin",choices=["toss","synthetic"],default="toss")
    return p


def validation_adequacy(calendar, first, selected, excluded, raw_complete, ledger, minimum):
    full_year=list(calendar)==list(exchange_sessions(first[:4]+"-01-01",first[:4]+"-12-31"))
    coverage=bool(full_year and not excluded and len(selected)==len(calendar) and raw_complete)
    sample=bool(ledger) and all(x["trades"]>=minimum for x in ledger.values())
    return coverage,sample,coverage and sample


def run(args):
    # Reject requested OOS dates before reading audit/source market evidence.
    if args.from_date and args.to_date:check_dates(args.from_date,args.to_date)
    elif args.from_date or args.to_date:raise ValueError("Both date bounds required")
    plan_hash=file_sha256(args.selection_plan)
    plan,paths=verify_plan(args.selection_plan)
    if file_sha256(args.selection_plan)!=plan_hash:raise ValueError("SELECTION_PLAN_CHANGED_DURING_PREFLIGHT")
    if args.smoke:
        if args.source_run:raise ValueError("Smoke is TRAIN only")
        if args.from_date or args.to_date:raise ValueError("Smoke uses the plan's first ten consecutive TRAIN dates; do not override")
        if plan.get("smokeRange") is None:raise ValueError("INSUFFICIENT_CONSECUTIVE_SMOKE_DATA: need ten consecutive planned usable training sessions")
        first,last=plan["smokeRange"]["fromDate"],plan["smokeRange"]["toDate"]
    else:
        first,last=args.from_date,args.to_date
        if not first or not last:raise ValueError("Explicit bounded dates or --smoke required; no implicit long training")
    check_dates(first,last)
    if not plan["fromDate"]<=first<=last<=plan["toDate"]:raise ValueError("Date bounds outside audited range")
    if args.min_evaluation_trades < 1:raise ValueError("Positive evaluation trade adequacy floor required")
    rng=random.Random(args.seed)
    v083=bool(getattr(args,"v083_mode",False))
    if v083 and (getattr(args,"max_restarts",0)<0 or getattr(args,"max_restarts",0)>10):
        raise ValueError("V083_MAX_RESTARTS_OUT_OF_RANGE")
    frozen=args.source_run is not None
    if frozen:
        if not (("2024-01-01"<=first<=last<="2024-12-31") or ("2025-01-01"<=first<=last<="2025-12-31")):
            raise ValueError("Frozen evaluation must be exclusively 2024 holdout or 2025 development validation")
    elif not "2022-01-01"<=first<=last<="2023-12-31":raise ValueError("Training is restricted to 2022-2023")
    calendar=exchange_sessions(first,last)
    if not calendar or (args.smoke and len(calendar)!=10):raise ValueError("Expected ten consecutive XNYS sessions for smoke")
    cfg=Config(**json.loads(args.config.read_text()))
    if cfg.fitness_mode!="v07_regime_timing" or cfg.evaluation_window_sessions!=5:
        raise ValueError("Available-data driver requires the existing five-session regime/timing fitness")
    if v083 and (cfg.selection_policy!="v081_survival" or not cfg.indicator_aware or not 2<=args.population<=50 or cfg.parent_pool_size!=2 or cfg.elite_count!=0):
        raise ValueError("V083_REQUIRES_BOUNDED_TOP_TWO_INDICATOR_CONFIG")
    if args.population<1 or (args.smoke and args.population>(100 if cfg.selection_policy in {"v08_profit_only", "v081_survival"} else 32)):raise ValueError("Invalid bounded smoke population")
    if frozen:
        source=args.source_run.resolve();source_info=json.loads((source/"run.json").read_text())
        if source_info.get("dataSelectionPolicy")!=POLICY or source_info.get("phase")!="completed" or not source_info.get("completedTrainGenerations"):
            raise ValueError("Need completed available-data training with at least one evaluated generation")
        if source_info.get("symbols")!=plan["symbols"]:raise ValueError("Frozen evaluation must retain the training universe")
        cfg=Config(**source_info["config"])
        if cfg.fitness_mode!="v07_regime_timing" or cfg.evaluation_window_sessions!=5:
            raise ValueError("Frozen source must use V07-R1 five-session config")
        records=json.loads((source/"final_population.json").read_text())
        cfg=replace(cfg,population_size=len(records),adaptive_resources=False)
        source_info_hash=file_sha256(source/"run.json")
        bots=load_population_records(records,cfg);source_hash=file_sha256(source/"final_population.json")
    else:
        cfg=replace(cfg,population_size=args.population,adaptive_resources=False)
        bots=initial_population(cfg,rng,first)
    preflight_resources = None
    if cfg.selection_policy in {"v08_profit_only", "v081_survival"}:
        import psutil
        psutil.cpu_percent(interval=0.2)  # prime, then measure rather than first-call zero
        measured_cpu = psutil.cpu_percent(interval=0.5)
        preflight_resources = sample_resources()
        preflight_resources["cpuPct"] = measured_cpu
        if (measured_cpu >= cfg.cpu_soft_limit_pct or
                preflight_resources["memoryPct"] >= cfg.memory_soft_limit_pct or
                preflight_resources["swapPct"] >= cfg.swap_soft_limit_pct):
            raise ValueError("V08_RESOURCE_PREFLIGHT_BLOCKED: CPU/RAM/swap pressure; fixed population not reduced")
    resource_preflight(args.results_dir,require_research_mount=args.require_ebs, research_mount=getattr(args,"research_mount","/mnt/research"))
    directory=new_output(args.results_dir/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+"-available-"+uuid.uuid4().hex[:8]),plan["dataRoot"])
    started=time.perf_counter();gen=0;attempt=0;pending=[];pending_dates=[]
    restarts=0;total_processed_sessions=0
    selected=[];excluded=[];evaluated=[];skipped=0;skipped_days=0;interrupted=0;trades=0;regimes={};final_evaluated=None
    selected_trades=0
    last_window_summary={}
    source_population_hash=population_hash(bots)
    git=subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,text=True,capture_output=True)
    info=dict(version="V083_TOP_TWO_1" if v083 else "V082_INDICATOR_1" if cfg.indicator_aware else "V081_SURVIVAL_1" if cfg.selection_policy=="v081_survival" else "V08_PROFIT_ONLY_1" if cfg.selection_policy=="v08_profit_only" else "V07_AVAILABLE_DATA_1",runId=directory.name,pid=os.getpid(),phase="running",researchOnly=True,
        dataSelectionPolicy=POLICY,finalOosGate="CLOSED",dataMode="PROVISIONAL_UNREVIEWED_DATA",dataOrigin=args.data_origin,
        performanceClaim="CONDITIONAL_RESEARCH_ONLY_NOT_AN_ANNUAL_OR_FORWARD_PERFORMANCE_CLAIM",selectionBiasWarnings=BIAS,
        inputManifest=dict(symbols=plan["sourceManifest"],timestampKind="end",priceAdjustmentConfirmed=False),
        initialPopulationHash=source_population_hash,generation=0,completedTrainGenerations=0,config=cfg.snapshot(),seed=args.seed,symbols=plan["symbols"],dataRoot=plan["dataRoot"],gitCommit=git.stdout.strip(),
        codeHashes={p.name:file_sha256(p) for p in sorted((ROOT/"research/evolution").glob("*.py"))},
        sourcePlan=str(args.selection_plan.resolve()),sourcePlanSha256=plan_hash,
        fromDate=first,toDate=last,timestampKind="end",requestedSessions=len(calendar),evaluationWindowSessions=5,
        fitnessMode=cfg.fitness_mode,minEvaluationTrades=args.min_evaluation_trades,evaluationMode="AVAILABLE_UNIVERSE_FROZEN_EVALUATION" if frozen else "AVAILABLE_UNIVERSE_TRAINING",
        executionModel="NEXT_COMPLETED_MINUTE_CLOSE_PROXY",population=len(bots),evaluationStatus="UNVERIFIED",
        sourceRunId=source_info["runId"] if frozen else None,sourcePopulationHash=source_population_hash if frozen else None)
    if cfg.selection_policy in {"v08_profit_only", "v081_survival"}:
        info.update(selectionPolicy=cfg.selection_policy, resourcePreflight=preflight_resources)
    if cfg.selection_policy=="v081_survival":
        info.update(runPurpose=getattr(args,"run_purpose","EXPLICIT_BOUNDED_RESEARCH"),fixedPopulation=len(bots),
            capacityReportSha256=file_sha256(args.capacity_report) if getattr(args,"capacity_report",None) else None)
    atomic_json(directory/"run.json",info);atomic_json(directory/"status.json",info)
    guard=getattr(args,"resource_monitor",None)
    if guard:guard.output=directory
    facts={s["sessionDate"]:s for s in plan["sessions"]}
    if cfg.selection_policy=="v081_survival":
        atomic_json(directory/"checkpoint.json",dict(completedGeneration=0,throughDate=None,population=[b.record() for b in bots],rngState=rng.getstate(),sourcePlanSha256=plan_hash,finalOosGate="CLOSED"))
    ledger={b.bot_id:dict(pnlUsd=0.,trades=0,transactionCost=0.,validDays=0,invalidDays=0) for b in bots}

    def discard(days,reason,caused_by):
        for d in days:
            excluded.append(d);append(directory/"excluded_sessions.jsonl",dict(date=d,reason=reason,causedBy=caused_by,attempt=attempt))

    processed_sessions=0
    try:
        # V083 may restart from the first permitted TRAIN session using only the
        # top two genomes; old versions retain the same single forward iterator.
        day_iterator=iter(scheduled_days(paths,{"symbols":plan["sourceManifest"]},calendar))
        day_index=0
        while True:
            try:day,rows=next(day_iterator)
            except StopIteration:break
            day_index+=1;total_processed_sessions+=1
            evidence=facts[day]
            session_started=time.perf_counter()
            if guard:
                guard.begin_session();guard.check()
            if cfg.selection_policy=="v081_survival":
                resource_preflight(directory,require_research_mount=args.require_ebs,research_mount=args.research_mount)
            else:resource_preflight(directory)
            pressure=sample_resources()
            if pressure["memoryPct"]>=cfg.memory_hard_limit_pct or pressure["swapPct"]>=cfg.swap_hard_limit_pct:
                raise ValueError("RESOURCE_GUARD_PAUSED: RAM/swap hard pressure; services untouched")
            rejection=evidence["reason"] if not evidence["eligible"] else None
            completed_now=False
            count=0;diagnostics={};evaluations=[];accounts=[]
            if rejection is None:
                accounts,diagnostics=evaluate_day(bots,rows,*calendar[day],plan["symbols"],cfg,data_policy="causal_available",**({"resource_guard":guard.check} if guard else {}))
                evaluations=[daily_evaluation(b,a,day,cfg,update_lifetime=False,mark_death=False) for b,a in zip(bots,accounts)]
                count=sum(e["trades"] for e in evaluations);trades+=count;evaluated.append(day)
                if any(not e["evaluationValid"] for e in evaluations):
                    rejection=("COHORT_HELD_SYMBOL_OBSERVATION_GAP" if any(a.cancellations.get("HELD_SYMBOL_OBSERVATION_GAP") for a in accounts)
                               else "COHORT_UNRESOLVED_SESSION_EXIT")
                diagnostics.update(availabilityEvidence=evidence,
                    causalSymbolsOnly=True,invalidBots=sum(not e["evaluationValid"] for e in evaluations))
                if cfg.selection_policy=="v081_survival":
                    diagnostics["invalidBotSamples"]=[dict(botId=b.bot_id,failureReason=e["failureReason"],
                        cancellations=a.cancellations,unresolvedSymbol=(a.position or {}).get("symbol"),gapSamples=getattr(a,"data_gap_samples",[]))
                        for b,a,e in zip(bots,accounts,evaluations) if not e["evaluationValid"]][:5]
                # Unique attempt/day directories preserve rejected trades and never
                # collide when generation number stays unchanged after a data break.
                save_day(directory/f"attempt_{attempt:04d}_{day}",bots,accounts,evaluations,bots,dict(selected=False),diagnostics)
            if rejection:
                skipped+=1;skipped_days+=1;interrupted+=bool(pending_dates)
                discard(pending_dates+[day],rejection,day)
                append(directory/"windows.jsonl",dict(attempt=attempt,status="INVALID_DATA",dates=pending_dates+[day],reason=rejection,
                    generation=gen,discardedPriorSessions=len(pending_dates),
                    **({"details":dict(referenceFailures=evidence.get("referenceFailures",[]),invalidBotSamples=diagnostics.get("invalidBotSamples",[]))} if cfg.selection_policy=="v081_survival" else {})))
                pending=[];pending_dates=[];attempt+=1
                if frozen:
                    for x in ledger.values():x["invalidDays"]+=1
            elif frozen:
                selected.append(day);selected_trades+=count
                for b,e in zip(bots,evaluations):
                    x=ledger[b.bot_id];x["pnlUsd"]+=e["dailyPnl"];x["trades"]+=e["trades"]
                    x["transactionCost"]+=e["transactionCost"];x["validDays"]+=1
            else:
                pending.append(evaluations);pending_dates.append(day)
                if len(pending)==5:
                    # Commit lifetime only after the whole consecutive window is
                    # valid. Failed windows cannot pollute fitness or lifetime.
                    for bi,b in enumerate(bots):
                        b.lifetime_days+=5;b.lifetime_trades+=sum(batch[bi]["trades"] for batch in pending)
                        b.lifetime_pnl+=sum(batch[bi]["dailyPnl"] for batch in pending)
                        b.lifetime_equity=b.lifetime_initial_capital+b.lifetime_pnl
                    window=aggregate_window_evaluations(bots,pending,cfg,complete=True,mark_death=True)
                    final_evaluated=[b.record() for b in bots]
                    if v083 and not frozen:
                        from research.evolution.v083_top_two import reproduce_top_two
                        children,selection=reproduce_top_two(bots,window,cfg,rng,gen+1,day)
                        selected_parent_ids=set(selection["parentIds"])
                        for row in window:
                            row["reproductionEligible"]=row["botId"] in selected_parent_ids
                            row["parentSelectionReason"]="V083_TOP_TWO_NET_PNL" if row["reproductionEligible"] else "V083_NOT_SELECTED"
                    else:
                        children,selection=reproduce(bots,window,cfg,rng,gen+1,day)
                    best=max(window,key=lambda e:(e["fitness"],e["botId"]))
                    last_window_summary=dict(
                        alive=sum(e["survival"]=="SURVIVE" for e in window),
                        dead=sum(e["survival"]=="DEAD" for e in window),
                        neutral=sum(e["survival"]=="NEUTRAL" for e in window),invalid=0,
                        bestBot=best["botId"],bestWindowReturn=max(e["windowReturn"] for e in window),
                        meanWindowReturn=statistics.mean(e["windowReturn"] for e in window),
                        medianWindowReturn=statistics.median(e["windowReturn"] for e in window),
                        parentPoolSize=selection["parentPoolSize"],mutationCount=selection["mutationCount"],
                        eliteCount=selection["eliteCount"],crossoverCount=selection["crossoverCount"],
                        randomRecovery=selection["randomRecovery"])
                    if cfg.selection_policy in {"v08_profit_only", "v081_survival"}:
                        last_window_summary["reproduction"] = selection
                    completed_now=True
                    atomic_json(directory/f"window_{gen:04d}.json",dict(dates=pending_dates,bots=window,selection=selection))
                    append(directory/"windows.jsonl",dict(attempt=attempt,status="SELECTED",generation=gen,dates=pending_dates))
                    append(directory/"lineage.jsonl",dict(generation=gen+1,date=day,population=[b.record() for b in children]))
                    selected_trades+=sum(e["trades"] for batch in pending for e in batch)
                    selected.extend(pending_dates);pending=[];pending_dates=[];gen+=1;attempt+=1;bots=children
                    if cfg.selection_policy=="v081_survival":
                        atomic_json(directory/"checkpoint.json",dict(completedGeneration=gen,throughDate=day,population=[b.record() for b in bots],rngState=rng.getstate(),sourcePlanSha256=plan_hash,finalOosGate="CLOSED",**({"restarts":restarts,"restartLimit":args.max_restarts} if v083 else {})))
                    if v083 and selection["restartRequested"]:
                        append(directory/"v083_restarts.jsonl",dict(afterGeneration=gen,throughDate=day,nextStartDate=first,
                            parentIds=selection["parentIds"],reason="NO_PROFITABLE_FIVE_SESSION_BOTS",
                            restartNumber=restarts+1,limit=args.max_restarts))
                        if restarts>=args.max_restarts:
                            raise ValueError("V083_RESTART_LIMIT_REACHED: no profitable bots; checkpoint preserved")
                        restarts+=1
                        day_iterator=iter(scheduled_days(paths,{"symbols":plan["sourceManifest"]},calendar))
                        day_index=0
            state=dict(info,**last_window_summary,date=day,currentHistoricalDate=day,completedSessions=day_index,
                generation=gen,completedTrainGenerations=gen,windowSessionsCompleted=5 if completed_now else len(pending_dates),windowComplete=completed_now,
                skippedGenerationAttempts=skipped,interruptedPartialWindows=interrupted,excludedDataSessions=skipped_days,selectedSessions=len(selected),
                tradesToday=count,totalTrades=trades,latestLog=rejection or "CAUSAL_AVAILABLE_OBSERVATIONS",
                resourceProcessRssMb=pressure["processRssMb"],
                **({"restarts":restarts,"totalProcessedSessions":total_processed_sessions} if v083 else {}))
            if guard:
                guard.session(time.perf_counter()-session_started)
                state["resourceReport"]=guard.report()
                atomic_json(directory/"resources.json",state["resourceReport"])
            append(directory/"generations.jsonl",state);atomic_json(directory/"status.json",state)
            processed_sessions=day_index
            for r,n in evidence["regimeMinutes"].items():regimes[r]=regimes.get(r,0)+n
        if pending_dates:
            discard(pending_dates,"TRAILING_INCOMPLETE_WINDOW",pending_dates[-1])
            append(directory/"windows.jsonl",dict(attempt=attempt,status="PARTIAL_NOT_SELECTED",generation=gen,dates=pending_dates))
        # Source data and artifacts stay read-only. Reject any mid-run mutation,
        # including a previously absent symbol becoming available.
        verify_plan(args.selection_plan)
        if file_sha256(args.selection_plan)!=info["sourcePlanSha256"]:raise ValueError("SELECTION_PLAN_CHANGED_DURING_RUN")
        if frozen and (population_hash(bots)!=source_population_hash or file_sha256(args.source_run/"final_population.json")!=source_hash or file_sha256(args.source_run/"run.json")!=source_info_hash):
            raise ValueError("FROZEN_POPULATION_CHANGED")
        if not frozen:
            atomic_json(directory/"final_population.json",[b.record() for b in bots])
            atomic_json(directory/"last_evaluated_population.json",final_evaluated or [])
        full_selected=not excluded and len(selected)==len(calendar)
        raw_complete=all(facts[d]["rawUniverseComplete"] for d in calendar)
        annual_coverage,adequate,annual=(validation_adequacy(calendar,first,selected,excluded,raw_complete,ledger,args.min_evaluation_trades)
                                        if frozen else (False,False,False))
        if frozen:
            atomic_json(directory/"validation_summary.json",dict(
                resultsValid=annual,annualValidationComplete=annual,annualCoverageComplete=annual_coverage,tradeSampleAdequate=adequate,evaluationStatus="ANNUAL_MECHANICAL_COMPLETE" if annual else "PARTIAL_UNVERIFIED",
                sessionsRequested=len(calendar),sessionsProcessed=len(selected),excludedSessions=excluded,
                bots=[dict(botId=k,**x,sampleAdequate=x["trades"]>=args.min_evaluation_trades,observedSubsetReturn=x["pnlUsd"]/cfg.initial_capital,
                    validationReturn=x["pnlUsd"]/cfg.initial_capital if annual else None) for k,x in ledger.items()],
                selectionBiasWarnings=BIAS,finalOosGate="CLOSED"))
        info["campaignStatus"]="UNVERIFIED_PROVISIONAL_DATA"
        status="ANNUAL_MECHANICAL_COMPLETE" if annual else "PARTIAL_UNVERIFIED" if frozen else "WINDOWS_EVALUATED_RESEARCH_ONLY" if gen else "INSUFFICIENT_USABLE_WINDOWS"
        info.update(**last_window_summary,phase="completed",generation=gen,completedTrainGenerations=gen,completedSessions=len(calendar),
            **({"restarts":restarts,"totalProcessedSessions":total_processed_sessions,"restartLimit":args.max_restarts} if v083 else {}),
            evaluationStatus=status,resultsValid=annual if frozen else gen>0,
            annualValidationComplete=annual if frozen else None,annualCoverageComplete=annual_coverage,tradeSampleAdequate=adequate,
            skippedGenerationAttempts=skipped,interruptedPartialWindows=interrupted,excludedDataSessions=skipped_days,
            selectedSessionCount=len(selected),excludedSessionCount=len(excluded),evaluatedSessionCount=len(evaluated),
            regimeMinutes=regimes,
            selectedRegimeMinutes={r:sum(facts[d]["regimeMinutes"].get(r,0) for d in selected) for r in regimes},
            excludedRegimeMinutes={r:sum(facts[d]["regimeMinutes"].get(r,0) for d in calendar if d not in selected) for r in regimes},
            observedTradesIncludingDiscarded=trades,selectionTrades=selected_trades,totalTrades=trades,runtimeSeconds=time.perf_counter()-started,inputHashesVerified=True,
            baselineComparability=compare_conditions(info,args.baseline_run,raw_complete and full_selected and not skipped),
            finalOosGate="CLOSED")
        if guard:info["resourceReport"]=guard.report()
        atomic_json(directory/"used_sessions.json",selected)
        atomic_json(directory/"evaluation_sessions.json",evaluated)
        atomic_json(directory/"run.json",info);atomic_json(directory/"status.json",info)
        print(json.dumps(dict(resultsDir=str(directory),generations=gen,skippedGenerationAttempts=skipped,
            selectedSessions=len(selected),evaluationStatus=status,finalOosGate="CLOSED")))
        return directory
    except BaseException as exc:
        if cfg.selection_policy=="v081_survival":
            info.update(generation=gen,completedTrainGenerations=gen,completedSessions=processed_sessions,selectedSessionCount=len(selected),evaluatedSessionCount=len(evaluated),checkpoint="checkpoint.json",
                **({"restarts":restarts,"totalProcessedSessions":total_processed_sessions} if v083 else {}))
        if guard:info["resourceReport"]=guard.report()
        info.update(phase="error",error=str(exc),resultsValid=False,evaluationStatus="UNVERIFIED",finalOosGate="CLOSED")
        atomic_json(directory/"run.json",info);atomic_json(directory/"status.json",info)
        if cfg.selection_policy=="v081_survival":print(json.dumps(dict(resultsDir=str(directory),phase="error",error=str(exc))),flush=True)
        raise


def main(argv=None):
    p=parser();a=p.parse_args(argv)
    try:return run(a)
    except (ValueError,OSError) as exc:p.exit(2,f"AVAILABLE RESEARCH BLOCKED: {exc}\n")


if __name__=="__main__":main()
