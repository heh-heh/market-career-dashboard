"""Bounded V4 research diagnostics. Never affects strategy decisions or orders."""
from __future__ import annotations

from collections import Counter, defaultdict


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
