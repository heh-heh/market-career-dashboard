"""IR_SPEC_V1 causal primitives. Research only; no market/broker imports.

Raw labels are START, availability is the exclusive END. Histories are advanced
only after a session finishes. All price execution uses subsequent minute closes.
"""
from __future__ import annotations

import math
import statistics as st
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

try:
    from .backtest_v4_strategies import Bar, MINUTE, NY
except ImportError:
    from backtest_v4_strategies import Bar, MINUTE, NY

SEMI = frozenset("NVDA AMD MU INTC AVGO MRVL AMAT LRCX KLAC TSM QCOM".split())
TECH = frozenset("AAPL MSFT META AMZN GOOGL TSLA PLTR NFLX COIN MSTR".split())
STOCKS = SEMI | TECH
WINDOWS = {"ir1": (time(10, 15), time(14)), "ir2": (time(10), time(11)),
           "ir3": (time(15, 30), time(15, 50))}
BASELINE = {
    "ir1": dict(impulseATR=1,impulseBars=3,preHighBars=6,rsMin=.25,pullbackMin=.2,pullbackMax=.5,
                pullbackBarsMax=3,contractionMax=.7,triggerVolumeRatioMin=1,bufferATR=.1,
                armWaitMinutes=5,trailActivationR=1,trailATR=1,stallMinutes=20,stallMFER=.3,maxHoldMinutes=45),
    "ir2": dict(openingRangeMinutes=15,widthMinATR=.5,widthMaxATR=2.5,gapMaxDailyATR=1,
                penetrationMinATR=.1,penetrationMaxATR=.75,reclaimBars=2,bufferATR=.1,
                target="OR_MIDPOINT",minimumNetRewardR=1,maxHoldMinutes=30),
    "ir3": dict(openingMinutes=30,openingHistorySessions=60,openingHistoryMinimum=40,
                openingZMin=.5,openingRVOLMin=1.25,signalTime="15:30",forceExitTime="15:50",stopATR=1),
    "common": dict(atr5Period=14,atr5Method="SMA",atrDailyPeriod=14,dailyHistoryMin=61,adv20Min=50_000_000,
                minPrice=5,volumeHistorySessions=20,volumeHistoryMin=10,volatilityHistorySessions=60,
                volatilityHistoryMin=40,returnMinutes=20,ERMinutes=20,ERTrendMin=.4,ERRangeMax=.3,
                rangeSlopeMaxATR=.15,volHighPercentile=.9,volLowPercentile=.2,chaseATR=.3,maxLatencySeconds=90,
                halfSpreadProxyBps=1,riskCostMax=.2,detailQueueSize=15),
}


def benchmark(symbol):
    if symbol in SEMI:
        return "SOXX"
    if symbol in TECH:
        return "QQQ"
    if symbol in {"SPY", "QQQ"}:
        return symbol
    raise ValueError(f"Unmapped/excluded IR instrument: {symbol}")


def ratio(x, y):
    return x/y if y is not None and y > 0 else None


def median_ratio(x, history, minimum):
    values = [v for v in history if v is not None]
    return ratio(x, st.median(values)) if len(values) >= minimum else None


def percentile(x, history, minimum=40):
    values = [v for v in history if v is not None]
    if x is None or len(values) < minimum:
        return None
    return (sum(v < x for v in values)+.5*sum(v == x for v in values))/len(values)


def cross_ranks(values):
    n = len(values)
    return {s: .5 if n == 1 else (sum(v < x for v in values.values())+
            .5*(sum(v == x for v in values.values())-1))/(n-1)
            for s, x in values.items()}


@dataclass
class History:
    daily: deque = field(default_factory=lambda: deque(maxlen=80))
    tr5: deque = field(default_factory=lambda: deque(maxlen=14))
    rv: dict = field(default_factory=dict)
    volumes: dict = field(default_factory=dict)
    opening_returns: deque = field(default_factory=lambda: deque(maxlen=60))
    opening_volumes: deque = field(default_factory=lambda: deque(maxlen=20))

    def eligibility(self):
        # Include missing sessions as unavailable, never jump across them.
        last = list(self.daily)[-61:]
        if len(last) < 61 or any(x is None for x in last):
            return None
        prev = last[-1]
        atr = st.fmean(x["tr"] for x in last[-14:])
        adv = st.median(x["close"]*x["volume"] for x in last[-20:])
        if prev["close"] < 5 or adv < 50_000_000 or atr <= 0:
            return None
        return dict(prevClose=prev["close"],dailyATR=atr,adv20=adv)


