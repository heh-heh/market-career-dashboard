#!/usr/bin/env python3
"""Frozen Evolution V0.3 validation. Loads trained genomes; never evolves or trades live."""
from __future__ import annotations

import argparse
import csv
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"research"))

from research.backtest_intraday_v4 import atomic_json, day_stream
from research.backtest_v4_strategies import exchange_sessions, file_sha256
from research.evolution import RESEARCH_ONLY, VERSION
from research.evolution.config import Config
from research.evolution.engine import evaluate_day
from research.evolution.features import BASE_FEATURES, POLICY_FEATURES
from research.evolution.population import daily_evaluation, load_population_records
from research.evolution.run_evolution_v0 import prepare_inputs, population_hash


def parser():
    p = argparse.ArgumentParser(description="Evolution V0.3 frozen validation; no mutation/crossover/selection/live orders")
    p.add_argument("--source-run", default="latest", help="Training run directory, or 'latest'")
    p.add_argument("--data-dir", type=Path, default=ROOT/"data/toss_1m")
    p.add_argument("--data-manifest", type=Path)
    p.add_argument("--provisional", action="store_true")
    p.add_argument("--timestamp-kind", choices=["end"], default="end")
    p.add_argument("--naive-timezone", choices=["UTC", "America/New_York"])
    p.add_argument("--universe", type=Path, default=ROOT/"research/universe_v3.json")
    p.add_argument("--symbols", help="Optional exact subset; defaults to source run symbols")
    p.add_argument("--from-date", default="2025-01-02")
    p.add_argument("--to-date", default="2025-12-31")
    p.add_argument("--results-dir", type=Path, default=Path(os.getenv("EVOLUTION_RESULTS_DIR", str(ROOT/"research/results/evolution"))))
    p.add_argument("--data-origin", choices=["toss","synthetic"], default="toss")
    return p


