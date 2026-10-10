"""Small deterministic clock/context diagnosis tests, no broker/data download."""
import csv
import json
import math
import tempfile
import unittest
import zipfile
from collections import deque
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from research import intraday_v4_engine as e
from research import backtest_intraday_v4 as runner
from research.intraday_v4_diagnostics import (Diagnostics,context_reason,eligibility_reason,
        feature_reason,idle_gate_reason,snapshot_evidence)
from research.audit_v4_clock_context import audit_data,scan_file,coverage_record,artifact_summary
from research.backtest_v4_strategies import exchange_sessions,normalize_minute_timestamp


def seeded_history():
    h=e.History()
    h.daily.extend([dict(close=100,volume=2_000_000,tr=2)]*61)
    h.tr5.extend([.5]*14)
    h.opening_returns.extend([.001*(-1 if n%2 else 1) for n in range(60)])
    h.opening_volumes.extend([30_000]*20)
    for k in range(1,391):
        h.rv[k]=deque([.0001+n*.00005 for n in range(60)],maxlen=60)
        h.volumes[k]=deque([k*1000]*20,maxlen=20)
    return h


def full_session(symbol="QQQ",date="2024-06-03",skip=(),flat=False):
    opening,closing=exchange_sessions(date,date)[date]
    s=e.Session(symbol,opening,closing,seeded_history())
    target=opening.replace(hour=15,minute=30)
    for n in range(1,int((min(closing,target)-opening).total_seconds()/60)+1):
        T=opening+n*e.MINUTE
        if T-e.MINUTE in skip: continue
        c=100 if flat else 100+n*.025
        b=e.Bar(T-e.MINUTE,T,c-.02,c+.25,c-.25,c,2200)
        s.observe(b)
    return s


def market(date="2024-06-03",qskip=(),pskip=()):
    return dict(QQQ=full_session("QQQ",date,qskip),SPY=full_session("SPY",date,pskip))