class Session:
    """One symbol's observable prefix; no precomputed future snapshot exists."""
    def __init__(self, symbol, opening, closing, history):
        self.symbol, self.opening, self.closing, self.history = symbol, opening, closing, history
        self.minutes, self.snapshots, self.fives = {}, {}, []
        self.cum_v = self.cum_pv = 0.0
        self.last_end = None
        self.prefix_complete = True
        self.eligibility = history.eligibility()
        self.or15 = None

    def window(self, T, count):
        bars = [self.minutes.get(T-MINUTE*k) for k in range(count-1, -1, -1)]
        return bars if all(b is not None for b in bars) else None

    def observe(self, bar):
        if not self.opening <= bar.start < self.closing:
            return
        expected = self.last_end or self.opening
        if bar.start != expected:
            self.prefix_complete = False
        self.minutes[bar.end] = bar
        self.last_end = bar.end
        self.cum_v += bar.v
        self.cum_pv += (bar.h+bar.l+bar.c)/3*bar.v
        vw = ratio(self.cum_pv, self.cum_v) if self.prefix_complete else None
        five = None
        offset = int((bar.end-self.opening).total_seconds()/60)
        if offset % 5 == 0:
            rows = self.window(bar.end, 5)
            if rows:
                five = Bar(rows[0].start, bar.end, rows[0].o, max(x.h for x in rows),
                           min(x.l for x in rows), bar.c, sum(x.v for x in rows))
                prev = self.fives[-1]["bar"] if self.fives else None
                tr = five.h-five.l
                if prev is not None and prev.end == five.start:
                    tr = max(tr, abs(five.h-prev.c), abs(five.l-prev.c))
                elif five.start != self.opening:
                    tr = None
            else:
                tr = None
            self.history.tr5.append(tr)
            atr = st.fmean(self.history.tr5) if len(self.history.tr5) == 14 and None not in self.history.tr5 else None
            if five:
                self.fives.append(dict(bar=five, atr=atr))
        atr = self.fives[-1]["atr"] if self.fives else None
        w = self.window(bar.end, 21)
        u20 = er = rv = None
        if w:
            u20 = math.log(w[-1].c/w[0].c)
            path = sum(abs(b.c-a.c) for a,b in zip(w,w[1:]))
            er = ratio(abs(w[-1].c-w[0].c),path)
            rv = math.sqrt(sum(math.log(b.c/a.c)**2 for a,b in zip(w,w[1:])))
        w5 = self.window(bar.end, 6)
        u5 = math.log(w5[-1].c/w5[0].c) if w5 else None
        old = self.snapshots.get(bar.end-10*MINUTE)
        slope = ratio(vw-old["vwap"],atr) if vw is not None and old and old["vwap"] is not None else None
        prior = self.window(bar.end-MINUTE,20)
        va = ratio(bar.v,st.median(x.v for x in prior)) if prior else None
        key = offset
        rvol = median_ratio(self.cum_v,list(self.history.volumes.get(key,()))[-20:],10) if self.prefix_complete else None
        pvol = percentile(rv,self.history.rv.get(key,()))
        self.snapshots[bar.end] = dict(close=bar.c,vwap=vw,atr=atr,u20=u20,er=er,
              rv=rv,u5=u5,slope=slope,rvol=rvol,pvol=pvol,va1=va,cumVolume=self.cum_v,prefixComplete=self.prefix_complete)
        if offset == 15:
            rows = self.window(bar.end,15)
            if rows:
                self.or15 = (min(x.l for x in rows),max(x.h for x in rows))
        return five

    def opening_stats(self):
        T = self.opening+30*MINUTE
        rows = self.window(T,30)
        if not rows:
            return None
        u = math.log(rows[-1].c/rows[0].o)
        prior = [v for v in self.history.opening_returns if v is not None]
        sigma = st.stdev(prior) if len(prior) >= 40 else None
        z = ratio(u,sigma)
        vol = median_ratio(sum(x.v for x in rows),self.history.opening_volumes,10)
        return dict(openingReturn=u,openingZ=z,openingRVOL=vol)

    def finish(self):
        opening = self.opening_stats()
        self.history.opening_returns.append(opening["openingReturn"] if opening else None)
        rows = self.window(self.opening+30*MINUTE,30)
        self.history.opening_volumes.append(sum(x.v for x in rows) if rows else None)
        for k in range(1,391):
            snap = self.snapshots.get(self.opening+k*MINUTE)
            self.history.rv.setdefault(k,deque(maxlen=60)).append(snap["rv"] if snap else None)
            self.history.volumes.setdefault(k,deque(maxlen=20)).append(
                snap["cumVolume"] if snap and snap["prefixComplete"] else None)
        # A partial day may not generate a misleading prior daily close/ADV.
        expected = int((self.closing-self.opening).total_seconds()/60)
        if self.prefix_complete and len(self.minutes) == expected:
            rows = list(self.minutes.values())
            hi,lo,c = max(x.h for x in rows),min(x.l for x in rows),rows[-1].c
            previous = self.history.daily[-1] if self.history.daily else None
            tr = max(hi-lo,abs(hi-previous["close"]),abs(lo-previous["close"])) if previous else hi-lo
            self.history.daily.append(dict(close=c,volume=sum(x.v for x in rows),tr=tr))
        else:
            self.history.daily.append(None)


