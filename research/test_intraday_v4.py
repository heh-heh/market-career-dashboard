"""Tiny deterministic IR causal/regression tests; never production data/orders."""
import ast
import csv
import json
import math
import tempfile
import unittest
from collections import deque
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch

from research import intraday_v4_engine as e
from research.backtest_intraday_v4 import atomic_json,provisional_inspect_file,read_stream,run_sessions,main
from research.intraday_v4_metrics import summarize,chronological_folds

DAY=datetime(2024,6,3,9,30,tzinfo=e.NY)
def clock(h,m): return DAY.replace(hour=h,minute=m)
def bar(T,c=100,v=1000,low=None,high=None,o=None):
    return e.Bar(T-e.MINUTE,T,c if o is None else o,c+.05 if high is None else high,c-.05 if low is None else low,c,v)
def snap(c=100):
    return dict(close=c,vwap=c-.2,atr=1,u20=.01,u5=.001,er=.5,slope=.1,pvol=.5,va1=1,rvol=1.5)
def ctx(trend="TREND_UP",recovery=True):
    return dict(direction=trend,volatility="NORMAL_VOL",qqq="UP" if trend=="TREND_UP" else "OTHER",spy="OTHER",recovery=recovery)
def session(symbol="AAPL"):
    s=e.Session(symbol,DAY,clock(16,0),e.History())
    s.eligibility=dict(prevClose=100,dailyATR=2,adv20=100_000_000)
    return s
def references(T):
    out={s:session(s) for s in ("SPY","QQQ","SOXX")}
    for s in out.values(): s.snapshots[T]=snap()
    return out
def install(s,T,c,low=None,high=None,v=1000):
    b=bar(T,c,v,low,high)
    s.minutes[T]=b;s.snapshots[T]=snap(c)
    return b
def setup_ir2():
    s=session();s.or15=(100,101)
    install(s,clock(9,31),100)
    s.fives=[dict(bar=bar(clock(9,55),100.2),atr=1)]
    install(s,clock(10,0),100.2)
    event=e.Event("ir2","AAPL")
    refs=references(clock(10,0));refs["AAPL"]=s
    e.evaluate(event,s,refs,clock(10,0),ctx("MIXED"),True)
    s.fives.append(dict(bar=e.Bar(clock(9,55),clock(10,0),100.2,100.5,100,100.2,5000),atr=1))
    T=clock(10,5);install(s,T,99.8,99.6,100.3)
    s.fives.append(dict(bar=e.Bar(clock(10,0),T,100.2,100.3,99.6,99.8,5000),atr=1))
    e.evaluate(event,s,refs,T,ctx("MIXED"),True)
    return s,event,refs


