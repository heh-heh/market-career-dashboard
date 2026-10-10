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
            out[strategy]=item
        return out
