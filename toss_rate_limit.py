"""Shared process-safe rate limiter for Toss Securities Open API."""
from __future__ import annotations
import fcntl
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCK_PATH = ROOT / "data" / ".toss_api_rate.lock"

# Conservative spacing below Toss' documented nominal TPS limits.
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

def wait_for_slot(group: str) -> None:
    interval = MIN_INTERVAL.get(group, MIN_INTERVAL["DEFAULT"])
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read().strip()
        now = time.monotonic()
        last = 0.0
        if raw:
            try:
                # Stored as monotonic time per group: group=value
                for part in raw.splitlines():
                    if "=" in part:
                        k, v = part.split("=", 1)
                        if k == group:
                            last = float(v)
                            break
            except Exception:
                last = 0.0
        delay = interval - (now - last)
        if delay > 0:
            time.sleep(delay)
            now = time.monotonic()
        f.seek(0)
        f.truncate()
        f.write(f"{group}={now:.9f}\n")
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
