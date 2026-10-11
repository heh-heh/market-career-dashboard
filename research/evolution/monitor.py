"""Bounded read-only status reader for Evolution Trading AI V0."""
import json
from pathlib import Path

_MAX_STATUS_BYTES = 2 * 1024 * 1024
_MAX_RECENT_RUNS = 6

_PUBLIC_FIELDS = {
    "phase", "runId", "generation", "nextPopulationGeneration", "completedTrainGenerations",
    "evaluatedPopulation",
    "population", "alive", "dead", "neutral", "invalid", "bestBot", "bestDailyReturn",
    "meanDailyReturn", "medianDailyReturn", "bestLifetimeEquity", "tradesToday",
    "currentHistoricalDate", "parentPoolSize", "mutationCount", "crossoverCount",
    "eliteCount", "latestLog", "dataMode", "dataOrigin", "updatedAt", "split",
    "researchOnly", "error", "performanceClaim", "runtimeSeconds", "pid",
    "requestedSessions", "completedSessions", "fromDate", "toDate", "createdAt",
    "finishedAt", "smoke", "splitDays", "adaptiveResources", "minPopulation",
    "maxPopulation", "nextPopulation", "safePopulation", "peakPopulation",
    "resourceDecision", "resourcePressure", "resourceCpuPct", "resourceProcessCpuPct",
    "resourceMemoryPct", "resourceMemoryAvailableGb", "resourceSwapPct",
    "resourceSwapUsedGb", "resourceSwapTotalGb", "resourceProcessRssMb",
    "sessionRuntimeSeconds", "evaluationWindowSessions", "windowSessionsCompleted",
    "windowStartDate", "windowEndDate", "windowComplete", "bestWindowReturn",
    "meanWindowReturn", "medianWindowReturn", "dailyAlive", "dailyDead", "dailyNeutral",
    "evaluationMode", "sourceRunId", "sourceTrainFrom", "sourceTrainTo",
    "sourceTrainGenerations", "sourcePopulation", "sourcePopulationHash",
    "bestValidationReturn", "meanValidationReturn", "medianValidationReturn",
    "validationTotalTrades", "sourcePopulationVerified",
}


def _read_status(path):
    if path.stat().st_size > _MAX_STATUS_BYTES:
        raise ValueError("Oversized Evolution status metadata")
    raw = json.loads(path.read_text(encoding="utf-8"))
    result = {k: v for k, v in raw.items() if k in _PUBLIC_FIELDS}
    symbols = raw.get("symbols")
    result["symbolCount"] = len(symbols) if isinstance(symbols, list) else None
    requested = int(result.get("requestedSessions") or 0)
    completed = int(result.get("completedSessions") or 0)
    result["progressPct"] = min(100.0, (100.0 * completed / requested)) if requested else 0.0
    return result


def _process_alive(pid):
    try:
        command = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
        return b"run_evolution_v0.py" in command or b"evaluate_population_v03.py" in command
    except (OSError, ValueError, TypeError):
        return False


def _normalize_process_state(result):
    result = dict(result)
    if result.get("phase") == "running" and not _process_alive(result.get("pid")):
        result.update(
            phase="interrupted",
            latestLog="Evolution process is no longer present; the last completed session is shown.",
        )
    return result


