#!/usr/bin/env python3
"""Cross-sectional scoring and multi-strategy intraday engine.

Broker-agnostic: receives Toss 1-minute candle rows and returns normalized
features plus four strategy scores. Account safety and order placement remain
in live_trader.py.
"""
from __future__ import annotations

import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
EPS = 1e-9

SEMICONDUCTOR = {
    "NVDA", "AMD", "MU", "INTC", "AVGO", "MRVL", "AMAT", "LRCX", "KLAC",
    "TSM", "QCOM", "SOXX", "SMH", "SOXL", "SOXS",
}


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def scale(x, lo, hi):
    if hi <= lo:
        return 0.0
    return clamp((float(x) - lo) / (hi - lo))


def percentile_ranks(values):
    n = len(values)
    if n <= 1:
        return [0.5] * n
    indexed = sorted(enumerate(values), key=lambda t: t[1])
    out = [0.0] * n
    for rank, (idx, _value) in enumerate(indexed):
        out[idx] = rank / (n - 1)
    return out


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_ts(row):
    raw = row.get("timestamp") or row.get("dateTime") or row.get("datetime") or row.get("time")
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        try:
            value = float(raw)
            return datetime.fromtimestamp(value / (1000.0 if value > 1e11 else 1.0), tz=NY)
        except Exception:
            return None
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY)
        return dt.astimezone(NY)
    except Exception:
        return None


def normalize_toss_rows(rows):
    """Convert Toss newest-first candles into chronological 1-minute bars."""
    out = []
    for row in reversed(list(rows or [])):
        try:
            o = _f(row.get("openPrice", row.get("open")))
            h = _f(row.get("highPrice", row.get("high")))
            l = _f(row.get("lowPrice", row.get("low")))
            c = _f(row.get("closePrice", row.get("close")))
            v = _f(row.get("volume"))
            if min(o, h, l, c) <= 0:
                continue
            out.append({"ts": _parse_ts(row), "o": o, "h": h, "l": l, "c": c, "v": max(0.0, v)})
        except Exception:
            continue
    return out


def aggregate(bars, minutes=5):
    if not bars:
        return []
    if all(b.get("ts") is not None for b in bars):
        groups, cur_key, cur = [], None, None
        for b in bars:
            dt = b["ts"]
            minute = (dt.minute // minutes) * minutes
            key = (dt.year, dt.month, dt.day, dt.hour, minute)
            if key != cur_key:
                if cur:
                    groups.append(cur)
                cur_key = key
                cur = dict(b)
            else:
                cur["h"] = max(cur["h"], b["h"])
                cur["l"] = min(cur["l"], b["l"])
                cur["c"] = b["c"]
                cur["v"] += b["v"]
                cur["ts"] = b["ts"]
        if cur:
            groups.append(cur)
        return groups

    out = []
    for i in range(0, len(bars) - minutes + 1, minutes):
        chunk = bars[i:i + minutes]
        if len(chunk) < minutes:
            continue
        out.append({
            "ts": chunk[-1].get("ts"),
            "o": chunk[0]["o"],
            "h": max(x["h"] for x in chunk),
            "l": min(x["l"] for x in chunk),
            "c": chunk[-1]["c"],
            "v": sum(x["v"] for x in chunk),
        })
    return out


def _same_regular_session(bars):
    stamped = [b for b in bars if b.get("ts") is not None]
    if not stamped:
        return bars
    last_date = stamped[-1]["ts"].date()
    regular = [
        b for b in stamped
        if b["ts"].date() == last_date
        and ((b["ts"].hour == 9 and b["ts"].minute >= 30) or 10 <= b["ts"].hour < 16)
    ]
    return regular if len(regular) >= 10 else [b for b in stamped if b["ts"].date() == last_date]


def ema_series(values, period):
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for x in values[1:]:
        out.append(alpha * float(x) + (1.0 - alpha) * out[-1])
    return out


def true_ranges(bars):
    out = []
    for i, b in enumerate(bars):
        prev = bars[i - 1]["c"] if i else b["c"]
        out.append(max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev)))
    return out


