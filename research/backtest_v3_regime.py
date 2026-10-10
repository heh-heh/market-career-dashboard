#!/usr/bin/env python3
"""V3 research: regime filters for the existing TQQQ V2 mean-reversion setup.

This module deliberately reuses the V2 loader, feature calculation, signal,
execution, stop/target and cost assumptions.  It only adds causal market-regime
observations from QQQ, SPY, SOXX and SMH.  It never collects data or submits an
order.  The production EC2 instance supplies ``data/toss_1m/*.csv.gz``.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime, time as dtime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = Path(__file__).resolve().parent
sys.path.insert(0, str(RESEARCH))

from backtest_v2_tqqq_mr import (  # noqa: E402
    NY,
    aggregate,
    features as v2_features,
    load_days,
    signal as v2_signal,
    simulate as v2_simulate,
)

REQUIRED_SYMBOLS = ("TQQQ", "QQQ", "SPY", "SOXX", "SMH")
SLIPPAGE_BPS = 2.0
MIN_HISTORY_DAYS = 10
QUARTILE = 0.75
TREND_DISTANCE_ATR_CEILING = 0.75
TREND_SLOPE_ATR_CEILING = 0.35
ER_CEILING = 0.60
MIN_FOLD_TRADES = 8
MIN_OOS_TRADES_TOTAL = 24

# MR009 as described in the prior research note.  These values are fixed in
# this iteration; no V2 parameter search is performed here.
BASE_PARAMS = {
    "z_entry": 1.2,
    "er_max": 0.40,
    "rvol_max": 1.8,
    "cross_min": 2,
    "slope_max": 0.35,
    "stop_atr": 1.2,
    "target": "VWAP",
    "min_target_r": 0.6,
    "target_r": 1.0,
    "max_hold_min": 60,
    "start": (10, 0),
    "end": (14, 30),
}


def parse_ts(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY)
    return dt.astimezone(NY)


def load_required_days(data_dir):
    missing = [s for s in REQUIRED_SYMBOLS if not (data_dir / f"{s}.csv.gz").is_file()]
    if missing:
        raise RuntimeError(
            "필수 데이터가 없습니다: " + ", ".join(missing)
            + f"; expected existing CSV files under {data_dir}. "
            "No fallback or download is attempted."
        )
    return {symbol: load_days(data_dir / f"{symbol}.csv.gz") for symbol in REQUIRED_SYMBOLS}


def daily_stats(days):
    """Daily volatility/gap observations; thresholds use prior days only."""
    out = {}
    previous_close = None
    for day, rows in sorted(days.items()):
        if not rows:
            continue
        high = max(x["h"] for x in rows)
        low = min(x["l"] for x in rows)
        opening = rows[0]["o"]
        close = rows[-1]["c"]
        if previous_close and previous_close > 0:
            tr = max(high - low, abs(high - previous_close), abs(low - previous_close))
            out[day] = {
                "open": opening,
                "close": close,
                "gap": abs(opening / previous_close - 1.0),
                "atr_pct": tr / previous_close,
            }
        previous_close = close
    return out


def percentile(values, q=QUARTILE):
    values = sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def prior_quartile(stats, day, field):
    prior = [row[field] for key, row in sorted(stats.items()) if key < day]
    if len(prior) < MIN_HISTORY_DAYS:
        return None
    return percentile(prior)


def _bars_until(rows, timestamp):
    return [row for row in rows if row["dt"] <= timestamp]


def _side(snapshot, distance_atr=0.10):
    if not snapshot or snapshot["atr"] <= 0:
        return 0
    distance = (snapshot["price"] - snapshot["vwap"]) / snapshot["atr"]
    if distance > distance_atr:
        return 1
    if distance < -distance_atr:
        return -1
    return 0


def _return_since(rows, minutes=15):
    if len(rows) < minutes + 1:
        return None
    old = rows[-minutes - 1]["c"]
    return rows[-1]["c"] / old - 1.0 if old else None


def compute_market_regime(day, decision_time, market_days, daily):
    """Compute only information available through ``decision_time``.

    ``day`` is the TQQQ session date.  All reference snapshots are cut at the
    same wall-clock timestamp, and daily quartiles exclude the current day.
    """
    snapshots = {}
    for symbol in ("QQQ", "SPY", "SOXX", "SMH"):
        rows = market_days.get(symbol, {}).get(day, [])
        cut = _bars_until(rows, decision_time)
        bars = aggregate(cut, 5)
        if len(bars) < 6:
            return {"ready": False, "reason": f"{symbol}_INSUFFICIENT_INTRADAY_DATA"}
        f = v2_features(bars)
        f["return15"] = _return_since(cut, 15)
        f["side"] = _side(f)
        snapshots[symbol] = f

    q, s = snapshots["QQQ"], snapshots["SPY"]
    sx, sm = snapshots["SOXX"], snapshots["SMH"]
    q_side, s_side = q["side"], s["side"]
    semi_sides = [x["side"] for x in (sx, sm) if x["side"]]
    semi_side = 0 if not semi_sides else (1 if sum(semi_sides) > 0 else -1 if sum(semi_sides) < 0 else 0)
    aligned = q_side != 0 and q_side == s_side
    disagreement = q_side != 0 and s_side != 0 and q_side != s_side
    if aligned and q_side > 0 and (q.get("return15") or 0) > 0 and (s.get("return15") or 0) > 0:
        risk_state = "risk_on"
    elif aligned and q_side < 0 and (q.get("return15") or 0) < 0 and (s.get("return15") or 0) < 0:
        risk_state = "risk_off"
    elif q_side == 0 and s_side == 0:
        risk_state = "neutral"
    else:
        risk_state = "mixed"
    semi_divergence = bool(aligned and semi_side and semi_side != q_side)
    trend_strength = max(
        abs((q["price"] - q["vwap"]) / max(q["atr"], 1e-12)),
        abs((s["price"] - s["vwap"]) / max(s["atr"], 1e-12)),
        abs(q["vwapSlope"]), abs(s["vwapSlope"]),
    )
    # Current-day volatility is measured only through the decision timestamp.
    # The full-day ATR values in ``daily`` are used only for prior-day quartiles.
    realized_values = []
    for symbol in ("QQQ", "SPY"):
        cut = _bars_until(market_days[symbol][day], decision_time)
        if cut and cut[0]["o"] > 0:
            realized_values.append((max(x["h"] for x in cut) - min(x["l"] for x in cut)) / cut[0]["o"])
    atr_values = realized_values
    gap_values = [daily[symbol].get(day, {}).get("gap") for symbol in ("QQQ", "SPY")]
    volatility = max((x for x in atr_values if x is not None), default=None)
    gap = max((x for x in gap_values if x is not None), default=None)
    vol_threshold = max((x for symbol in ("QQQ", "SPY") if (x := prior_quartile(daily[symbol], day, "atr_pct")) is not None), default=None)
    gap_threshold = max((x for symbol in ("QQQ", "SPY") if (x := prior_quartile(daily[symbol], day, "gap")) is not None), default=None)
    return {
        "ready": True,
        "day": day,
        "timestamp": decision_time.isoformat(),
        "qqq_side": q_side,
        "spy_side": s_side,
        "qqq_vwap_distance_atr": (q["price"] - q["vwap"]) / max(q["atr"], 1e-12),
        "spy_vwap_distance_atr": (s["price"] - s["vwap"]) / max(s["atr"], 1e-12),
        "qqq_vwap_slope": q["vwapSlope"],
        "spy_vwap_slope": s["vwapSlope"],
        "qqq_er": q["er"],
        "spy_er": s["er"],
        "qqq_return15": q.get("return15"),
        "spy_return15": s.get("return15"),
        "volatility_value": volatility,
        "volatility_threshold": vol_threshold,
        "volatility_extreme": volatility is not None and vol_threshold is not None and volatility > vol_threshold,
        "gap_value": gap,
        "gap_threshold": gap_threshold,
        "gap_extreme": gap is not None and gap_threshold is not None and gap > gap_threshold,
        "trend_strength": trend_strength,
        "trend_distance_extreme": max(abs((q["price"] - q["vwap"]) / max(q["atr"], 1e-12)),
                                       abs((s["price"] - s["vwap"]) / max(s["atr"], 1e-12))) > TREND_DISTANCE_ATR_CEILING,
        "trend_slope_extreme": max(abs(q["vwapSlope"]), abs(s["vwapSlope"])) > TREND_SLOPE_ATR_CEILING,
        "trend_extreme": max(abs((q["price"] - q["vwap"]) / max(q["atr"], 1e-12)),
                              abs((s["price"] - s["vwap"]) / max(s["atr"], 1e-12))) > TREND_DISTANCE_ATR_CEILING
                        or max(abs(q["vwapSlope"]), abs(s["vwapSlope"])) > TREND_SLOPE_ATR_CEILING,
        "semi_side": semi_side,
        "semi_relative_return15": (sx.get("return15") or 0.0) - (q.get("return15") or 0.0),
        "aligned": aligned,
        "disagreement": disagreement,
        "risk_state": risk_state,
        "semi_divergence": semi_divergence,
    }


def passes_regime_filter(regime, variant_id):
    if variant_id == "baseline":
        return True, "BASELINE"
    if not regime.get("ready"):
        return False, regime.get("reason", "REGIME_DATA_MISSING")
    if variant_id == "h1_trend_exclusion":
        ok = not regime["trend_extreme"]
        return ok, "MARKET_TREND_TOO_STRONG" if not ok else "H1_TREND_OK"
    if variant_id == "h1_trend_er_ceiling":
        ok = not regime["trend_extreme"] and max(regime["qqq_er"], regime["spy_er"]) <= ER_CEILING
        return ok, "TREND_OR_ER_TOO_STRONG" if not ok else "H1_TREND_ER_OK"
    if variant_id == "h2_high_vol_exclusion":
        return not regime["volatility_extreme"], "HIGH_VOLATILITY" if regime["volatility_extreme"] else "H2_VOL_OK"
    if variant_id == "h2_high_gap_exclusion":
        return not regime["gap_extreme"], "HIGH_OPENING_GAP" if regime["gap_extreme"] else "H2_GAP_OK"
    if variant_id == "h2_vol_or_gap_exclusion":
        bad = regime["volatility_extreme"] or regime["gap_extreme"]
        return not bad, "VOL_OR_GAP_EXTREME" if bad else "H2_VOL_GAP_OK"
    if variant_id == "h3_disagreement_exclusion":
        return not regime["disagreement"], "QQQ_SPY_DISAGREE" if regime["disagreement"] else "H3_AGREE_OK"
    if variant_id == "h3_sector_confirmation":
        ok = regime["aligned"] and regime["semi_side"] == regime["qqq_side"]
        return ok, "MARKET_SECTOR_NOT_CONFIRMED" if not ok else "H3_SECTOR_OK"
    if variant_id == "h3_mixed_or_divergence_exclusion":
        bad = regime["risk_state"] == "mixed" or regime["semi_divergence"]
        return not bad, "MIXED_OR_SECTOR_DIVERGENCE" if bad else "H3_RISK_OK"
    raise ValueError(f"Unknown regime variant: {variant_id}")


VARIANTS = (
    ("baseline", "Baseline: V2 MR009 without a market filter"),
    ("h1_trend_exclusion", "H1-1: exclude moderate QQQ/SPY trend strength"),
    ("h1_trend_er_ceiling", "H1-2: H1-1 plus QQQ/SPY ER ceiling"),
    ("h2_high_vol_exclusion", "H2-1: exclude highest historical ATR-volatility quartile"),
    ("h2_high_gap_exclusion", "H2-2: exclude highest historical opening-gap quartile"),
    ("h2_vol_or_gap_exclusion", "H2-3: exclude either extreme volatility or gap"),
    ("h3_disagreement_exclusion", "H3-1: exclude QQQ/SPY directional disagreement"),
    ("h3_sector_confirmation", "H3-2: require market alignment and SOXX/SMH confirmation"),
    ("h3_mixed_or_divergence_exclusion", "H3-3: exclude mixed risk or semiconductor divergence"),
)


def _day_slice(days, keys):
    return {key: days[key] for key in keys if key in days}


def _folds(days):
    keys = sorted(days)
    if len(keys) < 30:
        raise RuntimeError(f"최소 30 거래일이 필요합니다. 현재 {len(keys)}일입니다.")
    # Same anchored chronological convention as V2: no shuffled or random folds.
    cuts = ((0.45, 0.60), (0.60, 0.75), (0.75, 1.0))
    out = []
    for train_end_ratio, test_end_ratio in cuts:
        train_end = max(1, int(len(keys) * train_end_ratio))
        test_end = max(train_end + 1, int(len(keys) * test_end_ratio))
        train_keys = keys[:train_end]
        test_keys = keys[train_end:test_end]
        if not test_keys:
            continue
        out.append({
            "train_keys": train_keys,
            "test_keys": test_keys,
            "train_range": [train_keys[0], train_keys[-1]],
            "oos_range": [test_keys[0], test_keys[-1]],
        })
    if len(out) < 3:
        raise RuntimeError("3개 chronological OOS fold를 만들 수 없습니다.")
    return out


def _group_metrics(trades, key_fn):
    groups = defaultdict(list)
    for trade in trades:
        groups[key_fn(trade)].append(trade)
    return {str(key): summarize(values) for key, values in sorted(groups.items())}


def summarize(trades):
    rs = [float(t["R"]) for t in trades]
    if not rs:
        return {"trades": 0, "win_rate_pct": 0.0, "expectancy_r": 0.0, "profit_factor": 0.0,
                "max_drawdown_r": 0.0, "gross_profit_r": 0.0, "gross_loss_r": 0.0}
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    equity = peak = 0.0
    max_dd = 0.0
    for value in rs:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(rs),
        "win_rate_pct": round(100.0 * len(wins) / len(rs), 2),
        "expectancy_r": round(statistics.fmean(rs), 5),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else (999.0 if wins else 0.0),
        "max_drawdown_r": round(max_dd, 5),
        "gross_profit_r": round(sum(wins), 5),
        "gross_loss_r": round(abs(sum(losses)), 5),
        "avg_mfe_r": round(statistics.fmean(float(t.get("mfeR", 0)) for t in trades), 5),
        "avg_mae_r": round(statistics.fmean(float(t.get("maeR", 0)) for t in trades), 5),
    }


def report_breakdowns(trades):
    def year(trade):
        return parse_ts(trade["entryTime"]).strftime("%Y")

    def tod(trade):
        t = parse_ts(trade["entryTime"]).time()
        if t < dtime(10): return "09:30-10:00"
        if t < dtime(11): return "10:00-11:00"
        if t < dtime(12): return "11:00-12:00"
        if t < dtime(14): return "12:00-14:00"
        if t < dtime(15): return "14:00-15:00"
        return "15:00-16:00"

    return {
        "yearly": _group_metrics(trades, year),
        "time_of_day": _group_metrics(trades, tod),
        "regime": _group_metrics(trades, lambda x: x.get("regimeState", "unknown")),
    }


def run_variant(tqqq_days, market_days, daily, variant_id, eval_keys, slippage_bps):
    trades = []
    filter_reasons = defaultdict(int)
    for day, rows in sorted(tqqq_days.items()):
        if day not in eval_keys:
            continue
        bars = aggregate(rows, 5)
        for index in range(5, len(bars)):
            history = bars[:index + 1]
            features = v2_features(history)
            decision_time = bars[index]["dt"]
            if not v2_signal(features, BASE_PARAMS, decision_time):
                continue
            regime = compute_market_regime(day, decision_time, market_days, daily)
            passed, reason = passes_regime_filter(regime, variant_id)
            if not passed:
                filter_reasons[reason] += 1
                continue
            entry_index = bars[index]["end"] + 1
            trade = v2_simulate(rows, entry_index, features, BASE_PARAMS, slippage_bps)
            if trade:
                trade.update({
                    "day": day,
                    "regimeState": regime.get("risk_state", "unavailable"),
                    "volatilityExtreme": regime.get("volatility_extreme"),
                    "gapExtreme": regime.get("gap_extreme"),
                    "marketTrendStrength": regime.get("trend_strength"),
                    "marketRegime": regime,
                    "variant": variant_id,
                })
                trades.append(trade)
            # V2's one-setup/day rule applies after the first accepted setup.
            break
    summary = summarize(trades)
    summary["filter_reasons"] = dict(sorted(filter_reasons.items()))
    return trades, summary


def _classification(variant_result):
    oos = [fold["oos"] for fold in variant_result["folds"]]
    eligible = [x for x in oos if x["trades"] >= MIN_FOLD_TRADES]
    positive = sum(x["expectancy_r"] > 0 for x in eligible)
    median_expectancy = statistics.median([x["expectancy_r"] for x in eligible]) if eligible else None
    median_pf = statistics.median([x["profit_factor"] for x in eligible]) if eligible else None
    total_trades = sum(x["trades"] for x in eligible)
    fold_profit = [max(0.0, x["gross_profit_r"] - x["gross_loss_r"]) for x in eligible]
    concentration = max(fold_profit) / sum(fold_profit) if sum(fold_profit) > 0 else 0.0
    result = {
        "positive_oos_folds": positive,
        "eligible_oos_folds": len(eligible),
        "oos_median_expectancy": median_expectancy,
        "oos_median_pf": median_pf,
        "oos_trades": total_trades,
        "largest_positive_fold_share": round(concentration, 5),
    }
    if (len(eligible) >= 3 and positive == len(eligible) and median_expectancy is not None
            and median_expectancy > 0 and median_pf is not None and median_pf >= 1.15
            and total_trades >= MIN_OOS_TRADES_TOTAL and concentration <= 0.70):
        result["classification"] = "PASS"
    elif positive >= 2 and median_expectancy is not None and median_expectancy > 0 and median_pf is not None and median_pf >= 1.05:
        result["classification"] = "WEAK"
    else:
        result["classification"] = "FAIL"
    return result


def evaluate_variant(tqqq_days, market_days, daily, folds, variant_id, slippage_bps):
    result = {"id": variant_id, "description": dict(VARIANTS)[variant_id], "folds": []}
    for fold_no, fold in enumerate(folds, 1):
        train_trades, train = run_variant(tqqq_days, market_days, daily, variant_id, set(fold["train_keys"]), slippage_bps)
        oos_trades, oos = run_variant(tqqq_days, market_days, daily, variant_id, set(fold["test_keys"]), slippage_bps)
        result["folds"].append({"fold": fold_no, "train_range": fold["train_range"], "oos_range": fold["oos_range"],
                                "train": train, "oos": oos,
                                "train_breakdown": report_breakdowns(train_trades),
                                "oos_breakdown": report_breakdowns(oos_trades)})
    result["assessment"] = _classification(result)
    return result


def _compact(result):
    rows = []
    for item in result["variants"]:
        a = item["assessment"]
        rows.append({"variant": item["id"], "classification": a["classification"],
                     "positive_oos_folds": a["positive_oos_folds"],
                     "oos_median_expectancy": a["oos_median_expectancy"],
                     "oos_median_pf": a["oos_median_pf"], "oos_trades": a["oos_trades"]})
    return rows


def run(data_dir, output):
    datasets = load_required_days(data_dir)
    common_days = set(datasets["TQQQ"])
    for symbol in REQUIRED_SYMBOLS[1:]:
        common_days &= set(datasets[symbol])
    if len(common_days) < 30:
        raise RuntimeError(f"필수 5종목의 공통 거래일이 30일 미만입니다: {len(common_days)}일")
    tqqq_days = {day: datasets["TQQQ"][day] for day in sorted(common_days)}
    market_days = {symbol: {day: datasets[symbol][day] for day in common_days} for symbol in REQUIRED_SYMBOLS[1:]}
    daily = {symbol: daily_stats(datasets[symbol]) for symbol in REQUIRED_SYMBOLS[1:]}
    folds = _folds(tqqq_days)
    variants = [evaluate_variant(tqqq_days, market_days, daily, folds, variant_id, SLIPPAGE_BPS) for variant_id, _ in VARIANTS]
    payload = {
        "engine": "backtest_v3_regime",
        "base_strategy": "V2 TQQQ VWAP mean reversion MR009 fixed parameters",
        "data_dir": str(data_dir),
        "required_symbols": list(REQUIRED_SYMBOLS),
        "common_days": len(common_days),
        "slippage_bps_per_side": SLIPPAGE_BPS,
        "execution_assumptions": {
            "entry": "next completed 5-minute bar's next 1-minute open, from V2",
            "stop_target": "stop before target on same candle, from V2",
            "one_setup_per_day": True,
            "target": "VWAP with V2 minimum positive reward fallback",
            "regime_features": "cut at the decision timestamp; daily quartiles exclude current day",
        },
        "regime_thresholds": {
            "trend_distance_atr_ceiling": TREND_DISTANCE_ATR_CEILING,
            "trend_slope_atr_ceiling": TREND_SLOPE_ATR_CEILING,
            "er_ceiling": ER_CEILING,
            "historical_quartile": QUARTILE,
            "minimum_prior_days_for_quartile": MIN_HISTORY_DAYS,
        },
        "folds": [{"fold": i + 1, "train_range": f["train_range"], "oos_range": f["oos_range"]} for i, f in enumerate(folds)],
        "variants": variants,
        "compact_summary": _compact({"variants": variants}),
        "generated_at": time.time(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "toss_1m")
    parser.add_argument("--out", type=Path, default=RESEARCH / "backtest_v3_regime_result.json")
    args = parser.parse_args()
    payload = run(args.data_dir, args.out)
    print("variant | class | positive OOS folds | median expectancy R | median PF | OOS trades")
    for row in payload["compact_summary"]:
        print(f"{row['variant']} | {row['classification']} | {row['positive_oos_folds']} | "
              f"{row['oos_median_expectancy']} | {row['oos_median_pf']} | {row['oos_trades']}")
    print(f"result={args.out}")


if __name__ == "__main__":
    main()
