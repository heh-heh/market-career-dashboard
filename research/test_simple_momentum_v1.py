"""Small deterministic forward-paper fixtures; no market downloads/backtests."""
import ast
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import simple_momentum_v1 as simple


def moment(hour=10, minute=0):
    return datetime(2026, 10, 7, hour, minute, tzinfo=simple.NY)


def candle(start, o=100, h=100.4, l=99.6, c=100.1, v=1000, complete=True):
    return dict(start=start, end=start+simple.MINUTE, open=o, high=h, low=l,
                close=c, volume=v, complete=complete)


def session():
    return dict(status="REGULAR", date="2026-10-07", open=moment(9, 30).isoformat(),
                close=moment(16).isoformat(), cutoff=moment(15, 50).isoformat())


def candidate(symbol="ABC"):
    return dict(symbol=symbol, price=103.4, dayChangePct=6, tradingVolume=200000,
                tradingAmount=20000000, candidateRank=1)


def valid_bars():
    prices = [(100, 100.8, 99.7, 100.5), (100.8, 101.6, 100.5, 101.3),
              (101.6, 102.4, 101.3, 102.1), (102.4, 103.2, 102.1, 102.9),
              (103.2, 105, 103, 104.8), (104.8, 104.9, 104, 104.1),
              (104.1, 104.2, 103.4, 103.5), (103.5, 103.6, 102.8, 102.9),
              (102.9, 103, 102, 102.5), (102.7, 103.5, 102.2, 103.4)]
    return [candle(moment(9, 50)+timedelta(minutes=i), *p) for i, p in enumerate(prices)]