class ClockContextTests(unittest.TestCase):
    def test_full_session_clock_context_available_and_triggered(self):
        sessions=market();s=sessions["QQQ"];T=s.opening.replace(hour=15,minute=30)
        ctx=e.context(sessions,T);ev=e.Event("ir3","QQQ")
        self.assertEqual(idle_gate_reason(ev,s,sessions,T,ctx,True),(None,None))
        e.evaluate(ev,s,sessions,T,ctx,True)
        self.assertEqual(ev.state,"TRIGGERED")
        self.assertEqual(ev.fields["signalTimestamp"],T.isoformat())
        self.assertEqual(s.minutes[T].start,T-e.MINUTE)
        self.assertNotIn(T+e.MINUTE,s.snapshots)

    def test_complete_data_expected_direction_filter_not_context_unavailable(self):
        sessions=market();T=sessions["QQQ"].closing.replace(hour=15,minute=30)
        ctx=e.context(sessions,T);ctx.update(qqq="OTHER",direction="MIXED")
        self.assertEqual(context_reason("ir3","QQQ",ctx,sessions,T,"IDLE")[0],"OWN_DIRECTION_FILTER")
        self.assertFalse(e.allowed_context("ir3","QQQ",ctx,sessions,T,"IDLE"))
        reason,_=idle_gate_reason(e.Event("ir3","QQQ"),sessions["QQQ"],sessions,T,ctx,True)
        self.assertEqual(reason,"CLOCK_OWN_DIRECTION_FILTER")

    def test_missing_1530_start_is_not_missing_decision_context(self):
        # START 15:30 is intentionally still unobserved at a 15:30 decision.
        sessions=market();s=sessions["QQQ"];T=s.opening.replace(hour=15,minute=30)
        self.assertNotIn(T+e.MINUTE,s.minutes)
        self.assertIsNone(idle_gate_reason(e.Event("ir3","QQQ"),s,sessions,T,e.context(sessions,T),True)[0])

    def test_missing_target_start_1529_is_atomic(self):
        opening,_=exchange_sessions("2024-06-03","2024-06-03")["2024-06-03"]
        target=opening.replace(hour=15,minute=29)
        sessions=market(qskip=(target,));T=target+e.MINUTE
        reason,_=idle_gate_reason(e.Event("ir3","QQQ"),sessions["QQQ"],sessions,T,e.context(sessions,T),True)
        self.assertEqual(reason,"CLOCK_TARGET_BAR_MISSING")

    def test_benchmark_target_missing_no_future_fill(self):
        opening,_=exchange_sessions("2024-06-03","2024-06-03")["2024-06-03"]
        target=opening.replace(hour=15,minute=29);sessions=market(pskip=(target,));T=target+e.MINUTE
        future=e.Bar(T,T+e.MINUTE,110,111,109,110,1000)
        sessions["SPY"].observe(future)
        self.assertEqual(context_reason("ir3","QQQ",e.context(sessions,T),sessions,T,"IDLE"),("BENCHMARK_BAR_MISSING","SPY"))
        self.assertNotIn(T,sessions["SPY"].snapshots)

    def test_one_early_gap_invalidates_prefix_even_with_clock_bar(self):
        opening,_=exchange_sessions("2024-06-03","2024-06-03")["2024-06-03"]
        sessions=market(pskip=(opening+80*e.MINUTE,));T=opening.replace(hour=15,minute=30)
        r,ref=context_reason("ir3","QQQ",e.context(sessions,T),sessions,T,"IDLE")
        self.assertEqual((r,ref),("BENCHMARK_SESSION_PREFIX_INCOMPLETE","SPY"))
        self.assertIsNotNone(sessions["SPY"].first_prefix_gap)

    def test_empty_history_distinct_from_incomplete_daily(self):
        s=full_session();s.history.daily.clear();s.eligibility=None
        self.assertEqual(eligibility_reason(s),"DAILY_HISTORY_INSUFFICIENT")
        s.history.daily.extend([dict(close=100,volume=2e6,tr=2)]*60+[None])
        self.assertEqual(eligibility_reason(s),"DAILY_SESSION_INCOMPLETE")

    def test_incomplete_daily_disables_61_future_sessions_without_relaxation(self):
        h=seeded_history();good=dict(close=100,volume=2e6,tr=2)
        h.daily.append(None)
        for _ in range(60):
            self.assertIsNone(h.eligibility());h.daily.append(good)
        self.assertIsNone(h.eligibility());h.daily.append(good)
        self.assertIsNotNone(h.eligibility())

    def test_volatility_history_separate_from_market_direction(self):
        sessions=market();T=sessions["QQQ"].opening.replace(hour=15,minute=30)
        sessions["SPY"].snapshots[T]["pvol"]=None
        self.assertEqual(context_reason("ir3","QQQ",e.context(sessions,T),sessions,T,"IDLE"),("VOLATILITY_HISTORY_INSUFFICIENT","SPY"))

    def test_ir1_ir2_market_volatility_sector_atomic(self):
        sessions=market();T=sessions["QQQ"].opening.replace(hour=15,minute=30)
        ctx=e.context(sessions,T)
        self.assertEqual(context_reason("ir1","AAPL",dict(ctx,direction="MIXED"),sessions,T,"SETUP")[0],"MARKET_DIRECTION_FILTER")
        self.assertEqual(context_reason("ir2","AAPL",dict(ctx,volatility="HIGH_VOL"),sessions,T,"SETUP")[0],"VOLATILITY_FILTER")
        self.assertEqual(context_reason("ir1","NVDA",ctx,sessions,T,"SETUP"),("SECTOR_MISSING","SOXX"))
        # Exact false boolean equivalence and stage-specific IR2 trend gating.
        for kind,symbol in (("ir1","AAPL"),("ir2","AAPL"),("ir3","QQQ")):
            for stage in ("IDLE","SETUP","ARMED","TRIGGERED"):
                for direction in ("TREND_UP","TREND_DOWN","RANGE","MIXED","UNKNOWN"):
                    for vol in ("NORMAL_VOL","HIGH_VOL","LOW_VOL","UNKNOWN"):
                        value=dict(ctx,direction=direction,volatility=vol)
                        self.assertEqual(context_reason(kind,symbol,value,sessions,T,stage)[0] is None,e.allowed_context(kind,symbol,value,sessions,T,stage))

    def test_atomic_cancel_preserves_legacy_category(self):
        sessions=market();s=full_session("AAPL");sessions["AAPL"]=s
        T=s.opening.replace(hour=10,minute=20);ev=e.Event("ir1","AAPL")
        ev.move("SETUP",T-e.MINUTE,A=1)
        ctx=dict(e.context(sessions,T),direction="MIXED",volatility="NORMAL_VOL")
        e.evaluate(ev,s,sessions,T,ctx,True)
        self.assertEqual(ev.fields["reason"],"CONTEXT_MARKET_DIRECTION_FILTER")
        self.assertEqual(ev.fields["legacyReason"],"CONTEXT_FAILED")
        self.assertEqual(ev.fields["rejectionContext"]["decisionTime"],T.isoformat())

    def test_start_end_clock_timestamp_equivalence_and_dst(self):
        for date,offset in (("2024-01-03",-5),("2024-03-08",-5),("2024-03-11",-4),
                            ("2024-06-03",-4),("2024-11-01",-4),("2024-11-04",-5)):
            with self.subTest(date=date):
                opening,closing=exchange_sessions(date,date)[date]
                self.assertEqual(opening.hour,9);self.assertEqual(opening.minute,30)
                self.assertEqual(closing.hour,16)
                self.assertEqual(opening.utcoffset().total_seconds()/3600,offset)
                T=opening.replace(hour=15,minute=30)
                label=(T-e.MINUTE).astimezone(timezone.utc).isoformat()
                self.assertEqual(normalize_minute_timestamp(label,"start",None)+e.MINUTE,T)
                self.assertEqual(normalize_minute_timestamp(T.astimezone(timezone.utc).isoformat(),"end",None)+e.MINUTE,T)
                sessions=market(date);self.assertIsNone(idle_gate_reason(e.Event("ir3","QQQ"),sessions["QQQ"],sessions,T,e.context(sessions,T),True)[0])

    def test_early_close_correct_calendar(self):
        opening,closing=exchange_sessions("2024-11-29","2024-11-29")["2024-11-29"]
        self.assertEqual(closing.hour,13)
        row=coverage_record("QQQ","2024-11-29",{},opening,closing,"start",False)
        self.assertTrue(row["earlyClose"]);self.assertEqual(row["expectedRegularMinuteCount"],210)

    def test_samples_bounded_and_counters_isolated_deterministic(self):
        d=Diagnostics(["ir1","ir2","ir3"],3)
        for _ in range(10):d.reject(e.Event("ir1","AAPL"),"CONTEXT_VOLATILITY_FILTER",dict(symbol="AAPL"))
        d.reject(e.Event("ir2","AAPL"),"CONTEXT_VOLATILITY_FILTER")
        result=d.as_dict()
        self.assertEqual(result["ir1"]["rejectReasons"]["CONTEXT_VOLATILITY_FILTER"],10)
        self.assertEqual(len(result["ir1"]["samplesByReason"]["CONTEXT_VOLATILITY_FILTER"]),3)
        self.assertEqual(result["ir2"]["rejectReasons"]["CONTEXT_VOLATILITY_FILTER"],1)
        self.assertEqual(result["ir3"]["rejectReasons"],{})
        self.assertEqual(d.as_dict(),result)


    def test_five_day_golden_preserves_pre_instrumentation_trades_and_funnel(self):
        # Frozen pre-change output, independently compared with the original
        # runner/engine. Five synthetic days, seeded past histories, no markets.
        schedule=exchange_sessions("2024-06-03","2024-06-07");days=[]
        for i,(day,(opening,closing)) in enumerate(schedule.items()):
            rows={}
            for symbol in ("SPY","QQQ","AAPL"):
                bars={}
                for n in range(390):
                    T=opening+n*e.MINUTE
                    if (i==2 and symbol=="SPY" and n==359) or (i==3 and symbol=="QQQ" and n==80):continue
                    c=100+(n+1)*.025 if i!=1 else 100+.005*((n%4)-2)
                    bars[T]=e.Bar(T,T+e.MINUTE,c-.02,c+.25,c-.25,c,2200)
                rows[symbol]=bars
            days.append((day,rows))
        diagnostics=Diagnostics(["ir1","ir2","ir3"])
        with patch.object(runner,"History",side_effect=seeded_history):
            trades,funnel,_,_,_=runner.run_sessions(iter(days),schedule,{"SPY","QQQ","AAPL"},["ir1","ir2","ir3"],entry_symbols={"SPY","QQQ","AAPL"},diagnostics=diagnostics)
        self.assertEqual(len(trades),1)
        trade=trades[0]
        self.assertEqual(trade["entryTimestamp"],"2024-06-03T15:31:00-04:00")
        self.assertEqual(trade["exitTimestamp"],"2024-06-03T15:50:00-04:00")
        self.assertEqual(trade["entryMarketPrice"],109.025)
        self.assertEqual(trade["exitMarketPrice"],109.5)
        self.assertAlmostEqual(trade["entryFillPrice"],109.0577075)
        self.assertAlmostEqual(trade["exitFillPrice"],109.46715)
        self.assertAlmostEqual(trade["pnlUsd"],.3754365550000189)
        golden=dict(evaluatedSessions=5,setups=2,signals=2,entries=1,exits=1,CLOCK_ELIGIBILITY_CONTEXT_UNAVAILABLE=8)
        for reason,count in golden.items():self.assertEqual(funnel[reason],count)
        reasons=diagnostics.data["ir3"]["rejectReasons"]
        self.assertEqual(reasons,dict(CAPACITY=1,CLOCK_OWN_DIRECTION_FILTER=2,CLOCK_BENCHMARK_BAR_MISSING=1,CLOCK_TARGET_BAR_MISSING=1,CLOCK_BENCHMARK_SESSION_PREFIX_INCOMPLETE=1,CLOCK_DAILY_SESSION_INCOMPLETE=3))
        stages=diagnostics.as_dict()["ir3"]["stageFunnel"]
        self.assertEqual([x["count"] for x in stages],[10,10,6,4,4,2,2,2,1])
        self.assertTrue(all(x["conversionFromPrevious"] is None or 0<=x["conversionFromPrevious"]<=1 for x in stages))

    def test_cancelled_missing_reference_and_warmup_have_separate_reasons(self):
        sessions=market();s=full_session("AAPL");sessions["AAPL"]=s
        T=s.opening.replace(hour=10,minute=20)
        for missing,expected in (("bar","CONTEXT_BENCHMARK_BAR_MISSING"),("atr","CONTEXT_BENCHMARK_ATR_WARMUP_INSUFFICIENT")):
            event=e.Event("ir1","AAPL");event.move("SETUP",T-e.MINUTE,A=1)
            old=sessions["SPY"].snapshots[T]
            if missing=="bar":del sessions["SPY"].snapshots[T]
            else:sessions["SPY"].snapshots[T]=dict(old,atr=None)
            e.evaluate(event,s,sessions,T,e.context(sessions,T),True)
            self.assertEqual(event.fields["reason"],expected)
            sessions["SPY"].snapshots[T]=old


