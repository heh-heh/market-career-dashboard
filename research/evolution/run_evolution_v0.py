#!/usr/bin/env python3
"""Explicitly bounded, research-only Evolution V0 CLI. Never contacts a broker."""
from __future__ import annotations
import argparse
import csv
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"research"))
from research.backtest_intraday_v4 import atomic_json, day_stream, provisional_inspect_file
from research.backtest_v4_strategies import exchange_sessions, file_sha256, manifest_input_path, validate_data_manifest
from research.build_v4_data_manifest import source_file
from research.evolution import RESEARCH_ONLY, VERSION
from research.evolution.config import Config
from research.evolution.engine import evaluate_day
from research.evolution.features import BASE_FEATURES, POLICY_FEATURES, benchmark_for_symbol
from research.evolution.population import daily_evaluation, initial_population, reproduce
from research.evolution.resource import decide_population, sample_resources


def parser():
    p = argparse.ArgumentParser(description="Evolution Trading AI V0: offline $100 daily accounts; no live execution")
    p.add_argument("--data-dir", type=Path, default=ROOT/"data/toss_1m")
    p.add_argument("--data-manifest", type=Path)
    p.add_argument("--provisional", action="store_true", help="Explicit mechanical audit only; never final performance evidence")
    p.add_argument("--timestamp-kind", choices=["end"], default="end", help="Exclusive minute-end labels; V0 never changes this basis")
    p.add_argument("--naive-timezone", choices=["UTC", "America/New_York"], help="Required declaration if source timestamps are naive")
    p.add_argument("--universe", type=Path, default=ROOT/"research/universe_v3.json")
    p.add_argument("--symbols", help="Comma-separated subset; SPY, QQQ, SOXX and mapped references also required")
    p.add_argument("--config", type=Path, help="JSON Config fields, including chronological split boundaries and fitness coefficients")
    p.add_argument("--population", type=int)
    p.add_argument("--initial-capital", type=float)
    p.add_argument("--adaptive-resources", action="store_true", help="Scale training population using CPU/RAM/swap pressure; validation/OOS stay frozen")
    p.add_argument("--min-population", type=int, help="Lower bound for adaptive training population")
    p.add_argument("--max-population", type=int, help="Upper bound for adaptive training population")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--from-date")
    p.add_argument("--to-date")
    p.add_argument("--results-dir", type=Path, default=Path(os.getenv("EVOLUTION_RESULTS_DIR", str(ROOT/"research/results/evolution"))))
    p.add_argument("--smoke", action="store_true", help="At most 32 bots / first 5 XNYS sessions; does not generate market data")
    p.add_argument("--data-origin", choices=["toss", "synthetic"], default="toss", help="Explicitly label small synthetic test fixtures")
    return p


def append_json(path, record):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True)+"\n")


def population_hash(bots):
    import hashlib
    return hashlib.sha256("\n".join(f"{b.bot_id}:{b.genome.digest}" for b in bots).encode()).hexdigest()


def prepare_inputs(args, symbols):
    needed = set(symbols) | {"SPY", "QQQ", "SOXX"}
    for s in symbols:
        needed.update(benchmark_for_symbol(s))
    root = args.data_dir.resolve()
    if args.data_manifest and args.data_manifest.exists():
        manifest = validate_data_manifest(args.data_manifest, needed, "end", data_dir=root)
        return {s: manifest_input_path(root, s, manifest["symbols"][s]) for s in sorted(needed)}, manifest, "REVIEWED_DATA"
    if args.data_manifest is not None:
        raise ValueError("Requested reviewed manifest is missing; no provisional fallback")
    if not args.provisional:
        raise ValueError("Provide --data-manifest; unreviewed data requires explicit --provisional")
    paths = {s: source_file(root, s) for s in sorted(needed)}
    meta = {s: provisional_inspect_file(p, s, "end", args.naive_timezone) for s, p in paths.items()}
    errors = [f"{s}: {', '.join(m['validationErrors'])}" for s, m in meta.items() if m["validationErrors"]]
    if errors:
        raise ValueError("Mechanical data audit failed: "+"; ".join(errors))
    return paths, {"symbols": meta, "timestampKind": "end", "priceAdjustmentConfirmed": False,
                   "priceAdjustment": "unknown", "calendar": "XNYS"}, "PROVISIONAL_UNREVIEWED_DATA"


