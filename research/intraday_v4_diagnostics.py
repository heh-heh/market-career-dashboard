"""Bounded, observational V4 diagnostics. Never changes eligibility/execution.

Reason priority follows the evaluated gate order; one primary reason per
terminal cancellation. Observation failures (potentially repeated each minute)
are explicitly separate from terminal event counts.
"""
from collections import Counter, defaultdict
from datetime import datetime

try:
    from .intraday_v4_engine import SEMI, MINUTE, WINDOWS
except ImportError:
    from intraday_v4_engine import SEMI, MINUTE, WINDOWS


def eligibility_reason(session):
    if session.eligibility is not None:
        return None
    daily = list(session.history.daily)[-61:]
    if len(daily) < 61:
        return "DAILY_HISTORY_INSUFFICIENT"
    if None in daily:
        return "DAILY_SESSION_INCOMPLETE"
    if daily[-1]["close"] < 5:
        return "DAILY_PRICE_FILTER"
    import statistics
    if statistics.median(x["close"]*x["volume"] for x in daily[-20:]) < 50_000_000:
        return "DAILY_DOLLAR_VOLUME_FILTER"
    return "DAILY_ATR_NONPOSITIVE"


def feature_reason(session, T, role="BENCHMARK"):
    if session is None:
        return role+"_MISSING"
    snap = session.snapshots.get(T)
    if snap is None:
        return role+"_BAR_MISSING"
    # Same ordering as availability requirements in direction(), with the
    # persistent prefix gap made explicit instead of a generic missing VWAP.
    if snap.get("vwap") is None:
        return role+("_SESSION_PREFIX_INCOMPLETE" if not session.prefix_complete else "_VWAP_UNAVAILABLE")
    if snap.get("u20") is None or snap.get("er") is None:
        return role+("_RETURN_WINDOW_INCOMPLETE" if session.window(T,21) is None else "_EFFICIENCY_UNDEFINED")
    if snap.get("atr") is None:
        return role+"_ATR_WARMUP_INSUFFICIENT"
    if snap.get("slope") is None:
        return role+"_VWAP_SLOPE_UNAVAILABLE"
    return None


def context_reason(kind, symbol, ctx, sessions, T, stage):
    """Explain precisely the existing allowed_context() boolean, without tuning."""
    if ctx["direction"] == "UNKNOWN":
        for ref in ("QQQ", "SPY"):
            reason = feature_reason(sessions.get(ref),T)
            if reason:
                return reason, ref
        return "BENCHMARK_DIRECTION_UNAVAILABLE", None
    if ctx["volatility"] == "UNKNOWN":
        for ref in ("QQQ", "SPY"):
            snap=sessions[ref].snapshots.get(T)
            if snap is None or snap.get("pvol") is None:
                return "VOLATILITY_HISTORY_INSUFFICIENT", ref
        return "VOLATILITY_PERCENTILE_UNAVAILABLE", None
    if kind == "ir1":
        if ctx["direction"] != "TREND_UP":
            return "MARKET_DIRECTION_FILTER", None
        if ctx["volatility"] != "NORMAL_VOL":
            return "VOLATILITY_FILTER", None
        if symbol in SEMI:
            ref=sessions.get("SOXX")
            x=ref.snapshots.get(T) if ref else None
            if x is None:
                return "SECTOR_BAR_MISSING" if ref else "SECTOR_MISSING", "SOXX"
            if x["u20"] is None:
                return "SECTOR_RETURN_WINDOW_INCOMPLETE", "SOXX"
            if x["u20"] < 0:
                return "SECTOR_RETURN_FILTER", "SOXX"
            if x["vwap"] is None:
                return "SECTOR_SESSION_PREFIX_INCOMPLETE" if not ref.prefix_complete else "SECTOR_VWAP_UNAVAILABLE", "SOXX"
            if x["close"] <= x["vwap"]:
                return "SECTOR_VWAP_FILTER", "SOXX"
    elif kind == "ir2":
        if ctx["volatility"] != "NORMAL_VOL":
            return "VOLATILITY_FILTER", None
        if (ctx["direction"] not in {"RANGE","MIXED"} if stage in {"IDLE","SETUP"} else ctx["direction"] == "TREND_DOWN"):
            return "MARKET_DIRECTION_FILTER", None
    else:
        own,other=(ctx["qqq"],ctx["spy"]) if symbol == "QQQ" else (ctx["spy"],ctx["qqq"])
        if own != "UP":
            return "OWN_DIRECTION_FILTER", symbol
        if other == "DOWN":
            return "REFERENCE_DIRECTION_FILTER", "SPY" if symbol == "QQQ" else "QQQ"
        if ctx["volatility"] not in {"NORMAL_VOL","HIGH_VOL"}:
            return "VOLATILITY_FILTER", None
    return None, None


