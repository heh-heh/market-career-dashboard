#!/usr/bin/env python3
"""Read-only received-tick paper trading pilot; NEVER a broker or order client.

20 separate cash-only virtual accounts consume *newly received* regular-session
tick CSV files. Fills are hypothetical adverse-price proxies, not market fills.
No external networking, API credentials, trading/order modules, or actual money.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import secrets
import signal
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path("/srv/ai-data/paper-arena")
TICKS = Path("/srv/ai-data/stock-collection/ticks")
SYMBOLS = ("NVDA", "AMD", "SOXL", "SOXS", "SPY", "QQQ")
BOT_COUNT = 20
START_CENTS = 100_000   # $1,000 per virtual account
INITIAL_CENTS = BOT_COUNT * START_CENTS
POLL_SECONDS = 4
FRESH_SECONDS = 90
MAX_BYTES_PER_SYMBOL = 512 * 1024
SCHEMA = ("source_timestamp", "received_at", "price", "volume", "currency",
          "connection_id", "connection_seq")
RUN = True


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("naive timestamp")
    return d.astimezone(timezone.utc)


def usd(cents):
    return round(int(cents) / 100, 2)


def cents(price):
    d = Decimal(str(price))
    if not d.is_finite() or not Decimal("0") < d < Decimal("1000000"):
        raise ValueError("invalid tick price")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def read_json(path):
    try:
        if path.is_symlink():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path, obj):
    if path.is_symlink():
        raise RuntimeError("SYMLINK_OUTPUT_BLOCKED")
    tmp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(obj, out, ensure_ascii=False, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def init_db(db):
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=4000")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, val TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY, cash INTEGER NOT NULL, realized INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS positions (
        bot INTEGER NOT NULL, symbol TEXT NOT NULL, qty INTEGER NOT NULL,
        cost INTEGER NOT NULL, opened TEXT NOT NULL,
        PRIMARY KEY(bot,symbol));
    CREATE TABLE IF NOT EXISTS quotes (
        symbol TEXT PRIMARY KEY, price INTEGER NOT NULL, fast REAL NOT NULL,
        slow REAL NOT NULL, samples INTEGER NOT NULL, received TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cursors (
        file TEXT PRIMARY KEY, offset_bytes INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS decisions (
        bot INTEGER NOT NULL, symbol TEXT NOT NULL, last_at TEXT NOT NULL,
        PRIMARY KEY(bot,symbol));
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
        bot INTEGER NOT NULL, side TEXT NOT NULL, symbol TEXT NOT NULL,
        qty INTEGER NOT NULL, fill_cents INTEGER NOT NULL,
        fee_cents INTEGER NOT NULL, note TEXT NOT NULL);
    """)
    spec = json.dumps({"version": 1, "bots": BOT_COUNT, "seedCents": START_CENTS,
                       "symbols": SYMBOLS}, sort_keys=True)
    with db:
        prior = db.execute("SELECT val FROM meta WHERE key='configuration'").fetchone()
        if prior and prior[0] != spec:
            raise RuntimeError("PERSISTED_CONFIGURATION_CHANGED")
        if not prior:
            db.execute("INSERT INTO meta(key,val) VALUES('configuration',?)", (spec,))
            db.executemany("INSERT INTO accounts(id,cash) VALUES(?,?)",
                           ((i, START_CENTS) for i in range(1, BOT_COUNT + 1)))


def market_gate(now):
    state = read_json(TICKS / "status.json")
    session = state.get("sessionDate")
    ny_today = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    if (state.get("state") != "collecting" or state.get("marketSession") != "REGULAR"
            or session != ny_today):
        return False, session, "TICK_COLLECTOR_NOT_REGULAR", state
    try:
        if not aware(state["openAt"]) <= now < aware(state["closeAt"]):
            return False, session, "OUTSIDE_REGULAR_WINDOW", state
    except (TypeError, ValueError, KeyError):
        return False, session, "UNVERIFIED_SESSION_WINDOW", state
    return True, session, None, state