class FeatureTests(unittest.TestCase):
    def test_1m_is_available_only_at_exclusive_end(self):
        s=session();b=bar(clock(9,31))
        s.observe(b)
        self.assertNotIn(b.start,s.snapshots)
        self.assertIn(b.end,s.snapshots)

    def test_5m_unavailable_until_0935(self):
        s=session()
        for n in range(1,5):
            s.observe(bar(DAY+n*e.MINUTE,100+n))
            self.assertEqual(s.fives,[])
        s.observe(bar(clock(9,35),105))
        self.assertEqual(s.fives[-1]["bar"].start,DAY)
        self.assertEqual(s.fives[-1]["bar"].end,clock(9,35))

    def test_missing_minute_does_not_build_5m(self):
        s=session()
        for n in (1,2,4,5): s.observe(bar(DAY+n*e.MINUTE))
        self.assertEqual(s.fives,[])
        self.assertIsNone(s.snapshots[clock(9,35)]["vwap"])

    def test_vwap_future_prefix_invariance(self):
        s=session();s.observe(bar(clock(9,31),100))
        earlier=dict(s.snapshots[clock(9,31)])
        s.observe(bar(clock(9,32),9999))
        self.assertEqual(s.snapshots[clock(9,31)],earlier)

    def test_previous20_volume_excludes_current(self):
        s=session()
        for n in range(1,22): s.observe(bar(DAY+n*e.MINUTE,100+n*.01,10 if n<=20 else 100))
        self.assertEqual(s.snapshots[DAY+21*e.MINUTE]["va1"],10)

    def test_returns_do_not_bridge_gap(self):
        s=session()
        for n in range(1,23):
            if n!=10:s.observe(bar(DAY+n*e.MINUTE,100+n*.01))
        self.assertIsNone(s.snapshots[DAY+22*e.MINUTE]["u20"])

    def test_or15_complete_and_frozen(self):
        s=session()
        for n in range(1,15):s.observe(bar(DAY+n*e.MINUTE,100+n*.01))
        self.assertIsNone(s.or15)
        s.observe(bar(clock(9,45),100.15));frozen=s.or15
        s.observe(bar(clock(9,46),900))
        self.assertEqual(s.or15,frozen)

    def test_opening_history_excludes_current_and_future_session(self):
        h=e.History();h.opening_returns=deque([.001*n for n in range(1,41)],maxlen=60)
        h.opening_volumes=deque([1000]*20,maxlen=20)
        s=e.Session("QQQ",DAY,clock(16,0),h)
        for n in range(1,31):s.observe(bar(DAY+n*e.MINUTE,100+n*.1,50))
        before=s.opening_stats()
        self.assertEqual(len(h.opening_returns),40)
        for n in range(31,36):s.observe(bar(DAY+n*e.MINUTE,9999,9999999))
        self.assertEqual(s.opening_stats(),before)

    def test_missing_benchmark_endpoint_never_future_fills(self):
        s=session();r=session("QQQ");T=clock(10,30)
        install(s,T,103);s.snapshots[T]["u20"]=.04
        s.minutes[T-20*e.MINUTE]=bar(T-20*e.MINUTE,100)
        r.snapshots[T+e.MINUTE]=snap()
        self.assertIsNone(e.rs20a(s,r,T,1))
        r.snapshots[T]=snap()
        self.assertAlmostEqual(e.rs20a(s,r,T,1),3)

    def test_mapping_is_explicit_no_smh_substitution(self):
        self.assertEqual(e.benchmark("NVDA"),"SOXX")
        self.assertEqual(e.benchmark("AAPL"),"QQQ")
        with self.assertRaises(ValueError):e.benchmark("TQQQ")

    def test_zero_denominators_are_unavailable(self):
        self.assertIsNone(e.ratio(1,0));self.assertIsNone(e.percentile(1,[]))


