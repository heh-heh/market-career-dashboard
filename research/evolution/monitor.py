"""Bounded read-only status reader. No run/kill/broker operations."""
import json
import os
from pathlib import Path


def status(root):
    root = Path(root)
    if not root.exists():
        return {"available": False, "phase": "waiting", "researchOnly": True, "latestLog": "No Evolution V0 runs"}
    latest = None
    # ISO-date run names sort chronologically; no loading large generation data.
    for p in root.iterdir():
        if p.is_dir() and (p/"status.json").is_file() and (latest is None or p.name > latest.name):
            latest = p
    if latest is None:
        return {"available": False, "phase": "waiting", "researchOnly": True}
    path = latest/"status.json"
    if path.stat().st_size > 2*1024*1024:
        raise ValueError("Oversized Evolution status metadata")
    result = json.loads(path.read_text())
    # Do not expose input paths/provenance payload in the public monitor.
    allowed = {"phase", "runId", "generation", "population", "alive", "dead", "neutral", "invalid", "bestBot",
               "bestDailyReturn", "meanDailyReturn", "medianDailyReturn", "bestLifetimeEquity", "tradesToday",
               "currentHistoricalDate", "parentPoolSize", "mutationCount", "latestLog", "dataMode", "dataOrigin",
               "updatedAt", "split", "researchOnly", "error", "performanceClaim", "runtimeSeconds", "pid"}
    result = {k: v for k, v in result.items() if k in allowed}
    if result.get("phase") == "running":
        pid = result.get("pid")
        try:
            command = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
            alive = b"run_evolution_v0.py" in command
        except (OSError, ValueError, TypeError):
            alive = False
        if not alive:
            result.update(phase="interrupted", latestLog="Process absent; no completed results claimed")
    return dict(result, available=True, researchOnly=True)