class SignalTests(unittest.TestCase):
    def evaluate(self, bars):
        return simple.analyze_setup(bars, candidate(), moment(), simple.Config(), session())

    def test_01_continuously_falling_no_buy(self):
        bars = [candle(moment(9, 50)+timedelta(minutes=i), 105-i, 105.2-i, 104.5-i, 104.7-i) for i in range(10)]
        self.assertFalse(self.evaluate(bars)["eligibleForEntry"])

    def test_02_impulse_without_pullback_no_buy(self):
        bars = valid_bars()
        bars[-2] = candle(moment(9, 58), 104.5, 106, 104.3, 105.8)
        bars[-1] = candle(moment(9, 59), 105.8, 107, 105.7, 106.8)
        self.assertFalse(self.evaluate(bars)["eligibleForEntry"])

    def test_03_pullback_still_falling_no_buy(self):
        bars = valid_bars()
        bars[-1] = candle(moment(9, 59), 103, 103.4, 102.1, 102.4)
        d = self.evaluate(bars)
        self.assertTrue(1 <= d["pullbackPct"] <= 3)
        self.assertFalse(d["eligibleForEntry"])

    def test_04_bullish_but_no_prior_high_break_no_buy(self):
        bars = valid_bars()
        bars[-1] = candle(moment(9, 59), 102.3, 103, 102.2, 102.9)
        d = self.evaluate(bars)
        self.assertTrue(d["bullishBar"])
        self.assertFalse(d["closeAbovePrevHigh"])
        self.assertFalse(d["eligibleForEntry"])

    def test_05_confirmed_reversal_triggers(self):
        d = self.evaluate(valid_bars())
        self.assertTrue(d["eligibleForEntry"])
        self.assertTrue(d["reversalConfirmed"])
        self.assertEqual(d["recentHigh"], 105)
        self.assertEqual(d["pullbackLow"], 102)
        self.assertAlmostEqual(d["recentImpulsePct"], 5)

    def test_confirmation_new_low_rejected(self):
        bars = valid_bars()
        bars[-1]["low"] = 101.9
        self.assertFalse(self.evaluate(bars)["eligibleForEntry"])

    def test_future_high_and_incomplete_candle_do_not_change_signal(self):
        bars = valid_bars()
        expected = self.evaluate(bars)
        self.assertEqual(self.evaluate(bars+[candle(moment(), 103, 200, 1, 150, complete=False)]), expected)

    def test_gap_in_window_and_stale_trigger_rejected(self):
        bars = valid_bars()
        bars[3]["start"] -= simple.MINUTE
        self.assertEqual(self.evaluate(bars)["rejectionReason"], "MISSING_WINDOW_MINUTE")
        d = simple.analyze_setup(valid_bars(), candidate(), moment(10, 3), simple.Config(), session())
        self.assertEqual(d["rejectionReason"], "STALE_TRIGGER_BAR")

    def test_start_end_and_open_only_current_bar(self):
        row = dict(timestamp=moment().isoformat(), openPrice=100, highPrice="bad", lowPrice="bad", closePrice="bad", volume="bad")
        bars = simple.normalize_candles([row], moment(), simple.Config())
        self.assertEqual(bars[0]["open"], 100)
        self.assertNotIn("high", bars[0])
        with self.assertRaises(ValueError):
            simple.normalize_candles([row], moment(10, 1), simple.Config())
        row.update(timestamp=moment(10, 1).isoformat(), highPrice=101, lowPrice=99, closePrice=100, volume=100)
        bars = simple.normalize_candles([row], moment(10, 1), simple.Config(timestamp_kind="end"))
        self.assertEqual(bars[0]["start"], moment())


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"simple"
        self.engine = simple.SimpleMomentumPaper(self.path, None)
        self.addCleanup(lambda: self.engine.close() if not self.engine.file_lock.closed else None)
        self.engine.set_enabled(True)

    def trigger(self):
        return self.engine.process_snapshot([candidate()], {"ABC": valid_bars()}, moment(), session())

    def enter(self, at=None):
        at = at or moment()
        signal = simple.analyze_setup(valid_bars(), candidate(), moment(), self.engine.config, session())
        signal.update(signalTimestamp=at.isoformat(), triggerBarTimestamp=(at-simple.MINUTE).isoformat(), sessionCutoff=session()["cutoff"])
        self.engine._enter(signal, candle(at, complete=False), at)
        return self.engine.state["openPosition"]

    def test_06_trigger_does_not_fill_same_bar_or_same_snapshot(self):
        current = candle(moment(), o=103.4, complete=False)
        self.engine.process_snapshot([candidate()], {"ABC": valid_bars()+[current]}, moment(), session())
        self.assertIsNone(self.engine.state["openPosition"])
        self.assertIsNotNone(self.engine.state["pendingSignal"])
        self.assertEqual(self.engine.state["paperCash"], 10000)

    def test_07_next_minute_open_enters_paper(self):
        self.trigger()
        current = candle(moment(), o=103.4, h=103.6, l=103.1, c=103.5)
        self.engine.process_snapshot([], {"ABC": valid_bars()+[current]}, moment(10, 1), session())
        p = self.engine.state["openPosition"]
        self.assertEqual(p["entryTimestamp"], moment().isoformat())
        self.assertEqual(p["entryMarketPrice"], 103.4)
        self.assertAlmostEqual(p["entryFill"], 103.4*1.0002)
        self.assertAlmostEqual(p["entryFill"]*p["quantity"], 100)
        self.assertEqual(self.engine.state["paperCash"], 9900)

    def test_08_hard_stop_and_same_bar_high_cannot_save_loss(self):
        p = self.enter()
        stop = p["stopPrice"]
        self.engine._manage([candle(moment(), h=105, l=98, c=101)], moment(10, 1))
        t = self.engine.recent_trades[-1]
        self.assertEqual(t["exitReason"], "HARD_STOP")
        self.assertAlmostEqual(t["exitFill"], stop*.9998)
        self.assertFalse(t["trailingActivated"])
        self.assertLess(t["mfePct"], .01)

    def activate(self):
        p = self.enter()
        self.engine._manage([candle(moment(), h=100.9, l=99.9, c=100.8)], moment(10, 1))
        return p

    def test_09_profit_threshold_activates_trail(self):
        p = self.activate()
        self.assertTrue(p["trailingActivated"])
        self.assertEqual(p["trailingActivationTimestamp"], moment(10, 1).isoformat())
        self.assertAlmostEqual(p["trailingStopPrice"], 100.9*.994)

    def test_10_new_high_updates_trailing_high(self):
        p = self.activate()
        self.engine._manage([candle(moment(10, 1), o=100.8, h=101.5, l=100.8, c=101.4)], moment(10, 2))
        self.assertEqual(p["highestPriceAfterEntry"], 101.5)
        self.assertAlmostEqual(p["trailingStopPrice"], 101.5*.994)

    def test_11_six_tenths_drawdown_exits(self):
        p = self.activate()
        stop = p["trailingStopPrice"]
        self.engine._manage([candle(moment(10, 1), o=100.8, h=101.5, l=100.2, c=100.4)], moment(10, 2))
        self.assertIsNone(self.engine.state["openPosition"])
        self.assertEqual(self.engine.recent_trades[-1]["exitReason"], "TRAILING_STOP")
        self.assertAlmostEqual(self.engine.recent_trades[-1]["exitMarketPrice"], stop)

    def test_12_no_trail_before_required_profit(self):
        p = self.enter()
        self.engine._manage([candle(moment(), h=100.79, l=99.8, c=100.7)], moment(10, 1))
        self.assertFalse(p["trailingActivated"])
        self.assertIsNone(p["trailingStopPrice"])

    def test_13_fifteen_minute_time_stop_without_progress(self):
        self.enter()
        bars = [candle(moment()+timedelta(minutes=i)) for i in range(16)]
        self.engine._manage(bars, moment(10, 16))
        t = self.engine.recent_trades[-1]
        self.assertEqual(t["exitReason"], "TIME_STOP")
        self.assertEqual(t["exitTimestamp"], moment(10, 15).isoformat())
        self.assertEqual(t["holdDurationSeconds"], 900)

    def test_14_forced_exit_at_1550(self):
        self.enter(moment(15, 49))
        self.engine._manage([candle(moment(15, 49)), candle(moment(15, 50), complete=False)], moment(15, 50))
        self.assertEqual(self.engine.recent_trades[-1]["exitReason"], "SESSION_EXIT")
        self.assertEqual(self.engine.recent_trades[-1]["exitTimestamp"], moment(15, 50).isoformat())

    def test_15_completed_symbol_locked_for_day_other_symbol_not_locked(self):
        self.enter()
        self.engine._exit(101, moment(10, 1), "TEST", moment(10, 1))
        self.trigger()
        self.assertEqual(self.engine.state["candidates"][0]["rejectionReason"], "COMPLETED_TODAY")
        self.engine.process_snapshot([candidate("XYZ")], {"XYZ": valid_bars()}, moment(), session())
        self.assertEqual(self.engine.state["pendingSignal"]["symbol"], "XYZ")

    def test_16_restart_restores_position_peak_trail_and_enabled(self):
        self.activate()
        self.engine._save()
        saved = copy.deepcopy(self.engine.state)
        self.engine.close()
        self.engine = simple.SimpleMomentumPaper(self.path, None)
        self.assertEqual(self.engine.state, saved)

    def test_17_v3_and_simple_accounts_are_isolated(self):
        from trading import PaperBroker
        legacy = PaperBroker()
        before = legacy.snapshot()
        self.enter()
        self.engine._exit(101, moment(10, 1), "TEST", moment(10, 1))
        self.assertEqual(legacy.snapshot(), before)
        simple_before = copy.deepcopy(self.engine.state)
        legacy.trade("ABC", "BUY", 1, 100)
        self.assertEqual(self.engine.state, simple_before)

    def test_no_averaging_or_pyramiding_and_disabled_does_not_enter(self):
        self.enter()
        self.trigger()
        self.assertEqual(self.engine.state["candidates"][0]["rejectionReason"], "POSITION_OPEN")
        self.engine.set_enabled(False)
        self.assertIsNotNone(self.engine.state["openPosition"])
        self.engine._manage([candle(moment(), h=101, l=98)], moment(10, 1))
        self.assertIsNone(self.engine.state["openPosition"])

    def test_missing_or_delayed_next_minute_never_fills_later_open(self):
        self.trigger()
        self.engine.process_snapshot([], {"ABC": [candle(moment(10, 2))]}, moment(10, 3), session())
        self.assertIsNone(self.engine.state["openPosition"])
        self.assertIsNone(self.engine.state["pendingSignal"])
        self.assertEqual(self.engine.state["paperCash"], 10000)

    def test_future_position_candles_and_uncompleted_high_are_not_observed(self):
        p = self.enter()
        self.engine._manage([candle(moment(), h=110, l=98), candle(moment(10, 1), h=120, l=97)], moment())
        self.assertIsNotNone(self.engine.state["openPosition"])
        self.assertFalse(p["trailingActivated"])
        self.assertEqual(p["highestPriceAfterEntry"], 100)

    def test_data_gap_exits_at_fresh_real_open_and_is_labelled(self):
        self.enter()
        self.engine._manage([candle(moment(10, 2), o=99.5, h=100, l=99, c=99.8)], moment(10, 3))
        trade = self.engine.recent_trades[-1]
        self.assertTrue(trade["dataGap"])
        self.assertEqual(trade["exitReason"], "DATA_GAP_EXIT")
        self.assertEqual(trade["exitMarketPrice"], 99.5)

    def test_newly_activated_trail_does_not_assume_high_before_low(self):
        p = self.enter()
        self.engine._manage([candle(moment(), h=101, l=99.5, c=100.8)], moment(10, 1))
        self.assertIsNotNone(self.engine.state["openPosition"])
        self.assertTrue(p["trailingActivated"])
        self.assertEqual(p["highestPriceAfterEntry"], 101)

    def test_protect_position_before_ranking_failure_and_limit_candle_requests(self):
        self.enter()
        class Feed:
            def __init__(self):
                self.calls = []
            def session(feed, now, config):
                return session()
            def candles(feed, symbol):
                feed.calls.append(symbol)
                return [dict(timestamp=moment().isoformat(), openPrice=100, highPrice=101, lowPrice=98, closePrice=99, volume=100)]
            def candidates(feed, config):
                self.assertIsNone(self.engine.state["openPosition"])
                raise RuntimeError("synthetic ranking outage")
        feed = Feed()
        self.engine.market_data = feed
        class FrozenTime(datetime):
            @staticmethod
            def now(zone):
                return moment(10, 1)
        with patch.object(simple, "datetime", FrozenTime):
            self.engine.scan()
        self.assertEqual(feed.calls, ["ABC"])
        self.assertEqual(self.engine.recent_trades[-1]["exitReason"], "HARD_STOP")
        self.assertIn("ranking outage", self.engine.state["lastError"])

    def test_exit_journal_repairs_crash_before_state_and_trade_projection(self):
        self.enter()
        with patch.object(self.engine, "_save", side_effect=OSError("simulated crash")):
            with self.assertRaises(OSError):
                self.engine._exit(101, moment(10, 1), "TEST", moment(10, 1))
        self.engine.close()
        self.engine = simple.SimpleMomentumPaper(self.path, None)
        self.assertIsNone(self.engine.state["openPosition"])
        self.assertEqual(self.engine.state["completedTrades"], 1)
        self.assertEqual(len(self.engine.recent_trades), 1)
        cash = self.engine.state["paperCash"]
        self.engine.close()
        self.engine = simple.SimpleMomentumPaper(self.path, None)
        self.assertEqual(self.engine.state["paperCash"], cash)
        self.assertEqual(len(self.engine.recent_trades), 1)

    def test_foreign_v3_journal_is_not_imported_or_overwritten(self):
        other = Path(self.tmp.name)/"v3"
        other.mkdir()
        journal = other/"signals.jsonl"
        content = json.dumps(dict(strategy="V3", stateAfter=dict(strategy="V3", mode="paper", revision=999)))+"\n"
        journal.write_text(content)
        with self.assertRaisesRegex(ValueError, "Foreign paper journal"):
            simple.SimpleMomentumPaper(other, None)
        self.assertEqual(journal.read_text(), content)
        self.assertFalse((other/"state.json").exists())


