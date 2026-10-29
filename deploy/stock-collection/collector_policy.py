"""Separate tick / historical-1m permissions and exclusive dispatch policy.

This is pure policy code, not an HTTP client or market-data collector.
The frozen research worker scripts remain separate and the scheduler launches
at most one process. Missing/invalid toggle files mean OFF (fail closed).
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path


def enabled(path):
    try:
        p=Path(path)
        if not path or p.is_symlink() or not p.is_file() or p.stat().st_size>4096:
            return False
        obj=json.loads(p.read_text(encoding="utf-8"))
        return isinstance(obj,dict) and obj.get("enabled") is True
    except (OSError,ValueError,TypeError):
        return False


def choose_worker(decision, *, ticks, history):
    """Return (role, status), for a confirmed market decision.

    Both ON: tick at open; historical during closed.
    Only history: historical all day, including pre-open, with verified
                  official trading calendar. (No live tick fallback.)
    Only ticks: tick at open, idle otherwise.
    Neither ON: idle.
    PAUSED/ERROR/unverified: no worker. Never guess market schedule.
    """
    mode=decision.get("mode")
    confirmed=decision.get("calendarConfirmed") is True
    if mode in ("PAUSED","ERROR"):
        return None,dict(decision)
    if not history and not ticks:
        return None,{**decision,"mode":"ALL_COLLECTORS_PAUSED",
                     "reason":"TICK_AND_HISTORY_DISABLED"}
    if mode=="MARKET_OPEN":
        if ticks:
            return "tick",dict(decision)
        if history and confirmed:
            return "backfill",{**decision,"mode":"HISTORY_ONLY_RUNNING",
                               "reason":"HISTORY_ONLY_DURING_MARKET_OPEN"}
        return None,{**decision,"mode":"ERROR",
                     "reason":"HISTORY_ONLY_CALENDAR_UNCONFIRMED"}
    if mode=="MARKET_CLOSED":
        if history:
            return "backfill",dict(decision)
        return None,{**decision,"mode":"WAITING_MARKET_OPEN",
                     "reason":"HISTORICAL_DISABLED_TICKS_WAITING"}
    if mode=="SWITCHING":
        if history and not ticks and confirmed and decision.get("reason")=="PREOPEN_REQUEST_GUARD":
            return "backfill",{**decision,"mode":"HISTORY_ONLY_RUNNING",
                               "reason":"HISTORY_ONLY_PREOPEN"}
        return None,dict(decision)
    return None,{**decision,"mode":"ERROR","reason":"UNVERIFIED_MARKET_MODE"}


def rest_allowed(state, now, *, is_market_open, ticks, history):
    """Per-request lease check used by the historical worker.

    When ticks are enabled, historical requests are forbidden during regular
    market hours even if a stale permit still says HISTORY_ONLY_RUNNING.
    """
    if not history:
        return False
    try:
        lease=datetime.fromisoformat(str(state["leaseExpiresAt"]).replace("Z","+00:00"))
        if lease.tzinfo is None or not now<lease:
            return False
        mode=state.get("mode")
        if mode=="HISTORY_ONLY_RUNNING":
            return (not ticks and state.get("calendarConfirmed") is True and
                    state.get("reason") in ("HISTORY_ONLY_DURING_MARKET_OPEN",
                                             "HISTORY_ONLY_PREOPEN"))
        if mode=="MARKET_CLOSED":
            deadline=datetime.fromisoformat(str(state["requestDeadline"]).replace("Z","+00:00"))
            return (deadline.tzinfo is not None and now<deadline and
                    not is_market_open and state.get("calendarConfirmed") is True)
        return False
    except (KeyError,ValueError,TypeError,OverflowError):
        return False