def direction(snap):
    if snap is None or any(snap.get(k) is None for k in ("u20","vwap","er","atr","slope")):
        return None
    if snap["u20"] > 0 and snap["close"] > snap["vwap"] and snap["er"] >= .4:
        return "UP"
    if snap["u20"] < 0 and snap["close"] < snap["vwap"] and snap["er"] >= .4:
        return "DOWN"
    return "OTHER"


def context(sessions,T):
    q,p = (sessions[s].snapshots.get(T) for s in ("QQQ","SPY"))
    dq,dp = direction(q),direction(p)
    if dq is None or dp is None:
        return dict(direction="UNKNOWN",volatility="UNKNOWN",recovery=False,qqq=dq,spy=dp)
    if dq == "UP" and dp != "DOWN":
        trend = "TREND_UP"
    elif dq == "DOWN" and dp != "UP":
        trend = "TREND_DOWN"
    elif all(x["er"] <= .3 and abs(x["slope"]) <= .15 for x in (q,p)):
        trend = "RANGE"
    else:
        trend = "MIXED"
    v = [x["pvol"] for x in (q,p)]
    vol = "UNKNOWN" if None in v else "HIGH_VOL" if max(v) >= .9 else "LOW_VOL" if max(v) <= .2 else "NORMAL_VOL"
    recovery = all(x["u5"] is not None and x["u5"] >= 0 for x in (q,p)) and dq != "DOWN" and dp != "DOWN"
    return dict(direction=trend,volatility=vol,recovery=recovery,qqq=dq,spy=dp,
                qqqReturn20=q["u20"],spyReturn20=p["u20"],qqqReturn5=q["u5"],spyReturn5=p["u5"],
                qqqER20=q["er"],spyER20=p["er"],qqqVWAPSlope=q["slope"],spyVWAPSlope=p["slope"],
                qqqVolatilityPercentile=q["pvol"],spyVolatilityPercentile=p["pvol"])


def rs20a(session,reference,T,A):
    s,b = session.snapshots.get(T),reference.snapshots.get(T)
    old = session.minutes.get(T-20*MINUTE)
    if not s or not b or old is None or s["u20"] is None or b["u20"] is None:
        return None
    return (s["u20"]-b["u20"])*old.c/A


@dataclass
class Event:
    strategy: str
    symbol: str
    state: str = "IDLE"
    fields: dict = field(default_factory=dict)
    transitions: list = field(default_factory=list)

    def move(self,state,T,reason=None,**values):
        self.fields.update(values)
        self.transitions.append(dict(fromState=self.state,toState=state,timestamp=T.isoformat(),reason=reason))
        self.state = state
        self.fields[state.lower()+"Timestamp"] = T.isoformat()
        if reason:
            self.fields["reason"] = reason