class StrategyTests(unittest.TestCase):
    def test_ir1_impulse_pullback_freezes_boundary_and_triggers_later(self):
        T=clock(10,15);s=session();refs=references(T);refs["AAPL"]=s
        for n in range(9):
            end=clock(9,35)+5*n*e.MINUTE
            c=102 if n<8 else 104
            o=102 if n!=6 else 102
            b=e.Bar(end-5*e.MINUTE,end,o,103 if n<6 else max(c,103)+.01,101.9,c,5000)
            s.fives.append(dict(bar=b,atr=.5))
        install(s,T,104);s.snapshots[T]["u20"]=.04
        s.minutes[T-20*e.MINUTE]=bar(T-20*e.MINUTE,102)
        event=e.Event("ir1","AAPL")
        e.evaluate(event,s,refs,T,ctx(),True)
        self.assertEqual(event.state,"SETUP");self.assertEqual(event.fields["A"],.5)
        T=clock(10,20);install(s,T,103.8,103.4,103.9)
        s.snapshots[T]["u20"]=.04;s.minutes[T-20*e.MINUTE]=bar(T-20*e.MINUTE,102)
        refs["QQQ"].snapshots[T]=snap()
        s.fives.append(dict(bar=e.Bar(clock(10,15),T,103.8,103.9,103.4,103.8,3000),atr=9))
        e.evaluate(event,s,refs,T,ctx(),True)
        self.assertEqual(event.state,"ARMED")
        self.assertAlmostEqual(event.fields["B"],103.95)
        frozen=event.fields["B"]
        T=clock(10,21);install(s,T,104,103.5,105)
        s.snapshots[T]["u20"]=.04;s.minutes[T-20*e.MINUTE]=bar(T-20*e.MINUTE,102)
        refs["QQQ"].snapshots[T]=snap()
        e.evaluate(event,s,refs,T,ctx(),True)
        self.assertEqual(event.state,"TRIGGERED");self.assertEqual(event.fields["B"],frozen)
        self.assertAlmostEqual(event.fields["volumeContraction"],.6)

    def test_ir2_first_bar_after_breakdown_valid(self):
        s,event,refs=setup_ir2();self.assertEqual(event.state,"ARMED")
        T=clock(10,10);install(s,T,100.2,99.6,100.3)
        s.fives.append(dict(bar=e.Bar(clock(10,5),T,99.8,100.3,99.6,100.2,5000),atr=1))
        e.evaluate(event,s,refs,T,ctx("MIXED"),True)
        self.assertEqual(event.state,"TRIGGERED");self.assertEqual(event.fields["reclaimCount"],1)

    def test_ir2_second_bar_after_breakdown_valid(self):
        s,event,refs=setup_ir2()
        for m,c in ((10,99.9),(15,100.2)):
            T=clock(10,m);install(s,T,c,99.6,max(100,c))
            s.fives.append(dict(bar=e.Bar(T-5*e.MINUTE,T,99.9,max(100,c),99.6,c,5000),atr=1))
            e.evaluate(event,s,refs,T,ctx("MIXED"),True)
        self.assertEqual(event.state,"TRIGGERED");self.assertEqual(event.fields["reclaimCount"],2)

    def test_ir2_third_reclaim_is_invalid(self):
        s,event,refs=setup_ir2()
        for m,c in ((10,99.9),(15,99.9),(20,100.2)):
            T=clock(10,m);install(s,T,c,99.6,max(100,c))
            s.fives.append(dict(bar=e.Bar(T-5*e.MINUTE,T,99.9,max(100,c),99.6,c,5000),atr=1))
            e.evaluate(event,s,refs,T,ctx("MIXED"),True)
        self.assertEqual(event.state,"CANCELLED");self.assertEqual(event.fields["reason"],"RECLAIM_EXPIRED")

    def test_ir2_reclaim_without_recovery_can_wait_second(self):
        s,event,refs=setup_ir2()
        for m,recovery in ((10,False),(15,True)):
            T=clock(10,m);install(s,T,100.2,99.6,100.3)
            s.fives.append(dict(bar=e.Bar(T-5*e.MINUTE,T,100,100.3,99.6,100.2,5000),atr=1))
            e.evaluate(event,s,refs,T,ctx("MIXED",recovery),True)
        self.assertEqual(event.state,"TRIGGERED")

    def test_ir2_any_1m_new_low_cancels_before_reclaim(self):
        s,event,refs=setup_ir2();T=clock(10,6);install(s,T,100.4,99.59,100.5)
        e.evaluate(event,s,refs,T,ctx("MIXED"),True)
        self.assertEqual(event.fields["reason"],"NEW_LOWER_LOW")

    def test_ir3_1530_signal_uses_only_completed_prefix_at_1530(self):
        s=session("QQQ");h=s.history
        h.opening_returns=deque([.001*(n+1) for n in range(40)],maxlen=60)
        h.opening_volumes=deque([1000]*20,maxlen=20)
        for n in range(1,31):s.observe(bar(DAY+n*e.MINUTE,100+n*.2,50))
        T=clock(15,30);install(s,T,110)
        s.fives=[dict(bar=bar(clock(15,25),110),atr=1),dict(bar=bar(T,110),atr=999)]
        event=e.Event("ir3","QQQ");refs=references(T);refs["QQQ"]=s
        e.evaluate(event,s,refs,T,ctx(),True)
        self.assertEqual(event.state,"TRIGGERED");self.assertEqual(event.fields["A"],1)
        self.assertEqual(event.fields["signalTimestamp"],T.isoformat())
        # The 15:30 START candle completes at 15:31; no later revision is legal.
        frozen=dict(event.fields)
        install(s,T+e.MINUTE,900)
        e.evaluate(event,s,refs,T+e.MINUTE,ctx(),True)
        self.assertEqual(event.fields,frozen)

    def test_terminal_attempt_never_rearms(self):
        s,event,refs=setup_ir2();event.move("CANCELLED",clock(10,6),"TEST")
        e.evaluate(event,s,refs,clock(10,5),ctx("MIXED"),True)
        self.assertEqual(event.state,"CANCELLED")


