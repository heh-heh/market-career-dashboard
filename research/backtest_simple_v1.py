#!/usr/bin/env python3
"""Historical research backtest for the deployed Simple Momentum V1 rules.

This is intentionally separate from live/paper execution. It reads the frozen
Toss 1-minute files, reconstructs a causal 30-symbol historical universe, and
uses a one-minute subsequent-observation proxy for fills.

Important limitation: historical Toss ranking snapshots/metadata are not stored.
The backtest therefore ranks only the collected universe by causal cumulative
daily volume and cannot reproduce the provider's historical top-100 ranking.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import heapq
import io
import json
import math
import os
import statistics
import time
from collections import defaultdict, deque
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from simple_momentum_v1 import Config, KNOWN_LEVERAGED

NY = ZoneInfo("America/New_York")
MINUTE = timedelta(minutes=1)
EPS = 1e-12


def parse_ts(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("historical candle timestamp lacks timezone offset")
    return dt.astimezone(NY)


def session_name(dt):
    t = dt.time()
    if dtime(4, 0) <= t < dtime(7, 0):
        return "DAY"
    if dtime(7, 0) <= t < dtime(9, 30):
        return "PRE"
    if dtime(9, 30) <= t < dtime(16, 0):
        return "REGULAR"
    return None


def cutoff_for(dt, session, config):
    day = dt.date()
    if session == "DAY":
        return datetime.combine(day, dtime(7, 0), NY) - timedelta(minutes=config.extended_exit_buffer_minutes)
    if session == "PRE":
        return datetime.combine(day, dtime(9, 30), NY) - timedelta(minutes=config.extended_exit_buffer_minutes)
    regular_close = datetime.combine(day, dtime(16, 0), NY)
    configured = datetime.combine(day, dtime.fromisoformat(config.force_exit_time), NY)
    return min(configured, regular_close - timedelta(minutes=10))


class CandleStream:
    def __init__(self, path, symbol):
        self.path = Path(path)
        self.symbol = symbol
        self.size = max(1, self.path.stat().st_size)
        self.raw = self.path.open("rb")
        self.gz = gzip.GzipFile(fileobj=self.raw, mode="rb")
        self.text = io.TextIOWrapper(self.gz, encoding="utf-8", newline="")
        self.reader = csv.DictReader(self.text)
        self.rows = 0

    def next(self):
        for r in self.reader:
            try:
                dt = parse_ts(r["timestamp"])
                o = float(r["open"]); h = float(r["high"]); l = float(r["low"]); c = float(r["close"])
                v = float(r.get("volume") or 0)
                if min(o, h, l, c) <= 0 or h + EPS < max(o, c, l) or l - EPS > min(o, c, h) or v < 0:
                    continue
                self.rows += 1
                return {
                    "symbol": self.symbol, "start": dt, "end": dt + MINUTE,
                    "open": o, "high": h, "low": l, "close": c, "volume": v,
                }
            except Exception:
                continue
        return None

    def compressed_offset(self):
        try:
            return min(self.size, self.raw.tell())
        except Exception:
            return 0

    def close(self):
        try: self.text.close()
        except Exception: pass
        try: self.gz.close()
        except Exception: pass
        try: self.raw.close()
        except Exception: pass


def atomic_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(tmp, p)


def append_log(path, message):
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fp:
        fp.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def write_state(path, log_path, **kwargs):
    payload = {
        "engine": "simple_momentum_v1_historical",
        "pid": os.getpid(),
        "updatedAt": time.time(),
        **kwargs,
    }
    if path:
        atomic_json(path, payload)
    if kwargs.get("message"):
        append_log(log_path, kwargs["message"])


def setup_from_window(window, config):
    if len(window) < config.window_minutes:
        return None, "INSUFFICIENT_COMPLETED_BARS"
    w = list(window)[-config.window_minutes:]
    if any(b["start"] - a["start"] != MINUTE for a, b in zip(w, w[1:])):
        return None, "MISSING_WINDOW_MINUTE"
    current, previous = w[-1], w[-2]
    before = w[:-1]
    peak = max(b["high"] for b in before)
    peak_index = max(i for i, b in enumerate(before) if b["high"] == peak)
    pullback = before[peak_index + 1:]
    impulse = 100 * (peak / w[0]["open"] - 1)
    depth = 100 * (1 - current["close"] / peak)
    low = min((b["low"] for b in pullback), default=None)
    bullish = current["close"] > current["open"]
    above = current["close"] > previous["high"]
    no_new_low = low is not None and current["low"] >= low
    reason = (
        "IMPULSE_BELOW_THRESHOLD" if impulse + 1e-9 < config.impulse_pct else
        "NO_POST_HIGH_PULLBACK" if not pullback else
        "PULLBACK_OUTSIDE_RANGE" if not config.pullback_min_pct - 1e-9 <= depth <= config.pullback_max_pct + 1e-9 else
        "REVERSAL_NOT_CONFIRMED" if not (bullish and above and no_new_low) else
        None
    )
    return {
        "recentImpulsePct": impulse,
        "recentHigh": peak,
        "pullbackPct": depth,
        "pullbackLow": low,
        "bullishBar": bullish,
        "closeAbovePrevHigh": above,
        "noNewLow": no_new_low,
        "triggerBarTimestamp": current["start"].isoformat(),
        "signalTimestamp": current["end"].isoformat(),
    }, reason


def summarize(trades, initial_cash=10000.0):
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "winRatePct": 0.0,
            "sumTradeReturnPct": 0.0, "expectancyPct": 0.0, "medianReturnPct": 0.0,
            "avgWinPct": 0.0, "avgLossPct": 0.0, "payoffRatio": 0.0,
            "profitFactor": 0.0, "pnlUsd": 0.0, "paperAccountReturnPct": 0.0,
            "maxDrawdownPct": 0.0,
        }
    rets = [float(t["returnPct"]) for t in trades]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x < 0]
    pnl = sum(float(t["pnlUsd"]) for t in trades)
    gross_profit = sum(float(t["pnlUsd"]) for t in trades if t["pnlUsd"] > 0)
    gross_loss = abs(sum(float(t["pnlUsd"]) for t in trades if t["pnlUsd"] < 0))
    avg_win = statistics.fmean(wins) if wins else 0.0
    avg_loss = abs(statistics.fmean(losses)) if losses else 0.0
    equity = peak = initial_cash
    mdd = 0.0
    for t in trades:
        equity += float(t["pnlUsd"])
        peak = max(peak, equity)
        mdd = min(mdd, equity / max(peak, EPS) - 1)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winRatePct": round(100 * len(wins) / len(trades), 3),
        "sumTradeReturnPct": round(sum(rets), 5),
        "expectancyPct": round(statistics.fmean(rets), 5),
        "medianReturnPct": round(statistics.median(rets), 5),
        "avgWinPct": round(avg_win, 5),
        "avgLossPct": round(avg_loss, 5),
        "payoffRatio": round(avg_win / avg_loss, 4) if avg_loss > EPS else (999.0 if avg_win > 0 else 0.0),
        "profitFactor": round(gross_profit / gross_loss, 4) if gross_loss > EPS else (999.0 if gross_profit > 0 else 0.0),
        "pnlUsd": round(pnl, 6),
        "paperAccountReturnPct": round(100 * pnl / initial_cash, 6),
        "maxDrawdownPct": round(100 * mdd, 6),
    }


def grouped_summary(trades, key_fn, initial_cash):
    groups = defaultdict(list)
    for t in trades:
        groups[str(key_fn(t))].append(t)
    return {k: summarize(v, initial_cash) for k, v in sorted(groups.items())}


def run(data_dir, config, state_path=None, result_path=None, trades_csv=None, log_path=None):
    data_dir = Path(data_dir)
    paths = sorted(data_dir.glob("*.csv.gz"))
    if not paths:
        raise RuntimeError(f"no historical *.csv.gz files in {data_dir}")

    symbols = [p.name[:-7] for p in paths]
    eligible_symbols = [s for s in symbols if s not in KNOWN_LEVERAGED]
    excluded = sorted(set(symbols) - set(eligible_symbols))
    paths = [p for p in paths if p.name[:-7] in eligible_symbols]
    streams = []
    heap = []
    serial = 0
    total_bytes = sum(p.stat().st_size for p in paths)

    symbol_state = {}
    for sym in eligible_symbols:
        symbol_state[sym] = {
            "date": None,
            "prevClose": None,
            "lastRegularClose": None,
            "dailyVolume": 0.0,
            "dailyNotional": 0.0,
            "session": None,
            "window": deque(maxlen=config.window_minutes),
        }

    trades = []
    funnel = defaultdict(int)
    completed_by_date = defaultdict(set)
    pending = None
    position = None
    paper_cash = config.initial_cash
    processed = 0
    last_state_at = 0.0
    current_time = None

    def progress_pct():
        read = sum(x.compressed_offset() for x in streams)
        return min(99.0, round(100 * read / max(total_bytes, 1), 2))

    def report(phase="running", message=None):
        nonlocal last_state_at
        last_state_at = time.monotonic()
        write_state(
            state_path, log_path,
            phase=phase, running=phase=="running", progress=100 if phase=="completed" else progress_pct(),
            symbols=len(eligible_symbols), excludedLeveraged=excluded, processedRows=processed,
            currentTimestamp=current_time.isoformat() if current_time else None,
            trades=len(trades), signals=funnel["signals"], entries=funnel["entries"],
            paperCash=round(paper_cash, 6), summary=summarize(trades, config.initial_cash),
            message=message,
        )

    def exit_position(price, observed_at, reason):
        nonlocal position, paper_cash
        slip = config.slippage_bps / 10000
        fill = price * (1 - slip)
        pnl = (fill - position["entryFillPrice"]) * position["quantity"]
        paper_cash += fill * position["quantity"]
        ret = 100 * (fill / position["entryFillPrice"] - 1)
        position["highestObservedPrice"] = max(position["highestObservedPrice"], price)
        position["lowestObservedPrice"] = min(position["lowestObservedPrice"], price)
        tr = {
            **position,
            "exitTimestamp": observed_at.isoformat(),
            "exitMarketPrice": price,
            "exitFillPrice": fill,
            "exitReason": reason,
            "pnlUsd": pnl,
            "returnPct": ret,
            "holdDurationSeconds": (observed_at - position["entryTimestampDt"]).total_seconds(),
            "mfePct": 100 * (position["highestObservedPrice"] / position["entryFillPrice"] - 1),
            "maePct": 100 * (position["lowestObservedPrice"] / position["entryFillPrice"] - 1),
        }
        tr.pop("entryTimestampDt", None)
        tr.pop("cutoffDt", None)
        trades.append(tr)
        completed_by_date[tr["date"]].add(tr["symbol"])
        funnel["exits"] += 1
        funnel["exit_" + reason] += 1
        position = None

    write_state(
        state_path, log_path, phase="starting", running=True, progress=0,
        symbols=len(eligible_symbols), excludedLeveraged=excluded, processedRows=0,
        trades=0, signals=0, entries=0,
        message=f"Simple V1 backtest starting · {len(eligible_symbols)} symbols",
    )
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")

    try:
        for p in paths:
            sym = p.name[:-7]
            stream = CandleStream(p, sym)
            streams.append(stream)
            row = stream.next()
            if row:
                heapq.heappush(heap, (row["start"], serial, row, stream))
                serial += 1

        while heap:
            start = heap[0][0]
            current_time = start
            group = []
            while heap and heap[0][0] == start:
                _, _, row, stream = heapq.heappop(heap)
                group.append(row)
                nxt = stream.next()
                if nxt:
                    heapq.heappush(heap, (nxt["start"], serial, nxt, stream))
                    serial += 1
            processed += len(group)

            current_bars = {}
            for bar in group:
                sym = bar["symbol"]
                session = session_name(bar["start"])
                if session not in config.entry_sessions:
                    continue
                st = symbol_state[sym]
                day = bar["start"].date().isoformat()
                if st["date"] != day:
                    st["date"] = day
                    st["prevClose"] = st["lastRegularClose"]
                    st["dailyVolume"] = 0.0
                    st["dailyNotional"] = 0.0
                    st["session"] = None
                    st["window"].clear()
                if st["session"] != session:
                    st["session"] = session
                    st["window"].clear()
                st["dailyVolume"] += bar["volume"]
                st["dailyNotional"] += ((bar["high"] + bar["low"] + bar["close"]) / 3) * bar["volume"]
                st["window"].append(bar)
                if session == "REGULAR":
                    st["lastRegularClose"] = bar["close"]
                current_bars[sym] = (bar, st, session)

            observed_at = start + MINUTE

            # A live Simple signal waits for a subsequent scan. Historical proxy:
            # use the next completed 1m close, not an ideal next-minute open.
            if pending:
                sym = pending["symbol"]
                item = current_bars.get(sym)
                if item:
                    bar, st, session = item
                    age = (observed_at - pending["signalTimeDt"]).total_seconds()
                    cutoff = pending["cutoffDt"]
                    valid = (
                        session == pending["session"] and
                        bar["start"].date().isoformat() == pending["date"] and
                        0 < age <= config.max_entry_delay_sec and
                        observed_at < cutoff and position is None
                    )
                    if valid:
                        price = bar["close"]
                        fill = price * (1 + config.slippage_bps / 10000)
                        budget = min(config.order_usd, paper_cash)
                        if budget >= config.order_usd - EPS:
                            qty = budget / fill
                            paper_cash -= budget
                            position = {
                                "symbol": sym, "date": pending["date"], "session": session,
                                "signalTimestamp": pending["signalTimestamp"],
                                "entryTimestamp": observed_at.isoformat(),
                                "entryTimestampDt": observed_at,
                                "entryMarketPrice": price, "entryFillPrice": fill,
                                "quantity": qty, "entryNotional": budget,
                                "dayChangeAtSignal": pending["dayChangePct"],
                                "impulsePct": pending["recentImpulsePct"],
                                "pullbackPct": pending["pullbackPct"],
                                "recentHigh": pending["recentHigh"],
                                "pullbackLow": pending["pullbackLow"],
                                "stopPrice": fill * (1 - config.hard_stop_pct / 100),
                                "trailActivationPrice": fill * (1 + config.trail_activation_pct / 100),
                                "trailingActivated": False,
                                "trailingStopPrice": None,
                                "highestObservedPrice": price,
                                "lowestObservedPrice": price,
                                "cutoffDt": cutoff,
                                "timeStopMinutes": config.time_stop_minutes,
                                "slippageBps": config.slippage_bps,
                                "executionProxy": "next-completed-1m-close-after-signal",
                                "entryLatencySeconds": age,
                            }
                            funnel["entries"] += 1
                        pending = None
                    elif age > config.max_entry_delay_sec or session != pending["session"] or observed_at >= cutoff:
                        funnel["pendingExpired"] += 1
                        pending = None

            # Manage only positions that existed before this observation. A just-
            # entered trade is not immediately re-evaluated at the same price.
            if position and position["entryTimestamp"] != observed_at.isoformat():
                item = current_bars.get(position["symbol"])
                if item:
                    bar, st, session = item
                    price = bar["close"]
                    old_trail = position["trailingStopPrice"] if position["trailingActivated"] else None
                    position["highestObservedPrice"] = max(position["highestObservedPrice"], price)
                    position["lowestObservedPrice"] = min(position["lowestObservedPrice"], price)
                    time_stop = position["entryTimestampDt"] + timedelta(minutes=position["timeStopMinutes"])
                    reason = (
                        "HARD_STOP" if price <= position["stopPrice"] else
                        "TRAILING_STOP" if old_trail is not None and price <= old_trail else
                        "SESSION_EXIT" if observed_at >= position["cutoffDt"] else
                        "TIME_STOP" if observed_at >= time_stop and not position["trailingActivated"] else
                        None
                    )
                    if reason:
                        exit_position(price, observed_at, reason)
                    else:
                        if (not position["trailingActivated"] and
                                price >= position["trailActivationPrice"]):
                            position["trailingActivated"] = True
                        if position["trailingActivated"]:
                            position["trailingStopPrice"] = (
                                position["highestObservedPrice"] * (1 - config.trail_distance_pct / 100)
                            )

            if not position and not pending:
                ranked = []
                for sym, (bar, st, session) in current_bars.items():
                    if st["prevClose"] is None or st["prevClose"] <= 0:
                        continue
                    day_change = 100 * (bar["close"] / st["prevClose"] - 1)
                    funnel["universeObservations"] += 1
                    if (bar["close"] < config.min_price or
                            day_change < config.min_day_change_pct or
                            st["dailyVolume"] < config.min_volume or
                            st["dailyNotional"] < config.min_amount_usd):
                        continue
                    funnel["thresholdCandidates"] += 1
                    ranked.append({
                        "symbol": sym, "bar": bar, "state": st, "session": session,
                        "price": bar["close"], "dayChangePct": day_change,
                        "tradingVolume": st["dailyVolume"],
                        "tradingAmount": st["dailyNotional"],
                    })
                ranked.sort(key=lambda x: (-x["tradingVolume"], x["symbol"]))
                for candidate in ranked[:config.max_candidates]:
                    funnel["candidateEvaluations"] += 1
                    setup, reason = setup_from_window(candidate["state"]["window"], config)
                    if reason is not None:
                        funnel["reject_" + reason] += 1
                        continue
                    day = candidate["bar"]["start"].date().isoformat()
                    if candidate["symbol"] in completed_by_date[day]:
                        funnel["reject_COMPLETED_TODAY"] += 1
                        continue
                    cutoff = cutoff_for(candidate["bar"]["start"], candidate["session"], config)
                    signal_time = candidate["bar"]["end"]
                    if signal_time >= cutoff:
                        funnel["reject_SESSION_CUTOFF"] += 1
                        continue
                    pending = {
                        **setup,
                        "symbol": candidate["symbol"], "date": day,
                        "session": candidate["session"], "dayChangePct": candidate["dayChangePct"],
                        "signalTimeDt": signal_time, "cutoffDt": cutoff,
                    }
                    funnel["signals"] += 1
                    break

            if time.monotonic() - last_state_at >= 2.0:
                report(message=None)

        if position:
            # Dataset end should not normally hold a position because each eligible
            # session has a cutoff. Keep the result explicit if data ended early.
            last = None
            for stream in streams:
                pass
            funnel["unresolvedAtDataEnd"] += 1

        result = {
            "engine": "simple_momentum_v1_historical",
            "generatedAt": time.time(),
            "data": {
                "directory": str(data_dir),
                "symbols": eligible_symbols,
                "excludedLeveragedSymbols": excluded,
                "historicalUniverseLimitation": (
                    "Historical provider ranking snapshots and metadata are unavailable. "
                    "Candidate ranking is reconstructed only within the collected universe "
                    "using causal cumulative daily volume."
                ),
            },
            "execution": {
                "signal": "completed 1m bars only",
                "entryProxy": "next completed 1m close after signal + slippage",
                "exitProxy": "subsequent completed 1m closes only - slippage",
                "slippageBpsPerSide": config.slippage_bps,
                "gapRule": "observed close is used; theoretical stop price is never granted",
                "intrabarHighLowUsedForExecution": False,
            },
            "configuration": {
                **vars(config),
                "entry_sessions": list(config.entry_sessions),
            },
            "funnel": dict(funnel),
            "overall": summarize(trades, config.initial_cash),
            "bySession": grouped_summary(trades, lambda x: x["session"], config.initial_cash),
            "bySymbol": grouped_summary(trades, lambda x: x["symbol"], config.initial_cash),
            "byExitReason": grouped_summary(trades, lambda x: x["exitReason"], config.initial_cash),
            "byYear": grouped_summary(trades, lambda x: x["date"][:4], config.initial_cash),
            "byMonth": grouped_summary(trades, lambda x: x["date"][:7], config.initial_cash),
            "trades": trades,
        }

        if result_path:
            atomic_json(result_path, result)
        if trades_csv:
            p = Path(trades_csv)
            p.parent.mkdir(parents=True, exist_ok=True)
            fields = [
                "date","session","symbol","signalTimestamp","entryTimestamp","exitTimestamp",
                "entryMarketPrice","entryFillPrice","exitMarketPrice","exitFillPrice",
                "returnPct","pnlUsd","exitReason","holdDurationSeconds","mfePct","maePct",
                "dayChangeAtSignal","impulsePct","pullbackPct","entryLatencySeconds",
            ]
            with p.open("w", encoding="utf-8", newline="") as fp:
                w = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                w.writerows(trades)

        report(phase="completed", message=f"Simple V1 backtest completed · {len(trades)} trades")
        return result
    finally:
        for stream in streams:
            stream.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "toss_1m"))
    ap.add_argument("--out", default="/var/lib/market-career-dashboard/backtest_simple_v1_result.json")
    ap.add_argument("--trades-csv", default="/var/lib/market-career-dashboard/backtest_simple_v1_trades.csv")
    ap.add_argument("--state", default="/var/lib/market-career-dashboard/backtest_simple_v1_state.json")
    ap.add_argument("--log", default="/var/lib/market-career-dashboard/backtest_simple_v1.log")
    args = ap.parse_args()
    config = Config.from_env()
    try:
        result = run(
            args.data, config,
            state_path=args.state, result_path=args.out,
            trades_csv=args.trades_csv, log_path=args.log,
        )
        print(json.dumps({
            "out": args.out,
            "tradesCsv": args.trades_csv,
            "overall": result["overall"],
            "funnel": result["funnel"],
        }, ensure_ascii=False, indent=2))
    except Exception as exc:
        write_state(
            args.state, args.log, phase="error", running=False, progress=0,
            error=str(exc), message="Simple V1 backtest error: " + str(exc),
        )
        raise


if __name__ == "__main__":
    main()
