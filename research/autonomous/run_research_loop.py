#!/usr/bin/env python3
"""Dry-run defaults to one bounded, artifact-based iteration; no live capability."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from research.autonomous.config import LoopConfig
from research.autonomous.experiment_registry import exclusive_lock, Registry, read_json
from research.autonomous.runner import loop


def parser():
    p = argparse.ArgumentParser(description="Autonomous Research ONLY; V08 2024 selection, 2025 development; 2026 permanently blocked in this loop")
    p.add_argument("--dry-run", action="store_true", help="Download/verify/analyze/plan only; no code changes, training or git commit")
    p.add_argument("--max-iterations", type=int, default=1, help="Finite upper bound; stop when no distinct registered hypothesis remains")
    p.add_argument("--registry-dir", type=Path, default=LoopConfig.registry)
    p.add_argument("--lock-file", type=Path, default=LoopConfig.lock)
    p.add_argument("--data-dir", type=Path, default=LoopConfig.data_dir)
    p.add_argument("--data-manifest", type=Path)
    p.add_argument("--provisional", action="store_true", help="Explicit mechanical-only mode; no final performance claim")
    p.add_argument("--naive-timezone", choices=["UTC", "America/New_York"])
    p.add_argument("--resume", action="store_true", help="Resume latest iteration, retain failed attempts")
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--inherited-lock-fd", type=int, help=argparse.SUPPRESS)
    return p


def main(argv=None):
    p = parser(); args = p.parse_args(argv)
    config = LoopConfig(registry=args.registry_dir.absolute(), lock=args.lock_file.absolute(),
                        data_dir=args.data_dir.absolute(), data_manifest=args.data_manifest,
                        provisional=args.provisional, naive_timezone=args.naive_timezone,
                        max_iterations=args.max_iterations)
    if not args.dry_run and not args.worker:
        if not args.resume:
            p.error("DRY_RUN_REVIEW_REQUIRED: complete --dry-run, then explicitly --resume")
        old = Registry(config.registry).iterations()
        latest = read_json(old[-1] / "experiment.json") if old else {}
        resumable = bool(latest.get("dryRunReviewed") and latest.get("dryRun") is not True)
        reviewed = any(
            (lambda x: x.get("dryRun") is True and x.get("state") == "completed")(
                read_json(folder / "experiment.json")
            )
            for folder in old
        )
        if not resumable and not reviewed:
            p.error("DRY_RUN_REVIEW_REQUIRED")
        # CLI returns immediately. Worker owns flock throughout the iteration.
        argv = list(sys.argv[1:] if argv is None else argv)
        # Transfer the same flock open-file description across exec, avoiding a
        # submission/worker startup race and misleading duplicate queued jobs.
        with exclusive_lock(config.lock) as fd:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *argv, "--worker",
                                      "--inherited-lock-fd", str(fd)], pass_fds=(fd,),
                                     cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        print(json.dumps({"state": "queued", "workerPid": child.pid, "researchOnly": True,
                          "statusFile": str(config.registry / "status.json")}))
        return
    try:
        with exclusive_lock(config.lock, inherited_fd=args.inherited_lock_fd):
            values = loop(config, dry_run=args.dry_run, resume=args.resume)
        print(json.dumps({"researchOnly": True, "iterations": len(values), "finalOosGate": "CLOSED"}))
    except (ValueError, OSError) as exc:
        # Detailed sanitized errors are persisted by the worker, never credentials.
        p.exit(2, "Autonomous Research FAILED; inspect registry status/report (no data deleted).\n")


if __name__ == "__main__":
    main()