class ExecutionTests(unittest.TestCase):
    def entered(self,strategy="ir1",symbol="AAPL",slip=2):
        T=clock(10,30) if strategy!="ir3" else clock(15,30)
        s=session(symbol);refs=references(T+e.MINUTE);refs[symbol]=s
        install(s,T+e.MINUTE,100);s.minutes[T-19*e.MINUTE]=bar(T-19*e.MINUTE,100)
        s.snapshots[T+e.MINUTE]["u20"]=.02
        event=e.Event(strategy,symbol);event.move("SETUP",T,A=1)
        event.move("ARMED",T,B=99.5,L_arm=99,ORL=99,failureLow=99,stop=98.9,target=None)
        event.move("TRIGGERED",T,signalTimestamp=T.isoformat(),triggerClose=100)
        book=e.Book(strategy,slip)
        return book,event,s,refs,T

    def test_no_same_signal_bar_fill_and_next_observable_close_used(self):
        book,event,s,refs,T=self.entered()
        self.assertFalse(book.enter(event,bar(T,100,o=1),T,ctx(),s,refs))
        b=bar(T+e.MINUTE,100,o=1)
        self.assertTrue(book.enter(event,b,T+e.MINUTE,ctx(),s,refs))
        self.assertAlmostEqual(book.position["entryPrice"],100.03)
        self.assertEqual(book.position["entryMarketPrice"],100)
        self.assertIsNone(book.position["entryLatencySeconds"])

    def test_missing_next_minute_and_latency_cancel(self):
        book,event,s,refs,T=self.entered()
        self.assertFalse(book.enter(event,bar(T+2*e.MINUTE),T+2*e.MINUTE,ctx(),s,refs))
        self.assertEqual(event.fields["reason"],"ENTRY_GAP_OR_LATENCY")

    def test_gap_stop_worse_close_and_cost_both_sides(self):
        book,event,s,refs,T=self.entered()
        book.enter(event,bar(T+e.MINUTE),T+e.MINUTE,ctx(),s,refs)
        done=book.manage(T+2*e.MINUTE,bar(T+2*e.MINUTE,96),clock(15,50))
        self.assertAlmostEqual(done["exitPrice"],96*(1-.0003))
        self.assertLess(done["exitPrice"],done["initialStop"])
        self.assertEqual(done["exitReason"],"HARD_STOP")

    def test_intrabar_touch_not_ideal_fill(self):
        book,event,s,refs,T=self.entered();book.enter(event,bar(T+e.MINUTE),T+e.MINUTE,ctx(),s,refs)
        self.assertIsNone(book.manage(T+2*e.MINUTE,bar(T+2*e.MINUTE,100,low=1,high=900),clock(15,50)))

    def test_trailing_updates_after_stop_check_and_never_loosens(self):
        book,event,s,refs,T=self.entered();book.enter(event,bar(T+e.MINUTE),T+e.MINUTE,ctx(),s,refs)
        book.manage(T+2*e.MINUTE,bar(T+2*e.MINUTE,102),clock(15,50))
        self.assertTrue(book.position["trailActivated"]);self.assertEqual(book.position["stopPrice"],101)
        done=book.manage(T+3*e.MINUTE,bar(T+3*e.MINUTE,100.5),clock(15,50))
        self.assertEqual(done["exitReason"],"TRAIL_STOP")

    def test_outage_preserves_cash_no_fill_and_recovery_flag(self):
        book,event,s,refs,T=self.entered();book.enter(event,bar(T+e.MINUTE),T+e.MINUTE,ctx(),s,refs)
        cash=book.cash
        self.assertIsNone(book.manage(T+2*e.MINUTE,None,clock(15,50)))
        self.assertEqual(book.cash,cash);self.assertEqual(book.position["dataStatus"],"DATA_STALE")
        done=book.manage(T+3*e.MINUTE,bar(T+3*e.MINUTE,95),clock(15,50))
        self.assertTrue(done["exitAffectedByDataStale"])

    def test_stall_exit_waits_one_further_observation(self):
        book,event,s,refs,T=self.entered();book.enter(event,bar(T+e.MINUTE),T+e.MINUTE,ctx(),s,refs)
        at=T+21*e.MINUTE
        self.assertIsNone(book.manage(at,bar(at),clock(15,50)))
        self.assertTrue(book.position["stallPending"])
        done=book.manage(at+e.MINUTE,bar(at+e.MINUTE),clock(15,50))
        self.assertEqual(done["exitReason"],"STALL_STOP")

    def test_ir3_force_cutoff_and_no_trailing(self):
        book,event,s,refs,T=self.entered("ir3","QQQ")
        book.enter(event,bar(T+e.MINUTE),T+e.MINUTE,ctx(),s,refs)
        book.manage(T+2*e.MINUTE,bar(T+2*e.MINUTE,105),clock(15,50))
        self.assertFalse(book.position["trailActivated"])
        done=book.manage(clock(15,50),bar(clock(15,50),104),clock(15,50))
        self.assertEqual(done["exitReason"],"SESSION_EXIT")

    def test_state_books_are_isolated(self):
        a,b=e.Book("ir1"),e.Book("ir2")
        a.cash=42
        self.assertEqual(b.cash,10000);self.assertIsNone(b.position)

    def test_runner_freezes_setup_priority_when_later_queue_score_is_missing(self):
        from research import backtest_intraday_v4 as runner
        opening,closing=clock(10,14),clock(10,20)
        symbols={"AAPL","QQQ","SPY"}
        rows={s:{(clock(10,m)-e.MINUTE):bar(clock(10,m)) for m in range(15,21)} for s in symbols}
        calls={"n":0}
        def queue(*args,**kwargs):
            calls["n"]+=1
            return ({"AAPL":50},["AAPL"]) if calls["n"]==1 else ({},["AAPL"])
        def signal(event,session,sessions,T,context,queued):
            if event.state=="IDLE":
                event.move("SETUP",T,A=1,stop=98.9,B=99,L_arm=98)
            elif event.state=="SETUP":
                event.move("ARMED",T,B=99,L_arm=98,stop=98.9)
                event.move("TRIGGERED",T,signalTimestamp=T.isoformat(),triggerClose=100)
        with patch.object(e.History,"eligibility",return_value=dict(prevClose=100,dailyATR=2,adv20=1e8)), \
             patch.object(runner,"context",return_value=ctx()),patch.object(e,"allowed_context",return_value=True), \
             patch.object(e,"rs20a",return_value=.5),patch.object(runner,"evaluate",side_effect=signal), \
             patch.object(runner,"candidate_queue",side_effect=queue):
            trades,funnel,unresolved,days,overlap=run_sessions(iter([(DAY.date().isoformat(),rows)]),
                {DAY.date().isoformat():(opening,closing)},symbols,["ir1"],entry_symbols={"AAPL"})
        self.assertGreaterEqual(funnel["signals"],1)
        self.assertNotIn("PRIORITY_UNAVAILABLE",funnel)

    def test_runner_day_strategy_capacity_and_duplicate_isolation(self):
        # Six minutes / four assets. Stub signal prerequisites only; exercise
        # real pending, fills, per-book capacity, terminal state and liquidation.
        from research import backtest_intraday_v4 as runner
        opening,closing=clock(10,14),clock(10,30)
        symbols={"AAPL","MSFT","QQQ","SPY"}
        rows={s:{(clock(10,m)-e.MINUTE):bar(clock(10,m)) for m in range(15,21)} for s in symbols}
        def signal(event,session,sessions,T,context,queued):
            if event.state!="IDLE":return
            event.move("SETUP",T,A=1)
            event.move("ARMED",T,B=99,L_arm=98,stop=98.9,ORL=98,failureLow=98,target=102 if event.strategy=="ir2" else None)
            event.move("TRIGGERED",T,signalTimestamp=T.isoformat(),triggerClose=100)
        with patch.object(e.History,"eligibility",return_value=dict(prevClose=100,dailyATR=2,adv20=1e8)), \
             patch.object(runner,"context",return_value=ctx()),patch.object(e,"allowed_context",return_value=True), \
             patch.object(e,"rs20a",return_value=.5),patch.object(runner,"evaluate",side_effect=signal), \
             patch.object(runner,"candidate_queue",return_value=({"AAPL":50,"MSFT":50},["AAPL","MSFT"])):
            trades,funnel,unresolved,days,overlap=run_sessions(iter([(DAY.date().isoformat(),rows)]),
                {DAY.date().isoformat():(opening,closing)},symbols,["ir1","ir2"],entry_symbols={"AAPL","MSFT"})
        self.assertEqual(len(trades),2)
        self.assertEqual({t["strategy"] for t in trades},{"ir1","ir2"})
        self.assertTrue(all(t["symbol"]=="AAPL" for t in trades))
        self.assertEqual(funnel["CAPACITY"],2)
        self.assertEqual(unresolved,[])