def atr(bars, period=14):
    tr = true_ranges(bars)
    if len(tr) < period:
        return statistics.fmean(tr) if tr else 0.0
    return statistics.fmean(tr[-period:])


def rsi(closes, period=14):
    if len(closes) < 2:
        return 50.0
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))][-period:]
    gain = statistics.fmean([max(x, 0.0) for x in diffs])
    loss = statistics.fmean([max(-x, 0.0) for x in diffs])
    if loss <= EPS:
        return 100.0 if gain > 0 else 50.0
    rs = gain / loss
    return 100.0 - 100.0 / (1.0 + rs)


def adx_di(bars, period=14):
    if len(bars) < period + 2:
        return 0.0, 0.0, 0.0
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(bars)):
        up = bars[i]["h"] - bars[i - 1]["h"]
        down = bars[i - 1]["l"] - bars[i]["l"]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        prev = bars[i - 1]["c"]
        trs.append(max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - prev), abs(bars[i]["l"] - prev)))
    start = max(0, len(trs) - period)
    trn = sum(trs[start:])
    if trn <= EPS:
        return 0.0, 0.0, 0.0
    pdi = 100.0 * sum(plus_dm[start:]) / trn
    mdi = 100.0 * sum(minus_dm[start:]) / trn
    dx = 100.0 * abs(pdi - mdi) / max(pdi + mdi, EPS)
    return dx, pdi, mdi


def efficiency_ratio(closes, period=12):
    if len(closes) <= period:
        return 0.0
    net = abs(closes[-1] - closes[-1 - period])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - period, len(closes)))
    return net / max(path, EPS)


def vwap_series(bars):
    out, pv, vv = [], 0.0, 0.0
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        pv += tp * b["v"]
        vv += b["v"]
        out.append(pv / max(vv, EPS))
    return out


def cross_count(values, refs, lookback=12):
    start = max(1, len(values) - lookback)
    count = 0
    prev = 1 if values[start - 1] >= refs[start - 1] else -1
    for i in range(start, len(values)):
        cur = 1 if values[i] >= refs[i] else -1
        if cur != prev:
            count += 1
        prev = cur
    return count


def _zscore(series, period=20):
    if len(series) < 2:
        return 0.0
    window = series[-period:]
    mean = statistics.fmean(window)
    std = statistics.pstdev(window) if len(window) > 1 else 0.0
    return (series[-1] - mean) / max(std, EPS)


def extract_features(rows):
    one = normalize_toss_rows(rows)
    five = _same_regular_session(aggregate(one, 5))
    if len(five) < 22:
        return {"ready": False, "reason": "5분봉 데이터 부족", "bars": len(five)}

    closes = [b["c"] for b in five]
    vols = [b["v"] for b in five]
    ema9s = ema_series(closes, 9)
    ema21s = ema_series(closes, 21)
    atr14 = atr(five, 14)
    adx14, di_plus, di_minus = adx_di(five, 14)
    vwaps = vwap_series(five)
    er12 = efficiency_ratio(closes, 12)
    base_vols = vols[-21:-1] if len(vols) >= 21 else vols[:-1]
    rv_base = statistics.median(base_vols) if base_vols else max(vols[-1], 1.0)
    rvol = vols[-1] / max(rv_base, EPS)
    vwap_slope = (vwaps[-1] - vwaps[-4]) / max(atr14, EPS) if len(vwaps) >= 4 else 0.0
    ema_strength = (ema9s[-1] - ema21s[-1]) / max(atr14, EPS)
    deviations = [closes[i] - vwaps[i] for i in range(len(closes))]
    z = _zscore(deviations, 20)
    z_prev = _zscore(deviations[:-1], 20) if len(deviations) > 2 else 0.0
    day_high = max(b["h"] for b in five)
    day_low = min(b["l"] for b in five)
    now_ts = five[-1].get("ts")

    return {
        "ready": True,
        "bars5m": len(five),
        "price": closes[-1],
        "prevClose": closes[-2],
        "prevHigh": five[-2]["h"],
        "prevLow": five[-2]["l"],
        "sessionOpen": five[0]["o"],
        "dayHigh": day_high,
        "dayLow": day_low,
        "ema9": ema9s[-1],
        "ema21": ema21s[-1],
        "emaStrength": ema_strength,
        "atr14": atr14,
        "atrPct": atr14 / max(closes[-1], EPS),
        "rsi14": rsi(closes, 14),
        "adx14": adx14,
        "diPlus": di_plus,
        "diMinus": di_minus,
        "vwap": vwaps[-1],
        "vwapSlope": vwap_slope,
        "vwapCrosses12": cross_count(closes, vwaps, 12),
        "er12": er12,
        "rvol": rvol,
        "zVwap": z,
        "zVwapPrev": z_prev,
        "volume5m": vols[-1],
        "volumeMedian20": rv_base,
        "distanceVwapAtr": abs(closes[-1] - vwaps[-1]) / max(atr14, EPS),
        "distanceEma21Atr": abs(closes[-1] - ema21s[-1]) / max(atr14, EPS),
        "aboveVwap": closes[-1] >= vwaps[-1],
        "timestamp": now_ts.isoformat() if now_ts else None,
        "_bars": five,
        "_closes": closes,
        "_vwaps": vwaps,
    }


