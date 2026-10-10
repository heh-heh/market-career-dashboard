#!/usr/bin/env python3
"""Collect Toss US realtime trade ticks for the V4 research universe.

Design goals:
- one WebSocket connection subscribes to all selected US symbols concurrently;
- collect only during the XNYS regular session (holidays/DST/early closes included);
- keep raw delivered trade ticks partitioned by session date and symbol;
- never pretend the Toss market-data stream is a lossless exchange tape.

Realtime files are plain CSV while the session is open. They are gzip-compressed
after the regular session closes. A session metadata file records reconnects,
subscription rejects and local queue drops so incomplete sessions stay visible.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import json
import os
import shutil
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd
import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from toss_auth import get_token as shared_toss_token
from toss_rate_limit import wait_for_slot

SECRETS = ROOT / "server_secrets.json"
UNIVERSE = ROOT / "research" / "universe_v3.json"
DEFAULT_DATA_DIR = ROOT / "data" / "toss_ticks"
STATUS_PATH = DEFAULT_DATA_DIR / "status.json"
WS_URL = "wss://openapi-ws.tossinvest.com/ws/v1"
TOSS_BASE = "https://openapi.tossinvest.com"
MARKET_CALENDAR_PATH = "/api/v1/market-calendar/US"
CALENDAR = xcals.get_calendar("XNYS")
SESSION_FIELDS = [
    ("DAY", "dayMarket"),
    ("PRE", "preMarket"),
    ("REGULAR", "regularMarket"),
    ("AFTER", "afterMarket"),
]

FIELDS = [
    "source_timestamp",
    "received_at",
    "price",
    "volume",
    "currency",
    "connection_id",
    "connection_seq",
]


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_symbols(raw: str | None) -> list[str]:
    if raw:
        values = [x.strip().upper() for x in raw.split(",") if x.strip()]
    else:
        values = json.loads(UNIVERSE.read_text(encoding="utf-8"))["symbols"]
    values = list(dict.fromkeys(values))
    if not values:
        raise ValueError("No symbols configured")
    if len(values) > 100:
        raise ValueError("Toss WebSocket permits at most 100 subscribed topics per connection")
    return values


def access_token(force: bool = False) -> str:
    cfg = json.loads(SECRETS.read_text(encoding="utf-8"))
    token = shared_toss_token(cfg, force=force)
    if not token:
        raise RuntimeError("Toss OAuth token issuance failed")
    return token


def as_utc_timestamp(value=None) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp.now(tz="UTC")
    ts = pd.Timestamp(value)
    if ts.tz is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _fallback_regular_window(now=None, error: str | None = None) -> dict:
    """Fallback to XNYS regular hours when Toss market-calendar is unavailable."""
    now = as_utc_timestamp(now)
    minute = now.floor("min")
    session = CALENDAR.minute_to_session(minute, direction="next")
    open_at = CALENDAR.session_open(session)
    close_at = CALENDAR.session_close(session)

    if now >= close_at:
        session = CALENDAR.minute_to_session(
            (minute + pd.Timedelta(minutes=1)), direction="next"
        )
        open_at = CALENDAR.session_open(session)
        close_at = CALENDAR.session_close(session)

    opened = bool(open_at <= now < close_at)
    return {
        "isOpen": opened,
        "sessionDate": session.date().isoformat(),
        "marketSession": "REGULAR",
        "openAt": open_at.isoformat(),
        "closeAt": close_at.isoformat(),
        "now": now.isoformat(),
        "calendarSource": "xnys_fallback",
        "calendarError": error,
        "isFinalSession": True,
    }


def _fetch_us_market_calendar(date_str: str) -> dict:
    """Fetch one US-local date from Toss Market Info without an account header."""
    last_error = None
    for attempt in range(2):
        try:
            token = access_token(force=attempt > 0)
            query = urllib.parse.urlencode({"date": date_str})
            url = TOSS_BASE + MARKET_CALENDAR_PATH + "?" + query
            wait_for_slot("MARKET_INFO")
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/json",
                    "User-Agent": "market-career-dashboard/tick-collector",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                obj = json.loads(r.read())
            result = obj.get("result", obj) if isinstance(obj, dict) else {}
            return result if isinstance(result, dict) else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")
            last_error = f"HTTP {exc.code}: {body[:300]}"
            if exc.code == 401 and attempt == 0:
                continue
            raise RuntimeError(last_error) from exc
        except Exception as exc:
            last_error = str(exc)
            if attempt == 0:
                continue
            raise RuntimeError(last_error) from exc
    raise RuntimeError(last_error or "market calendar unavailable")


def _calendar_windows(day_obj: dict, session_date: str) -> list[dict]:
    windows = []
    if not isinstance(day_obj, dict):
        return windows
    for session_name, field in SESSION_FIELDS:
        raw = day_obj.get(field)
        if not isinstance(raw, dict):
            continue
        start = raw.get("startTime")
        end = raw.get("endTime")
        if not start or not end:
            continue
        try:
            start_ts = as_utc_timestamp(start)
            end_ts = as_utc_timestamp(end)
        except Exception:
            continue
        if end_ts <= start_ts:
            continue
        windows.append(
            {
                "sessionDate": session_date,
                "marketSession": session_name,
                "openAt": start_ts.isoformat(),
                "closeAt": end_ts.isoformat(),
                "_open": start_ts,
                "_close": end_ts,
            }
        )
    windows.sort(key=lambda x: x["_open"])
    for idx, item in enumerate(windows):
        item["isFinalSession"] = idx == len(windows) - 1
    return windows


def market_window(now=None) -> dict:
    """Return Toss DAY/PRE/REGULAR/AFTER state, with XNYS regular fallback."""
    now = as_utc_timestamp(now)
    try:
        ny_now = now.tz_convert("America/New_York")
        requested_dates = [ny_now.date().isoformat()]

        # DAY market for a trading date can begin on the previous US calendar
        # day, so also query the next XNYS session date and merge the windows.
        minute = now.floor("min")
        next_session = CALENDAR.minute_to_session(minute, direction="next")
        next_date = next_session.date().isoformat()
        if next_date not in requested_dates:
            requested_dates.append(next_date)

        windows = []
        for date_str in requested_dates:
            result = _fetch_us_market_calendar(date_str)
            day_obj = result.get("today") if isinstance(result, dict) else None
            if not isinstance(day_obj, dict):
                day_obj = result if isinstance(result, dict) else {}
            windows.extend(_calendar_windows(day_obj, date_str))

        # Deduplicate in case current date and next-session date resolve to the
        # same Toss window set.
        deduped = {}
        for item in windows:
            key = (item["marketSession"], item["openAt"], item["closeAt"])
            deduped[key] = item
        windows = sorted(deduped.values(), key=lambda x: x["_open"])

        active = next(
            (item for item in windows if item["_open"] <= now < item["_close"]),
            None,
        )
        if active is not None:
            return {
                "isOpen": True,
                "sessionDate": active["sessionDate"],
                "marketSession": active["marketSession"],
                "openAt": active["openAt"],
                "closeAt": active["closeAt"],
                "now": now.isoformat(),
                "calendarSource": "toss_market_calendar",
                "calendarError": None,
                "isFinalSession": bool(active.get("isFinalSession")),
            }

        future = next((item for item in windows if item["_open"] > now), None)
        if future is not None:
            return {
                "isOpen": False,
                "sessionDate": future["sessionDate"],
                "marketSession": future["marketSession"],
                "openAt": future["openAt"],
                "closeAt": future["closeAt"],
                "now": now.isoformat(),
                "calendarSource": "toss_market_calendar",
                "calendarError": None,
                "isFinalSession": bool(future.get("isFinalSession")),
            }

        return _fallback_regular_window(now, "Toss calendar returned no usable session windows")
    except Exception as exc:
        return _fallback_regular_window(now, str(exc))


def write_status(**values) -> None:
    current = {}
    if STATUS_PATH.exists():
        try:
            current = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update(values)
    current["updatedAt"] = utc_now_iso()
    atomic_json(STATUS_PATH, current)


def disk_free_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / (1024 ** 3)


class TickWriter:
    def __init__(
        self,
        root: Path,
        session_date: str,
        symbols: list[str],
        queue_size: int,
        flush_seconds: float,
        meta: dict,
    ):
        self.root = root
        self.session_date = session_date
        self.symbols = set(symbols)
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=queue_size)
        self.flush_seconds = max(0.1, flush_seconds)
        self.meta = meta
        self.counts = Counter({
            str(k): int(v) for k, v in (meta.get("ticksBySymbol") or {}).items()
        })
        self.local_drops = int(meta.get("localQueueDrops") or 0)
        self.invalid_messages = 0
        self.handles = {}
        self.writers = {}
        self.session_dir = root / session_date
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.session_dir / "session_meta.json"

    def enqueue(self, item: dict) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.local_drops += 1

    def _writer(self, symbol: str):
        if symbol in self.writers:
            return self.writers[symbol]
        path = self.session_dir / f"{symbol}.csv"
        existed = path.exists() and path.stat().st_size > 0
        handle = path.open(
            "a", newline="", encoding="utf-8", buffering=1024 * 1024
        )
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not existed:
            writer.writeheader()
        self.handles[symbol] = handle
        self.writers[symbol] = writer
        return writer

    def _write_item(self, item: dict) -> None:
        symbol = item.pop("symbol")
        if symbol not in self.symbols:
            self.invalid_messages += 1
            return
        self._writer(symbol).writerow(item)
        self.counts[symbol] += 1

    def _flush(self) -> None:
        for handle in self.handles.values():
            handle.flush()

    def _update_meta(self) -> None:
        self.meta["ticksBySymbol"] = dict(sorted(self.counts.items()))
        self.meta["ticksTotal"] = int(sum(self.counts.values()))
        self.meta["localQueueDrops"] = int(self.local_drops)
        self.meta["invalidMessages"] = int(self.invalid_messages)
        self.meta["queueDepth"] = self.queue.qsize()
        self.meta["updatedAt"] = utc_now_iso()
        atomic_json(self.meta_path, self.meta)

    async def run(self, stop: asyncio.Event) -> None:
        last_flush = time.monotonic()
        last_meta = 0.0
        try:
            while not (stop.is_set() and self.queue.empty()):
                batch = []
                try:
                    first = await asyncio.wait_for(
                        self.queue.get(), timeout=self.flush_seconds
                    )
                    batch.append(first)
                    for _ in range(4999):
                        try:
                            batch.append(self.queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                except asyncio.TimeoutError:
                    pass

                for item in batch:
                    self._write_item(item)
                    self.queue.task_done()

                now = time.monotonic()
                if now - last_flush >= self.flush_seconds:
                    self._flush()
                    last_flush = now
                if now - last_meta >= 5.0:
                    self._update_meta()
                    last_meta = now
        finally:
            self._flush()
            self._update_meta()
            for handle in self.handles.values():
                handle.close()


def compress_session(session_dir: Path) -> None:
    """Compress completed plain CSV partitions without rewriting existing gzip."""
    for src in sorted(session_dir.glob("*.csv")):
        dst = src.with_suffix(".csv.gz")
        if dst.exists():
            # Never silently merge two tick streams.
            continue
        tmp = Path(str(dst) + ".tmp")
        try:
            with src.open("rb") as rf, gzip.open(tmp, "wb", compresslevel=3) as wf:
                shutil.copyfileobj(rf, wf, length=1024 * 1024)
            os.replace(tmp, dst)
            src.unlink()
        finally:
            tmp.unlink(missing_ok=True)


async def keepalive(ws) -> None:
    while True:
        await asyncio.sleep(60)
        await ws.send("PING")


async def close_at(ws, close_at: pd.Timestamp) -> None:
    delay = max(0.0, (close_at - as_utc_timestamp()).total_seconds())
    await asyncio.sleep(delay)
    await ws.close(code=1000, reason="market session closed")


async def stream_once(
    symbols: list[str],
    writer: TickWriter,
    close_at: pd.Timestamp,
    meta: dict,
    force_token: bool,
) -> None:
    token = access_token(force=force_token)
    headers = {"Authorization": "Bearer " + token}
    try:
        major = int(str(getattr(websockets, "__version__", "15")).split(".", 1)[0])
    except Exception:
        major = 15
    kwargs = {"additional_headers": headers} if major >= 14 else {"extra_headers": headers}

    connection_id = uuid.uuid4().hex[:12]
    meta["connectionAttempts"] = int(meta.get("connectionAttempts", 0)) + 1
    meta.setdefault("connections", []).append(
        {"id": connection_id, "connectedAt": utc_now_iso()}
    )
    if len(meta["connections"]) > 100:
        meta["connections"] = meta["connections"][-100:]
    atomic_json(writer.meta_path, meta)

    expected = {f"trade:us:{symbol}" for symbol in symbols}
    declaration = [
        {"id": "tick-collector-" + connection_id},
        {"type": "trade:us", "codes": symbols},
    ]

    async with websockets.connect(
        WS_URL,
        ping_interval=None,
        close_timeout=5,
        max_queue=4096,
        max_size=1024 * 1024,
        **kwargs,
    ) as ws:
        await ws.send(json.dumps(declaration, separators=(",", ":")))
        write_status(
            state="connecting",
            provider="toss_ws",
            sessionDate=writer.session_date,
            connectionId=connection_id,
            symbols=symbols,
        )

        keep_task = asyncio.create_task(keepalive(ws))
        close_task = asyncio.create_task(close_at(ws, close_at))
        seq = 0
        acked = False
        try:
            async for raw in ws:
                try:
                    payload = json.loads(raw)
                except Exception:
                    writer.invalid_messages += 1
                    continue

                frame_type = payload.get("type")
                if frame_type == "subscriptions":
                    rejected = payload.get("rejected") or []
                    subscribed = set(payload.get("subscribed") or [])
                    missing = sorted(expected - subscribed)
                    meta["subscribed"] = sorted(subscribed)
                    meta["rejectedSubscriptions"] = rejected
                    meta["missingSubscriptions"] = missing
                    atomic_json(writer.meta_path, meta)
                    if rejected or missing:
                        raise RuntimeError(
                            f"incomplete Toss subscription rejected={rejected} missing={missing}"
                        )
                    acked = True
                    write_status(
                        state="collecting",
                        provider="toss_ws",
                        sessionDate=writer.session_date,
                        connectionId=connection_id,
                        subscribed=len(subscribed),
                        symbols=symbols,
                    )
                    continue
                if frame_type == "pong":
                    continue
                if frame_type == "error":
                    raise RuntimeError(
                        "Toss WebSocket error: " + json.dumps(
                            payload.get("error"), ensure_ascii=False
                        )
                    )
                if frame_type != "message":
                    continue

                topic = str(payload.get("topic") or "")
                if not topic.startswith("trade:us:"):
                    continue
                symbol = topic.rsplit(":", 1)[-1].upper()
                if symbol not in writer.symbols:
                    writer.invalid_messages += 1
                    continue

                data = payload.get("data") or {}
                source_ts = data.get("timestamp")
                price = data.get("price")
                volume = data.get("volume")
                if source_ts is None or price is None or volume is None:
                    writer.invalid_messages += 1
                    continue

                seq += 1
                writer.enqueue(
                    {
                        "symbol": symbol,
                        "source_timestamp": str(source_ts),
                        "received_at": utc_now_iso(),
                        "price": str(price),
                        "volume": str(volume),
                        "currency": str(data.get("currency") or "USD"),
                        "connection_id": connection_id,
                        "connection_seq": seq,
                    }
                )
        finally:
            keep_task.cancel()
            close_task.cancel()
            if meta.get("connections"):
                meta["connections"][-1]["disconnectedAt"] = utc_now_iso()
                meta["connections"][-1]["acked"] = acked
            atomic_json(writer.meta_path, meta)


async def collect_market_session(args, window: dict, stop: asyncio.Event) -> None:
    session_date = window["sessionDate"]
    close_at = as_utc_timestamp(window["closeAt"])
    symbols = args.symbols_list
    session_dir = args.data_dir / session_date
    session_dir.mkdir(parents=True, exist_ok=True)
    meta_path = session_dir / "session_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    else:
        meta = {}

    meta.update(
        {
            "version": 1,
            "provider": "Toss Securities Open API",
            "transport": "websocket",
            "channel": "trade:us",
            "market": "US",
            "session": "TOSS_US_EXTENDED",
            "currentSession": window.get("marketSession", "REGULAR"),
            "sessionDate": session_date,
            "openAt": window["openAt"],
            "closeAt": window["closeAt"],
            "calendarSource": window.get("calendarSource"),
            "symbols": symbols,
            "lossySource": True,
            "sourceSequenceAvailable": False,
            "completenessClaim": "NONE",
            "note": (
                "Toss trade WebSocket is documented as LOSSY and supplies no "
                "gap-detection sequence. Rows are ticks delivered to this client, "
                "not a guaranteed complete exchange tape."
            ),
            "startedAt": meta.get("startedAt") or utc_now_iso(),
            "endedAt": None,
        }
    )
    session_window = {
        "session": window.get("marketSession", "REGULAR"),
        "openAt": window["openAt"],
        "closeAt": window["closeAt"],
        "calendarSource": window.get("calendarSource"),
    }
    history = meta.setdefault("sessionWindows", [])
    if session_window not in history:
        history.append(session_window)
    atomic_json(meta_path, meta)

    writer = TickWriter(
        args.data_dir,
        session_date,
        symbols,
        args.queue_size,
        args.flush_seconds,
        meta,
    )
    writer_stop = asyncio.Event()
    writer_task = asyncio.create_task(writer.run(writer_stop))
    backoff = 1.0
    force_token = False

    try:
        while not stop.is_set() and as_utc_timestamp() < close_at:
            free = disk_free_gb(args.data_dir)
            if free < args.min_free_gb:
                meta["lowDiskPauses"] = int(meta.get("lowDiskPauses", 0)) + 1
                meta["lastError"] = (
                    f"low disk: {free:.2f} GiB free < {args.min_free_gb:.2f} GiB"
                )
                atomic_json(meta_path, meta)
                write_status(
                    state="paused_low_disk",
                    sessionDate=session_date,
                    freeGb=round(free, 2),
                    minFreeGb=args.min_free_gb,
                )
                await asyncio.sleep(min(60.0, max(1.0, (close_at - as_utc_timestamp()).total_seconds())))
                continue

            try:
                await stream_once(
                    symbols, writer, close_at, meta, force_token=force_token
                )
                force_token = False
                now = as_utc_timestamp()
                if now < close_at and not stop.is_set():
                    meta["reconnects"] = int(meta.get("reconnects", 0)) + 1
                    meta.setdefault("gaps", []).append(
                        {"detectedAt": utc_now_iso(), "error": "connection_closed"}
                    )
                    if len(meta["gaps"]) > 200:
                        meta["gaps"] = meta["gaps"][-200:]
                    atomic_json(meta_path, meta)
                    write_status(
                        state="reconnecting",
                        sessionDate=session_date,
                        error="connection_closed",
                        retrySeconds=backoff,
                    )
                    remaining = max(0.0, (close_at - now).total_seconds())
                    await asyncio.sleep(min(backoff, remaining))
                    backoff = min(30.0, backoff * 2)
                else:
                    backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                now = as_utc_timestamp()
                if now >= close_at or stop.is_set():
                    break
                meta["reconnects"] = int(meta.get("reconnects", 0)) + 1
                meta["lastError"] = str(exc)[:1000]
                meta.setdefault("gaps", []).append(
                    {"detectedAt": utc_now_iso(), "error": str(exc)[:500]}
                )
                if len(meta["gaps"]) > 200:
                    meta["gaps"] = meta["gaps"][-200:]
                atomic_json(meta_path, meta)
                write_status(
                    state="reconnecting",
                    sessionDate=session_date,
                    error=str(exc)[:500],
                    retrySeconds=backoff,
                )
                # One forced token refresh after a failed connection attempt also
                # recovers revoked/expired cached credentials.
                force_token = True
                remaining = max(0.0, (close_at - now).total_seconds())
                await asyncio.sleep(min(backoff, remaining))
                backoff = min(30.0, backoff * 2)
    finally:
        writer_stop.set()
        await writer_task
        ended_after_close = as_utc_timestamp() >= close_at
        meta["endedAt"] = utc_now_iso()
        meta["endReason"] = "regular_session_closed" if ended_after_close else "service_stopped"
        atomic_json(meta_path, meta)

        if ended_after_close and window.get("isFinalSession", False):
            write_status(
                state="compressing",
                sessionDate=session_date,
                marketSession=window.get("marketSession"),
                calendarSource=window.get("calendarSource"),
            )
            await asyncio.to_thread(compress_session, session_dir)
            meta["compressedAt"] = utc_now_iso()
            atomic_json(meta_path, meta)


async def daemon(args) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    while not stop.is_set():
        window = market_window()
        if not window["isOpen"]:
            seconds = max(
                1.0,
                (as_utc_timestamp(window["openAt"]) - as_utc_timestamp()).total_seconds(),
            )
            write_status(
                state="waiting_for_market_open",
                provider="toss_ws",
                sessionDate=window["sessionDate"],
                marketSession=window.get("marketSession"),
                calendarSource=window.get("calendarSource"),
                calendarError=window.get("calendarError"),
                nextOpenAt=window["openAt"],
                nextCloseAt=window["closeAt"],
                openAt=None,
                closeAt=None,
                connectionId=None,
                subscribed=0,
                error=None,
                symbols=args.symbols_list,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=min(300.0, seconds))
            except asyncio.TimeoutError:
                pass
            continue

        write_status(
            state="opening_market_session",
            provider="toss_ws",
            sessionDate=window["sessionDate"],
            marketSession=window.get("marketSession"),
            calendarSource=window.get("calendarSource"),
            openAt=window["openAt"],
            closeAt=window["closeAt"],
            symbols=args.symbols_list,
        )
        await collect_market_session(args, window, stop)

    write_status(state="stopped")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--symbols",
        help="Comma-separated US symbols. Default: research/universe_v3.json",
    )
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--queue-size", type=int, default=200_000)
    ap.add_argument("--flush-seconds", type=float, default=1.0)
    ap.add_argument("--min-free-gb", type=float, default=10.0)
    ap.add_argument(
        "--status-once",
        action="store_true",
        help="Print current Toss US market-session state and exit",
    )
    args = ap.parse_args()
    args.symbols_list = load_symbols(args.symbols)
    if args.queue_size < 1000:
        ap.error("--queue-size must be >= 1000")
    if args.flush_seconds <= 0:
        ap.error("--flush-seconds must be > 0")
    if args.min_free_gb < 0:
        ap.error("--min-free-gb must be >= 0")
    return args


def main() -> int:
    args = parse_args()
    if args.status_once:
        print(
            json.dumps(
                {
                    **market_window(),
                    "supportedSessions": [x[0] for x in SESSION_FIELDS],
                    "symbols": args.symbols_list,
                    "symbolCount": len(args.symbols_list),
                    "dataDir": str(args.data_dir),
                    "freeGb": round(disk_free_gb(args.data_dir), 2),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    asyncio.run(daemon(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