def _run_summary(run_dir):
    status_file = run_dir / "status.json"
    if not status_file.is_file():
        return None
    result = _normalize_process_state(_read_status(status_file))
    return {
        "runId": result.get("runId") or run_dir.name,
        "phase": result.get("phase"),
        "smoke": bool(result.get("smoke")),
        "fromDate": result.get("fromDate"),
        "toDate": result.get("toDate"),
        "currentHistoricalDate": result.get("currentHistoricalDate"),
        "generation": result.get("generation"),
        "population": result.get("population"),
        "evaluatedPopulation": result.get("evaluatedPopulation"),
        "completedSessions": result.get("completedSessions"),
        "requestedSessions": result.get("requestedSessions"),
        "progressPct": result.get("progressPct"),
        "bestBot": result.get("bestBot"),
        "bestDailyReturn": result.get("bestDailyReturn"),
        "meanDailyReturn": result.get("meanDailyReturn"),
        "bestLifetimeEquity": result.get("bestLifetimeEquity"),
        "tradesToday": result.get("tradesToday"),
        "evaluationWindowSessions": result.get("evaluationWindowSessions"),
        "windowSessionsCompleted": result.get("windowSessionsCompleted"),
        "windowStartDate": result.get("windowStartDate"),
        "windowEndDate": result.get("windowEndDate"),
        "windowComplete": result.get("windowComplete"),
        "bestWindowReturn": result.get("bestWindowReturn"),
        "meanWindowReturn": result.get("meanWindowReturn"),
        "adaptiveResources": result.get("adaptiveResources"),
        "nextPopulation": result.get("nextPopulation"),
        "safePopulation": result.get("safePopulation"),
        "peakPopulation": result.get("peakPopulation"),
        "resourceDecision": result.get("resourceDecision"),
        "resourceMemoryPct": result.get("resourceMemoryPct"),
        "resourceSwapPct": result.get("resourceSwapPct"),
        "resourceCpuPct": result.get("resourceCpuPct"),
        "evaluationMode": result.get("evaluationMode"),
        "sourceRunId": result.get("sourceRunId"),
        "sourceTrainFrom": result.get("sourceTrainFrom"),
        "sourceTrainTo": result.get("sourceTrainTo"),
        "sourceTrainGenerations": result.get("sourceTrainGenerations"),
        "sourcePopulation": result.get("sourcePopulation"),
        "bestValidationReturn": result.get("bestValidationReturn"),
        "meanValidationReturn": result.get("meanValidationReturn"),
        "validationTotalTrades": result.get("validationTotalTrades"),
        "dataMode": result.get("dataMode"),
        "dataOrigin": result.get("dataOrigin"),
        "updatedAt": result.get("updatedAt") or result.get("finishedAt") or result.get("createdAt"),
    }


def _read_diagnostics(run_dir):
    path=run_dir/"generalization_diagnostics.json"
    if not path.is_file() or path.stat().st_size > _MAX_STATUS_BYTES:
        return {}
    try:
        raw=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        return {}
    allowed={
        "verdict","positiveBots","negativeBots","neutralBots","positiveShare",
        "medianValidationReturn","worstValidationReturn","trainValidationSpearman",
        "tradesValidationPearson","costValidationPearson","meanTradesPerBot",
        "medianTradesPerBot","totalTransactionCost","meanTransactionCostPerBot",
        "positiveTrainAndValidationBots","finalOosAllowed","nextStep",
    }
    return {k:raw.get(k) for k in allowed if k in raw}


def status(root):
    root = Path(root)
    if not root.exists():
        return {
            "available": False,
            "phase": "waiting",
            "researchOnly": True,
            "progressPct": 0.0,
            "recentRuns": [],
            "latestLog": "No Evolution V0 runs",
        }

    run_dirs = sorted(
        (p for p in root.iterdir() if p.is_dir() and (p / "status.json").is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    if not run_dirs:
        return {
            "available": False,
            "phase": "waiting",
            "researchOnly": True,
            "progressPct": 0.0,
            "recentRuns": [],
            "latestLog": "No Evolution V0 runs",
        }

    latest_run=run_dirs[0]
    latest = _normalize_process_state(_read_status(latest_run / "status.json"))
    latest.update(_read_diagnostics(latest_run))
    recent = []
    for run_dir in run_dirs[:_MAX_RECENT_RUNS]:
        item = _run_summary(run_dir)
        if item is not None:
            recent.append(item)

    return {
        **latest,
        "available": True,
        "researchOnly": True,
        "recentRuns": recent,
    }