def _orb(f):
    bars, a, close = f["_bars"], max(f["atr14"], EPS), f["price"]
    if len(bars) < 6:
        return {"score": 0.0, "direction": "NONE", "active": False, "reason": ["opening range unavailable"]}
    opening = bars[:3]
    orh, orl = max(b["h"] for b in opening), min(b["l"] for b in opening)
    recent = bars[max(3, len(bars) - 6):]
    long_broke = any(b["c"] > orh + 0.05 * a for b in bars[3:])
    short_broke = any(b["c"] < orl - 0.05 * a for b in bars[3:])
    lb, sb = scale((close-orh)/a, 0, .6), scale((orl-close)/a, 0, .6)
    lr = 1.0 if long_broke and any(b["l"] <= orh + .20*a and b["c"] > orh for b in recent) else 0.0
    sr = 1.0 if short_broke and any(b["h"] >= orl - .20*a and b["c"] < orl for b in recent) else 0.0
    rvq = scale(f["rvol"], .8, 2.2)
    lv, sv = scale((close-f["vwap"])/a, 0, .8), scale((f["vwap"]-close)/a, 0, .8)
    ld = clamp(.55*scale(f["emaStrength"],0,1)+.45*scale(f["diPlus"]-f["diMinus"],0,25))
    sd = clamp(.55*scale(-f["emaStrength"],0,1)+.45*scale(f["diMinus"]-f["diPlus"],0,25))
    ls = 25*lb + 25*lr + 20*rvq + 15*lv + 15*ld
    ss = 25*sb + 25*sr + 20*rvq + 15*sv + 15*sd
    if ls >= ss:
        return {"score":round(ls,2),"direction":"LONG","active":bool(lr),"stopPrice":min(f["prevLow"],orh-.15*a),
                "reason":[f"ORH={orh:.4f}",f"break={lb:.2f}",f"retest={lr:.0f}",f"rvol={f['rvol']:.2f}"],"orh":orh,"orl":orl}
    return {"score":round(ss,2),"direction":"SHORT","active":bool(sr),"stopPrice":max(f["prevHigh"],orl+.15*a),
            "reason":[f"ORL={orl:.4f}",f"break={sb:.2f}",f"retest={sr:.0f}",f"rvol={f['rvol']:.2f}"],"orh":orh,"orl":orl}