def idle_gate_reason(event, session, sessions, T, ctx, queued):
    """Existing pre-SETUP gates only; no price-pattern rule is added here."""
    prefix="CLOCK_" if event.strategy == "ir3" else "CONTEXT_"
    if session.minutes.get(T) is None:
        return prefix+("TARGET_BAR_MISSING" if event.strategy == "ir3" else "SYMBOL_BAR_MISSING"), event.symbol
    lo,hi=WINDOWS[event.strategy]
    if not lo <= T.time() < hi or T >= session.closing-10*MINUTE:
        return prefix+"ENTRY_WINDOW_ENDED", None
    r=eligibility_reason(session)
    if r:
        return prefix+r, event.symbol
    if not queued:
        return "CONTEXT_DETAIL_QUEUE_UNAVAILABLE", event.symbol
    r,ref=context_reason(event.strategy,event.symbol,ctx,sessions,T,event.state)
    if r:
        return prefix+r, ref
    five=session.fives[-1] if session.fives and session.fives[-1]["bar"].end == T else None
    ix=-2 if five else -1
    A=event.fields.get("A",session.fives[ix]["atr"] if len(session.fives)>=abs(ix) else None)
    if not A:
        return prefix+"PRIOR_ATR_UNAVAILABLE", event.symbol
    return None,None


def snapshot_evidence(event,session,sessions,T,ctx,reference=None):
    target=session.minutes.get(T)
    refs={}
    for ref in dict.fromkeys(["QQQ","SPY"]+(["SOXX"] if event.strategy=="ir1" and event.symbol in SEMI else [])):
        s=sessions.get(ref); x=s.snapshots.get(T) if s else None
        refs[ref]=dict(snapshot=x,available=x is not None,
                      prefixComplete=s.prefix_complete if s else None,
                      firstPrefixGap=getattr(s,"first_prefix_gap",None),
                      nearestPreviousObservableTimestamp=max((t.isoformat() for t in s.minutes if t<=T),default=None) if s else None,
                      volatilityHistoryCount=sum(v is not None for v in s.history.rv.get(int((T-s.opening).total_seconds()/60),())) if s else 0)
    evidence_session=sessions.get(reference,session)
    nearest=max((t for t in evidence_session.minutes if t<=T),default=None)
    daily=list(session.history.daily)[-61:]
    return dict(symbol=event.symbol,sessionDate=T.date().isoformat(),strategy=event.strategy,
                decisionTime=T.isoformat(),signalTime=event.fields.get("signalTimestamp"),
                barStartTime=target.start.isoformat() if target else None,
                barObservableTime=target.end.isoformat() if target else None,
                expectedTimestamp=(T-MINUTE).isoformat(),expectedObservableTimestamp=T.isoformat(),
                nearestPreviousTimestamp=(nearest-MINUTE).isoformat() if nearest else None,
                nearestNextTimestamp=None,nextTimestampNote="Future rows are not inspected at decision time",
                benchmark=reference,marketContext=dict(ctx),references=refs,
                eligibilityAvailable=session.eligibility is not None,priorDailySessions=len(daily),
                incompletePriorDailySessions=sum(x is None for x in daily),
                prefixComplete=session.prefix_complete,firstPrefixGap=getattr(session,"first_prefix_gap",None))