class StreamingAuditTests(unittest.TestCase):
    def write_file(self,path,values):
        with path.open("w",newline="") as f:
            w=csv.writer(f);w.writerow(["timestamp","open","high","low","close","volume"])
            for t in values:w.writerow([t,100,101,99,100,1000])

    def test_duplicate_and_out_of_order_counted_not_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"QQQ.csv"
            self.write_file(p,["2024-06-03T15:29:00-04:00","2024-06-03T15:28:00-04:00","2024-06-03T15:29:00-04:00"])
            r=scan_file(p,"QQQ","start")
            self.assertEqual(r["duplicates"],1);self.assertEqual(r["outOfOrder"],1)
            self.assertTrue(r["errors"])

    def test_naive_input_rejected_unless_explicit_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"QQQ.csv";self.write_file(p,["2024-06-03T15:29:00"])
            self.assertEqual(scan_file(p,"QQQ","start")["badTimezone"],1)
            self.assertEqual(scan_file(p,"QQQ","start","America/New_York")["badTimezone"],0)

    def test_small_data_audit_outputs_clock_and_duplicate_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);paths={s:root/(s+".csv") for s in ("SPY","QQQ")}
            opening,closing=exchange_sessions("2024-06-03","2024-06-03")["2024-06-03"]
            for s,p in paths.items():
                times=[(opening+n*e.MINUTE).isoformat() for n in range(390) if s!="SPY" or n!=359]
                times.insert(0,times[0]);self.write_file(p,times)
            r=audit_data(paths,"start",root/"out.json",root/"sessions.jsonl",sample_limit=1)
            self.assertEqual(r["aggregate"]["sessions_total"],2)
            self.assertEqual(r["aggregate"]["sessions_with_1530"],2)
            self.assertEqual(r["aggregate"]["sessions_missing_decision_target"],1)
            lines=[json.loads(x) for x in (root/"sessions.jsonl").read_text().splitlines()]
            self.assertTrue(all(x["duplicateTimestampCount"]==1 for x in lines))
            self.assertEqual(sum(x["missingMinuteCount"] for x in lines),1)
            self.assertEqual(r["reviewStatus"],"PROVISIONAL_UNREVIEWED_DATA")
            self.assertTrue(all(len(v)<=1 for v in r["samplesByReason"].values()))

    def test_legacy_artifact_bounds_prove_missing_target_without_inventing_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"old.zip"
            with zipfile.ZipFile(path,"w") as z:
                z.writestr("old_result.json",json.dumps(dict(configuration=dict(dataMode="PROVISIONAL_UNREVIEWED_DATA"),overall={},funnel={})))
                z.writestr("old_data_audit.json",json.dumps(dict(symbols=dict(SPY=dict(lastTimestamp="2024-06-03T10:59:00-04:00",timestamp_kind="start",naive_timezone=None)))))
                events=[dict(strategy="ir3",symbol=s,date="2024-06-03",state="CANCELLED",diagnostics=dict(reason="CLOCK_ELIGIBILITY_CONTEXT_UNAVAILABLE",cancelledTimestamp="2024-06-03T15:30:00-04:00")) for s in ("SPY","QQQ")]
                z.writestr("old_decisions.jsonl","\n".join(json.dumps(e) for e in events))
            summary=artifact_summary(path,1)
            self.assertEqual(summary["provableAtomicFailuresFromFileBounds"],dict(CLOCK_TARGET_BAR_MISSING=1))
            self.assertEqual(len(summary["fileBoundEvidence"]),1)
            self.assertEqual(summary["terminalBreakdown"]["ir3"]["CLOCK_ELIGIBILITY_CONTEXT_UNAVAILABLE"]["count"],2)

    def test_invalid_data_fails_without_context_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);paths={s:root/(s+".csv") for s in ("QQQ","SPY")}
            for p in paths.values():self.write_file(p,["2024-06-03T15:30:00"])
            with patch("research.audit_v4_clock_context.day_stream",side_effect=AssertionError("must not replay")):
                r=audit_data(paths,"start",root/"out.json",root/"sessions.jsonl")
            self.assertEqual(r["status"],"FAIL")


if __name__=="__main__":unittest.main()