def _trend(f):
    a, close = max(f["atr14"], EPS), f["price"]
    long = f["emaStrength"] >= 0
    erq = scale(f["er12"], .20, .65)
    emaq = scale(abs(f["emaStrength"]), .05, 1.0)
    slopeq = scale(abs(f["vwapSlope"]), .02, .55)
    pull = min(f["distanceVwapAtr"], f["distanceEma21Atr"])
    pullq = 1.0 - scale(pull, .25, 1.20)
    if long:
        reclaim = .5*(close > f["ema9"]) + .5*(close > f["prevHigh"])
        aligned = f["vwapSlope"] > 0 and f["ema9"] > f["ema21"]
        stop = min(f["prevLow"], close-a)
        direction = "LONG"
    else:
        reclaim = .5*(close < f["ema9"]) + .5*(close < f["prevLow"])
        aligned = f["vwapSlope"] < 0 and f["ema9"] < f["ema21"]
        stop = max(f["prevHigh"], close+a)
        direction = "SHORT"
    score = 25*erq + 20*emaq + 20*slopeq + 20*pullq + 15*reclaim
    if not aligned:
        score *= .72
    ext = abs(close-f["vwap"])/a
    if ext > 2:
        score *= max(.35,1-.25*(ext-2))
    return {"score":round(clamp(score/100)*100,2),"direction":direction,"active":bool(aligned and pull<=.70),
            "stopPrice":stop,"reason":[f"ER={f['er12']:.2f}",f"ema={f['emaStrength']:.2f}ATR",
            f"vwapSlope={f['vwapSlope']:.2f}",f"pullback={pull:.2f}ATR"]}


def _mean_reversion(f):
    close, z, zp = f["price"], f["zVwap"], f["zVwapPrev"]
    lr = zp < -1.2 and z > zp and close > f["prevClose"]
    sr = zp > 1.2 and z < zp and close < f["prevClose"]
    if lr:
        direction, rev = "LONG", 1.0
    elif sr:
        direction, rev = "SHORT", 1.0
    else:
        direction, rev = ("LONG" if z < 0 else "SHORT"), 0.0
    low_er = 1-scale(f["er12"],.20,.60)
    crossq = scale(f["vwapCrosses12"],1,5)
    flat = 1-scale(abs(f["vwapSlope"]),.05,.45)
    low_trend = 1-scale(f["adx14"],15,30)
    extreme = scale(abs(z),.8,2.5)
    score = 25*low_er + 20*crossq + 20*flat + 15*low_trend + 10*rev + 10*extreme
    if f["rvol"] >= 2.5:
        score *= .72
    stop = close-f["atr14"] if direction=="LONG" else close+f["atr14"]
    return {"score":round(clamp(score/100)*100,2),"direction":direction,"active":bool(rev and low_er>=.35),
            "stopPrice":stop,"targetPrice":f["vwap"],"reason":[f"Z={z:.2f}",f"ER={f['er12']:.2f}",
            f"crosses={f['vwapCrosses12']}",f"ADX={f['adx14']:.1f}"]}


def _close_momentum(f):
    bars = f["_bars"]
    if len(bars) < 8:
        return {"score":0.0,"direction":"NONE","active":False,"reason":["insufficient opening bars"]}
    open_ret = bars[min(6,len(bars)-1)-1]["c"]/max(bars[0]["o"],EPS)-1
    strength = scale(abs(open_ret)/max(f["atrPct"],.001),.35,2.0)
    direction = "LONG" if open_ret >= 0 else "SHORT"
    rvq = scale(f["rvol"],.8,2.0)
    if direction=="LONG":
        vq = scale((f["price"]-f["vwap"])/max(f["atr14"],EPS),0,1)
        tq = clamp(.5*scale(f["emaStrength"],0,1)+.5*scale(f["er12"],.2,.65))
        persist = sum(1 for b,v in zip(bars[-8:],f["_vwaps"][-8:]) if b["c"]>=v)/max(1,min(8,len(bars)))
        stop = f["price"]-f["atr14"]
    else:
        vq = scale((f["vwap"]-f["price"])/max(f["atr14"],EPS),0,1)
        tq = clamp(.5*scale(-f["emaStrength"],0,1)+.5*scale(f["er12"],.2,.65))
        persist = sum(1 for b,v in zip(bars[-8:],f["_vwaps"][-8:]) if b["c"]<=v)/max(1,min(8,len(bars)))
        stop = f["price"]+f["atr14"]
    score = 30*strength + 20*rvq + 20*vq + 15*tq + 15*persist
    ts = bars[-1].get("ts")
    active = bool(ts is not None and ts.hour==15 and 15<=ts.minute<=45)
    return {"score":round(clamp(score/100)*100,2),"direction":direction,"active":active,"stopPrice":stop,
            "reason":[f"openRet={open_ret*100:.2f}%",f"persist={persist:.2f}",f"rvol={f['rvol']:.2f}"]}