def read_incremental(db, session, now):
    """Read complete appended records only; on first encounter skip old file bytes.

    Persist the cursor with quotes and any paper fills in the SAME SQLite commit.
    The collector owns the source files. This reader never writes into TICKS.
    """
    gathered = []
    new_offsets = {}
    for symbol in SYMBOLS:
        path = TICKS / session / (symbol + ".csv")
        if path.is_symlink() or not path.is_file():
            continue
        key = session + "/" + symbol + ".csv"
        size = path.stat().st_size
        row = db.execute("SELECT offset_bytes FROM cursors WHERE file=?", (key,)).fetchone()
        if row is None:
            # New launch does not retroactively simulate earlier trades.
            new_offsets[key] = size
            continue
        start = row[0]
        if start > size:  # Truncated or replaced file: no historical rereading.
            new_offsets[key] = size
            continue
        if start == size:
            continue
        if path.stat().st_mtime < now.timestamp() - FRESH_SECONDS:
            new_offsets[key] = size
            continue
        with path.open("rb") as source:
            source.seek(start)
            payload = source.read(MAX_BYTES_PER_SYMBOL)
        end = payload.rfind(b"\n")
        if end < 0:
            continue  # Partial CSV row; revisit after writer flushes.
        completed = payload[:end + 1]
        new_offsets[key] = start + len(completed)
        for line in completed.splitlines():
            try:
                values = next(csv.reader(io.StringIO(line.decode("utf-8", "strict"))))
                if values == list(SCHEMA):
                    continue
                if len(values) != len(SCHEMA):
                    continue
                tick = dict(zip(SCHEMA, values))
                stamp = aware(tick["received_at"])
                # Reject old/future/out-of-order events for paper execution.
                if not -5 <= (now - stamp).total_seconds() <= FRESH_SECONDS:
                    continue
                price = cents(tick["price"])
                if tick["currency"] not in ("USD", "$"):
                    continue
                gathered.append((stamp, symbol, price))
            except (ValueError, UnicodeDecodeError, InvalidOperation, OverflowError,
                    StopIteration):
                continue
    gathered.sort(key=lambda t: (t[0], t[1]))
    return gathered, new_offsets


def update_quote(db, stamp, symbol, price):
    prev = db.execute("SELECT price,fast,slow,samples,received FROM quotes WHERE symbol=?",
                      (symbol,)).fetchone()
    now_iso = stamp.isoformat()
    if prev and aware(prev[4]) >= stamp:
        return
    fast = (prev[1] * 0.83 + price * 0.17) if prev else float(price)
    slow = (prev[2] * 0.965 + price * 0.035) if prev else float(price)
    samples = min(100_000, prev[3] + 1) if prev else 1
    db.execute("""INSERT INTO quotes(symbol,price,fast,slow,samples,received)
                  VALUES(?,?,?,?,?,?)
                  ON CONFLICT(symbol) DO UPDATE SET
                  price=excluded.price,fast=excluded.fast,slow=excluded.slow,
                  samples=excluded.samples,received=excluded.received""",
               (symbol, price, fast, slow, samples, now_iso))


def traded(db, bot, symbol, now):
    row = db.execute("SELECT last_at FROM decisions WHERE bot=? AND symbol=?",
                     (bot, symbol)).fetchone()
    return row and (now - aware(row[0])).total_seconds() < 90