def save_day(path, bots, accounts, evaluations, next_population, selection, diagnostics):
    path.mkdir(exist_ok=False)
    atomic_json(path/"population.json", [b.record() for b in bots])
    atomic_json(path/"next_population.json", [b.record() for b in next_population])
    for state, filename in (("SURVIVE", "survivors"), ("DEAD", "dead"), ("NEUTRAL", "neutral"), ("INVALID", "invalid")):
        atomic_json(path/(filename+".json"), [e for e in evaluations if e["survival"] == state])
    atomic_json(path/"metrics.json", dict(bots=evaluations, selection=selection, diagnostics=diagnostics))
    atomic_json(path/"lineage.json", [b.record() for b in next_population])
    trades = [dict(t, botId=b.bot_id, genomeHash=b.genome.digest) for b, a in zip(bots, accounts) for t in a.trades]
    fields = sorted({k for t in trades for k in t}) or ["botId", "symbol", "entryTimestamp", "exitTimestamp", "pnlUsd"]
    with (path/"trades.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for t in trades:
            writer.writerow({k: json.dumps(v, sort_keys=True, allow_nan=False) if isinstance(v, (dict, list)) else v for k, v in t.items()})


def run(args):
    config = Config(**json.loads(args.config.read_text())) if args.config else Config()
    changes = {}
    if args.population is not None:
        changes["population_size"] = args.population
    if args.initial_capital is not None:
        changes["initial_capital"] = args.initial_capital
    if args.adaptive_resources:
        changes["adaptive_resources"] = True
    if args.min_population is not None:
        changes["min_population"] = args.min_population
    if args.max_population is not None:
        changes["max_population"] = args.max_population
    if args.smoke:
        smoke_population = min(changes.get("population_size", config.population_size), 32)
        changes["population_size"] = smoke_population
        adaptive = changes.get("adaptive_resources", config.adaptive_resources)
        if adaptive:
            changes["max_population"] = min(changes.get("max_population", config.max_population), 32)
            changes["min_population"] = min(changes.get("min_population", config.min_population), smoke_population)
    config = replace(config, **changes)
    if args.timestamp_kind != "end":
        raise ValueError("V0 requires exclusive minute-end timestamps")
    first, last = args.from_date, args.to_date
    if args.smoke:
        first, last = first or "2024-06-03", last or "2024-06-14"
    if first is None or last is None:
        raise ValueError("Explicit --from-date and --to-date required outside --smoke; no implicit multi-year training")
    if datetime.fromisoformat(first).date() > datetime.fromisoformat(last).date():
        raise ValueError("Reversed date bounds")
    calendar = exchange_sessions(first, last)
    if args.smoke:
        calendar = dict(list(calendar.items())[:5])
    if not calendar:
        raise ValueError("No XNYS sessions in requested range")
    allowed = set(json.loads(args.universe.read_text())["symbols"])
    symbols = sorted(set(args.symbols.split(",")) if args.symbols else allowed)
    if not symbols or any(s not in allowed for s in symbols):
        raise ValueError("Symbols must be a nonempty subset of universe_v3")
    for d in calendar:
        if config.split(d) is None:
            raise ValueError(f"{d}: outside configured train/validation/final_oos splits")
    out = args.results_dir.resolve()
    data_root = args.data_dir.resolve()
    if any(out == p or p in out.parents for p in (data_root, (ROOT/"data").resolve())):
        raise ValueError("Research artifacts must not be written into raw data")
    # Fail closed on preflight before allocation; source files stay read-only.
    paths, manifest, mode = prepare_inputs(args, symbols)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid.uuid4().hex[:10]
    directory = out/run_id
    directory.mkdir(parents=True, exist_ok=False)
    rng = random.Random(args.seed)
    bots = initial_population(config, rng, first)
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True)
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True, capture_output=True)
    code_files = sorted((ROOT/"research/evolution").glob("*.py")) + [ROOT/"research"/n for n in
                 ("backtest_intraday_v4.py", "backtest_v4_strategies.py", "build_v4_data_manifest.py")]
    code_hashes = {str(p.relative_to(ROOT)): file_sha256(p) for p in code_files}
    from importlib.metadata import version
    started = time.perf_counter()
    run_info = dict(version=VERSION, runId=run_id, researchOnly=RESEARCH_ONLY, seed=args.seed,
                    pid=os.getpid(), phase="running", dataMode=mode, dataOrigin=args.data_origin,
                    performanceClaim="NOT_VALID_FOR_FINAL_PERFORMANCE_CLAIMS" if mode != "REVIEWED_DATA" or args.data_origin == "synthetic" else "RESEARCH_ONLY_NO_SIGNIFICANCE_CLAIM",
                    timestampKind="end", sourceTimezone="Manifest-declared; aware rows preserved and converted to America/New_York",
                    config=config.snapshot(), symbols=symbols, featureSchema=BASE_FEATURES, policySchema=POLICY_FEATURES,
                    inputManifest=manifest, dataRoot=str(data_root), gitCommit=git.stdout.strip(),
                    workingTreeDirty=bool(dirty.stdout.strip()), codeHashes=code_hashes,
                    runtimeVersions={"python": sys.version.split()[0], "numpy": version("numpy"), "exchange_calendars": version("exchange_calendars")},
                    fromDate=first, toDate=last, smoke=args.smoke, requestedSessions=len(calendar),
                    executionModel="NEXT_COMPLETED_MINUTE_CLOSE_PROXY", generation=0, completedSessions=0,
                    adaptiveResources=config.adaptive_resources, minPopulation=config.min_population,
                    maxPopulation=config.max_population, nextPopulation=config.population_size,
                    safePopulation=config.population_size, peakPopulation=config.population_size,
                    createdAt=datetime.now(timezone.utc).isoformat())
    atomic_json(directory/"run.json", run_info)
    status = dict(run_info, population=config.population_size, latestLog="Mechanical audit passed; streaming historical sessions")
    atomic_json(directory/"status.json", status)
    generation = 0
    processed = []
    frozen = None
    split_days = {n: 0 for n in ("train", "validation", "final_oos")}
    evaluation_ledger = {n: {} for n in ("validation", "final_oos")}
    safe_population = len(bots)
    peak_population = len(bots)
    if config.adaptive_resources:
        # Prime psutil interval counters before the first measured training session.
        sample_resources()
    try:
        for day, rows in day_stream(paths, manifest):
            if day > max(calendar):
                break
            if day not in calendar:
                continue
            split = config.split(day)
            if split != "train" and frozen is None:
                frozen = population_hash(bots)
                atomic_json(directory/"frozen_population.json", dict(populationHash=frozen, selectionBasis="FINAL_TRAIN_POPULATION_ONLY" if split_days["train"] else "UNTRAINED_RANDOM_BASELINE",
                           population=[b.record() for b in bots]))
            if frozen is not None and population_hash(bots) != frozen:
                raise AssertionError("Validation/OOS genome mutation detected")
            opening, closing = calendar[day]
            session_started = time.perf_counter()
            accounts, diagnostics = evaluate_day(bots, rows, opening, closing, symbols, config)
            session_runtime = time.perf_counter() - session_started
            resources = sample_resources(session_runtime) if config.adaptive_resources else None
            # OOS never changes training lineage, accumulated lifetime or selection.
            evaluations = [daily_evaluation(b, a, day, config, update_lifetime=split == "train") for b, a in zip(bots, accounts)]
            if split != "train":
                for e in evaluations:
                    ledger = evaluation_ledger[split].setdefault(e["botId"], dict(validDays=0, invalidDays=0, pnlUsd=0.0, trades=0))
                    ledger["trades"] += e["trades"]
                    if e["evaluationValid"]:
                        ledger["validDays"] += 1
                        ledger["pnlUsd"] += e["dailyPnl"]
                    else:
                        ledger["invalidDays"] += 1
                    e["splitLifetimeEquity"] = config.initial_capital+ledger["pnlUsd"]
            returns = [e["dailyReturn"] for e in evaluations if e["evaluationValid"]]
            best = max((e for e in evaluations if e["evaluationValid"]), key=lambda e: (e["dailyReturn"], e["botId"]), default=None)
            selection = dict(parentPoolSize=0, mutationCount=0, frozen=True)
            following = bots
            if split == "train":
                decision = decide_population(len(bots), resources or {}, config, enabled=config.adaptive_resources)
                target_population = decision["nextPopulation"]
                next_config = replace(config, population_size=target_population)
                following, selection = reproduce(bots, evaluations, next_config, rng, generation+1, day)
                peak_population = max(peak_population, target_population, len(bots))
                if decision["resourcePressure"] != "HARD":
                    safe_population = max(safe_population, len(bots))
                if config.adaptive_resources:
                    selection.update(
                        decision,
                        adaptiveResources=True,
                        safePopulation=safe_population,
                        peakPopulation=peak_population,
                        resourceCpuPct=resources["cpuPct"],
                        resourceProcessCpuPct=resources["processCpuPct"],
                        resourceMemoryPct=resources["memoryPct"],
                        resourceMemoryAvailableGb=resources["memoryAvailableGb"],
                        resourceSwapPct=resources["swapPct"],
                        resourceSwapUsedGb=resources["swapUsedGb"],
                        resourceSwapTotalGb=resources["swapTotalGb"],
                        resourceProcessRssMb=resources["processRssMb"],
                        sessionRuntimeSeconds=resources["sessionRuntimeSeconds"],
                    )
            elif config.adaptive_resources:
                selection.update(
                    resourceDecision="FROZEN_OOS",
                    resourcePressure="FROZEN",
                    nextPopulation=len(bots),
                    adaptiveResources=True,
                    safePopulation=safe_population,
                    peakPopulation=peak_population,
                    resourceCpuPct=resources["cpuPct"],
                    resourceProcessCpuPct=resources["processCpuPct"],
                    resourceMemoryPct=resources["memoryPct"],
                    resourceMemoryAvailableGb=resources["memoryAvailableGb"],
                    resourceSwapPct=resources["swapPct"],
                    resourceSwapUsedGb=resources["swapUsedGb"],
                    resourceSwapTotalGb=resources["swapTotalGb"],
                    resourceProcessRssMb=resources["processRssMb"],
                    sessionRuntimeSeconds=resources["sessionRuntimeSeconds"],
                )
            split_days[split] += 1
            day_dir = directory/(f"generation_{generation:04d}" if split == "train" else f"{split}_{split_days[split]:04d}")
            save_day(day_dir, bots, accounts, evaluations, following, selection, diagnostics)
            for b in bots:
                append_json(directory/"lineage.jsonl", dict(date=day, split=split, **b.record()))
            summary = dict(date=day, split=split, generation=generation, population=len(bots),
                           alive=sum(e["survival"] == "SURVIVE" for e in evaluations),
                           dead=sum(e["survival"] == "DEAD" for e in evaluations),
                           neutral=sum(e["survival"] == "NEUTRAL" for e in evaluations),
                           invalid=sum(not e["evaluationValid"] for e in evaluations),
                           bestBot=best["botId"] if best else None,
                           bestDailyReturn=best["dailyReturn"] if best else None,
                           meanDailyReturn=statistics.mean(returns) if returns else None,
                           medianDailyReturn=statistics.median(returns) if returns else None,
                           bestLifetimeEquity=max(b.lifetime_equity for b in bots),
                           tradesToday=sum(e["trades"] for e in evaluations),
                           currentHistoricalDate=day, **selection)
            append_json(directory/"generations.jsonl", summary)
            # Bounded daily winners, not validation/OOS-selected genomes.
            if split == "train":
                atomic_json(directory/"best_genomes.json", [b.record() for b in bots if best and b.bot_id == best["botId"]])
            processed.append(day)
            status.update(summary, completedSessions=len(processed), updatedAt=datetime.now(timezone.utc).isoformat(), latestLog=f"{split}: {day} evaluated; {summary['tradesToday']} closed simulated trades")
            atomic_json(directory/"status.json", status)
            print(json.dumps(summary, sort_keys=True, allow_nan=False))
            bots = following
            if split == "train":
                generation += 1
        if not processed:
            raise ValueError("No actual data sessions in requested range")
        missing_days = sorted(set(calendar)-set(processed))
        # Recheck *all* byte hashes; a collecting/mutating input cannot finalize.
        for s, path in paths.items():
            if file_sha256(path) != manifest["symbols"][s]["sha256"]:
                raise ValueError(f"{s}: input hash changed during run; results INVALID")
        if frozen is not None and population_hash(bots) != frozen:
            raise AssertionError("Frozen OOS population changed")
        atomic_json(directory/"final_population.json", [b.record() for b in bots])
        atomic_json(directory/"evaluation_summary.json", evaluation_ledger)
        run_info.update(phase="completed", generation=summary["generation"], nextPopulationGeneration=generation,
                        completedTrainGenerations=split_days["train"], completedSessions=len(processed),
                        missingSessions=missing_days, splitDays=split_days, frozenPopulationHash=frozen,
                        safePopulation=safe_population, peakPopulation=peak_population,
                        runtimeSeconds=time.perf_counter()-started, inputHashesVerified=True,
                        finishedAt=datetime.now(timezone.utc).isoformat())
        status.update(run_info, latestLog="Completed offline evaluation; no live orders or deployment")
        atomic_json(directory/"run.json", run_info)
        atomic_json(directory/"status.json", status)
    except BaseException as exc:
        run_info.update(phase="error", error=str(exc), resultsValid=False, runtimeSeconds=time.perf_counter()-started)
        atomic_json(directory/"run.json", run_info)
        status.update(run_info, latestLog=str(exc))
        atomic_json(directory/"status.json", status)
        raise
    return directory


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        directory = run(args)
    except (ValueError, OSError) as exc:
        p.exit(2, f"Evolution V0 FAILED: {exc}\n")
    print(json.dumps({"resultsDir": str(directory), "researchOnly": True}))
    return directory


if __name__ == "__main__":
    main()
