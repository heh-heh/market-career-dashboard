"""Shared process-safe rate limiter for Toss Securities Open API."""
from __future__ import annotations
import fcntl
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCK_PATH = Path(os.getenv("TOSS_RATE_LIMIT_FILE", str(ROOT / "data" / ".toss_api_rate.lock")))

# Conservative operational spacing; not a claim about current contractual TPS limits.
# This is shared across the collector, live trader and dashboard process.
MIN_INTERVAL = {
    "MARKET_DATA_CHART": 0.12,   # <= ~8.3 req/s
    "MARKET_DATA": 0.09,         # <= ~11.1 req/s
    "RANKING": 0.25,             # <= 4 req/s
    "STOCK": 0.25,               # <= 4 req/s
    "MARKET_INFO": 0.40,         # <= 2.5 req/s
    "ACCOUNT": 1.20,             # <= ~0.83 req/s
    "ASSET": 0.25,               # <= 4 req/s
    "ORDER": 0.12,
    "ORDER_HISTORY": 0.25,
    "ORDER_INFO": 0.20,
    "DEFAULT": 0.20,
}

PATH_GROUPS = [
    ("/api/v1/candles", "MARKET_DATA_CHART"),
    ("/api/v1/prices", "MARKET_DATA"),
    ("/api/v1/orderbook", "MARKET_DATA"),
    ("/api/v1/trades", "MARKET_DATA"),
    ("/api/v1/price-limits", "MARKET_DATA"),
    ("/api/v1/rankings", "RANKING"),
    ("/api/v1/stocks", "STOCK"),
    ("/api/v1/market-calendar/", "MARKET_INFO"),
    ("/api/v1/accounts", "ACCOUNT"),
    ("/api/v1/holdings", "ASSET"),
    ("/api/v1/orders/", "ORDER_INFO"),
    ("/api/v1/orders", "ORDER_HISTORY"),
    ("/api/v1/buying-power", "ORDER_INFO"),
    ("/api/v1/sellable-quantity", "ORDER_INFO"),
    ("/api/v1/commissions", "ORDER_INFO"),
]

def group_for_path(path: str) -> str:
    for prefix, group in PATH_GROUPS:
        if path.startswith(prefix):
            return group
    if path.startswith("/api/v1/"):
        return "DEFAULT"
    return "DEFAULT"

def _read_groups(stream):
    stream.seek(0)
    result = {}
    for line in stream.read().splitlines():
        try:
            key, value = line.split("=", 1)
            result[key] = float(value)
        except ValueError:
            continue
    return result


def _write_groups(stream, groups):
    stream.seek(0); stream.truncate()
    stream.write("".join(f"{key}={value:.9f}\n" for key, value in sorted(groups.items())))
    stream.flush(); os.fsync(stream.fileno())


def mark_rate_limited(group, retry_seconds):
    """Shared per-group cooldown. Other API groups remain available."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        groups = _read_groups(stream)
        key = "cooldown:"+group
        groups[key] = max(groups.get(key, 0), time.monotonic()+retry_seconds)
        _write_groups(stream, groups)


def wait_for_slot(group: str, request_guard=None) -> None:
    interval = MIN_INTERVAL.get(group, MIN_INTERVAL["DEFAULT"])
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    while True:
        if request_guard is not None and not request_guard():
            raise RuntimeError("BACKFILL_REQUEST_BLOCKED")
        with LOCK_PATH.open("a+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            groups = _read_groups(stream)
            now = time.monotonic()
            last = groups.get(group, 0)
            if last > now:
                # Host reboot invalidates old monotonic clock values, including cooldowns.
                last = 0
                groups.pop("cooldown:"+group, None)
            due = max(last+interval, groups.get("cooldown:"+group, 0))
            delay = due-now
            if delay <= 0:
                groups[group] = now
                _write_groups(stream, groups)
                return
        # Never hold the shared lock during a wait: unrelated groups can proceed.
        time.sleep(min(delay, 0.25) if request_guard is not None else delay)
