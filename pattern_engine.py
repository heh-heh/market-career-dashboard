#!/usr/bin/env python3
"""3-minute pattern engine based on the user's supplied trading formulas.

The engine intentionally keeps the supplied formulas explicit. When a formula is
internally inconsistent (for example Pattern 5 including the current bar in Box_High),
both the literal and corrected interpretation can be evaluated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean


def aggregate_3m(candles: list[dict]) -> list[dict]:
    rows = []
    for x in candles or []:
        try:
            t = int(x["t"])
            o = float(x["o"]); h = float(x["h"]); l = float(x["l"])
            c = float(x["c"]); v = float(x.get("v") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((t, o, h, l, c, v))
    rows.sort(key=lambda z: z[0])
    if not rows:
        return []

    # Epoch 3-minute buckets align with US regular-session 09:30 ET boundaries.
    buckets: dict[int, list[tuple]] = {}
    for row in rows:
        bucket = row[0] - (row[0] % 180)
        buckets.setdefault(bucket, []).append(row)

    out = []
    for bucket, group in sorted(buckets.items()):
        if len(group) < 3:
            # Ignore an incomplete 3-minute candle.
            continue
        out.append({
            "t": bucket,
            "time": datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat(),
            "o": group[0][1],
            "h": max(x[2] for x in group),
            "l": min(x[3] for x in group),
            "c": group[-1][4],
            "v": sum(x[5] for x in group),
        })
    return out


def _ma(values: list[float], n: int) -> float | None:
    return mean(values[-n:]) if len(values) >= n else None


def add_indicators(bars: list[dict]) -> list[dict]:
    out = [dict(x) for x in bars]
    for i, b in enumerate(out):
        closes = [x["c"] for x in out[:i + 1]]
        vols = [x["v"] for x in out[:i + 1]]
        b["ma5"] = _ma(closes, 5)
        b["ma10"] = _ma(closes, 10)
        b["ma20"] = _ma(closes, 20)
        b["v_ma5"] = _ma(vols, 5)
    return out


def first_regular_bar(bars: list[dict]) -> dict | None:
    return bars[0] if bars else None


def daily_max_return_from_intraday(bars: list[dict], previous_close: float | None) -> float | None:
    if not bars or not previous_close:
        return None
    return max((b["h"] / previous_close - 1.0) * 100.0 for b in bars)


def common_precondition(bars: list[dict], previous_close: float | None,
                        trading_rank: int | None = None,
                        fx_krw_per_usd: float = 1400.0) -> dict:
    # The source formula requires a daily cumulative trading-value rank <= 150.
    rank_ok = trading_rank is not None and trading_rank <= 150
    intraday_high_return = daily_max_return_from_intraday(bars, previous_close)
    max_return_ok = intraday_high_return is not None and intraday_high_return >= 7.0

    # 3-minute trading value in USD, converted to KRW using a configurable FX assumption.
    value_threshold_usd = 2_000_000_000 / fx_krw_per_usd
    max_3m_value_krw = 0.0
    if bars:
        max_3m_value_krw = max(b["c"] * b["v"] * fx_krw_per_usd for b in bars)

    value_ok = max_3m_value_krw >= 2_000_000_000
    return {
        "rankAvailable": trading_rank is not None,
        "rank": trading_rank,
        "rankOk": rank_ok,
        "intradayMaxReturnPct": intraday_high_return,
        "maxReturnOk": max_return_ok,
        "fxKrwPerUsd": fx_krw_per_usd,
        "threeMinuteValueThresholdUsd": value_threshold_usd,
        "maxThreeMinuteValueKrw": max_3m_value_krw,
        "threeMinuteValueOk": value_ok,
        "fullyEvaluable": trading_rank is not None,
        "fullyPasses": rank_ok and value_ok and max_return_ok,
    }


def detect_pattern_1(bars: list[dict], i: int) -> dict | None:
    if i < 5:
        return None
    cur, p = bars[i], bars[i - 1]
    p_ma5 = p["ma5"]; p_vma5 = p["v_ma5"]
    vma5 = cur["v_ma5"]
    if p_ma5 is None or p_vma5 is None or vma5 is None:
        return None
    cond = (
        p["c"] < p["o"] and
        p["c"] > p_ma5 and
        p["v"] < p_vma5 and
        cur["c"] > cur["o"] and
        cur["c"] > p["h"] and
        cur["v"] > vma5 * 1.5
    )
    if not cond:
        return None
    return {"pattern":"P1_상한선","index":i,"referenceMid":(cur["o"] + cur["c"]) / 2.0,
            "reason":"직전 음봉이 MA5 위에서 거래량 감소 + 현재 고점 돌파 + V>V_MA5*1.5"}


def detect_pattern_2(bars: list[dict], i: int) -> dict | None:
    if i < 10:
        return None
    cur, p = bars[i], bars[i - 1]
    if p["ma5"] is None or p["ma10"] is None:
        return None
    vma5 = cur["v_ma5"]
    if vma5 is None:
        return None
    cond = (
        p["c"] < p["ma5"] and
        p["c"] > p["ma10"] and
        cur["c"] > cur["o"] and
        cur["c"] > cur["ma5"] and
        cur["v"] > p["v"] * 2.0
    )
    if not cond:
        return None
    return {"pattern":"P2_5MA턴어라운드","index":i,"referenceMid":(cur["o"] + cur["c"]) / 2.0,
            "reason":"직전 MA5 아래/MA10 위 + 현재 MA5 회복 + 거래량 2배"}


def detect_pattern_3(bars: list[dict], i: int, fixed_band: bool = False) -> dict | None:
    if i < 20:
        return None
    cur, p = bars[i], bars[i - 1]
    m20 = p["ma20"]
    if m20 is None or cur["ma5"] is None or cur["v_ma5"] is None:
        return None
    if fixed_band:
        touch = m20 * 0.995 <= p["l"] <= m20 * 1.005
    else:
        # Literal supplied formula: only the upper bound is present.
        touch = p["l"] <= m20 * 1.005
    cond = (
        min(p["o"], p["c"]) >= m20 and
        touch and
        cur["c"] > cur["o"] and
        cur["c"] > cur["ma5"] and
        cur["v"] > cur["v_ma5"]
    )
    if not cond:
        return None
    return {"pattern":"P3_20MA터치_수정" if fixed_band else "P3_20MA터치_원식",
            "index":i,"referenceMid":(cur["o"] + cur["c"]) / 2.0,
            "reason":"20MA 부근 지지 + 현재 양봉/MA5 회복 + 평균 이상 거래량"}


def _target_price(bars: list[dict]) -> float | None:
    first = first_regular_bar(bars)
    return first["h"] if first else None


def detect_pattern_4(bars: list[dict], i: int, require_prior_breakout: bool = False) -> dict | None:
    if i < 3:
        return None
    cur, p1, p2 = bars[i], bars[i - 1], bars[i - 2]
    target = _target_price(bars)
    if target is None:
        return None
    cond = (
        min(p1["c"], p2["c"]) >= target and
        min(p1["l"], p2["l"]) <= target * 1.003 and
        cur["c"] > cur["o"] and
        cur["v"] > p1["v"] * 1.5
    )
    if require_prior_breakout:
        prior_breakout = any(x["c"] > target for x in bars[:i - 2])
        cond = cond and prior_breakout
    if not cond:
        return None
    return {"pattern":"P4_Pivot_수정" if require_prior_breakout else "P4_Pivot_원식",
            "index":i,"referenceMid":(cur["o"] + cur["c"]) / 2.0,
            "targetPrice":target,
            "reason":"장초반 핵심가격 지지 + 재차 거래량 유입" + (" + 과거 돌파 확인" if require_prior_breakout else "")}


def detect_pattern_5(bars: list[dict], i: int, exclude_current_from_box: bool = True) -> dict | None:
    if i < 10:
        return None
    cur = bars[i]
    hist = bars[i - 10:i] if exclude_current_from_box else bars[i - 9:i + 1]
    if len(hist) < 10:
        return None
    mas = [cur["ma5"], cur["ma10"], cur["ma20"]]
    if any(x is None for x in mas):
        return None
    # Source convergence condition is written for recent 10 bars; require it across the window.
    for b in hist:
        vals = [b["ma5"], b["ma10"], b["ma20"]]
        if any(x is None for x in vals):
            return None
        spread = (max(vals) - min(vals)) / min(vals) if min(vals) else 999
        if spread > 0.008:
            return None

    box_high = max(x["h"] for x in hist)
    max_vol = max(x["v"] for x in hist)
    cond = cur["c"] > box_high and cur["v"] > max_vol * 1.8
    if not cond:
        return None
    return {"pattern":"P5_질질이_수정" if exclude_current_from_box else "P5_질질이_원식",
            "index":i,"referenceMid":(cur["o"] + cur["c"]) / 2.0,
            "boxHigh":box_high,
            "reason":"MA 수렴 + 박스 상단 돌파 + 거래량 1.8배"}


def scan_patterns(bars: list[dict]) -> list[dict]:
    bars = add_indicators(bars)
    signals = []
    for i in range(len(bars)):
        for detector, kwargs in [
            (detect_pattern_1, {}),
            (detect_pattern_2, {}),
            (detect_pattern_3, {"fixed_band": False}),
            (detect_pattern_3, {"fixed_band": True}),
            (detect_pattern_4, {"require_prior_breakout": False}),
            (detect_pattern_4, {"require_prior_breakout": True}),
            (detect_pattern_5, {"exclude_current_from_box": False}),
            (detect_pattern_5, {"exclude_current_from_box": True}),
        ]:
            sig = detector(bars, i, **kwargs) if kwargs else detector(bars, i)
            if sig:
                signals.append(sig)
    return signals


def evaluate_signals(bars: list[dict], signals: list[dict]) -> list[dict]:
    out = []
    for s in signals:
        i = s["index"]
        entry = bars[i]["c"]
        ref_mid = s.get("referenceMid")
        max_forward = min(len(bars) - 1, i + 10)
        row = dict(s)
        row["entryPrice"] = entry
        row["return3Pct"] = ((bars[min(len(bars)-1, i+3)]["c"] / entry) - 1) * 100 if i+3 < len(bars) else None
        row["return5Pct"] = ((bars[min(len(bars)-1, i+5)]["c"] / entry) - 1) * 100 if i+5 < len(bars) else None
        row["return10Pct"] = ((bars[min(len(bars)-1, i+10)]["c"] / entry) - 1) * 100 if i+10 < len(bars) else None
        stop_i = None
        if ref_mid is not None:
            for j in range(i + 1, max_forward + 1):
                if bars[j]["c"] < ref_mid:
                    stop_i = j
                    break
        row["stopWithin10Bars"] = stop_i is not None
        row["stopAfterBars"] = (stop_i - i) if stop_i is not None else None
        out.append(row)
    return out
