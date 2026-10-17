"""Read-only, bounded V0.8.2 bot roster for authenticated admin console.

Never computes trades, selects parents or opens 2026 price files. Current
offspring and last evaluated bots are distinct populations.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import stat
from datetime import datetime, timezone

PROTOCOL = "V082_BOTS_1"
MAX_BOTS = 50
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_LINEAGE_TAIL = 2 * 1024 * 1024
BOT_ID = re.compile(r"g[0-9]{4}-b[0-9]{4}\Z")
STAGES = ("train", "smoke")
PARAMETERS = ("buy_threshold", "sell_threshold", "stop_loss_pct",
              "take_profit_pct", "max_hold_minutes", "position_fraction")


def _json_file(file: Path):
    """Only regular, size-bounded local research artifacts; never follow symlinks."""
    if file.is_symlink():
        raise ValueError("BOT_ARTIFACT_SYMLINK")
    try:
        info = file.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("BOT_ARTIFACT_NOT_BOUNDED")
    with file.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _records(items, kind):
    if not isinstance(items, list) or len(items) > MAX_BOTS:
        raise ValueError("BOT_POPULATION_SIZE_INVALID")
    seen = set()
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("BOT_RECORD_INVALID")
        ident = row.get("bot_id" if kind == "population" else "botId")
        if not isinstance(ident, str) or not BOT_ID.fullmatch(ident) or ident in seen:
            raise ValueError("BOT_IDENTIFIER_INVALID")
        seen.add(ident)
    return items


def _latest_lineage(path):
    if path.is_symlink() or not path.is_file():
        return None
    size = path.stat().st_size
    if size <= 0:
        return None
    with path.open("rb") as stream:
        stream.seek(max(0, size - MAX_LINEAGE_TAIL))
        block = stream.read(MAX_LINEAGE_TAIL)
    lines = block.splitlines()
    if size > MAX_LINEAGE_TAIL:
        lines = lines[1:]
    if block and block[-1:] != b"\n":
        lines = lines[:-1]  # ignore a partially appended JSON line
    if not lines:
        return None
    row = json.loads(lines[-1])
    if not isinstance(row, dict):
        raise ValueError("BOT_LINEAGE_INVALID")
    return row.get("population")


def _directory_for_stage(manager, stage_state, stage):
    base = manager.RESULTS
    if base.is_symlink() or not base.is_dir():
        return None
    raw = stage_state.get("runDir")
    if raw:
        folder = Path(raw)
        if folder.is_dir() and not folder.is_symlink() and folder.resolve().parent == base.resolve():
            return folder
    if stage_state.get("phase") not in ("running", "starting"):
        return None
    pid = stage_state.get("childPid")
    if not isinstance(pid, int):
        return None
    # During a run the controller may not have received the result path yet.
    # Match only a bounded list of directory status PIDs, never guess newest.
    matches = []
    for folder in list(base.iterdir())[:500]:
        if folder.is_dir() and not folder.is_symlink() and folder.resolve().parent == base.resolve():
            status = _json_file(folder / "status.json") or {}
            if status.get("pid") == pid and status.get("version") == "V082_INDICATOR_1":
                matches.append(folder)
    return matches[0] if len(matches) == 1 else None


def _latest_attempt_population(folder):
    candidates = sorted(folder.glob("attempt_*/population.json"))
    for path in reversed(candidates[-4:]):
        if path.parent.is_symlink():
            continue
        data = _json_file(path)
        if data is not None:
            return data, path.parent.name
    return None, None


def _window(folder):
    for path in reversed(sorted(folder.glob("window_[0-9][0-9][0-9][0-9].json"))[-4:]):
        raw = _json_file(path)
        if isinstance(raw, dict) and len(raw.get("dates") or []) == 5:
            rows = _records(raw.get("bots"), "evaluation")
            selection = raw.get("selection") or {}
            if not isinstance(selection, dict):
                selection = {}
            return rows, selection, raw.get("dates"), path.name
    return [], {}, [], None


def _safe_float(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    import math
    return value if math.isfinite(value) else None


def _bot(record, evaluated):
    genome = record.get("genome") or {}
    if not isinstance(genome, dict):
        genome = {}
    bot_id = record["bot_id"]
    last = evaluated.get(bot_id)
    return {
        "id": bot_id,
        "generation": record.get("generation"),
        "origin": record.get("origin"),
        "parents": record.get("parent_ids") if isinstance(record.get("parent_ids"), list) else [],
        "birthDate": record.get("birth_date"),
        "mutationCount": record.get("mutation_count"),
        "genomeHash": record.get("genome_hash"),
        "genome": {
            **{k: genome.get(k) for k in PARAMETERS if k in genome},
            "weights": genome.get("weights") if isinstance(genome.get("weights"), dict) else {},
            "context_weights": genome.get("context_weights") if isinstance(genome.get("context_weights"), dict) else {},
            "indicator_weights": genome.get("indicator_weights") if isinstance(genome.get("indicator_weights"), dict) else {},
        },
        # Inherited lifetime fields are not earnings attributable to this new child.
        "inheritedLifetime": {
            "pnlUsd": _safe_float(record.get("lifetime_pnl")),
            "return": _safe_float(record.get("lifetime_return")),
            "trades": record.get("lifetime_trades"),
            "equityUsd": _safe_float(record.get("lifetime_equity")),
            "days": record.get("lifetime_days"),
        },
        "latestEvaluation": _evaluation(last, None) if last else None,
        "status": "EVALUATED" if last else "NOT_EVALUATED_IN_LAST_WINDOW",
    }


def _evaluation(row, parent_ids):
    return {
        "id": row["botId"],
        "generation": int(row["botId"][1:5]),
        "survival": row.get("survival"),
        "evaluationValid": row.get("evaluationValid") is True,
        "windowComplete": row.get("windowComplete") is True,
        "windowSessions": row.get("windowSessions"),
        "validDays": row.get("validDays"),
        "pnlUsd": _safe_float(row.get("windowPnl")),
        "return": _safe_float(row.get("windowReturn")),
        "maxDrawdown": _safe_float(row.get("windowMaxDrawdown") if row.get("windowMaxDrawdown") is not None else row.get("maxDrawdown")),
        "fitness": _safe_float(row.get("fitness")),
        "trades": row.get("trades"),
        "winRate": _safe_float(row.get("winRate")),
        "transactionCost": _safe_float(row.get("transactionCost")),
        "profitableSessions": row.get("profitableSessions"),
        "losingSessions": row.get("losingSessions"),
        "selectedParent": row["botId"] in parent_ids if parent_ids is not None else None,
        "parentSelectionReason": row.get("parentSelectionReason"),
        "failureReason": row.get("failureReason"),
    }


def snapshot():
    import v082_web_jobs as manager
    out = {
        "protocol": PROTOCOL, "version": "V0.8.2", "researchOnly": True,
        "finalOosGate": "CLOSED", "asOf": datetime.now(timezone.utc).isoformat(),
        "expectedBots": MAX_BOTS, "jobId": None, "stage": None, "phase": "not_started",
        "sourceKind": None, "snapshotDate": None, "currentBots": [],
        "evaluatedBots": [], "evaluatedDates": [], "evaluationWindow": None,
        "message": "V0.8.2 연구 실행 후 봇 현황이 표시됩니다.",
    }
    if manager.ROOT.is_symlink() or not manager.ROOT.is_dir():
        return out
    job = manager._job()
    if job is None or job.is_symlink() or job.resolve().parent != manager.ROOT.resolve():
        return out
    out["jobId"] = job.name
    states = {s: manager._state(job, s) for s in STAGES}
    stage = "train" if states["train"].get("phase") != "not_started" else "smoke"
    state = states[stage]
    out["stage"], out["phase"] = stage, state.get("phase")
    folder = _directory_for_stage(manager, state, stage)
    if folder is None:
        out["message"] = "작업이 시작되었지만 아직 확인 가능한 봇 결과 스냅샷이 없습니다."
        return out
    info = _json_file(folder / "status.json") or {}
    if info.get("version") != "V082_INDICATOR_1" or info.get("finalOosGate") != "CLOSED":
        raise ValueError("BOT_RUN_VERSION_OR_OOS_INVALID")
    out["runId"] = info.get("runId")
    out["processedSessions"] = info.get("completedSessions")
    out["completedGenerations"] = info.get("completedTrainGenerations")
    out["dataOrigin"] = info.get("dataOrigin")
    out["evaluationStatus"] = info.get("evaluationStatus")
    rows, selection, dates, file_name = _window(folder)
    if rows:
        parents = set(selection.get("parentIds") or [])
        out["evaluatedBots"] = [_evaluation(e, parents) for e in rows]
        out["evaluatedDates"] = dates
        out["evaluationWindow"] = file_name
        out["parentMode"] = selection.get("breedingMode")
        out["parentCount"] = selection.get("parentPoolSize")
    population = None
    if info.get("phase") == "completed":
        population = _json_file(folder / "final_population.json")
        if population is not None:
            out["sourceKind"] = "COMPLETED_FINAL_POPULATION"
    if population is None:
        population = _latest_lineage(folder / "lineage.jsonl")
        if population is not None:
            out["sourceKind"] = "LATEST_GENERATED_LINEAGE"
    if population is None:
        population, last_day = _latest_attempt_population(folder)
        if population is not None:
            out["sourceKind"] = "LAST_DAILY_POPULATION"
            out["snapshotDate"] = last_day
    if population is not None:
        _records(population, "population")
        assessed = {e["botId"]: e for e in rows}
        out["currentBots"] = [_bot(b, assessed) for b in population]
    if not out["currentBots"] and not out["evaluatedBots"]:
        out["message"] = "봇 데이터가 아직 저장되지 않았습니다. 연구 로그를 확인하세요."
    else:
        out["message"] = "과거 연구 스냅샷입니다. 현재 실계좌 포지션이나 실시간 시세가 아닙니다."
    return out


def console(parts):
    if parts == ["bots", "report"]:
        return 0, json.dumps(snapshot(), ensure_ascii=False, allow_nan=False)
    raise ValueError("BOTS_READ_ONLY_COMMAND_NOT_ALLOWED")