def evaluate_rows(rows):
    f = extract_features(rows)
    if not f.get("ready"):
        return {"ready":False,"signal":"HOLD","reason":f.get("reason","insufficient data"),
                "price":f.get("price"),"strategyScores":{},"patternScore":0}
    strategies = {
        "ORB_RETEST":_orb(f),
        "VWAP_TREND_PULLBACK":_trend(f),
        "VWAP_MEAN_REVERSION":_mean_reversion(f),
        "CLOSE_MOMENTUM":_close_momentum(f),
    }
    best_name,best=max(strategies.items(),key=lambda kv:kv[1]["score"])
    public={k:v for k,v in f.items() if not k.startswith("_")}
    ma5=statistics.fmean(f["_closes"][-5:])
    return {
        "ready":True,"price":f["price"],"signal":"HOLD",
        "reason":f"best={best_name} score={best['score']:.1f}",
        "bestStrategy":best_name,"bestStrategyScore":best["score"],"bestDirection":best["direction"],
        "stopPrice":best.get("stopPrice"),"targetPrice":best.get("targetPrice"),
        "strategyScores":strategies,"features":public,
        "ma5":round(ma5,4),"ma10":round(statistics.fmean(f["_closes"][-10:]),4),
        "ma20":round(statistics.fmean(f["_closes"][-20:]),4),
        "rsi14":round(f["rsi14"],2),"rvol":round(f["rvol"],2),"atr14":round(f["atr14"],4),
        "patternScore":round(best["score"],2),"patterns":[best_name],
        "referenceMid":best.get("stopPrice"),"riskR":abs(f["price"]-best.get("stopPrice",f["price"])),
        "targetPct":None,"volume3m":f["volume5m"],
        "dayHighPct":round((f["dayHigh"]/max(f["sessionOpen"],EPS)-1)*100,3),
        "dayLowRecoveryPct":round((f["price"]/max(f["dayLow"],EPS)-1)*100,3),
        "dollarValue3m":round(f["price"]*f["volume5m"],2),
        "ma5GapAtr":round(abs(f["price"]-ma5)/max(f["atr14"],EPS),3),
    }


def _strategy_stock_score(name,row):
    f=row.get("features") or {}
    c=row.get("_stock_components") or {}
    if name=="ORB_RETEST":
        s=.35*c.get("rvol",.5)+.15*c.get("gap",.5)+.15*c.get("atr",.5)+.15*c.get("open_volume",.5)+.10*c.get("liquidity",.5)+.10*c.get("dollar_volume",.5)
    elif name=="VWAP_TREND_PULLBACK":
        s=.25*scale(f.get("er12",0),.2,.65)+.20*scale(abs(f.get("vwapSlope",0)),.02,.55)+.15*scale(abs(f.get("emaStrength",0)),.05,1)+.15*c.get("rvol",.5)+.10*c.get("atr",.5)+.10*row.get("_sector_alignment_raw",.5)+.05*c.get("liquidity",.5)
    elif name=="VWAP_MEAN_REVERSION":
        s=.25*(1-scale(f.get("er12",0),.2,.6))+.20*scale(f.get("vwapCrosses12",0),1,5)+.15*scale(abs(f.get("zVwap",0)),.8,2.5)+.15*c.get("liquidity",.5)+.10*(1-c.get("rvol",.5))+.15*(1-c.get("gap",.5))
    else:
        s=.30*scale(abs((f.get("price",0)/max(f.get("sessionOpen",f.get("price",1)),EPS))-1)/max(f.get("atrPct",.001),.001),.3,2)+.20*c.get("rvol",.5)+.20*scale(f.get("er12",0),.2,.65)+.15*(1 if f.get("aboveVwap") else 0)+.10*row.get("_sector_alignment_raw",.5)+.05*c.get("liquidity",.5)
    return round(100*clamp(s),2)