class Diagnostics:
    """O(strategies * reason_codes * sample_limit), independent of row count."""
    stages=("eligibleSessions","clockReached","targetContextAvailable","benchmarkContextAvailable",
            "strategyConditionEvaluated","conditionPassed","signalEmitted","executionObserved","tradeEntered")
    pattern_stages=("eligibleSessions","contextAvailable","setupCandidates","setupValid","triggerObserved",
                    "signalEmitted","executionObserved","tradeEntered")

    def __init__(self, strategies, sample_limit=5):
        if not 0 <= sample_limit <= 20:
            raise ValueError("sample_limit must be 0..20")
        self.sample_limit=sample_limit
        self.data={k:dict(rejectReasons=Counter(),samplesByReason={},observationRejectReasons=Counter(),observationSamplesByReason={},sessionExclusionReasons=Counter(),
                         observationCounts=Counter(),stages=Counter(),clockCoverage=Counter(),legacyRejectReasons=Counter()) for k in strategies}
        self.direction_collector=DiagnosticCollector(strategies,sample_limit=max(1,sample_limit))
        self.seen=set()  # Cleared each session; one symbol x strategy x stage only.

    def new_session(self):
        self.seen.clear()

    def stage(self,event,stage):
        key=(event.strategy,event.symbol,stage)
        if key not in self.seen:
            self.seen.add(key); self.data[event.strategy]["stages"][stage]+=1

    def reject(self,event,reason,sample=None,legacy=None):
        d=self.data[event.strategy];d["rejectReasons"][reason]+=1
        if legacy: d["legacyRejectReasons"][legacy]+=1
        examples=d["samplesByReason"].setdefault(reason,[])
        if sample is not None and len(examples)<self.sample_limit:
            examples.append(dict(reason=reason,**sample))

    def observation_reject(self,event,reason,sample_factory):
        d=self.data[event.strategy];d["observationRejectReasons"][reason]+=1
        examples=d["observationSamplesByReason"].setdefault(reason,[])
        if len(examples)<self.sample_limit: examples.append(dict(reason=reason,**sample_factory()))

    def as_dict(self):
        result={}
        for kind,d in self.data.items():
            stages=self.stages if kind=="ir3" else self.pattern_stages
            funnel=[];previous=None
            for stage in stages:
                count=d["stages"][stage]
                funnel.append(dict(stage=stage,count=count,conversionFromPrevious=count/previous if previous else None))
                previous=count
            total=sum(d["rejectReasons"].values())
            result[kind]={**d,"rejectReasonBreakdown":{r:dict(count=n,pct=100*n/total if total else None) for r,n in sorted(d["rejectReasons"].items())},"stageFunnel":funnel,"sampleLimitPerReason":self.sample_limit,
                          "terminalReasonsAreExclusive":True,
                          "stageUnit":"unique symbol/strategy/session; raw observations are separate",
                          "observationReasonsUnit":"per decision attempt, may repeat; not terminal events"}
        if "ir3" in result:
            result["ir3"]["directionFailureBreakdown"]=self.direction_collector.to_dict()["ir3"]["directionFailureBreakdown"]
        return result


def opening_reason(session):
    if session.window(session.opening+30*MINUTE,30) is None:
        return "CLOCK_OPENING_RANGE_MISSING"
    prior=[v for v in session.history.opening_returns if v is not None]
    if len(prior)<40:
        return "CLOCK_OPENING_RETURN_HISTORY_INSUFFICIENT"
    opening=session.opening_stats()
    if opening["openingZ"] is None:
        return "CLOCK_OPENING_STD_UNDEFINED"
    prior=[v for v in session.history.opening_volumes if v is not None]
    if len(prior)<10:
        return "CLOCK_OPENING_VOLUME_HISTORY_INSUFFICIENT"
    return "CLOCK_OPENING_VOLUME_BASELINE_NONPOSITIVE"