class DataSafetyTests(unittest.TestCase):
    def test_18_read_only_transport_and_no_live_order_import(self):
        client = simple.ReadOnlyTossMarketData(lambda force=False: "synthetic-token")
        for path in ("/api/v1/orders", "/api/v1/orders/1", "/api/v1/accounts", "https://example.com"):
            with self.assertRaises(PermissionError):
                client.get(path)
        requests = []
        def receive(request, timeout):
            requests.append(request)
            return io.BytesIO(b'{"result": {"candles": []}}')
        with patch.object(simple, "wait_for_slot") as rate, patch.object(simple.urllib.request, "urlopen", side_effect=receive):
            self.assertEqual(client.candles("ABC"), [])
        self.assertEqual(rate.call_count, 1)
        self.assertEqual(requests[0].get_method(), "GET")
        self.assertIsNone(requests[0].data)
        self.assertNotIn("orders", requests[0].full_url)
        tree = ast.parse(Path(simple.__file__).read_text())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertNotIn("live_trader", imports)
        self.assertNotIn("trading", imports)

    def test_real_calendar_early_close_cutoff_and_missing_calendar_fail(self):
        client = simple.ReadOnlyTossMarketData(lambda force=False: "unused")
        today = dict(regularMarket=dict(startTime="2026-11-27T09:30:00-05:00", endTime="2026-11-27T13:00:00-05:00"))
        with patch.object(client, "get", return_value={"today": today}):
            s = client.session(datetime(2026, 11, 27, 12, tzinfo=simple.NY), simple.Config())
        self.assertEqual(s["cutoff"], "2026-11-27T12:50:00-05:00")
        with patch.object(client, "get", return_value={}):
            with self.assertRaises(ValueError):
                client.session(moment(), simple.Config())

    def test_ranking_excludes_bad_metadata_and_shortlists_only(self):
        symbols = ["GOOD", "CHEAP", "LOW", "TQQQ", "PREF", "ETN", "EUR", "INACTIVE"]
        rankings = [dict(symbol=s, rank=i+1, price=dict(lastPrice=10, changeRate=.06), tradingVolume=200000, tradingAmount=2000000) for i, s in enumerate(symbols)]
        rankings[1]["price"]["lastPrice"] = 2
        rankings[2]["price"]["changeRate"] = .04
        metadata = [dict(symbol=s, securityType="STOCK", isCommonShare=True, currency="USD", status="ACTIVE") for s in symbols]
        metadata[4]["isCommonShare"] = False
        metadata[5]["securityType"] = "ETN"
        metadata[6]["currency"] = "EUR"
        metadata[7]["status"] = "INACTIVE"
        client = simple.ReadOnlyTossMarketData(lambda force=False: "unused")
        with patch.object(client, "get", side_effect=[dict(rankings=rankings), metadata]) as get:
            out = client.candidates(simple.Config(max_candidates=1))
        self.assertEqual([x["symbol"] for x in out], ["GOOD"])
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
