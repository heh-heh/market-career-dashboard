#!/usr/bin/env python3
"""Collect historical 1-minute OHLCV candles from Toss Securities Open API.

The collector uses a persistent SQLite database per symbol so historical candles
are never accumulated in a large in-memory dict. The final CSV.gz is exported
from SQLite only when collection completes.
"""
import argparse
import csv
import gzip
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "server_secrets.json"
sys.path.insert(0, str(ROOT))
from toss_auth import get_token as shared_toss_token

BASE = "https://openapi.tossinvest.com"
DEFAULT_SYMBOLS = ["NVDA", "AMD", "INTC", "SOXL", "SOXS", "TQQQ"]
DEFAULT_UNTIL = "2026-10-01T23:59:59+00:00"
PROGRESS = ROOT / "data" / "toss_1m_progress.json"
ERROR_LOG = ROOT / "data" / "toss_errors.jsonl"
FIELDS = ["timestamp", "open", "high", "low", "close", "volume", "currency"]


def token(force=False):
    cfg = json.loads(SECRETS.read_text(encoding="utf-8"))
    tok = shared_toss_token(cfg, force=force)
    if not tok:
        raise RuntimeError("Toss OAuth token을 발급받지 못했습니다.")
    return tok


def record_error(symbol, before, attempt, error, http_code=None, body=None):
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "symbol": symbol,
        "before": before,
        "attempt": attempt,
        "httpCode": http_code,
        "error": str(error),
        "response": (body or "")[:1000],
    }
    with ERROR_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def fetch_page(tok, symbol, before=None, count=100, refresh_token=None):
    q = {
        "symbol": symbol,
        "interval": "1m",
        "count": str(count),
        "adjusted": "true",
    }
    if before:
        q["before"] = before
    url = BASE + "/api/v1/candles?" + urlencode(q)

    for attempt in range(8):
        try:
            req = Request(
                url,
                headers={
                    "Authorization": "Bearer " + tok,
                    "User-Agent": "market-career-dashboard/history-collector",
                },
            )
            with urlopen(req, timeout=20) as r:
                return json.loads(r.read()).get("result", {})
        except HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            if e.code == 429:
                record_error(symbol, before, attempt, "HTTPError", e.code, body)
                if attempt < 7:
                    time.sleep(min(30, 2 ** attempt))
                    continue
            if e.code == 401 and attempt < 2 and refresh_token is not None:
                record_error(symbol, before, attempt, "HTTPError", e.code, body)
                msg = body.lower()
                if "token-revoked" in msg or "unauthorized" in msg or "invalid-token" in msg:
                    tok = refresh_token()
                    time.sleep(0.5)
                    continue
            record_error(symbol, before, attempt, "HTTPError", e.code, body)
            raise RuntimeError(f"Toss candles HTTP {e.code}: {body[:300]}")
        except Exception as e:
            record_error(symbol, before, attempt, type(e).__name__, None, str(e))
            raise


def write_progress(symbol, payload):
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    if PROGRESS.exists():
        try:
            current = json.loads(PROGRESS.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current[symbol] = payload
    tmp = PROGRESS.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, PROGRESS)


