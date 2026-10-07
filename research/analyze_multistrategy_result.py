#!/usr/bin/env python3
"""Post-process a completed multi-strategy backtest.

This intentionally does not rerun market simulation. It analyzes the executed
trade ledger already stored in backtest_multistrategy_v1_result.json and adds:
- R-multiple / fixed-risk equity metrics
- score calibration buckets
- conditional threshold analysis
- ticker / strategy / time / exit-reason breakdowns
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

EPS = 1e-12


def f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float(default)


def r_multiple(t):
    risk = f(t.get("riskPct"))
    return f(t.get("returnPct")) / risk if risk > EPS else 0.0


def score_bucket(score):
    s = f(score)
    if s < 60: return "55-59"
    if s < 65: return "60-64"
    if s < 70: return "65-69"
    if s < 75: return "70-74"
    if s < 80: return "75-79"
    return "80+"


def time_bucket(ts):
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        h, m = dt.hour, dt.minute
    except Exception:
        return "unknown"
    minute = h * 60 + m
    if minute < 10*60: return "09:30-09:59"
    if minute < 11*60: return "10:00-10:59"
    if minute < 12*60: return "11:00-11:59"
    if minute < 14*60: return "12:00-13:59"
    if minute < 15*60: return "14:00-14:59"
    return "15:00-15:59"


def summarize(trades, risk_fraction=0.002):
    if not trades:
        return {
            "trades":0,"winRatePct":0,"avgReturnPct":0,"expectancyPct":0,
            "profitFactor":0,"avgR":0,"expectancyR":0,"profitFactorR":0,
            "compoundedReturnPct":0,"maxDrawdownPct":0,
        }

    rets=[f(t.get("returnPct")) for t in trades]
    rs=[r_multiple(t) for t in trades]
    wins=[x for x in rets if x>0]
    losses=[x for x in rets if x<=0]
    rwins=[x for x in rs if x>0]
    rloss=[x for x in rs if x<=0]

    gross_profit=sum(wins)
    gross_loss=abs(sum(losses))
    rgp=sum(rwins)
    rgl=abs(sum(rloss))

    equity=1.0
    peak=1.0
    mdd=0.0
    for r in rs:
        # Fixed fractional risk. Clamp catastrophic data artefacts rather than
        # allowing an impossible negative account value.
        trade_ret=max(-0.99, risk_fraction*r)
        equity *= (1.0 + trade_ret)
        peak=max(peak,equity)
        dd=equity/peak-1.0
        mdd=min(mdd,dd)

    wr=len(wins)/len(rets)
    return {
        "trades":len(trades),
        "winRatePct":round(100*wr,2),
        "avgReturnPct":round(statistics.fmean(rets),5),
        "avgWinPct":round(statistics.fmean(wins),5) if wins else 0,
        "avgLossPct":round(abs(statistics.fmean(losses)),5) if losses else 0,
        "expectancyPct":round(statistics.fmean(rets),5),
        "profitFactor":round(gross_profit/gross_loss,4) if gross_loss>EPS else (999.0 if gross_profit>0 else 0),
        "avgR":round(statistics.fmean(rs),5),
        "expectancyR":round(statistics.fmean(rs),5),
        "avgWinR":round(statistics.fmean(rwins),5) if rwins else 0,
        "avgLossR":round(abs(statistics.fmean(rloss)),5) if rloss else 0,
        "profitFactorR":round(rgp/rgl,4) if rgl>EPS else (999.0 if rgp>0 else 0),
        "compoundedReturnPct":round((equity-1.0)*100,3),
        "maxDrawdownPct":round(mdd*100,3),
    }


def group_summary(trades, key_fn, risk_fraction=0.002, min_trades=1):
    g=defaultdict(list)
    for t in trades:
        g[str(key_fn(t))].append(t)
    out={}
    for k,v in sorted(g.items()):
        if len(v)>=min_trades:
            out[k]=summarize(v,risk_fraction)
    return out


def analyze(result, risk_fraction=0.002):
    trades=list(result.get("trades") or [])
    thresholds=[55,60,65,70,75,80,85]
    threshold={}
    for th in thresholds:
        subset=[t for t in trades if f(t.get("finalScore"))>=th]
        threshold[str(th)]=summarize(subset,risk_fraction)

    score_cal=group_summary(trades,lambda t:score_bucket(t.get("finalScore")),risk_fraction)
    by_strategy=group_summary(trades,lambda t:t.get("strategy") or "UNKNOWN",risk_fraction)
    by_ticker=group_summary(trades,lambda t:t.get("symbol") or "UNKNOWN",risk_fraction)
    by_combo=group_summary(
        trades,lambda t:f"{t.get('symbol','?')}::{t.get('strategy','?')}",
        risk_fraction,min_trades=10
    )
    by_time=group_summary(trades,lambda t:time_bucket(t.get("entryTime")),risk_fraction)
    by_exit=group_summary(trades,lambda t:t.get("exitReason") or "unknown",risk_fraction)

    # Rank only combinations with enough observations.
    combos=[(k,v) for k,v in by_combo.items() if v["trades"]>=20]
    combos.sort(key=lambda kv:(kv[1]["expectancyR"],kv[1]["profitFactorR"],kv[1]["trades"]),reverse=True)

    score_pairs=[(score_bucket(t.get("finalScore")),r_multiple(t)) for t in trades]
    bucket_order=["55-59","60-64","65-69","70-74","75-79","80+"]
    calibration=[]
    for b in bucket_order:
        vals=[r for bb,r in score_pairs if bb==b]
        if vals:
            calibration.append({"bucket":b,"trades":len(vals),"avgR":round(statistics.fmean(vals),5)})

    # Is score actually calibrated? Spearman-like rank correlation via Pearson
    # on ranks is overkill here; simple Pearson on score vs R is transparent.
    xs=[f(t.get("finalScore")) for t in trades]
    ys=[r_multiple(t) for t in trades]
    corr=0.0
    if len(xs)>1 and statistics.pstdev(xs)>EPS and statistics.pstdev(ys)>EPS:
        xm,ym=statistics.fmean(xs),statistics.fmean(ys)
        cov=statistics.fmean([(x-xm)*(y-ym) for x,y in zip(xs,ys)])
        corr=cov/(statistics.pstdev(xs)*statistics.pstdev(ys))

    return {
        "engine":result.get("engine"),
        "sourceMinFinalScore":result.get("minFinalScore"),
        "riskModel":{"riskFractionPerTrade":risk_fraction,"description":"fixed fractional risk × R-multiple"},
        "overall":summarize(trades,risk_fraction),
        "scoreCorrelationWithR":round(corr,5),
        "scoreCalibration":calibration,
        "conditionalThresholds":threshold,
        "byStrategy":by_strategy,
        "byTicker":by_ticker,
        "byTickerStrategy":by_combo,
        "bestTickerStrategy":dict(combos[:12]),
        "worstTickerStrategy":dict(reversed(combos[-12:])),
        "byEntryTime":by_time,
        "byExitReason":by_exit,
        "notes":[
            "conditionalThresholds filters the already-executed trade ledger; it is diagnostic, not a full rerun at each threshold.",
            "maxDrawdownPct uses a compounded fixed-fractional equity curve rather than summing raw trade percentages.",
        ],
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",default="/var/lib/market-career-dashboard/backtest_multistrategy_v1_result.json")
    ap.add_argument("--out",default="/var/lib/market-career-dashboard/backtest_multistrategy_v1_analysis.json")
    ap.add_argument("--risk-fraction",type=float,default=0.002)
    args=ap.parse_args()

    result=json.loads(Path(args.input).read_text(encoding="utf-8"))
    out=analyze(result,args.risk_fraction)
    p=Path(args.out)
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "out":str(p),
        "overall":out["overall"],
        "scoreCorrelationWithR":out["scoreCorrelationWithR"],
        "conditionalThresholds":out["conditionalThresholds"],
    },ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