def score_universe(rows):
    if not rows:
        return []
    fields={
        "rvol":[_f((r.get("features") or {}).get("rvol")) for r in rows],
        "gap":[abs(_f(r.get("changePct"))) for r in rows],
        "atr":[_f((r.get("features") or {}).get("atrPct")) for r in rows],
        "dollar_volume":[_f(r.get("tradingAmountUsd")) for r in rows],
        "open_volume":[_f((r.get("features") or {}).get("volume5m")) for r in rows],
        "liquidity":[_f(r.get("tradingAmountUsd")) for r in rows],
    }
    ranks={k:percentile_ranks(v) for k,v in fields.items()}
    semi=[r for r in rows if str(r.get("symbol") or "").upper() in SEMICONDUCTOR]
    breadth=(sum(1 for r in semi if (r.get("features") or {}).get("aboveVwap"))/len(semi)) if semi else .5
    for i,row in enumerate(rows):
        c={k:ranks[k][i] for k in ranks}
        row["_stock_components"]=c
        stock=100*(.30*c["rvol"]+.20*c["gap"]+.15*c["atr"]+.15*c["dollar_volume"]+.10*c["open_volume"]+.10*c["liquidity"])
        row["stockScore"]=round(stock,2)
        symbol=str(row.get("symbol") or "").upper()
        raw_align=breadth if symbol in SEMICONDUCTOR else .5
        row["_sector_alignment_raw"]=raw_align
        row["sectorBreadth"]=round(breadth,3) if symbol in SEMICONDUCTOR else None
        opp=[]
        for name,res in (row.get("strategyScores") or {}).items():
            direction=res.get("direction","NONE")
            market=(breadth if direction=="LONG" else 1-breadth) if symbol in SEMICONDUCTOR else .5
            ss=_strategy_stock_score(name,row)
            final=.30*stock+.50*_f(res.get("score"))+.10*ss+.10*(100*market)
            opp.append({"strategy":name,"direction":direction,"strategyScore":round(_f(res.get("score")),2),
                        "strategyStockScore":ss,"marketAlignment":round(100*market,2),"finalScore":round(final,2),
                        "active":bool(res.get("active")),"stopPrice":res.get("stopPrice"),
                        "targetPrice":res.get("targetPrice"),"reason":res.get("reason") or []})
        opp.sort(key=lambda x:(x["active"],x["finalScore"]),reverse=True)
        row["opportunities"]=opp
        if opp:
            best=opp[0]
            row.update(bestStrategy=best["strategy"],bestDirection=best["direction"],
                       bestStrategyScore=best["strategyScore"],finalScore=best["finalScore"],
                       stopPrice=best.get("stopPrice"),targetPrice=best.get("targetPrice"),
                       strategyActive=best.get("active"))
        else:
            row["finalScore"]=0.0
            row["strategyActive"]=False
        row.pop("_stock_components",None)
        row.pop("_sector_alignment_raw",None)
    return sorted(rows,key=lambda r:_f(r.get("finalScore")),reverse=True)


def apply_trade_decision(row,min_score=55.0):
    score=_f(row.get("finalScore"))
    direction=str(row.get("bestDirection") or "NONE")
    active=bool(row.get("strategyActive"))
    tradable=active and direction=="LONG" and score>=float(min_score)
    risk_scale=1.0 if score>=75 else .67 if score>=65 else .33 if score>=55 else 0.0
    row["signal"]="BUY" if tradable else "HOLD"
    row["riskScale"]=risk_scale if tradable else 0.0
    if tradable:
        entry=_f(row.get("price"))
        stop=_f(row.get("stopPrice"),entry)
        risk=max(0.0,entry-stop)
        tp=_f(row.get("targetPrice")) if row.get("targetPrice") is not None else 0.0
        target=tp if tp>entry else entry+1.5*risk if risk>0 else entry*1.01
        row["targetPct"]=max(.2,min(3.0,(target/max(entry,EPS)-1)*100))
        row["referenceMid"]=stop if stop>0 else None
        row["reason"]=f"{row.get('bestStrategy')} BUY final={score:.1f}"
    else:
        why="inactive" if not active else "short-disabled" if direction=="SHORT" else "score"
        row["reason"]=f"{row.get('bestStrategy')} HOLD final={score:.1f} {why}"
    return row