def hypothetical_order(db, bot, symbol, side, market_price, now):
    wallet = db.execute("SELECT cash FROM accounts WHERE id=?", (bot,)).fetchone()
    if wallet is None:
        raise RuntimeError("WALLET_NOT_FOUND")
    cash = wallet[0]
    position = db.execute("SELECT qty,cost,opened FROM positions WHERE bot=? AND symbol=?",
                          (bot, symbol)).fetchone()
    # Explicit pessimistic execution proxy: 10 bps slippage + 5 bps fee,
    # rounded against the agent. NOT an actual bid/ask fill.
    fill = max(1, math.ceil(market_price * 1.001)) if side == "BUY" else max(
        1, math.floor(market_price * 0.999))
    if side == "BUY":
        if position or db.execute("SELECT COUNT(*) FROM positions WHERE bot=?",
                                  (bot,)).fetchone()[0] >= 3:
            return False
        budget = min(cash * 35 // 100, START_CENTS * 35 // 100)
        qty = budget // (fill + max(1, math.ceil(fill * 0.0005)))
        if qty < 1:
            return False
        total = qty * fill
        fee = max(1, math.ceil(total * 0.0005))
        if total + fee > cash:
            return False
        db.execute("UPDATE accounts SET cash=cash-?,realized=realized-? WHERE id=?",
                   (total + fee, fee, bot))
        db.execute("INSERT INTO positions VALUES(?,?,?,?,?)",
                   (bot, symbol, qty, fill, now.isoformat()))
    else:
        if not position or (now - aware(position[2])).total_seconds() < 90:
            return False
        qty = position[0]
        total = qty * fill
        fee = max(1, math.ceil(total * 0.0005))
        net = total - fee
        db.execute("UPDATE accounts SET cash=cash+?,realized=realized+? WHERE id=?",
                   (net, net - qty * position[1], bot))
        db.execute("DELETE FROM positions WHERE bot=? AND symbol=?", (bot, symbol))
    db.execute("INSERT INTO trades(at,bot,side,symbol,qty,fill_cents,fee_cents,note) "
               "VALUES(?,?,?,?,?,?,?,?)",
               (now.isoformat(), bot, side, symbol, qty, fill, fee, "ADVERSE_TICK_PROXY"))
    db.execute("INSERT INTO decisions(bot,symbol,last_at) VALUES(?,?,?) "
               "ON CONFLICT(bot,symbol) DO UPDATE SET last_at=excluded.last_at",
               (bot, symbol, now.isoformat()))
    return True


def decide(db, now):
    quotes = db.execute("SELECT symbol,price,fast,slow,samples,received FROM quotes").fetchall()
    for symbol, price, fast, slow, samples, received in quotes:
        if samples < 20 or slow <= 0 or (now - aware(received)).total_seconds() > 30:
            continue
        deviation = (fast - slow) / slow
        for bot in range(1, BOT_COUNT + 1):
            # Spread six markets over agents; fixed deterministic variants.
            if (SYMBOLS.index(symbol) + bot) % 6 > 3:
                continue
            if traded(db, bot, symbol, now):
                continue
            threshold = 0.00045 + (bot % 5) * 0.0002
            # Half momentum, half mean reversion; both use *observed* recent ticks.
            impulse = deviation if bot % 2 else -deviation
            has_position = db.execute("SELECT 1 FROM positions WHERE bot=? AND symbol=?",
                                      (bot, symbol)).fetchone() is not None
            if impulse > threshold and not has_position:
                hypothetical_order(db, bot, symbol, "BUY", price, now)
            elif impulse < -threshold / 2 and has_position:
                hypothetical_order(db, bot, symbol, "SELL", price, now)


def agent_statuses(db, now, paused, market_ok, marks):
    """Stable per-account read model; no live order access and no mutation."""
    accounts = db.execute("SELECT id,cash,realized FROM accounts ORDER BY id").fetchall()
    positions = db.execute(
        "SELECT bot,symbol,qty,cost,opened FROM positions ORDER BY bot,symbol").fetchall()
    trades = db.execute(
        "SELECT bot,COUNT(*),MAX(id) FROM trades GROUP BY bot").fetchall()
    trade_count = {bot: count for bot, count, _ in trades}
    last_trade = {}
    for bot, _, latest_id in trades:
        item = db.execute(
            "SELECT at,side,symbol,qty,fill_cents FROM trades WHERE id=?",
            (latest_id,)).fetchone()
        if item:
            last_trade[bot] = {
                "at": item[0], "side": item[1], "symbol": item[2],
                "qty": item[3], "fillUsd": usd(item[4])
            }
    by_bot = {bot: [] for bot, _, _ in accounts}
    for bot, symbol, qty, cost, opened in positions:
        quote = marks.get(symbol)
        px = quote[1] if quote else cost
        age = (now - aware(quote[2])).total_seconds() if quote else None
        by_bot[bot].append({
            "symbol": symbol,
            "qty": qty,
            "entryUsd": usd(cost),
            "markUsd": usd(px),
            "marketValueUsd": usd(qty * px),
            "unrealizedPnlUsd": usd(qty * (px - cost)),
            "openedAt": opened,
            "quoteAt": quote[2] if quote else None,
            "markStale": age is None or age > FRESH_SECONDS,
        })
    agents = []
    for bot, cash, realized in accounts:
        owned = by_bot[bot]
        # Use integer-cent quote marks for authoritative per-agent accounting.
        invested_cents = sum(
            qty * (marks[symbol][1] if symbol in marks else cost)
            for b, symbol, qty, cost, _ in positions if b == bot
        )
        equity_cents = cash + invested_cents
        watched = [sym for i, sym in enumerate(SYMBOLS) if (i + bot) % 6 <= 3]
        ready = any(
            (record := marks.get(sym)) and
            (now - aware(record[2])).total_seconds() <= 30
            and db.execute("SELECT samples FROM quotes WHERE symbol=?", (sym,)).fetchone()[0] >= 20
            for sym in watched
        )
        state = ("PAUSED" if paused else
                 "WAITING_FOR_MARKET" if not market_ok else
                 "HOLDING" if owned else
                 "READY" if ready else "DATA_WARMUP")
        agents.append({
            "id": f"A{bot:02d}",
            "strategy": "MOMENTUM" if bot % 2 else "MEAN_REVERSION",
            "state": state,
            "watchlist": watched,
            "initialCapitalUsd": usd(START_CENTS),
            "cashUsd": usd(cash),
            "investedUsd": usd(invested_cents),
            "equityUsd": usd(equity_cents),
            "pnlUsd": usd(equity_cents - START_CENTS),
            "returnPct": round((equity_cents - START_CENTS) * 100 / START_CENTS, 3),
            "realizedPnlUsd": usd(realized),
            "unrealizedPnlUsd": usd(sum(
                qty * ((marks[symbol][1] if symbol in marks else cost) - cost)
                for b, symbol, qty, cost, _ in positions if b == bot
            )),
            "positionCount": len(owned),
            "holdings": owned,
            "tradeCount": trade_count.get(bot, 0),
            "lastTrade": last_trade.get(bot),
            "markStale": any(item["markStale"] for item in owned),
        })
    return agents


def snapshot(db, now, paused, market_ok, session, reason, processed):
    wallets = db.execute("SELECT COALESCE(SUM(cash),0),COALESCE(SUM(realized),0),"
                         "COUNT(*) FROM accounts").fetchone()
    holdings = db.execute("""SELECT symbol,SUM(qty),COUNT(*),SUM(qty*cost)
                             FROM positions GROUP BY symbol ORDER BY symbol""").fetchall()
    marks = {r[0]: r for r in db.execute("SELECT symbol,price,received FROM quotes")}
    mv = 0
    entries = []
    for symbol, qty, agents, basis in holdings:
        record = marks.get(symbol)
        value = qty * record[1] if record else basis
        mv += value
        entries.append({"symbol": symbol, "shares": qty, "accounts": agents,
                        "marketValueUsd": usd(value),
                        "unrealizedPnlUsd": usd(value - basis),
                        "quoteTime": record[2] if record else None})
    equity = wallets[0] + mv
    recent = db.execute("""SELECT at,bot,side,symbol,qty,fill_cents,fee_cents
                           FROM trades ORDER BY id DESC LIMIT 10""").fetchall()
    latest = [{"symbol": s, "priceUsd": usd(row[1]), "receivedAt": row[2]}
              for s, row in sorted(marks.items())]
    active = db.execute("SELECT COUNT(DISTINCT bot) FROM positions").fetchone()[0]
    trades = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    updated = {
        "mode": "RECEIVED_TICK_PAPER_PILOT",
        "version": "0.9-lite",
        "researchOnly": True,
        "realOrdersEnabled": False,
        "executionAssumption": "TICK_PRICE_ADVERSE_10BPS_PLUS_FEE_5BPS",
        "updatedAt": now.isoformat(),
        "state": "PAUSED" if paused else ("MARKET_ACTIVE" if market_ok else "WAITING_FOR_MARKET"),
        "feedReason": reason,
        "sessionDate": session,
        "bots": wallets[2],
        "botsWithPositions": active,
        "initialCapitalUsd": usd(INITIAL_CENTS),
        "cashUsd": usd(wallets[0]),
        "investedUsd": usd(mv),
        "equityUsd": usd(equity),
        "pnlUsd": usd(equity - INITIAL_CENTS),
        "returnPct": round((equity - INITIAL_CENTS) * 100 / INITIAL_CENTS, 3),
        "realizedPnlUsd": usd(wallets[1]),
        "positionsCount": sum(row[2] for row in holdings),
        "uniqueHoldings": len(entries),
        "tradeCount": trades,
        "ticksProcessedThisPoll": processed,
        "holdings": entries,
        "quotes": latest,
        "agents": agent_statuses(db, now, paused, market_ok, marks),
        "recentTrades": [
            {"at": a, "bot": f"A{b:02d}", "side": side, "symbol": symbol,
             "qty": qty, "fillUsd": usd(price), "feeUsd": usd(fee)}
            for a, b, side, symbol, qty, price, fee in recent
        ],
    }
    atomic_json(BASE / "status.json", updated)


def cycle(db):
    now = utcnow()
    control = read_json(BASE / "control.json")
    paused = control.get("paused") is not False  # First install fails closed.
    market_ok, session, reason, _ = market_gate(now)
    processed = 0
    if not paused and market_ok:
        gathered, offsets = read_incremental(db, session, now)
        with db:
            for stamp, symbol, price in gathered:
                update_quote(db, stamp, symbol, price)
                processed += 1
            for name, offset in offsets.items():
                db.execute("""INSERT INTO cursors(file,offset_bytes) VALUES(?,?)
                              ON CONFLICT(file) DO UPDATE SET offset_bytes=excluded.offset_bytes""",
                           (name, offset))
            if gathered:
                decide(db, now)
    snapshot(db, now, paused, market_ok, session, reason, processed)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if BASE.is_symlink() or not BASE.is_dir():
        raise SystemExit("PAPER_DATA_ROOT_MISSING_OR_SYMLINK")
    db_path = BASE / "ledger.sqlite3"
    if db_path.is_symlink():
        raise SystemExit("DATABASE_SYMLINK_REJECTED")
    db = sqlite3.connect(db_path, timeout=10)
    init_db(db)
    def stop(*_):
        global RUN
        RUN = False
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    try:
        while RUN:
            cycle(db)
            if args.once:
                break
            time.sleep(POLL_SECONDS)
    finally:
        db.close()


if __name__ == "__main__":
    main()
