"""Opt-in observational waterfalls. No rule, indicator, or execution changes.

Each chain is a SINGLE decision in a SINGLE state, not a trade lifecycle.
Repeated waiting gates are observations, never terminal cancellations. Facets
are marginal (not a Cartesian product) and do not retain candles/timestamps.
"""
from collections import Counter

try:
    from .intraday_v4_engine import SEMI, benchmark
except ImportError:
    from intraday_v4_engine import SEMI, benchmark


def bucket(T):
    hm=T.strftime("%H:%M")
    for lo,hi in (("09:30","10:00"),("10:00","11:00"),("11:00","12:00"),
                  ("12:00","14:00"),("14:00","15:00"),("15:00","16:00")):
        if lo<=hm<hi: return lo+"–"+hi
    return "OUTSIDE_RTH"


class GateTrace:
    def __init__(self):
        self.chains={}

    def probe(self,event,T,ctx,scope=None,unit="decision_observation",sessions=None):
        scope=scope or event.state
        key=(event.strategy,scope,unit)
        chain=self.chains.setdefault(key,dict(inputs=0,order=[],gates={},facets={}))
        chain["inputs"]+=1
        facets=dict(year=str(T.year),symbol=event.symbol,timeOfDay=bucket(T),
                    regime=ctx.get("direction","UNKNOWN")+"/"+ctx.get("volatility","UNKNOWN"),
                    benchmark=benchmark(event.symbol),
                    marketConfirmation=str(ctx.get("qqq"))+"/"+str(ctx.get("spy")),
                    sector="SEMICONDUCTOR" if event.symbol in SEMI else "INDEX" if event.symbol in {"SPY","QQQ"} else "TECH")
        if event.symbol in SEMI:
            reference=(sessions or {}).get("SOXX")
            x=reference.snapshots.get(T) if reference else None
            facets["sectorConfirmation"]=("MISSING" if not x else "RETURN_UNAVAILABLE" if x.get("u20") is None
                else "NEGATIVE_RETURN" if x["u20"]<0 else "VWAP_UNAVAILABLE" if x.get("vwap") is None
                else "ABOVE_VWAP" if x["close"]>x["vwap"] else "BELOW_OR_AT_VWAP")
        else:
            facets["sectorConfirmation"]="NOT_REQUIRED"
        failed=False

        def check(name,passed,reason=None):
            nonlocal failed
            if failed:
                raise AssertionError("Waterfall must stop after first rejection")
            if name not in chain["gates"]:
                chain["order"].append(name)
                chain["gates"][name]=Counter()
            row=chain["gates"][name]
            row["input"]+=1; row["pass" if passed else "reject"]+=1
            if not passed and reason:
                chain.setdefault("reasons",Counter())[reason]+=1
            for facet,label in facets.items():
                group=chain["facets"].setdefault(facet,{}).setdefault(label,{})
                counts=group.setdefault(name,Counter())
                counts["input"]+=1; counts["pass" if passed else "reject"]+=1
                if not passed and reason: counts["reason:"+reason]+=1
            failed=not bool(passed)
            return passed
        return check

    def observe_context(self,event,T,ctx,sessions):
        """Independent existing context components; no new admission rule."""
        kind=event.strategy
        checks=dict(DIRECTION_DATA_AVAILABLE=ctx["direction"]!="UNKNOWN",
                    VOLATILITY_HISTORY_AVAILABLE=ctx["volatility"]!="UNKNOWN")
        if ctx["direction"]!="UNKNOWN":
            if kind=="ir1": checks["MARKET_DIRECTION"]=ctx["direction"]=="TREND_UP"
            elif kind=="ir2": checks["MARKET_DIRECTION"]=(ctx["direction"] in {"RANGE","MIXED"} if event.state in {"IDLE","SETUP"} else ctx["direction"]!="TREND_DOWN")
            else:
                own,other=(ctx["qqq"],ctx["spy"]) if event.symbol=="QQQ" else (ctx["spy"],ctx["qqq"])
                checks.update(OWN_DIRECTION=own=="UP",OTHER_NOT_DOWN=other!="DOWN")
        if ctx["volatility"]!="UNKNOWN":
            checks["VOLATILITY_FILTER"]=(ctx["volatility"] in {"NORMAL_VOL","HIGH_VOL"} if kind=="ir3" else ctx["volatility"]=="NORMAL_VOL")
        if kind=="ir1" and event.symbol in SEMI:
            ref=sessions.get("SOXX");x=ref.snapshots.get(T) if ref else None
            checks["SECTOR_FEATURES_AVAILABLE"]=bool(x and x.get("u20") is not None and x.get("vwap") is not None)
            if checks["SECTOR_FEATURES_AVAILABLE"]:
                checks.update(SECTOR_RETURN=x["u20"]>=0,SECTOR_ABOVE_VWAP=x["close"]>x["vwap"])
        for name,passed in checks.items():
            self.probe(event,T,ctx,event.state+"_CONTEXT_COMPONENT_"+name,sessions=sessions)("COMPONENT",passed)

    def as_dict(self):
        result={}
        for (strategy,scope,unit),chain in sorted(self.chains.items()):
            rows=[]
            for name in chain["order"]:
                c=chain["gates"][name]
                rows.append(dict(gate=name,input=c["input"],passed=c["pass"],rejected=c["reject"],
                    rejectPct=100*c["reject"]/c["input"] if c["input"] else None,
                    conversionFromGateInput=c["pass"]/c["input"] if c["input"] else None,
                    survivalFromChainInput=c["pass"]/chain["inputs"] if chain["inputs"] else None))
            result.setdefault(strategy,{})[scope]=dict(unit=unit,inputs=chain["inputs"],gates=rows,byFacet=chain["facets"],rejectReasons=chain.get("reasons",{}),
                relationship="INDEPENDENT_COMPONENT" if "_COMPONENT_" in scope else "CONDITIONAL_WATERFALL")
        return dict(version=1,strategies=result,
            semantics="CONDITIONAL_WATERFALL_PER_STATE; not one global lifecycle denominator",
            countsIncludeRepeatedWaits=True,terminalRejections="Use diagnostics.rejectReasons, not the sum of gate waits",
            facetSemantics="Independent marginal breakdowns; do not add facets together",
            conditionalGateWarning="Later gates are measured only when prior gates pass; unobserved failures are not inferred")
