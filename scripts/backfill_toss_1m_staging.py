#!/usr/bin/env python3
"""Isolated historical gap acquisition. Does not touch production candle files.

Default mode is a configuration preview. --execute explicitly permits API calls.
The resulting staging data is unverified and MUST NOT be merged automatically.
"""
import argparse
import csv
import gzip
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from toss_auth import get_token
from toss_rate_limit import wait_for_slot, group_for_path

FIELDS = ("timestamp", "open", "high", "low", "close", "volume", "currency")
STAGING_ROOT = Path("/mnt/research/staging")
PRODUCTION = (ROOT / "data" / "toss_1m").resolve()


def instant(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    return dt.astimezone(timezone.utc)


def destination(value):
    base = STAGING_ROOT.resolve(strict=False)
    path = Path(value).expanduser().resolve(strict=False)
    if path == base or base not in path.parents:
        raise ValueError("Output must be a child of /mnt/research/staging")
    if path == PRODUCTION or path in PRODUCTION.parents or PRODUCTION in path.parents:
        raise ValueError("Production path overlap refused")
    if path.is_symlink():
        raise ValueError("Symlink staging directory refused")
    return path


def api_page(token, symbol, before):
    params = urlencode({"symbol": symbol, "interval": "1m", "count": "100",
                        "adjusted": "true", "before": before})
    wait_for_slot("MARKET_DATA_CHART")
    request = Request("https://openapi.tossinvest.com/api/v1/candles?" + params,
                      headers={"Authorization": "Bearer " + token,
                               "User-Agent": "market-career-dashboard/staging-backfill"})
    with urlopen(request, timeout=25) as response:
        result = json.load(response).get("result", {})
    if not isinstance(result, dict):
        raise ValueError("Unexpected API result")
    return result


def collect(symbol, since, until, dest, pages, delay):
    directory = destination(dest)
    db_path = directory / (symbol + ".sqlite3")
    csv_path = directory / (symbol + ".csv.gz")
    progress_path = directory / "progress.json"
    for p in (db_path, csv_path, progress_path):
        if p.is_symlink():
            raise ValueError("Symlink output refused: " + str(p))
    # Refuse any pre-existing output, so previous staging runs remain immutable.
    if any(p.exists() for p in (db_path, csv_path, progress_path)):
        raise FileExistsError("Use a NEW staging directory for this run")
    directory.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "server_secrets.json").read_text(encoding="utf-8"))
    token = get_token(config)
    if not token:
        raise RuntimeError("Toss access token unavailable")

    db = sqlite3.connect(db_path)
    state = {"symbol": symbol, "since": since, "until": until,
             "status": "running", "pagesRead": 0, "rowCount": 0,
             "oldestApiTimestamp": None, "verified": False}
    previous_oldest = None
    before = until

    try:
        db.execute("CREATE TABLE candles (timestamp TEXT PRIMARY KEY, open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, currency TEXT)")
        for i in range(pages):
            obj = api_page(token, symbol, before)
            candles = obj.get("candles") or []
            if not candles:
                state["status"] = "api_exhausted"
                break
            observed, rows = [], []
            for candle in candles:
                ts = candle.get("timestamp")
                if not ts:
                    continue
                dt = instant(ts)
                observed.append(dt)
                if instant(since) <= dt <= instant(until):
                    rows.append((str(ts), candle.get("openPrice"), candle.get("highPrice"),
                                 candle.get("lowPrice"), candle.get("closePrice"),
                                 candle.get("volume"), candle.get("currency")))
            if not observed:
                raise RuntimeError("API returned no valid timestamp")
            oldest = min(observed)
            if previous_oldest is not None and oldest >= previous_oldest:
                raise RuntimeError("Pagination failed to move backward")
            previous_oldest = oldest
            db.executemany("INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?)", rows)
            db.commit()
            state["pagesRead"] = i + 1
            state["rowCount"] = db.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
            state["oldestApiTimestamp"] = oldest.isoformat()
            if oldest < instant(since):
                state["status"] = "lower_boundary_reached"
                break
            next_before = obj.get("nextBefore")
            if not next_before or instant(next_before) >= instant(before):
                state["status"] = "pagination_stopped"
                break
            before = next_before
            if delay:
                time.sleep(delay)
        else:
            state["status"] = "page_limit"

        if state["rowCount"]:
            tmp = directory / (symbol + ".csv.tmp.gz")
            with gzip.open(tmp, "wt", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(FIELDS)
                writer.writerows(db.execute("SELECT timestamp,open,high,low,close,volume,currency FROM candles ORDER BY timestamp"))
            tmp.replace(csv_path)
    except Exception as exc:
        state["status"] = "error"
        state["error"] = str(exc)[:500]
        raise
    finally:
        db.close()
        progress_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(state, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="AAPL,TQQQ")
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pages", type=int, default=120)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--execute", action="store_true", help="Perform API requests; default is preview only")
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols or any(not re.fullmatch(r"[A-Z0-9.-]{1,16}", s) for s in symbols):
        parser.error("Invalid symbols")
    if len(symbols) != len(set(symbols)):
        parser.error("Duplicate symbols")
    if not 1 <= args.pages <= 10000 or args.sleep < 0:
        parser.error("Invalid page/sleep")
    if instant(args.since) >= instant(args.until):
        parser.error("since must precede until")
    out = destination(args.output_dir)
    print(json.dumps({"symbols": symbols, "since": args.since, "until": args.until,
                      "outputDir": str(out), "pages": args.pages, "execute": args.execute,
                      "productionUntouched": True}, indent=2))
    if not args.execute:
        print("PREVIEW ONLY; no API calls or files created")
        return
    if len(symbols) > 1:
        # Separate per-symbol directories protect independently recoverable runs.
        for symbol in symbols:
            collect(symbol, args.since, args.until, out / symbol, args.pages, args.sleep)
    else:
        collect(symbols[0], args.since, args.until, out / symbols[0], args.pages, args.sleep)


if __name__ == "__main__":
    main()
