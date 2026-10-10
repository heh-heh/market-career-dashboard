"""Descriptive metrics; no optimizer or profitability classification."""
import statistics as st
from collections import defaultdict
from datetime import datetime,timedelta


def summarize(trades,initial_cash=10000):
    rets = [t["returnPct"] for t in trades]
    wins,losses = [r for r in rets if r > 0],[r for r in rets if r < 0]
    rvalues = [t["netR"] for t in trades]
    positive,negative = sum(r for r in rvalues if r > 0),-sum(r for r in rvalues if r < 0)
    gp = sum(t["pnlUsd"] for t in trades if t["pnlUsd"] > 0)
    gl = -sum(t["pnlUsd"] for t in trades if t["pnlUsd"] < 0)
    by_exit = defaultdict(lambda: [0.0,0.0])
    for t in trades:
        by_exit[t["exitTimestamp"]][0] += t["pnlUsd"]
        by_exit[t["exitTimestamp"]][1] += t["netR"]
    equity = peak = initial_cash
    curve = peakr = mdd = mddr = 0.0
    for ts in sorted(by_exit):
        usd,r = by_exit[ts]
        equity += usd; peak = max(peak,equity)
        curve += r; peakr = max(peakr,curve)
        mdd = max(mdd,100*(peak-equity)/peak)
        mddr = max(mddr,peakr-curve)
    def avg(xs): return st.fmean(xs) if xs else None
    def field_avg(k): return avg([t[k] for t in trades if t.get(k) is not None])
    aw,al = avg(wins),avg([-v for v in losses])
    return dict(trades=len(trades),wins=len(wins),losses=len(losses),breakeven=len(rets)-len(wins)-len(losses),
                winRatePct=100*len(wins)/len(rets) if rets else None,sumTradeReturnPct=sum(rets),
                expectancyPct=avg(rets),medianReturnPct=st.median(rets) if rets else None,
                avgWinPct=aw,avgLossPct=al,payoffRatio=aw/al if aw is not None and al else None,
                profitFactor=gp/gl if gl else None,profitFactorInfinite=gp>0 and gl==0,
                profitFactorR=positive/negative if negative else None,profitFactorRInfinite=positive>0 and negative==0,
                expectancyR=avg(rvalues),maxDrawdownR=mddr,maxDrawdownPct=mdd,pnlUsd=gp-gl,
                averageWinR=avg([x for x in rvalues if x>0]),averageLossR=avg([-x for x in rvalues if x<0]),
                paperAccountReturnPct=100*(gp-gl)/initial_cash,averageMFE_R=field_avg("MFE_R"),
                averageMAE_R=field_avg("MAE_R"),averageMFEPct=field_avg("mfePct"),averageMAEPct=field_avg("maePct"),
                averageHoldSeconds=field_avg("holdDurationSeconds"),
                medianHoldSeconds=st.median([t["holdDurationSeconds"] for t in trades]) if trades else None,
                entryToMFESeconds=field_avg("entryToMFESeconds"),entryToMAESeconds=field_avg("entryToMAESeconds"),
                affectedTrades=sum(t.get("exitAffectedByDataStale",False) for t in trades))


def daily_consistency(trades,days,initial_cash=10000):
    pnl=defaultdict(float)
    for t in trades:pnl[t["exitTimestamp"][:10]]+=t["pnlUsd"]
    values=[pnl[d]/initial_cash for d in days]
    if len(values)<2 or st.stdev(values)==0:return None
    return st.fmean(values)/st.stdev(values)*252**.5


def grouped(trades,key,initial_cash=10000):
    groups = defaultdict(list)
    for t in trades: groups[str(key(t))].append(t)
    return {k:summarize(v,initial_cash) for k,v in sorted(groups.items())}


def time_bucket(t):
    hm = t["entryTimestamp"][11:16]
    for begin,end in (("09:30","10:00"),("10:00","11:00"),("11:00","12:00"),
                      ("12:00","14:00"),("14:00","15:00"),("15:00","16:00")):
        if begin <= hm < end: return begin+"–"+end
    return "OUTSIDE_RTH"


def add_months(value,n):
    y,m = divmod(value.year*12+value.month-1+n,12)
    return value.replace(year=y,month=m+1,day=1)


def chronological_folds(trades,days,initial_cash=10000):
    if not days: return dict(status="NOT_EVALUABLE",folds=[])
    start = datetime.fromisoformat(days[0]).replace(day=1)
    folds = []
    while True:
        a,b,c,d = [add_months(start,n).date().isoformat() for n in (0,12,15,18)]
        if d > days[-1]:
            # Fold end is exclusive. The next month's first date need not be
            # present in the data (nor be a trading day) to cover the fold.
            end=datetime.fromisoformat(d)
            if (end-timedelta(days=10)).date().isoformat()>days[-1]: break
            try:
                from .backtest_v4_strategies import exchange_sessions
            except ImportError:
                from backtest_v4_strategies import exchange_sessions
            last_required=max(exchange_sessions((end-timedelta(days=10)).date().isoformat(),
                                               (end-timedelta(days=1)).date().isoformat()),default=None)
            if last_required is None or last_required>days[-1]: break
        embargo = max((x for x in days if x < c),default=None)
        select = lambda lo,hi: [t for t in trades if lo <= t["date"] < hi and t["date"] != embargo and lo <= t["exitTimestamp"][:10] < hi]
        folds.append(dict(trainPeriod=[a,b],validationPeriod=[b,c],oosPeriod=[c,d],embargoSession=embargo,
                          train=summarize(select(a,b),initial_cash),validation=summarize(select(b,c),initial_cash),oos=summarize(select(c,d),initial_cash)))
        start = add_months(start,3)
    return dict(status="DESCRIPTIVE_FROZEN" if len(folds)>=3 else "NOT_EVALUABLE",minimumOOSFolds=3,
                parametersSelected=False,folds=folds)