def allowed_context(strategy,symbol,ctx,sessions,T,stage):
    if ctx["direction"] == "UNKNOWN" or ctx["volatility"] == "UNKNOWN":
        return False
    if strategy == "ir1":
        if ctx["direction"] != "TREND_UP" or ctx["volatility"] != "NORMAL_VOL":
            return False
        if symbol in SEMI:
            x = sessions["SOXX"].snapshots.get(T)
            return bool(x and x["u20"] is not None and x["u20"] >= 0 and x["vwap"] is not None and x["close"] > x["vwap"])
        return True
    if strategy == "ir2":
        return ctx["volatility"] == "NORMAL_VOL" and (ctx["direction"] in {"RANGE","MIXED"} if stage in {"IDLE","SETUP"} else ctx["direction"] != "TREND_DOWN")
    own,other = (ctx["qqq"],ctx["spy"]) if symbol == "QQQ" else (ctx["spy"],ctx["qqq"])
    return own == "UP" and other != "DOWN" and ctx["volatility"] in {"NORMAL_VOL","HIGH_VOL"}


def context_rejection_reason(strategy,symbol,ctx,sessions,T,stage):
    """Explain a failed allowed_context() without changing strategy semantics."""
    required={"QQQ","SPY"}|({"SOXX"} if strategy=="ir1" and symbol in SEMI else set())
    if any(x not in sessions or sessions[x].snapshots.get(T) is None for x in required):
        return "MISSING_BENCHMARK_DATA"
    if ctx["direction"]=="UNKNOWN":
        return "MARKET_FEATURES_UNAVAILABLE"
    if ctx["volatility"]=="UNKNOWN":
        return "REGIME_HISTORY_UNAVAILABLE"
    if strategy=="ir1":
        if ctx["direction"]!="TREND_UP":
            return "CONTEXT_DIRECTION_FAILED"
        if ctx["volatility"]!="NORMAL_VOL":
            return "CONTEXT_VOLATILITY_FAILED"
        if symbol in SEMI:
            x=sessions["SOXX"].snapshots.get(T)
            if x is None or x.get("u20") is None or x.get("vwap") is None:
                return "SECTOR_CONTEXT_UNAVAILABLE"
            if x["u20"]<0 or not (x["close"]>x["vwap"]):
                return "SECTOR_CONTEXT_FAILED"
    elif strategy=="ir2":
        if ctx["volatility"]!="NORMAL_VOL":
            return "CONTEXT_VOLATILITY_FAILED"
        if stage in {"IDLE","SETUP"} and ctx["direction"] not in {"RANGE","MIXED"}:
            return "CONTEXT_DIRECTION_FAILED"
        if stage not in {"IDLE","SETUP"} and ctx["direction"]=="TREND_DOWN":
            return "CONTEXT_DIRECTION_FAILED"
    elif strategy=="ir3":
        own,other=(ctx["qqq"],ctx["spy"]) if symbol=="QQQ" else (ctx["spy"],ctx["qqq"])
        if own!="UP" or other=="DOWN":
            return "CLOCK_DIRECTION_FILTER_FAILED"
        if ctx["volatility"] not in {"NORMAL_VOL","HIGH_VOL"}:
            return "CLOCK_VOLATILITY_FILTER_FAILED"
    return "CONTEXT_FILTER_FAILED"


def idle_rejection_reason(strategy,event,session,sessions,T,ctx,queued):
    """Classify an IDLE outcome after evaluate(); diagnostics only."""
    clock = strategy=="ir3"
    if session.minutes.get(T) is None:
        return "CLOCK_TARGET_BAR_MISSING" if clock else "MISSING_SYMBOL_DATA"
    lo,hi=WINDOWS[strategy]
    if not lo<=T.time()<hi or T>=session.closing-10*MINUTE:
        return "CLOCK_OUTSIDE_ELIGIBLE_WINDOW" if clock else "ENTRY_WINDOW_ENDED"
    if session.eligibility is None:
        h=list(session.history.daily)[-61:]
        return "DAILY_HISTORY_UNAVAILABLE" if len(h)<61 or None in h else "DAILY_LIQUIDITY_REJECTED"
    if not queued:
        return "QUEUE_UNAVAILABLE"
    if not allowed_context(strategy,event.symbol,ctx,sessions,T,event.state):
        return context_rejection_reason(strategy,event.symbol,ctx,sessions,T,event.state)
    five=session.fives[-1] if session.fives and session.fives[-1]["bar"].end==T else None
    prior_index=-2 if five else -1
    prior_atr=session.fives[prior_index]["atr"] if len(session.fives)>=abs(prior_index) else None
    A=event.fields.get("A",prior_atr)
    if not A:
        return "CLOCK_ATR_UNAVAILABLE" if clock else "ATR_UNAVAILABLE"
    return "CLOCK_UNCLASSIFIED_IDLE" if clock else "UNCLASSIFIED_IDLE"


