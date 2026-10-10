#!/usr/bin/env python3
"""Run the frozen V4 research baselines sequentially after a successful V3 run.

This helper never fabricates or weakens the reviewed-manifest requirement.
It waits for V3 to complete, waits for the reviewed V4 manifest to exist,
then runs IR1 -> IR2 -> IR3 one at a time. V4-all is intentionally excluded.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest_manager import BacktestManager

DEFAULT_DIR = Path(os.getenv("BACKTEST_DATA_DIR", "/var/lib/market-career-dashboard"))


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class Sequence:
    ENGINES = ("v4-ir1", "v4-ir2", "v4-ir3")

    def __init__(self, manager: BacktestManager, state_path: Path, poll_seconds: float = 10.0):
        self.manager = manager
        self.state_path = state_path
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.started_at = datetime.now(timezone.utc).isoformat()

    def update(self, phase: str, **extra) -> None:
        payload = {
            "phase": phase,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "startedAt": self.started_at,
            **extra,
        }
        atomic_json(self.state_path, payload)
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    def wait_for_v3(self) -> None:
        while True:
            status = self.manager.status("v3")
            if status.get("running"):
                self.update(
                    "waiting_v3",
                    v3Phase=status.get("phase"),
                    v3Progress=status.get("progress"),
                    v3CurrentTimestamp=status.get("currentTimestamp"),
                )
                time.sleep(self.poll_seconds)
                continue
            phase = str(status.get("phase") or "").lower()
            if phase != "completed" or not status.get("resultAvailable"):
                self.update(
                    "blocked_v3",
                    error=f"V3 did not complete successfully: phase={phase or 'unknown'}",
                )
                raise RuntimeError("V3 must complete successfully before V4 sequence")
            self.update("v3_completed", v3Progress=status.get("progress", 100))
            return

    def wait_for_manifest(self) -> Path:
        manifest = Path(os.getenv("V4_DATA_MANIFEST", str(ROOT / "research/v4_data_manifest.json")))
        while not manifest.is_file():
            self.update(
                "waiting_manifest",
                manifest=str(manifest),
                message="Reviewed V4 data manifest is required; validation is not bypassed.",
            )
            time.sleep(max(self.poll_seconds, 30.0))
        self.update("manifest_ready", manifest=str(manifest))
        return manifest

    def run_engine(self, engine: str) -> None:
        while True:
            active = self.manager.active_job()
            if not active:
                break
            self.update("waiting_slot", engine=engine, activeJob=active)
            time.sleep(self.poll_seconds)

        self.manager.start(engine)
        self.update("running", engine=engine, progress=0)

        while True:
            status = self.manager.status(engine)
            self.update(
                "running" if status.get("running") else str(status.get("phase") or "unknown"),
                engine=engine,
                progress=status.get("progress", 0),
                currentTimestamp=status.get("currentTimestamp"),
                error=status.get("error"),
            )
            if status.get("running"):
                time.sleep(self.poll_seconds)
                continue
            phase = str(status.get("phase") or "").lower()
            if phase != "completed" or not status.get("resultAvailable"):
                raise RuntimeError(
                    f"{engine} failed: phase={phase or 'unknown'} error={status.get('error') or '-'}"
                )
            return

    def run(self) -> None:
        self.wait_for_v3()
        self.wait_for_manifest()
        completed = []
        for engine in self.ENGINES:
            self.run_engine(engine)
            completed.append(engine)
            self.update("engine_completed", engine=engine, completed=completed)
        self.update(
            "completed",
            completed=completed,
            message="IR1 -> IR2 -> IR3 completed. v4-all was intentionally not started.",
        )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--state", type=Path, default=DEFAULT_DIR / "v4_sequence_state.json")
    return parser.parse_args()


def main():
    args = parse_args()
    seq = Sequence(BacktestManager(ROOT), args.state, args.poll_seconds)
    try:
        seq.run()
    except Exception as exc:
        seq.update("error", error=str(exc))
        raise


if __name__ == "__main__":
    main()