class ResultsTests(unittest.TestCase):
    def test_metrics_chronological_tied_drawdown_and_zero_loss_pf(self):
        def t(ts,r):return dict(returnPct=r,netR=r,pnlUsd=r,exitTimestamp=ts,holdDurationSeconds=60)
        trades=[t("b",-2),t("a",3),t("b",1)]
        m=summarize(trades)
        self.assertEqual(m["maxDrawdownR"],1)
        self.assertAlmostEqual(m["expectancyR"],2/3)
        self.assertEqual(m["profitFactorR"],2)
        m=summarize([t("a",1)])
        self.assertIsNone(m["profitFactor"]);self.assertTrue(m["profitFactorInfinite"])

    def test_folds_chronological_and_insufficient_marked(self):
        days=[f"{y}-{m:02d}-01" for y in range(2021,2025) for m in range(1,13)]
        f=chronological_folds([],days)
        self.assertGreaterEqual(len(f["folds"]),3)
        for x in f["folds"]:self.assertEqual(x["trainPeriod"][1],x["validationPeriod"][0]);self.assertEqual(x["validationPeriod"][1],x["oosPeriod"][0])
        self.assertEqual(chronological_folds([],days[:6])["status"],"NOT_EVALUABLE")

    def test_atomic_state_display_survives_new_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"state.json";atomic_json(p,dict(progress=17,phase="running"))
            self.assertEqual(json.loads(p.read_text())["progress"],17)
            self.assertFalse(list(Path(tmp).glob("*.tmp")))

    def test_stream_disorder_fails_no_arbitrary_sort(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"AAPL.csv"
            with p.open("w",newline="") as f:
                w=csv.writer(f);w.writerow(["timestamp","open","high","low","close","volume"])
                for m in (32,31):w.writerow([clock(9,m).isoformat(),100,101,99,100,10])
            with self.assertRaisesRegex(ValueError,"out-of-order"):
                list(read_stream(p,"AAPL",dict(naive_timezone=None)))

    def test_research_modules_have_no_broker_or_network_imports(self):
        for name in ("backtest_intraday_v4.py","intraday_v4_engine.py","intraday_v4_metrics.py"):
            source=(Path(__file__).parent/name).read_text();tree=ast.parse(source)
            imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
            self.assertFalse(any(x in {"live_trader","paper_trader","simple_momentum_v1","trading","urllib.request","requests"} for x in imports))
            self.assertNotIn("/api/v1/orders",source)

    def test_provisional_streaming_audit_detects_duplicates_without_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"AAPL.csv"
            with p.open("w",newline="") as f:
                w=csv.writer(f);w.writerow(["timestamp","open","high","low","close","volume"])
                t=clock(9,31).isoformat()
                w.writerow([t,100,101,99,100,10])
                w.writerow([t,100,101,99,100,10])
                w.writerow([clock(9,32).isoformat(),100,101,99,100,10])
            meta=provisional_inspect_file(p,"AAPL")
            self.assertEqual(meta["auditMode"],"streaming_bounded_memory")
            self.assertEqual(meta["duplicates"],1)
            self.assertEqual(meta["exactDuplicates"],1)
            self.assertEqual(meta["conflictingDuplicates"],0)
            self.assertEqual(meta["validationErrors"],[])

    def test_tiny_cli_provisional_smoke_without_reviewed_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/"data";data.mkdir()
            for symbol in ("SPY","QQQ"):
                with (data/(symbol+".csv")).open("w",newline="") as f:
                    w=csv.writer(f);w.writerow(["timestamp","open","high","low","close","volume"])
                    for n in range(35):w.writerow([(DAY+n*e.MINUTE).isoformat(),100,101,99,100,10])
            argv=["--strategy","ir3","--data-dir",str(data),"--provisional"]
            for option in ("out","state","log","trades-csv","data-audit","decisions"):
                argv += ["--"+option,str(root/(option+".artifact"))]
            with patch("research.backtest_intraday_v4.inspect_file",side_effect=AssertionError("SQLite audit must not run in provisional mode")):
                self.assertEqual(main(argv),0)
            result=json.loads((root/"out.artifact").read_text())
            self.assertEqual(result["researchVerdict"],"PROVISIONAL_UNREVIEWED_DATA")
            self.assertTrue(result["configuration"]["provisional"])
            self.assertEqual(result["configuration"]["dataMode"],"PROVISIONAL_UNREVIEWED_DATA")
            self.assertIsNone(result["data"]["manifestSha256"])
            self.assertIn("PROVISIONAL_UNREVIEWED_DATA",result["data"]["warnings"])
            audit=json.loads((root/"data-audit.artifact").read_text())
            self.assertTrue(audit["validationPassed"])
            self.assertTrue(audit["provisional"])

    def test_tiny_cli_smoke_audits_inputs_and_writes_engine_artifacts(self):
        from build_v4_data_manifest import build_manifest
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/"data";data.mkdir()
            for symbol in ("SPY","QQQ"):
                with (data/(symbol+".csv")).open("w",newline="") as f:
                    w=csv.writer(f);w.writerow(["timestamp","open","high","low","close","volume"])
                    for n in range(35):w.writerow([(DAY+n*e.MINUTE).isoformat(),100,101,99,100,10])
            manifest=build_manifest(data,["SPY","QQQ"],"start","split-adjusted",verified_by="synthetic-test",
                verification_note="Fabricated small fixture only; not production evidence",confirm_split_consistency=True,
                confirm_point_in_time_eligibility=True,confirm_symbol_identity=True)
            self.assertTrue(manifest["validationPassed"])
            mpath=root/"manifest.json";atomic_json(mpath,manifest)
            argv=["--strategy","ir3","--data-dir",str(data),"--manifest",str(mpath)]
            for option in ("out","state","log","trades-csv","data-audit","decisions"):
                argv += ["--"+option,str(root/(option+".artifact"))]
            self.assertEqual(main(argv),0)
            result=json.loads((root/"out.artifact").read_text())
            self.assertEqual(result["engine"],"v4-ir3")
            self.assertEqual(result["overall"]["trades"],0)
            self.assertEqual(json.loads((root/"state.artifact").read_text())["progress"],100)
            audit=json.loads((root/"data-audit.artifact").read_text())
            self.assertGreater(audit["symbols"]["QQQ"]["missingRegularMinutes"],0)
            self.assertTrue((root/"trades-csv.artifact").exists())


if __name__=="__main__":unittest.main()