def _latest_training_run(results_dir):
    root = Path(results_dir).resolve()
    candidates = []
    if root.exists():
        for d in root.iterdir():
            if not d.is_dir() or not (d/"final_population.json").is_file() or not (d/"run.json").is_file():
                continue
            try:
                info = json.loads((d/"run.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if info.get("phase") == "completed" and int(info.get("completedTrainGenerations") or 0) > 0:
                candidates.append(d)
    if not candidates:
        raise ValueError("No completed training run with final_population.json found")
    return sorted(candidates, key=lambda p: p.name)[-1]


def _source_run(path_or_latest, results_dir):
    d = _latest_training_run(results_dir) if path_or_latest == "latest" else Path(path_or_latest).expanduser().resolve()
    if not d.is_dir():
        raise ValueError(f"Source run directory missing: {d}")
    for name in ("run.json","final_population.json"):
        if not (d/name).is_file():
            raise ValueError(f"Source run missing {name}: {d}")
    return d


def _write_trades(path, bots, accounts):
    trades = [dict(t, botId=b.bot_id, genomeHash=b.genome.digest) for b,a in zip(bots,accounts) for t in a.trades]
    fields = sorted({k for t in trades for k in t}) or ["botId","symbol","entryTimestamp","exitTimestamp","pnlUsd"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for t in trades:
            w.writerow({k: json.dumps(v, sort_keys=True, allow_nan=False) if isinstance(v,(dict,list)) else v for k,v in t.items()})


def run(args):
    source = _source_run(args.source_run, args.results_dir)
    source_info = json.loads((source/"run.json").read_text(encoding="utf-8"))
    source_records = json.loads((source/"final_population.json").read_text(encoding="utf-8"))
    config = Config(**source_info["config"])
    # Validation must never inherit adaptive population behavior as an action.
    config = replace(config, adaptive_resources=False, population_size=len(source_records))
    bots = load_population_records(source_records, config)
    source_hash = population_hash(bots)
    source_file_hash = file_sha256(source/"final_population.json")

    first, last = args.from_date, args.to_date
    if not first or not last or datetime.fromisoformat(first).date() > datetime.fromisoformat(last).date():
        raise ValueError("Valid chronological --from-date/--to-date are required")
    calendar = exchange_sessions(first, last)
    if not calendar:
        raise ValueError("No XNYS sessions in validation range")
    for day in calendar:
        if config.split(day) != "validation":
            raise ValueError(f"{day}: frozen validator accepts validation split only")

    source_symbols = list(source_info.get("symbols") or [])
    symbols = sorted(set(args.symbols.split(","))) if args.symbols else sorted(source_symbols)
    if not symbols:
        raise ValueError("No source symbols available")
    if set(symbols) != set(source_symbols):
        raise ValueError("Validation symbol universe must exactly match the source training run")

    paths, manifest, mode = prepare_inputs(args, symbols)
    out = args.results_dir.resolve()
    data_root = args.data_dir.resolve()
    if any(out == p or p in out.parents for p in (data_root, (ROOT/"data").resolve())):
        raise ValueError("Validation artifacts must not be written into raw data")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid.uuid4().hex[:10]
    directory = out/run_id
    directory.mkdir(parents=True, exist_ok=False)

    git = subprocess.run(["git","rev-parse","HEAD"], cwd=ROOT, text=True, capture_output=True)
    started = time.perf_counter()
    run_info = dict(
        version=VERSION, runId=run_id, researchOnly=RESEARCH_ONLY, phase="running",
        evaluationMode="FROZEN_VALIDATION", split="validation",
        sourceRunId=source_info.get("runId") or source.name,
        sourceRunDir=str(source), sourcePopulationHash=source_hash,
        sourcePopulationFileHash=source_file_hash,
        sourceTrainFrom=source_info.get("fromDate"), sourceTrainTo=source_info.get("toDate"),
        sourceTrainGenerations=source_info.get("completedTrainGenerations"),
        sourcePopulation=len(bots), population=len(bots), evaluatedPopulation=len(bots),
        nextPopulation=len(bots), adaptiveResources=False, resourceDecision="FROZEN_VALIDATION",
        mutationCount=0, crossoverCount=0, parentPoolSize=0,
        dataMode=mode, dataOrigin=args.data_origin,
        performanceClaim="NOT_VALID_FOR_FINAL_PERFORMANCE_CLAIMS" if mode != "REVIEWED_DATA" or args.data_origin == "synthetic" else "RESEARCH_ONLY_NO_SIGNIFICANCE_CLAIM",
        timestampKind="end", config=config.snapshot(), symbols=symbols,
        featureSchema=BASE_FEATURES, policySchema=POLICY_FEATURES,
        inputManifest=manifest, dataRoot=str(data_root), gitCommit=git.stdout.strip(),
        fromDate=first, toDate=last, requestedSessions=len(calendar), completedSessions=0,
        generation=max((b.generation for b in bots), default=0),
        createdAt=datetime.now(timezone.utc).isoformat(),
    )
    atomic_json(directory/"run.json", run_info)
    status = dict(run_info, latestLog="Frozen population loaded; validation started")
    atomic_json(directory/"status.json", status)

    ledger = {b.bot_id: dict(validDays=0, invalidDays=0, pnlUsd=0.0, trades=0, transactionCost=0.0) for b in bots}
    processed = []
    original_hash = source_hash
    try:
        for day, rows in day_stream(paths, manifest):
            if day > max(calendar):
                break
            if day not in calendar:
                continue
            opening, closing = calendar[day]
            accounts, diagnostics = evaluate_day(bots, rows, opening, closing, symbols, config)
            evaluations = [daily_evaluation(b,a,day,config,update_lifetime=False,mark_death=False) for b,a in zip(bots,accounts)]
            for e in evaluations:
                x = ledger[e["botId"]]
                x["trades"] += e["trades"]
                x["transactionCost"] += e["transactionCost"]
                if e["evaluationValid"]:
                    x["validDays"] += 1
                    x["pnlUsd"] += e["dailyPnl"]
                else:
                    x["invalidDays"] += 1
                e["validationPnlUsd"] = x["pnlUsd"]
                e["validationReturn"] = x["pnlUsd"]/config.initial_capital

            day_dir = directory/f"validation_{len(processed)+1:04d}"
            day_dir.mkdir(exist_ok=False)
            atomic_json(day_dir/"metrics.json", dict(bots=evaluations, diagnostics=diagnostics))
            _write_trades(day_dir/"trades.csv", bots, accounts)

            valid = [x for x in ledger.values() if x["invalidDays"] == 0]
            returns = [x["pnlUsd"]/config.initial_capital for x in valid]
            best_id = None
            best_ret = None
            if valid:
                best_id = max(ledger, key=lambda k: (-10**99 if ledger[k]["invalidDays"] else ledger[k]["pnlUsd"], k))
                best_ret = ledger[best_id]["pnlUsd"]/config.initial_capital
            summary = dict(
                date=day, split="validation", evaluationMode="FROZEN_VALIDATION",
                generation=run_info["generation"], population=len(bots), evaluatedPopulation=len(bots),
                mutationCount=0, crossoverCount=0, parentPoolSize=0,
                alive=sum(x["invalidDays"] == 0 and x["pnlUsd"] > config.epsilon for x in ledger.values()),
                dead=sum(x["invalidDays"] == 0 and x["pnlUsd"] < -config.epsilon for x in ledger.values()),
                neutral=sum(x["invalidDays"] == 0 and abs(x["pnlUsd"]) <= config.epsilon for x in ledger.values()),
                invalid=sum(x["invalidDays"] > 0 for x in ledger.values()),
                bestBot=best_id, bestValidationReturn=best_ret,
                meanValidationReturn=statistics.mean(returns) if returns else None,
                medianValidationReturn=statistics.median(returns) if returns else None,
                bestDailyReturn=max((e["dailyReturn"] for e in evaluations if e["evaluationValid"]), default=None),
                meanDailyReturn=statistics.mean([e["dailyReturn"] for e in evaluations if e["evaluationValid"]]) if any(e["evaluationValid"] for e in evaluations) else None,
                tradesToday=sum(e["trades"] for e in evaluations),
                validationTotalTrades=sum(x["trades"] for x in ledger.values()),
                currentHistoricalDate=day, resourceDecision="FROZEN_VALIDATION",
                nextPopulation=len(bots),
            )
            processed.append(day)
            with (directory/"validation.jsonl").open("a",encoding="utf-8") as f:
                f.write(json.dumps(summary, ensure_ascii=False, allow_nan=False, sort_keys=True)+"\n")
            status.update(summary, completedSessions=len(processed), updatedAt=datetime.now(timezone.utc).isoformat(),
                          latestLog=f"validation: {day}; frozen genomes; {summary['tradesToday']} closed simulated trades")
            atomic_json(directory/"status.json", status)
            print(json.dumps(summary, sort_keys=True, allow_nan=False))

            if population_hash(bots) != original_hash:
                raise AssertionError("Frozen validation population changed")

        if not processed:
            raise ValueError("No actual data sessions in validation range")
        missing = sorted(set(calendar)-set(processed))
        for symbol,path in paths.items():
            if file_sha256(path) != manifest["symbols"][symbol]["sha256"]:
                raise ValueError(f"{symbol}: input hash changed during validation; results INVALID")
        if population_hash(bots) != original_hash:
            raise AssertionError("Frozen validation population changed")
        if file_sha256(source/"final_population.json") != source_file_hash:
            raise ValueError("Source population artifact changed during validation")

        per_bot = []
        for b in bots:
            x = ledger[b.bot_id]
            per_bot.append(dict(
                botId=b.bot_id, genomeHash=b.genome.digest,
                validDays=x["validDays"], invalidDays=x["invalidDays"], trades=x["trades"],
                pnlUsd=x["pnlUsd"], validationReturn=x["pnlUsd"]/config.initial_capital,
                transactionCost=x["transactionCost"],
            ))
        atomic_json(directory/"validation_summary.json", dict(
            sourceRunId=run_info["sourceRunId"], sourcePopulationHash=source_hash,
            sessionsProcessed=len(processed), sessionsRequested=len(calendar), missingSessions=missing,
            population=len(bots), bots=per_bot,
        ))
        run_info.update(
            phase="completed", completedSessions=len(processed), missingSessions=missing,
            inputHashesVerified=True, sourcePopulationVerified=True,
            runtimeSeconds=time.perf_counter()-started, finishedAt=datetime.now(timezone.utc).isoformat(),
        )
        final_status = dict(status)
        final_status.update(run_info)
        final_status["latestLog"] = "Frozen validation completed; no evolution or live orders"
        atomic_json(directory/"run.json", run_info)
        atomic_json(directory/"status.json", final_status)
    except BaseException as exc:
        run_info.update(phase="error", error=str(exc), resultsValid=False, runtimeSeconds=time.perf_counter()-started)
        atomic_json(directory/"run.json", run_info)
        status.update(run_info, latestLog=str(exc))
        atomic_json(directory/"status.json", status)
        raise
    return directory


def main(argv=None):
    p=parser(); args=p.parse_args(argv)
    try:
        directory=run(args)
    except (ValueError,OSError) as exc:
        p.exit(2,f"Evolution V0.3 validation FAILED: {exc}\n")
    print(json.dumps({"resultsDir":str(directory),"researchOnly":True,"evaluationMode":"FROZEN_VALIDATION"}))
    return directory


if __name__=="__main__":
    main()
