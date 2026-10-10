"""Regression tests for V4 context-rejection diagnostics."""
import unittest
from collections import deque
from datetime import datetime

from research import intraday_v4_engine as e
from research.intraday_v4_diagnostics import DiagnosticCollector

DAY=datetime(2024,6,3,9,30,tzinfo=e.NY)
def clock(h,m): return DAY.replace(hour=h,minute=m)

def session(symbol):
    s=e.Session(symbol,DAY,clock(16,0),e.History())
    s.eligibility=dict(prevClose=100,dailyATR=2,adv20=100_000_000)
    return s

def install(s,T):
    b=e.Bar(T-e.MINUTE,T,100,101,99,100,1000)
    s.minutes[T]=b
    s.snapshots[T]=dict(close=100,vwap=99,atr=1,u20=.01,u5=.001,er=.5,slope=.1,pvol=.5,va1=1,rvol=1.5)
    return b

def context(direction="TREND_UP",volatility="NORMAL_VOL",qqq="UP",spy="OTHER"):
    return dict(direction=direction,volatility=volatility,recovery=True,qqq=qqq,spy=spy)


class ClockReasonTests(unittest.TestCase):
    def test_missing_target_completed_bar_is_atomic(self):
        T=clock(15,30)
        q=session("QQQ"); p=session("SPY")
        install(p,T)
        sessions={"QQQ":q,"SPY":p}
        ev=e.Event("ir3","QQQ")
        reason=e.idle_rejection_reason("ir3",ev,q,sessions,T,context(),True)
        self.assertEqual(reason,"CLOCK_TARGET_BAR_MISSING")

    def test_normal_context_filter_is_not_reported_as_missing_context(self):
        T=clock(15,30)
        q=session("QQQ"); p=session("SPY")
        install(q,T);install(p,T)
        sessions={"QQQ":q,"SPY":p}
        ev=e.Event("ir3","QQQ")
        c=context(direction="MIXED",qqq="OTHER",spy="OTHER")
        self.assertFalse(e.allowed_context("ir3","QQQ",c,sessions,T,"IDLE"))
        self.assertEqual(e.idle_rejection_reason("ir3",ev,q,sessions,T,c,True),"CLOCK_DIRECTION_FILTER_FAILED")

    def test_unknown_regime_is_separate_from_direction_filter(self):
        T=clock(15,30)
        q=session("QQQ"); p=session("SPY")
        install(q,T);install(p,T)
        sessions={"QQQ":q,"SPY":p}
        ev=e.Event("ir3","QQQ")
        c=context(volatility="UNKNOWN")
        self.assertEqual(e.idle_rejection_reason("ir3",ev,q,sessions,T,c,True),"REGIME_HISTORY_UNAVAILABLE")

    def test_ir1_sector_failure_has_its_own_reason(self):
        T=clock(11,0)
        a=session("NVDA");q=session("QQQ");p=session("SPY");soxx=session("SOXX")
        for s in (a,q,p,soxx): install(s,T)
        soxx.snapshots[T]["u20"]=-.01
        sessions={"NVDA":a,"QQQ":q,"SPY":p,"SOXX":soxx}
        c=context()
        self.assertEqual(e.context_rejection_reason("ir1","NVDA",c,sessions,T,"SETUP"),"SECTOR_CONTEXT_FAILED")


class CollectorTests(unittest.TestCase):
    def test_samples_are_bounded(self):
        d=DiagnosticCollector(["ir3"],sample_limit=2)
        T=clock(15,30)
        for i in range(5):
            ev=e.Event("ir3","QQQ")
            ev.move("CANCELLED",T,"CLOCK_DIRECTION_FILTER_FAILED")
            d.record_rejection(ev,"2024-06-03",T)
        out=d.to_dict()["ir3"]
        self.assertEqual(out["rejectReasons"]["CLOCK_DIRECTION_FILTER_FAILED"],5)
        self.assertEqual(len(out["samplesByReason"]["CLOCK_DIRECTION_FILTER_FAILED"]),2)

    def test_clock_coverage_counts_completed_input_not_future_bar(self):
        d=DiagnosticCollector(["ir3"])
        T=clock(15,30)
        q=session("QQQ");p=session("SPY")
        install(q,T);install(p,T)
        d.observe_ir3_clock("QQQ","2024-06-03",T,q,{"QQQ":q,"SPY":p},context())
        cov=d.to_dict()["ir3"]["clockCoverage"]
        self.assertEqual(cov["targetCompletedBarAvailable"],1)
        self.assertEqual(cov["benchmarkContextAvailable"],1)

    def test_ir3_direction_breakdown_counts_overlapping_components(self):
        d=DiagnosticCollector(["ir3"],sample_limit=2)
        T=clock(15,30)
        q=session("QQQ");p=session("SPY")
        install(q,T);install(p,T)
        q.snapshots[T]["u20"]=-.01
        q.snapshots[T]["vwap"]=101
        q.snapshots[T]["er"]=.2
        p.snapshots[T]["u20"]=-.01
        p.snapshots[T]["vwap"]=101
        p.snapshots[T]["er"]=.5
        sessions={"QQQ":q,"SPY":p}
        c=context(direction="MIXED",qqq="OTHER",spy="DOWN")
        d.observe_ir3_direction_failure("QQQ","2024-06-03",T,sessions,c,.4)
        out=d.to_dict()["ir3"]["directionFailureBreakdown"]
        self.assertEqual(out["failuresObserved"],1)
        self.assertEqual(out["componentCounts"]["OWN_U20_NONPOSITIVE"],1)
        self.assertEqual(out["componentCounts"]["OWN_NOT_ABOVE_VWAP"],1)
        self.assertEqual(out["componentCounts"]["OWN_ER_BELOW_TREND_MIN"],1)
        self.assertEqual(out["componentCounts"]["OTHER_INDEX_DOWN"],1)
        signature="OWN_U20_NONPOSITIVE+OWN_NOT_ABOVE_VWAP+OWN_ER_BELOW_TREND_MIN+OTHER_INDEX_DOWN"
        self.assertEqual(out["signatureCounts"][signature],1)
        self.assertEqual(out["requirements"]["ownER20Min"],.4)

    def test_ir3_direction_breakdown_samples_are_bounded(self):
        d=DiagnosticCollector(["ir3"],sample_limit=1)
        T=clock(15,30)
        q=session("QQQ");p=session("SPY")
        install(q,T);install(p,T)
        q.snapshots[T]["er"]=.2
        sessions={"QQQ":q,"SPY":p}
        c=context(direction="MIXED",qqq="OTHER",spy="OTHER")
        for day in ("2024-06-03","2024-06-04"):
            d.observe_ir3_direction_failure("QQQ",day,T,sessions,c,.4)
        out=d.to_dict()["ir3"]["directionFailureBreakdown"]
        self.assertEqual(out["failuresObserved"],2)
        self.assertEqual(out["componentCounts"]["OWN_ER_BELOW_TREND_MIN"],2)
        self.assertEqual(len(out["samplesBySignature"]["OWN_ER_BELOW_TREND_MIN"]),1)


if __name__=="__main__":
    unittest.main()