def open_db(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candles (
            timestamp TEXT PRIMARY KEY,
            open TEXT,
            high TEXT,
            low TEXT,
            close TEXT,
            volume TEXT,
            currency TEXT
        )
        """
    )
    conn.commit()
    return conn


def import_existing_csv(conn, path, symbol):
    if not path.exists():
        return 0

    # If the database already has rows, it is already initialized and we do not
    # need to rescan the existing CSV on every restart.
    existing = conn.execute("SELECT 1 FROM candles LIMIT 1").fetchone()
    if existing:
        return 0

    imported = 0
    batch = []
    try:
        with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                ts = r.get("timestamp")
                if not ts:
                    continue
                batch.append((
                    ts,
                    r.get("open"),
                    r.get("high"),
                    r.get("low"),
                    r.get("close"),
                    r.get("volume"),
                    r.get("currency"),
                ))
                if len(batch) >= 10000:
                    conn.executemany(
                        "INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?)",
                        batch,
                    )
                    conn.commit()
                    imported += len(batch)
                    batch.clear()
        if batch:
            conn.executemany(
                "INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?)",
                batch,
            )
            conn.commit()
            imported += len(batch)
    except Exception:
        conn.rollback()
        raise

    print(f"{symbol}: imported existing CSV into SQLite rows={imported}", flush=True)
    return imported


def export_csv(conn, path):
    tmp = path.with_suffix(".tmp.gz")
    with gzip.open(tmp, "wt", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        cur = conn.execute(
            "SELECT timestamp,open,high,low,close,volume,currency "
            "FROM candles ORDER BY timestamp"
        )
        for row in cur:
            w.writerow(dict(zip(FIELDS, row)))
    os.replace(tmp, path)
    return conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]


def get_bounds(conn):
    row = conn.execute(
        "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM candles"
    ).fetchone()
    return int(row[0] or 0), row[1], row[2]


def coverage_pct(latest_ts, oldest_ts, since):
    if not latest_ts or not oldest_ts or not since:
        return 0.0
    try:
        import datetime as dt
        a = dt.datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()
        b = dt.datetime.fromisoformat(latest_ts.replace("Z", "+00:00")).timestamp()
        o = dt.datetime.fromisoformat(oldest_ts.replace("Z", "+00:00")).timestamp()
        if b <= a:
            return 0.0
        return max(0.0, min(100.0, ((b - o) / (b - a)) * 100.0))
    except Exception:
        return 0.0


def collect(symbol, since, until, pages, sleep_s, batch_pages, batch_sleep):
    tok = token()
    out = ROOT / "data" / "toss_1m"
    out.mkdir(parents=True, exist_ok=True)

    path = out / (symbol + ".csv.gz")
    db_path = out / (symbol + ".sqlite3")
    conn = open_db(db_path)

    try:
        import_existing_csv(conn, path, symbol)
        stored, existing_oldest, existing_latest = get_bounds(conn)

        # The API paginates backwards from 'before'. Resume from the oldest
        # persisted timestamp, so a restart never needs the full dataset in RAM.
        before = existing_oldest if existing_oldest and existing_oldest > since else until
        latest_ts = existing_latest

        started = time.time()
        write_progress(symbol, {
            "symbol": symbol,
            "status": "starting",
            "page": 0,
            "pages": pages,
            "stored": stored,
            "fetched": 0,
            "coveragePct": round(coverage_pct(latest_ts, existing_oldest, since), 2),
            "latestTimestamp": latest_ts,
            "oldestTimestamp": existing_oldest,
            "since": since,
            "startedAt": started,
            "updatedAt": started,
            "storage": "sqlite_streaming",
        })

        refresh = lambda: token(force=True)

        for n in range(pages):
            try:
                obj = fetch_page(tok, symbol, before, refresh_token=refresh)
                candles = obj.get("candles") or []
            except Exception as e:
                stored, oldest_ts, latest_ts = get_bounds(conn)
                write_progress(symbol, {
                    "symbol": symbol,
                    "status": "error",
                    "page": n,
                    "pages": pages,
                    "stored": stored,
                    "fetched": 0,
                    "coveragePct": round(coverage_pct(latest_ts, oldest_ts, since), 2),
                    "latestTimestamp": latest_ts,
                    "oldestTimestamp": oldest_ts,
                    "since": since,
                    "startedAt": started,
                    "updatedAt": time.time(),
                    "error": str(e)[:500],
                    "storage": "sqlite_streaming",
                })
                print(f"{symbol}: ERROR after page {n}; SQLite checkpoint retained rows={stored}", flush=True)
                raise

            if not candles:
                break

            rows = []
            page_ts = []
            for c in candles:
                ts = str(c.get("timestamp") or "")
                if not ts:
                    continue
                page_ts.append(ts)
                if since and ts < since:
                    continue
                if until and ts > until:
                    continue
                rows.append((
                    ts,
                    c.get("openPrice"),
                    c.get("highPrice"),
                    c.get("lowPrice"),
                    c.get("closePrice"),
                    c.get("volume"),
                    c.get("currency"),
                ))

            before_changes = conn.total_changes
            if rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?)",
                    rows,
                )
                conn.commit()
            added = conn.total_changes - before_changes

            stored, oldest_ts, latest_db_ts = get_bounds(conn)
            if latest_ts is None:
                latest_ts = latest_db_ts
            page_oldest = min(page_ts) if page_ts else oldest_ts
            coverage = coverage_pct(latest_ts, page_oldest, since)

            next_before = obj.get("nextBefore")
            write_progress(symbol, {
                "symbol": symbol,
                "status": "collecting",
                "page": n + 1,
                "pages": pages,
                "stored": stored,
                "fetched": len(candles),
                "added": added,
                "coveragePct": round(coverage, 2),
                "latestTimestamp": latest_ts,
                "oldestTimestamp": oldest_ts,
                "since": since,
                "startedAt": started,
                "updatedAt": time.time(),
                "storage": "sqlite_streaming",
            })
            print(
                f"{symbol}: page {n+1}/{pages}, fetched={len(candles)}, "
                f"added={added}, stored={stored}",
                flush=True,
            )

            if since and next_before and next_before < since:
                break
            if not next_before or next_before == before:
                break

            before = next_before
            time.sleep(sleep_s)

            # Checkpointing is now just a durable SQLite commit; no large
            # in-memory dataset is serialized.
            if (n + 1) % batch_pages == 0:
                write_progress(symbol, {
                    "symbol": symbol,
                    "status": "checkpoint",
                    "page": n + 1,
                    "pages": pages,
                    "stored": stored,
                    "fetched": len(candles),
                    "added": added,
                    "coveragePct": round(coverage, 2),
                    "latestTimestamp": latest_ts,
                    "oldestTimestamp": oldest_ts,
                    "since": since,
                    "startedAt": started,
                    "updatedAt": time.time(),
                    "storage": "sqlite_streaming",
                })
                print(
                    f"{symbol}: SQLite checkpoint rows={stored} at page {n+1}; "
                    f"pausing {batch_sleep:.1f}s",
                    flush=True,
                )
                if n + 1 < pages:
                    time.sleep(batch_sleep)

        stored, oldest_ts, latest_ts = get_bounds(conn)
        exported = export_csv(conn, path)
        write_progress(symbol, {
            "symbol": symbol,
            "status": "completed",
            "page": n + 1 if "n" in locals() else 0,
            "pages": pages,
            "stored": exported,
            "fetched": stored,
            "added": 0,
            "coveragePct": 100,
            "latestTimestamp": latest_ts,
            "oldestTimestamp": oldest_ts,
            "since": since,
            "startedAt": started,
            "updatedAt": time.time(),
            "storage": "sqlite_streaming",
        })
        print(f"{symbol}: total saved {exported} -> {path}", flush=True)
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--since", default="2021-01-01T00:00:00+00:00")
    ap.add_argument("--until", default=DEFAULT_UNTIL)
    ap.add_argument("--pages", type=int, default=10000)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--batch-pages", type=int, default=150)
    ap.add_argument("--batch-sleep", type=float, default=10.0)
    a = ap.parse_args()

    for s in [x.strip().upper() for x in a.symbols.split(",") if x.strip()]:
        collect(s, a.since, a.until, a.pages, a.sleep, a.batch_pages, a.batch_sleep)


if __name__ == "__main__":
    main()