def evaluate(event,session,sessions,T,ctx,queued):
    """One state transition per event phase; cancellation checked before trigger."""
    s,kind,f = event.state,event.strategy,event.fields
    if s in {"CANCELLED","EXIT","MANAGING","TRIGGERED","ENTRY"}:
        return
    bar = session.minutes.get(T)
    if bar is None:
        if s != "IDLE": event.move("CANCELLED",T,"MISSING_SYMBOL_DATA")
        return
    lo,hi = WINDOWS[kind]
    if not lo <= T.time() < hi or T >= session.closing-10*MINUTE:
        if s != "IDLE": event.move("CANCELLED",T,"ENTRY_WINDOW_ENDED")
        return
    if s == "IDLE" and (session.eligibility is None or not queued):
        return
    valid = allowed_context(kind,event.symbol,ctx,sessions,T,s)
    if not valid:
        required={"QQQ","SPY"}|({"SOXX"} if kind=="ir1" and event.symbol in SEMI else set())
        missing=any(x not in sessions or sessions[x].snapshots.get(T) is None for x in required)
        if s != "IDLE": event.move("CANCELLED",T,context_rejection_reason(kind,event.symbol,ctx,sessions,T,s))
        return
    snap = session.snapshots[T]
    five = session.fives[-1] if session.fives and session.fives[-1]["bar"].end == T else None
    # Event scale is last completed 5m ATR strictly BEFORE the setup timestamp.
    prior_index = -2 if five else -1
    prior_atr = session.fives[prior_index]["atr"] if len(session.fives)>=abs(prior_index) else None
    A = f.get("A",prior_atr)
    if not A:
        if s != "IDLE": event.move("CANCELLED",T,"ATR_UNAVAILABLE")
        return
    if kind == "ir3":
        if T.time() != time(15,30) or s != "IDLE": return
        opening = session.opening_stats()
        if (session.closing-session.opening).total_seconds() != 390*60:
            event.move("CANCELLED",T,"EARLY_CLOSE_DISABLED"); return
        if not opening or opening["openingZ"] is None or opening["openingRVOL"] is None:
            event.move("CANCELLED",T,"OPENING_HISTORY_UNAVAILABLE"); return
        if opening["openingZ"] < .5 or opening["openingRVOL"] < 1.25:
            event.move("CANCELLED",T,"OPENING_CONDITION_FAILED"); return
        event.move("SETUP",T,A=A,**opening)
        event.move("ARMED",T)
        event.move("TRIGGERED",T,triggerClose=bar.c,signalTimestamp=T.isoformat())
        return
    if kind == "ir2":
        if s == "IDLE":
            if session.or15 is None: return
            orl,orh = session.or15
            g = (session.minutes[session.opening+MINUTE].o-session.eligibility["prevClose"])/session.eligibility["dailyATR"]
            if .5 <= (orh-orl)/A <= 2.5 and abs(g) <= 1:
                event.move("SETUP",T,A=A,ORL=orl,ORH=orh,target=(orl+orh)/2,gapATR=g)
            return
        if s == "SETUP":
            if bar.l < f["ORL"]-.75*A:
                event.move("CANCELLED",T,"EXCESSIVE_PENETRATION"); return
            if five and len(session.fives) >= 2:
                prev = session.fives[-2]["bar"]
                x = five["bar"]
                penetration = (f["ORL"]-x.l)/A
                if prev.end == x.start and prev.c >= f["ORL"] and x.c < f["ORL"]-.1*A and .1 <= penetration <= .75:
                    event.move("ARMED",T,failureLow=x.l,breakdownTimestamp=T.isoformat(),penetrationATR=penetration,reclaimCount=0)
            return
        if bar.l < f["failureLow"]:
            event.move("CANCELLED",T,"NEW_LOWER_LOW"); return
        if five:
            # Chronological ordinal, not number of surviving aggregates: gaps
            # must not turn a third physical bar into the second reclaim bar.
            elapsed = (T-datetime.fromisoformat(f["breakdownTimestamp"])).total_seconds()/300
            f["reclaimCount"] = int(elapsed)
            if elapsed <= 2 and five["bar"].c > f["ORL"]+.1*A and ctx["recovery"]:
                event.move("TRIGGERED",T,triggerClose=bar.c,stop=f["failureLow"]-.1*A,signalTimestamp=T.isoformat(),noNewLow=True,marketRecovery=True)
            elif elapsed >= 2:
                event.move("CANCELLED",T,"RECLAIM_EXPIRED")
        return
    # IR1: RS/reference only uses synchronized same-session completed windows.
    RS = rs20a(session,sessions[benchmark(event.symbol)],T,A)
    if s == "IDLE":
        if not five or len(session.fives) < 9 or RS is None: return
        nine = session.fives[-9:]
        if any(a["bar"].end != b["bar"].start for a,b in zip(nine,nine[1:])): return
        impulse = [x["bar"] for x in nine[-3:]]
        origin,end = impulse[0].o,impulse[-1].c
        advance = end-origin
        prehigh = max(x["bar"].h for x in nine[:6])
        vol = st.fmean(x.v for x in impulse)
        if advance >= A and end > prehigh+.1*A and snap["vwap"] is not None and end > snap["vwap"] and RS >= .25 and vol > 0:
            event.move("SETUP",T,A=A,origin=origin,impulseEnd=end,advance=advance,impulseATR=advance/A,
                      impulseVolume=vol,relativeStrength=RS,pullbacks=[],impulseTimestamp=T.isoformat())
        return
    if RS is None or snap["vwap"] is None:
        event.move("CANCELLED",T,"MISSING_BENCHMARK_DATA"); return
    if bar.c <= max(f["origin"],snap["vwap"]) or bar.l < f["impulseEnd"]-.5*f["advance"]:
        event.move("CANCELLED",T,"PULLBACK_INVALIDATED"); return
    if s == "SETUP":
        if five:
            f["pullbacks"].append(dict(low=five["bar"].l,high=five["bar"].h,volume=five["bar"].v))
            pb = f["pullbacks"]
            low = min(x["low"] for x in pb)
            depth = (f["impulseEnd"]-low)/f["advance"]
            vc = st.fmean(x["volume"] for x in pb)/f["impulseVolume"]
            if .2 <= depth <= .5 and vc <= .7:
                event.move("ARMED",T,B=five["bar"].h+.1*A,L_arm=low,pullbackDepth=depth,volumeContraction=vc,stop=low-.1*A)
            elif len(pb) >= 3:
                event.move("CANCELLED",T,"PULLBACK_EXPIRED")
        return
    arm = datetime.fromisoformat(f["armedTimestamp"])
    if bar.l < f["L_arm"] or RS < .25:
        event.move("CANCELLED",T,"ARMED_INVALIDATED")
    elif T > arm+5*MINUTE:
        event.move("CANCELLED",T,"TRIGGER_EXPIRED")
    elif T > arm and bar.c > f["B"] and snap["va1"] is not None and snap["va1"] >= 1:
        event.move("TRIGGERED",T,triggerClose=bar.c,signalTimestamp=T.isoformat(),relativeStrength=RS,breakoutDistanceATR=(bar.c-f["B"])/A)


