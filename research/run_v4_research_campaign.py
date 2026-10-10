#!/usr/bin/env python3
"""Non-destructive sequential baseline/candidate campaign; no optimization/orders.

Use --plan to review commands. Execution requires explicit timestamp semantics
and the existing reviewed manifest or explicitly labelled provisional mode.
Each campaign has a new directory; existing dashboard artifacts are never reset.
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest_manager import BacktestManager
from research.backtest_intraday_v4 import atomic_json


def plan(args, directory):
    jobs = [("baseline", s, 2) for s in ("ir1", "ir2", "ir3", "all")]
    jobs += [("ir1-r1", "ir1", 2)]

    commands = []
    for variant, strategy, slip in jobs:
        name = f"{variant}-{strategy}-{slip}bps"
        dest = directory / name
        cmd = [sys.executable, str(ROOT / "research/backtest_intraday_v4.py"),
               "--strategy", strategy, "--research-variant", variant,
               "--data-dir", str(args.data_dir.resolve()), "--manifest", str(args.manifest.resolve()),
               "--timestamp-kind", args.timestamp_kind, "--slippage-bps", str(slip),
               "--run-id", directory.name + "-" + name]
        if args.provisional: cmd.append("--provisional")
        for key in ("from_date", "to_date", "symbols"):
            value = getattr(args, key)
            if value: cmd += ["--" + key.replace("_", "-"), value]
        for key, filename in (("out", "result.json"), ("state", "state.json"),
                              ("log", "run.log"), ("trades-csv", "trades.csv"),
                              ("data-audit", "audit.json"), ("decisions", "decisions.jsonl")):
            cmd += ["--" + key, str(dest / filename)]
        commands.append(dict(name=name, directory=str(dest), command=cmd))
    return commands


def run(args):
    root = args.results_dir.resolve()
    if root.is_relative_to(args.data_dir.resolve()):
        raise ValueError("Research outputs cannot be inside the historical data directory")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = root / stamp
    jobs = plan(args, directory)
    if args.plan:
        print(json.dumps(dict(campaign=str(directory), jobs=jobs), indent=2))
        return 0
    if not args.data_dir.is_dir():
        raise ValueError(f"DATA_ACCESS_UNAVAILABLE: {args.data_dir}; no backtest launched")
    if not args.manifest.is_file() and not args.provisional:
        raise ValueError("REVIEWED_MANIFEST_REQUIRED; --provisional does not review provenance")
    # Same lock as the admin manager. Never call its artifact-clearing start().
    manager = BacktestManager(ROOT, args.manager_dir)
    manager.directory.mkdir(parents=True, exist_ok=True)
    with (manager.directory / "historical_backtest.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        active = manager.active_job()
        if active: raise ValueError("Another historical backtest is running: " + active)
        directory.mkdir(parents=True, exist_ok=False)
        state = dict(campaign=str(directory), phase="starting", jobs=jobs, completed=[],
                     parametersSelected=False, baselinePreserved=True, sourceHashes={})
        child = None
        try:
            for job in jobs:
                dest = Path(job["directory"])
                dest.mkdir(exist_ok=False)
                state.update(phase="running", currentJob=job["name"])
                atomic_json(directory / "campaign.json", state)
                with (dest / "terminal.log").open("x") as output:
                    child = subprocess.Popen(job["command"], cwd=ROOT, stdout=output,
                                             stderr=subprocess.STDOUT)
                    code = child.wait()
                    child = None
                if code: raise RuntimeError(f"{job['name']} failed ({code}); inspect {dest / 'terminal.log'}")
                result = json.loads((dest / "result.json").read_text())
                audit = json.loads((dest / "audit.json").read_text())
                mode=result["configuration"]["dataMode"]
                if state.setdefault("dataMode",mode)!=mode:
                    raise RuntimeError("Data review mode changed between runs; campaign cannot be compared")
                for symbol, item in audit["symbols"].items():
                    old = state["sourceHashes"].setdefault(symbol, item["sha256"])
                    if old != item["sha256"]:
                        raise RuntimeError(f"{symbol}: data changed between runs; campaign cannot be compared")
                state["completed"].append(dict(name=job["name"], result=str(dest / "result.json"),
                                                 dataMode=result["configuration"]["dataMode"],
                                                 overall=result["overall"]))
            state.update(phase="completed", currentJob=None)
            atomic_json(directory / "campaign.json", state)
            print(json.dumps(dict(campaign=str(directory), phase="completed")))
            return 0
        except BaseException as exc:
            if child is not None:
                child.terminate()
                try: child.wait(timeout=30)
                except subprocess.TimeoutExpired: child.kill(); child.wait()
            state.update(phase="interrupted" if isinstance(exc, KeyboardInterrupt) else "error", error=str(exc))
            atomic_json(directory / "campaign.json", state)
            raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, default=ROOT / "data/toss_1m")
    p.add_argument("--manifest", type=Path, default=ROOT / "research/v4_data_manifest.json")
    p.add_argument("--timestamp-kind", choices=("start", "end"), required=True)
    p.add_argument("--provisional", action="store_true")
    p.add_argument("--results-dir", type=Path, default=ROOT / "research/results/campaigns")
    p.add_argument("--manager-dir", type=Path, default=Path(os.getenv("BACKTEST_DATA_DIR", "/var/lib/market-career-dashboard")))
    p.add_argument("--from-date")
    p.add_argument("--to-date")
    p.add_argument("--symbols", help="Must be applicable to all four baselines; omit for the full baseline universe")
    p.add_argument("--cost-stress", action="store_true", help="Deprecated; use compare_v4_research fixed-trade cost sensitivity instead")
    p.add_argument("--plan", action="store_true", help="Print commands without reading or writing data")
    args = p.parse_args(argv)
    if args.cost_stress: p.error("Cost sensitivity is fixed-trade analysis in compare_v4_research.py; no additional backtest jobs")
    for value in (args.from_date, args.to_date):
        if value: datetime.strptime(value, "%Y-%m-%d")
    if args.from_date and args.to_date and args.from_date > args.to_date:
        p.error("from-date > to-date")
    if args.symbols: p.error("Mixed stock/index baselines have disjoint symbol universes; omit --symbols for this campaign")
    return run(args)


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (ValueError, RuntimeError, BlockingIOError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