class DiagnosticCollector:
    """Collect rejection reasons and a few examples without retaining minute rows."""

    def __init__(self, strategies, sample_limit=5):
        self.strategies=list(strategies)
        self.sample_limit=max(1,int(sample_limit))
        self.reject={k:Counter() for k in self.strategies}
        self.transitions={k:Counter() for k in self.strategies}
        self.samples={k:defaultdict(list) for k in self.strategies}
        self.clock=Counter()
        self.ir3_direction_components=Counter()
        self.ir3_direction_components_by_symbol=defaultdict(Counter)
        self.ir3_direction_signatures=Counter()
        self.ir3_direction_samples=defaultdict(list)
        self.ir3_er_trend_min=None

    def _sample(self,event,day,T,reason):
        bucket=self.samples[event.strategy][reason]
        if len(bucket)>=self.sample_limit:
            return
        bucket.append(dict(
            reason=reason,
            symbol=event.symbol,
            sessionDate=day,
            decisionTime=T.isoformat(),
            state=event.state,
            transitionCount=len(event.transitions),
        ))

    def record_rejection(self,event,day,T):
        if event.strategy not in self.reject:
            return
        reason=event.fields.get("reason") or "UNKNOWN_REJECTION"
        self.reject[event.strategy][reason]+=1
        self.transitions[event.strategy]["CANCELLED"]+=1
        self._sample(event,day,T,reason)

    def record_transition(self,event,day,T,before):
        if event.strategy not in self.transitions or event.state==before:
            return
        self.transitions[event.strategy][event.state]+=1

    def observe_ir3_clock(self,symbol,day,T,session,sessions,ctx):
        self.clock["decisions"]+=1
        if session.minutes.get(T) is None:
            self.clock["targetCompletedBarMissing"]+=1
        else:
            self.clock["targetCompletedBarAvailable"]+=1
        refs=("SPY","QQQ")
        if any(x not in sessions or sessions[x].snapshots.get(T) is None for x in refs):
            self.clock["benchmarkContextMissing"]+=1
        else:
            self.clock["benchmarkContextAvailable"]+=1
        if ctx.get("direction")=="UNKNOWN":
            self.clock["marketFeaturesUnavailable"]+=1
        else:
            self.clock["marketFeaturesAvailable"]+=1
        if ctx.get("volatility")=="UNKNOWN":
            self.clock["regimeHistoryUnavailable"]+=1
        else:
            self.clock["regimeHistoryAvailable"]+=1

    def observe_ir3_direction_failure(self,symbol,day,T,sessions,ctx,er_trend_min):
        """Break down CLOCK_DIRECTION_FILTER_FAILED without changing the decision.

        Component counts intentionally overlap: one decision can fail u20, VWAP,
        ER and the cross-index confirmation simultaneously. Exact intersections
        are preserved in signatureCounts.
        """
        self.ir3_er_trend_min=er_trend_min
        other_symbol="SPY" if symbol=="QQQ" else "QQQ"
        own_session=sessions.get(symbol)
        other_session=sessions.get(other_symbol)
        own=own_session.snapshots.get(T) if own_session is not None else None
        other=other_session.snapshots.get(T) if other_session is not None else None
        components=[]

        if own is None:
            components.append("OWN_SNAPSHOT_UNAVAILABLE")
        else:
            u20=own.get("u20")
            close=own.get("close")
            vwap=own.get("vwap")
            er=own.get("er")
            if u20 is None:
                components.append("OWN_U20_UNAVAILABLE")
            elif u20<=0:
                components.append("OWN_U20_NONPOSITIVE")
            if close is None or vwap is None:
                components.append("OWN_VWAP_RELATION_UNAVAILABLE")
            elif close<=vwap:
                components.append("OWN_NOT_ABOVE_VWAP")
            if er is None:
                components.append("OWN_ER_UNAVAILABLE")
            elif er<er_trend_min:
                components.append("OWN_ER_BELOW_TREND_MIN")

        other_direction=ctx.get(other_symbol.lower())
        if other_direction is None:
            components.append("OTHER_DIRECTION_UNAVAILABLE")
        elif other_direction=="DOWN":
            components.append("OTHER_INDEX_DOWN")

        if not components:
            components.append("DIRECTION_FAILURE_UNCLASSIFIED")

        signature="+".join(components)
        self.ir3_direction_signatures[signature]+=1
        for component in components:
            self.ir3_direction_components[component]+=1
            self.ir3_direction_components_by_symbol[symbol][component]+=1

        bucket=self.ir3_direction_samples[signature]
        if len(bucket)<self.sample_limit:
            def value(snapshot,key):
                return snapshot.get(key) if snapshot is not None else None
            bucket.append(dict(
                symbol=symbol,
                sessionDate=day,
                decisionTime=T.isoformat(),
                ownSymbol=symbol,
                otherSymbol=other_symbol,
                ownDirection=ctx.get(symbol.lower()),
                otherDirection=other_direction,
                ownReturn20=value(own,"u20"),
                ownClose=value(own,"close"),
                ownVWAP=value(own,"vwap"),
                ownER20=value(own,"er"),
                otherReturn20=value(other,"u20"),
                otherClose=value(other,"close"),
                otherVWAP=value(other,"vwap"),
                otherER20=value(other,"er"),
                components=components,
            ))

    def to_dict(self):
        out={}
        for strategy in self.strategies:
            item=dict(
                rejectReasons=dict(sorted(self.reject[strategy].items())),
                transitions=dict(sorted(self.transitions[strategy].items())),
                samplesByReason={k:v for k,v in sorted(self.samples[strategy].items())},
                sampleLimitPerReason=self.sample_limit,
            )
            if strategy=="ir3":
                item["clockCoverage"]=dict(sorted(self.clock.items()))
                item["directionFailureBreakdown"]=dict(
                    failuresObserved=sum(self.ir3_direction_signatures.values()),
                    componentCounts=dict(sorted(self.ir3_direction_components.items())),
                    componentCountsBySymbol={
                        symbol:dict(sorted(counts.items()))
                        for symbol,counts in sorted(self.ir3_direction_components_by_symbol.items())
                    },
                    signatureCounts=dict(sorted(self.ir3_direction_signatures.items())),
                    samplesBySignature={
                        signature:samples
                        for signature,samples in sorted(self.ir3_direction_samples.items())
                    },
                    sampleLimitPerSignature=self.sample_limit,
                    componentCountsOverlap=True,
                    requirements=dict(
                        ownReturn20Positive=True,
                        ownCloseAboveVWAP=True,
                        ownER20Min=self.ir3_er_trend_min,
                        otherIndexMustNotBeDown=True,
                    ),
                )
            out[strategy]=item
        return out