class Book:
    def __init__(self,strategy,slippage_bps=2):
        self.strategy, self.slip = strategy, slippage_bps/10000
        self.cash = 10000.0
        self.position = None
        self.trades = []
        self.funnel = Counter()

    def enter(self,event,bar,T,ctx,session,sessions):
        f = event.fields
        signal = datetime.fromisoformat(f["signalTimestamp"])
        if T <= signal: return False
        reason = None
        if self.position: reason = "CAPACITY"
        elif (T-signal).total_seconds() > 90 or bar.end-bar.start != MINUTE or T != signal+MINUTE:
            reason = "ENTRY_GAP_OR_LATENCY"
        elif not allowed_context(event.strategy,event.symbol,ctx,sessions,T,"TRIGGERED"):
            reason = "MISSING_BENCHMARK_DATA" if ctx["direction"] == "UNKNOWN" else "ENTRY_CONTEXT_FAILED"
        lo,hi = WINDOWS[event.strategy]
        if not lo <= T.time() < hi or T >= session.closing-10*MINUTE: reason = "ENTRY_WINDOW_ENDED"
        E = bar.c*(1+self.slip+.0001)
        S = E-f["A"] if event.strategy == "ir3" else f["stop"]
        risk = E-S
        if risk <= 0 or 2*(self.slip+.0001)*E/risk > .2: reason = reason or "RISK_COST_GUARD"
        if E > f["triggerClose"]+.3*f["A"]: reason = reason or "CHASE"
        if event.strategy == "ir1":
            RS = rs20a(session,sessions[benchmark(event.symbol)],T,f["A"])
            if E <= f["B"] or bar.c <= f["L_arm"] or RS is None or RS < .25: reason = reason or "ENTRY_STRUCTURE_RS_FAILED"
        if event.strategy == "ir2":
            reward = f["target"]*(1-self.slip-.0001)-E
            if not S < E < f["target"] or E <= f["ORL"] or bar.c < f["failureLow"] or not ctx["recovery"] or reward/max(risk,1e-12) < 1:
                reason = reason or "ENTRY_REWARD_RECOVERY_FAILED"
        if self.cash < 100: reason = reason or "CASH_LIMIT"
        if reason:
            event.move("CANCELLED",T,reason); self.funnel[reason] += 1; return False
        event.move("ENTRY",T)
        p = dict(strategy=event.strategy,symbol=event.symbol,date=T.date().isoformat(),session="REGULAR",
                 signalKey=["IR_SPEC_V1",event.strategy,event.symbol,T.date().isoformat(),f["setupTimestamp"],f["signalTimestamp"]],
                 setupTimestamp=f["setupTimestamp"],armedTimestamp=f["armedTimestamp"],
                 triggerTimestamp=f["signalTimestamp"],signalTimestamp=f["signalTimestamp"],
                 signalBarStart=(signal-(5*MINUTE if event.strategy=="ir2" else MINUTE)).isoformat(),signalBarEnd=signal.isoformat(),
                 signalTimeframe="5m" if event.strategy=="ir2" else "clock_snapshot" if event.strategy=="ir3" else "1m",
                 entryBarStart=bar.start.isoformat(),entryBarEnd=bar.end.isoformat(),
                 entryObservationTimestamp=T.isoformat(),entryTimestamp=T.isoformat(),
                 entryMarketPrice=bar.c,entryFillPrice=E,entryPrice=E,quantity=100/E,
                 entryLatencySeconds=None,simulatedLatencySeconds=(T-signal).total_seconds(),
                 sourceTimestamp=None,receiptTimestamp=None,stopPrice=S,initialStop=S,
                 targetPrice=f.get("target"),ATR=f["A"],VWAP=session.snapshots[T]["vwap"],
                 RVOL=session.snapshots[T]["rvol"],marketRegime=dict(ctx),benchmark=benchmark(event.symbol),
                 diagnostics={k:v for k,v in f.items() if k != "pullbacks"},
                 peak=bar.c,trough=bar.c,peakTime=T.isoformat(),troughTime=T.isoformat(),
                 lastObservation=T.isoformat(),dataStatus="FRESH",staleSince=None,staleObservationCount=0,
                 sessionCutoff=min(session.closing-10*MINUTE,session.opening.replace(hour=15,minute=50)).isoformat(),
                 exitAffectedByDataStale=False,trailActivated=False,stallChecked=False,stallPending=False,
                 ambiguousIntrabarCount=0,unexecutedIntrabarStopTouches=0,
                 runMode="historical_proxy",quoteAgeVerified=False,executabilityConfirmed=False)
        self.position = p
        self.cash -= 100
        event.move("MANAGING",T)
        self.funnel["entries"] += 1
        return True

    def manage(self,T,bar,cutoff):
        p = self.position
        if p is None: return None
        dt = datetime
        cutoff = dt.fromisoformat(p["sessionCutoff"])
        last = dt.fromisoformat(p["lastObservation"])
        if bar is None:
            p["dataStatus"] = "DATA_STALE"
            p["staleSince"] = p["staleSince"] or T.isoformat()
            p["staleObservationCount"] += 1
            p["exitAffectedByDataStale"] = True
            self.funnel["DATA_STALE"] += 1
            return None
        if T <= last: return None
        if T-last > MINUTE:
            p["exitAffectedByDataStale"] = True
            p["staleSince"] = p["staleSince"] or (last+MINUTE).isoformat()
        entry = dt.fromisoformat(p["entryTimestamp"])
        hold = (T-entry).total_seconds()
        E,S = p["entryPrice"],p["initialStop"]
        R = E-S
        if bar.l <= p["stopPrice"] and bar.c > p["stopPrice"]:
            p["unexecutedIntrabarStopTouches"] += 1
        if p["targetPrice"] is not None and bar.l <= p["stopPrice"] and bar.h >= p["targetPrice"]:
            p["ambiguousIntrabarCount"] += 1
        reason = None
        # Conditions on the observable mark; historical lows are never fills.
        if bar.c <= S: reason = "HARD_STOP"
        elif p["trailActivated"] and bar.c <= p["stopPrice"]: reason = "TRAIL_STOP"
        elif p["targetPrice"] is not None and bar.c >= p["targetPrice"]: reason = "TARGET"
        elif T >= cutoff: reason = "SESSION_EXIT"
        elif hold >= (45 if self.strategy == "ir1" else 30)*60 and self.strategy != "ir3": reason = "TIME_STOP"
        elif p["stallPending"]: reason = "STALL_STOP"
        if bar.c > p["peak"]: p["peak"],p["peakTime"] = bar.c,T.isoformat()
        if bar.c < p["trough"]: p["trough"],p["troughTime"] = bar.c,T.isoformat()
        p["lastObservation"],p["dataStatus"] = T.isoformat(),"FRESH"
        mfe = max(0,(p["peak"]-E)/R)
        if reason:
            X = bar.c*(1-self.slip-.0001)
            pnl = p["quantity"]*(X-E)
            p.update(exitObservationTimestamp=T.isoformat(),exitTimestamp=T.isoformat(),exitMarketPrice=bar.c,
                     exitFillPrice=X,exitPrice=X,exitReason=reason,pnlUsd=pnl,returnPct=100*(X/E-1),
                     grossR=(bar.c-p["entryMarketPrice"])/R,netR=pnl/(p["quantity"]*R),
                     MFE_R=mfe,MAE_R=min(0,(p["trough"]-E)/R),
                     mfePct=max(0,100*(p["peak"]/E-1)),maePct=min(0,100*(p["trough"]/E-1)),
                     holdDurationSeconds=hold,entryToMFESeconds=(dt.fromisoformat(p["peakTime"])-entry).total_seconds(),
                     entryToMAESeconds=(dt.fromisoformat(p["troughTime"])-entry).total_seconds(),
                     exitTriggerTimestamp=(cutoff if reason == "SESSION_EXIT" else T).isoformat(),
                     exitLatencySeconds=None,simulatedExitLatencySeconds=(T-cutoff).total_seconds() if reason=="SESSION_EXIT" else 0,
                     slippageCostUsd=p["quantity"]*self.slip*(p["entryMarketPrice"]+bar.c),
                     spreadProxyCostUsd=p["quantity"]*.0001*(p["entryMarketPrice"]+bar.c),feeUsd=0,feeVerified=False)
            self.cash += p["quantity"]*X
            self.trades.append(dict(p))
            self.position = None
            self.funnel["exits"] += 1
            return p
        if self.strategy == "ir1":
            if mfe >= 1:
                p["trailActivated"] = True
                p["stopPrice"] = max(p["stopPrice"],p["peak"]-p["ATR"])
            if hold >= 20*60 and not p["stallChecked"]:
                p["stallChecked"] = True
                p["stallPending"] = mfe < .3
        return None
