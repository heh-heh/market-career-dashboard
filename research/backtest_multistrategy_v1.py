#!/usr/bin/env python3
"""Cross-sectional backtest for strategy_engine.py using collected Toss 1-minute data.

Conservative assumptions:
- next-minute-open entry
- long-only execution (inverse ETFs can express bearish views)
- stop is checked before target when both touch in the same minute
- per-side slippage is applied
- one new position at a time in this first validation harness
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import statistics
import sys
import os
import time
from collections import defaultdict
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategy_engine import evaluate_rows, score_universe, apply_trade_decision

NY = ZoneInfo("America/New_York")
EPS = 1e-9


def parse_ts(value):
    s = str(value or "").strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY)
    return dt.astimezone(NY)


def load_symbol(path):
    rows = []
    with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                ts = parse_ts(r["timestamp"])
                o, h, l, c = map(float, (r["open"], r["high"], r["low"], r["close"]))
                v = float(r.get("volume") or 0)
                if min(o, h, l, c) <= 0:
                    continue
                rows.append({
                    "timestamp": ts.isoformat(),
                    "openPrice": o,
                    "highPrice": h,
                    "lowPrice": l,
                    "closePrice": c,
                    "volume": v,
                    "_dt": ts,
                })
            except Exception:
                continue
    rows.sort(key=lambda x: x["_dt"])
    by_day = defaultdict(list)
    for r in rows:
        dt = r["_dt"]
        if dtime(9, 30) <= dt.time() < dtime(16, 0):
            by_day[dt.date().isoformat()].append(r)
    return dict(by_day)


def at_or_before(rows, hh, mm):
    target = dtime(hh, mm)
    idx = -1
    for i, r in enumerate(rows):
        if r["_dt"].time() <= target:
            idx = i
        else:
            break
    return idx


def public_rows(rows):
    return [{k: v for k, v in r.items() if k != "_dt"} for r in rows]


def build_candidate(symbol, day_rows, idx):
    if idx < 5:
        return None
    window = day_rows[max(0, idx - 239): idx + 1]
    analysis = evaluate_rows(list(reversed(public_rows(window))))
    if not analysis.get("ready"):
        return None
    price = float(analysis.get("price") or day_rows[idx]["closePrice"])
    volume = sum(float(x.get("volume") or 0) for x in day_rows[:idx + 1])
    amount = sum(float(x["closePrice"]) * float(x.get("volume") or 0) for x in day_rows[:idx + 1])
    session_open = float(day_rows[0]["openPrice"])
    change_pct = (price / max(session_open, EPS) - 1.0) * 100.0
    return {
        "symbol": symbol,
        "name": symbol,
        "tradingVolume": volume,
        "tradingAmountUsd": amount,
        "price": price,
        "changePct": change_pct,
        **analysis,
    }


def simulate_long(day_rows, entry_idx, candidate, slippage_bps=2.0, max_hold_minutes=60):
    if entry_idx >= len(day_rows):
        return None
    raw_entry = float(day_rows[entry_idx]["openPrice"])
    slip = slippage_bps / 10000.0
    entry = raw_entry * (1.0 + slip)

    atr = float(candidate.get("atr14") or 0)
    stop = float(candidate.get("stopPrice") or 0)
    if stop <= 0 or stop >= entry:
        stop = entry - max(atr, entry * 0.003)
    risk = entry - stop
    if risk <= 0:
        return None

    target_pct = float(candidate.get("targetPct") or 0)
    target = entry * (1.0 + target_pct / 100.0) if target_pct > 0 else entry + 1.5 * risk
    max_idx = min(len(day_rows) - 1, entry_idx + max_hold_minutes)
    exit_px = float(day_rows[max_idx]["closePrice"])
    exit_reason = "time"

    for j in range(entry_idx, max_idx + 1):
        bar = day_rows[j]
        low, high = float(bar["lowPrice"]), float(bar["highPrice"])
        if low <= stop:
            exit_px = stop
            exit_reason = "stop"
            max_idx = j
            break
        if high >= target:
            exit_px = target
            exit_reason = "target"
            max_idx = j
            break
        if bar["_dt"].time() >= dtime(15, 55):
            exit_px = float(bar["closePrice"])
            exit_reason = "close"
            max_idx = j
            break

    sell_fill = exit_px * (1.0 - slip)
    ret = sell_fill / max(entry, EPS) - 1.0
    return {
        "entry": entry,
        "exit": sell_fill,
        "returnPct": ret * 100.0,
        "exitReason": exit_reason,
        "entryTime": day_rows[entry_idx]["_dt"].isoformat(),
        "exitTime": day_rows[max_idx]["_dt"].isoformat(),
        "riskPct": risk / max(entry, EPS) * 100.0,
    }


def summarize(trades):
    vals = [float(t["returnPct"]) for t in trades]
    if not vals:
        return {
            "trades": 0, "winRatePct": 0, "avgWinPct": 0, "avgLossPct": 0,
            "expectancyPct": 0, "profitFactor": 0, "totalReturnPct": 0,
            "maxDrawdownPct": 0,
        }
    wins = [x for x in vals if x > 0]
    losses = [x for x in vals if x <= 0]
    wr = len(wins) / len(vals)
    avgw = statistics.fmean(wins) if wins else 0.0
    avgl = abs(statistics.fmean(losses)) if losses else 0.0
    gp, gl = sum(wins), abs(sum(losses))
    equity = peak = 0.0
    mdd = 0.0
    for x in vals:
        equity += x
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    return {
        "trades": len(vals),
        "winRatePct": round(wr * 100, 2),
        "avgWinPct": round(avgw, 4),
        "avgLossPct": round(avgl, 4),
        "expectancyPct": round(wr * avgw - (1.0 - wr) * avgl, 4),
        "profitFactor": round(gp / gl, 3) if gl > 0 else (999.0 if gp > 0 else 0.0),
        "totalReturnPct": round(sum(vals), 3),
        "maxDrawdownPct": round(mdd, 3),
    }



def atomic_json(path, payload):
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def append_log(path, message):
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with p.open("a", encoding="utf-8") as fp:
        fp.write(f"[{stamp}] {message}\n")


def progress_state(state_path, log_path, **kwargs):
    payload = {
        "engine": "strategy_engine_v3",
        "updatedAt": time.time(),
        **kwargs,
    }
    atomic_json(state_path, payload)
    msg = kwargs.get("message")
    if msg:
        append_log(log_path, msg)


def run(data_dir, symbols, min_score, slippage_bps, state_path=None, log_path=None):
    datasets = {}
    for sym in symbols:
        path = data_dir / f"{sym}.csv.gz"
        if path.exists():
            datasets[sym] = load_symbol(path)

    all_days = sorted(set().union(*(set(v.keys()) for v in datasets.values()))) if datasets else []
    progress_state(
        state_path, log_path,
        phase="backtest", progress=1, running=True,
        symbols=sorted(datasets), totalDays=len(all_days), completedDays=0,
        trades=0, currentDay=None,
        message=f"백테스트 시작 · {len(datasets)}종목 · {len(all_days)}거래일",
    )
    checkpoints = [
        (10, 0), (10, 15), (10, 30), (10, 45),
        (11, 0), (11, 30), (12, 0), (12, 30),
        (13, 0), (13, 30), (14, 0), (14, 30),
        (15, 0), (15, 20), (15, 35),
    ]

    trades = []
    funnel = defaultdict(int)
    used = set()

    for day_no, day in enumerate(all_days, start=1):
        progress = 2 + int((day_no - 1) / max(1, len(all_days)) * 94)
        progress_state(
            state_path, log_path,
            phase="backtest", progress=progress, running=True,
            symbols=sorted(datasets), totalDays=len(all_days), completedDays=day_no-1,
            trades=len(trades), currentDay=day,
            message=(f"진행 {day_no}/{len(all_days)} · {day}" if day_no == 1 or day_no % 10 == 0 else None),
        )
        for hh, mm in checkpoints:
            candidates, idx_map = [], {}
            for sym, days in datasets.items():
                rows = days.get(day)
                if not rows:
                    continue
                idx = at_or_before(rows, hh, mm)
                if idx < 5:
                    continue
                c = build_candidate(sym, rows, idx)
                if c:
                    candidates.append(c)
                    idx_map[sym] = idx
                    funnel["evaluations"] += 1

            if not candidates:
                continue

            ranked = score_universe(candidates)
            for c in ranked:
                apply_trade_decision(c, min_score)
                if c.get("strategyActive"):
                    funnel["activeStrategySetups"] += 1
                if c.get("signal") == "BUY":
                    funnel["buySignals"] += 1

            chosen = next(
                (c for c in ranked if c.get("signal") == "BUY" and (day, c["symbol"]) not in used),
                None,
            )
            if not chosen:
                continue

            sym = chosen["symbol"]
            rows = datasets[sym][day]
            result = simulate_long(rows, idx_map[sym] + 1, chosen, slippage_bps=slippage_bps)
            if not result:
                continue
            used.add((day, sym))
            funnel["trades"] += 1
            trades.append({
                "day": day,
                "symbol": sym,
                "strategy": chosen.get("bestStrategy"),
                "finalScore": chosen.get("finalScore"),
                "stockScore": chosen.get("stockScore"),
                "strategyScore": chosen.get("bestStrategyScore"),
                **result,
            })

    by_strategy = defaultdict(list)
    by_combo = defaultdict(list)
    for t in trades:
        by_strategy[t["strategy"]].append(t)
        by_combo[f"{t['symbol']}::{t['strategy']}"].append(t)

    result = {
        "engine": "strategy_engine_v3",
        "execution": {
            "direction": "long-only",
            "slippageBpsPerSide": slippage_bps,
            "entry": "next-minute-open",
            "maxHoldMinutes": 60,
            "sameBarRule": "stop-before-target",
        },
        "symbols": sorted(datasets),
        "days": len(all_days),
        "minFinalScore": min_score,
        "funnel": dict(funnel),
        "overall": summarize(trades),
        "byStrategy": {k: summarize(v) for k, v in sorted(by_strategy.items())},
        "byTickerStrategy": {k: summarize(v) for k, v in sorted(by_combo.items())},
        "trades": trades,
    }
    progress_state(
        state_path, log_path,
        phase="completed", progress=100, running=False,
        symbols=sorted(datasets), totalDays=len(all_days), completedDays=len(all_days),
        trades=len(trades), currentDay=None,
        summary=result["overall"], byStrategy=result["byStrategy"],
        message=f"백테스트 완료 · 거래 {len(trades)}건",
    )
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "toss_1m"))
    ap.add_argument("--symbols", default="auto")
    ap.add_argument("--out", default=str(ROOT / "research" / "backtest_multistrategy_v1_result.json"))
    ap.add_argument("--min-score", type=float, default=55.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--state", default="")
    ap.add_argument("--log", default="")
    args = ap.parse_args()

    data_dir = Path(args.data)
    if args.symbols.strip().lower() == "auto":
        symbols = sorted(p.name[:-7] for p in data_dir.glob("*.csv.gz"))
    else:
        symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    try:
        result = run(
            data_dir, symbols, args.min_score, args.slippage_bps,
            state_path=(args.state or None), log_path=(args.log or None),
        )
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "out": str(out),
            "symbols": result["symbols"],
            "days": result["days"],
            "overall": result["overall"],
            "funnel": result["funnel"],
        }, ensure_ascii=False, indent=2))
    except Exception as exc:
        progress_state(
            (args.state or None), (args.log or None),
            phase="error", progress=0, running=False,
            error=str(exc), message="백테스트 오류: " + str(exc),
        )
        raise


if __name__ == "__main__":
    main()
