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


def observation(price, at, symbol="ABC"):
    return dict(symbol=symbol, price=price, observedAt=at.isoformat(), sourceTimestamp=at.isoformat(),
                sourcePriceTimestamp=at.isoformat(), parsedTimezone=str(simple.NY), source="toss_current_price", warning=None)


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
        self.engine = simple.SimpleMomentumPaper(self.path, None, simple.Config())
        self.addCleanup(lambda: self.engine.close() if not self.engine.file_lock.closed else None)
        self.engine.set_enabled(True)

    def trigger(self):
        return self.engine.process_snapshot([candidate()], {"ABC": valid_bars()}, moment(), session())

    def enter(self, at=None):
        at = at or moment()
        signal = simple.analyze_setup(valid_bars(), candidate(), moment(), self.engine.config, session())
        signal.update(signalTimestamp=(at-timedelta(seconds=1)).isoformat(),
                      triggerDecisionTimestamp=(at-timedelta(seconds=1)).isoformat(),
                      triggerBarTimestamp=(at-simple.MINUTE).isoformat(), sessionCutoff=session()["cutoff"])
        self.engine._enter(signal, observation(100, at), at)
        return self.engine.state["openPosition"]

    def manage(self, price, at):
        self.engine._manage(observation(price, at) if price is not None else None, at)

    def exit(self):
        self.engine._exit(observation(101, moment(10, 1)), moment(10, 1), "TEST", moment(10, 1))

    def test_day_and_pre_sessions_can_trigger_when_enabled(self):
        for name in ("DAY", "PRE"):
            with self.subTest(session=name):
                s = session()
                s["status"] = name
                self.engine.state["pendingSignal"] = None
                self.engine.state["seenTriggers"] = {}
                result = self.engine.process_snapshot([candidate()], {"ABC": valid_bars()}, moment(), s)
                self.assertIsNotNone(result["pendingSignal"])
                self.assertEqual(result["pendingSignal"]["sessionName"], name)

    def test_06_trigger_does_not_fill_same_bar_or_same_snapshot(self):
        current = candle(moment(), o=103.4, complete=False)
        self.engine.process_snapshot([candidate()], {"ABC": valid_bars()+[current]}, moment(), session(),
                                     observations={"ABC": observation(103.9, moment())})
        self.assertIsNone(self.engine.state["openPosition"])
        self.assertIsNotNone(self.engine.state["pendingSignal"])
        self.assertEqual(self.engine.state["paperCash"], 10000)
        self.engine.process_snapshot([], {}, moment(), session(), observations={"ABC": observation(103.9, moment())})
        self.assertIsNone(self.engine.state["openPosition"])

    def test_07_first_observable_price_enters_not_past_next_minute_open(self):
        self.trigger()
        current = candle(moment(), o=103.4, h=104.2, l=103.1, c=103.9)
        self.engine.process_snapshot([], {"ABC": valid_bars()+[current]}, moment(10, 1), session(),
                                     observations={"ABC": observation(104, moment(10, 1))})
        p = self.engine.state["openPosition"]
        self.assertEqual(p["entryTimestamp"], moment(10, 1).isoformat())
        self.assertEqual(p["entryDecisionTimestamp"], moment(10, 1).isoformat())
        self.assertEqual(p["firstObservedPriceTimestamp"], moment(10, 1).isoformat())
        self.assertEqual(p["entryLatencySeconds"], 60)
        self.assertEqual(p["entryMarketPrice"], 104)
        self.assertAlmostEqual(p["entryFillPrice"], 104*1.0002)
        self.assertAlmostEqual(p["entryFill"]*p["quantity"], 100)
        self.assertEqual(self.engine.state["paperCash"], 9900)

    def test_past_candle_open_without_current_observation_never_fills(self):
        self.trigger()
        self.engine.process_snapshot([], {"ABC": [candle(moment(), o=103.4)]}, moment(10, 1), session())
        self.assertIsNone(self.engine.state["openPosition"])
        self.assertIsNotNone(self.engine.state["pendingSignal"])

    def test_08_gap_below_hard_stop_receives_worse_observable_price(self):
        p = self.enter()
        stop = p["stopPrice"]
        self.manage(97, moment(10, 1))
        t = self.engine.recent_trades[-1]
        self.assertEqual(t["exitReason"], "HARD_STOP")
        self.assertEqual(t["exitMarketPrice"], 97)
        self.assertAlmostEqual(t["exitFillPrice"], 97*.9998)
        self.assertLess(t["exitFillPrice"], stop)
        self.assertFalse(t["trailingActivated"])
        self.assertLess(t["mfePct"], .01)
        self.assertEqual(t["exitTriggerTimestamp"], t["exitObservationTimestamp"])
        self.assertEqual(t["exitLatencySeconds"], 0)

    def activate(self):
        p = self.enter()
        self.manage(100.9, moment(10, 1))
        return p

    def test_09_profit_threshold_activates_trail(self):
        p = self.activate()
        self.assertTrue(p["trailingActivated"])
        self.assertEqual(p["trailingActivationTimestamp"], moment(10, 1).isoformat())
        self.assertAlmostEqual(p["trailingStopPrice"], 100.9*.994)

    def test_10_new_high_updates_trailing_high(self):
        p = self.activate()
        self.manage(101.5, moment(10, 2))
        self.assertEqual(p["highestPriceAfterEntry"], 101.5)
        self.assertAlmostEqual(p["trailingStopPrice"], 101.5*.994)

    def test_11_six_tenths_drawdown_exits_at_observed_price(self):
        p = self.activate()
        stop = p["trailingStopPrice"]
        self.manage(100.2, moment(10, 2))
        self.assertIsNone(self.engine.state["openPosition"])
        self.assertEqual(self.engine.recent_trades[-1]["exitReason"], "TRAILING_STOP")
        self.assertEqual(self.engine.recent_trades[-1]["exitMarketPrice"], 100.2)
        self.assertLess(100.2, stop)

    def test_12_no_trail_before_required_profit(self):
        p = self.enter()
        self.manage(100.79, moment(10, 1))
        self.assertFalse(p["trailingActivated"])
        self.assertIsNone(p["trailingStopPrice"])

    def test_13_fifteen_minute_time_stop_executes_only_at_actual_observation(self):
        self.enter()
        self.manage(100, moment(10, 16))
        t = self.engine.recent_trades[-1]
        self.assertEqual(t["exitReason"], "TIME_STOP")
        self.assertEqual(t["exitTimestamp"], moment(10, 16).isoformat())
        self.assertEqual(t["exitTriggerTimestamp"], moment(10, 15).isoformat())
        self.assertEqual(t["exitLatencySeconds"], 60)
        self.assertEqual(t["holdDurationSeconds"], 960)

    def test_14_forced_exit_at_1550(self):
        self.enter(moment(15, 49))
        self.manage(100, moment(15, 50))
        self.assertEqual(self.engine.recent_trades[-1]["exitReason"], "SESSION_EXIT")
        self.assertEqual(self.engine.recent_trades[-1]["exitTimestamp"], moment(15, 50).isoformat())

    def test_15_completed_symbol_locked_for_day_other_symbol_not_locked(self):
        self.enter()
        self.exit()
        self.trigger()
        self.assertEqual(self.engine.state["candidates"][0]["rejectionReason"], "COMPLETED_TODAY")
        self.engine.process_snapshot([candidate("XYZ")], {"XYZ": valid_bars()}, moment(), session())
        self.assertEqual(self.engine.state["pendingSignal"]["symbol"], "XYZ")

    def test_16_restart_restores_position_peak_trail_and_enabled(self):
        self.activate()
        self.engine._save()
        saved = copy.deepcopy(self.engine.state)
        self.engine.close()
        self.engine = simple.SimpleMomentumPaper(self.path, None, simple.Config())
        self.assertEqual(self.engine.state, saved)

    def test_17_v3_and_simple_accounts_are_isolated(self):
        from trading import PaperBroker
        legacy = PaperBroker()
        before = legacy.snapshot()
        self.enter()
        self.exit()
        self.assertEqual(legacy.snapshot(), before)
        simple_before = copy.deepcopy(self.engine.state)
        legacy.trade("ABC", "BUY", 1, 100)
        self.assertEqual(self.engine.state, simple_before)

    def test_no_averaging_or_pyramiding_and_disabled_does_not_enter(self):
        self.enter()
        self.engine.process_snapshot([candidate()], {"ABC": valid_bars()}, moment(), session(), manage_positions=False)
        self.assertEqual(self.engine.state["candidates"][0]["rejectionReason"], "POSITION_OPEN")
        self.engine.set_enabled(False)
        self.assertIsNotNone(self.engine.state["openPosition"])
        self.manage(98, moment(10, 1))
        self.assertIsNone(self.engine.state["openPosition"])

    def test_delayed_entry_cancels_even_with_current_observation_and_past_open(self):
        self.trigger()
        now = moment()+timedelta(seconds=91)
        self.engine.process_snapshot([], {"ABC": [candle(moment(), o=100)]}, now, session(),
                                     observations={"ABC": observation(104, now)})
        self.assertIsNone(self.engine.state["openPosition"])
        self.assertIsNone(self.engine.state["pendingSignal"])
        self.assertEqual(self.engine.state["paperCash"], 10000)
        event = json.loads((self.path/"signals.jsonl").read_text().splitlines()[-1])
        self.assertEqual(event["reason"], "ENTRY_LATENCY_EXCEEDED")

    def test_future_position_candles_and_uncompleted_high_are_not_observed(self):
        p = self.enter()
        self.engine.process_snapshot([], {"ABC": [candle(moment(), h=110, l=98), candle(moment(10, 1), h=120, l=97)]},
                                     moment(10, 1), session(), observations={"ABC": observation(100.1, moment(10, 1))})
        self.assertIsNotNone(self.engine.state["openPosition"])
        self.assertFalse(p["trailingActivated"])
        self.assertEqual(p["highestPriceAfterEntry"], 100.1)

    def test_data_outage_never_fabricates_fill_and_recovery_is_flagged(self):
        p = self.enter()
        cash = self.engine.state["paperCash"]
        self.manage(None, moment(10, 1))
        self.manage(None, moment(10, 2))
        self.assertEqual(p["dataStatus"], "DATA_STALE")
        self.assertEqual(p["staleSince"], moment(10, 1).isoformat())
        self.assertEqual(p["staleObservationCount"], 2)
        self.assertEqual(self.engine.state["paperCash"], cash)
        self.assertEqual(self.engine.recent_trades, [])
        self.manage(97, moment(10, 3))
        t = self.engine.recent_trades[-1]
        self.assertTrue(t["exitAffectedByDataStale"])
        self.assertEqual(t["lastStaleSince"], moment(10, 1).isoformat())
        self.assertEqual(t["staleRecoveredAt"], moment(10, 3).isoformat())
        self.assertEqual(t["exitMarketPrice"], 97)
        self.assertEqual(t["exitTimestamp"], moment(10, 3).isoformat())

    def test_recovery_above_stop_does_not_claim_missed_historical_stop(self):
        p = self.enter()
        self.manage(None, moment(10, 1))
        self.engine.process_snapshot([], {"ABC": [candle(moment(), l=90)]}, moment(10, 2), session(),
                                     observations={"ABC": observation(100.1, moment(10, 2))})
        self.assertIs(self.engine.state["openPosition"], p)
        self.assertEqual(p["dataStatus"], "LIVE")
        self.assertTrue(p["everDataStale"])
        self.assertEqual(p["lowestPriceAfterEntry"], 100)

    def test_stale_state_survives_restart_and_cached_receipt_cannot_recover_it(self):
        self.enter()
        self.manage(None, moment(10, 1))
        self.engine.close()
        self.engine = simple.SimpleMomentumPaper(self.path, None, simple.Config())
        p = self.engine.state["openPosition"]
        self.assertEqual(p["dataStatus"], "DATA_STALE")
        self.manage(100, moment(10, 2))
        self.engine._manage(observation(97, moment(10, 2)), moment(10, 2)+timedelta(seconds=1))
        self.assertIsNotNone(self.engine.state["openPosition"])
        self.assertEqual(p["dataStatus"], "DATA_STALE")
        self.assertEqual(p["staleObservationCount"], 2)

    def test_newly_activated_trail_does_not_assume_high_before_low(self):
        p = self.enter()
        self.engine.process_snapshot([], {"ABC": [candle(moment(), h=101, l=99.5, c=100.8)]}, moment(10, 1), session(),
                                     observations={"ABC": observation(100.9, moment(10, 1))})
        self.assertIsNotNone(self.engine.state["openPosition"])
        self.assertTrue(p["trailingActivated"])
        self.assertEqual(p["highestPriceAfterEntry"], 100.9)

    def test_stale_or_future_quote_is_not_a_fill(self):
        self.enter()
        for at in (moment(), moment(10, 3)):
            self.engine._manage(observation(90, at), moment(10, 2))
        self.assertIsNotNone(self.engine.state["openPosition"])
        self.assertEqual(self.engine.state["openPosition"]["dataStatus"], "DATA_STALE")

    def test_protect_position_before_ranking_failure_and_limit_candle_requests(self):
        self.enter()
        class Feed:
            def __init__(feed):
                feed.calls = []
            def session(feed, now, config):
                return session()
            def observation(feed, symbol, config):
                feed.calls.append(symbol)
                return observation(97, moment(10, 1))
            def candles(feed, symbol):
                raise AssertionError("Position protection must not depend on candles")
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
                self.exit()
        self.engine.close()
        self.engine = simple.SimpleMomentumPaper(self.path, None, simple.Config())
        self.assertIsNone(self.engine.state["openPosition"])
        self.assertEqual(self.engine.state["completedTrades"], 1)
        self.assertEqual(len(self.engine.recent_trades), 1)
        cash = self.engine.state["paperCash"]
        self.engine.close()
        self.engine = simple.SimpleMomentumPaper(self.path, None, simple.Config())
        self.assertEqual(self.engine.state["paperCash"], cash)
        self.assertEqual(len(self.engine.recent_trades), 1)

    def test_legacy_pending_signal_cancelled_without_rewriting_account(self):
        self.trigger()
        self.engine.state["pendingSignal"].pop("triggerDecisionTimestamp")
        self.engine.state["pendingSignal"].pop("executionModel")
        self.engine._save()
        cash = self.engine.state["paperCash"]
        self.engine.close()
        self.engine = simple.SimpleMomentumPaper(self.path, None, simple.Config())
        self.assertIsNone(self.engine.state["pendingSignal"])
        self.assertEqual(self.engine.state["paperCash"], cash)

    def test_unconfirmed_timestamp_kind_disables_entry_loudly(self):
        self.engine.config = simple.Config(timestamp_kind_confirmed=False)
        result = self.trigger()
        self.assertIn("UNCONFIRMED", result["timestampWarning"])
        self.assertEqual(result["candidates"][0]["rejectionReason"], "TIMESTAMP_KIND_UNCONFIRMED")
        self.assertIsNone(result["pendingSignal"])

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
    def observed(self, payload, at=None, config=None):
        at = at or moment(10, 1)
        client = simple.ReadOnlyTossMarketData(lambda force=False: "synthetic-token")
        class FrozenTime(datetime):
            @staticmethod
            def now(zone):
                return at
        with patch.object(simple, "datetime", FrozenTime), patch.object(client, "get", side_effect=payload):
            return client.observation("ABC", config or simple.Config())

    def test_current_quote_is_observable_and_source_age_is_validated(self):
        at = moment(10, 1)
        row = dict(symbol="ABC", lastPrice=104, timestamp=(at-timedelta(seconds=2)).isoformat())
        observed = self.observed([[row]])
        self.assertEqual(observed["price"], 104)
        self.assertEqual(observed["observedAt"], at.isoformat())
        self.assertEqual(observed["sourcePriceTimestamp"], row["timestamp"])
        self.assertEqual(observed["source"], "toss_current_price")

    def test_90_second_old_quote_or_future_quote_never_becomes_fresh_on_receipt(self):
        for source in (moment(10, 1)-timedelta(seconds=90), moment(10, 2)):
            row = dict(symbol="ABC", lastPrice=104, timestamp=source.isoformat())
            with self.assertRaisesRegex(RuntimeError, "No current market observation"):
                self.observed([[row], {"candles": []}])

    def test_forming_fallback_uses_current_last_price_never_historical_open(self):
        at = moment(10, 1)+timedelta(seconds=5)
        historical = dict(timestamp=moment().isoformat(), openPrice=90, highPrice=101,
                          lowPrice=90, closePrice=100, volume=100)
        current = dict(timestamp=moment(10, 1).isoformat(), openPrice=100, closePrice=104)
        observed = self.observed([[], {"candles": [historical, current]}], at)
        self.assertEqual(observed["price"], 104)
        self.assertEqual(observed["source"], "toss_forming_1m_last_price")
        self.assertEqual(observed["barTimestamp"], moment(10, 1).isoformat())
        self.assertIn("age unverified", observed["warning"])
        with self.assertRaisesRegex(RuntimeError, "No current market observation"):
            self.observed([[], {"candles": [historical]}], at)
        with self.assertRaisesRegex(RuntimeError, "No current market observation"):
            self.observed([[], {"candles": [dict(timestamp=moment(10, 1).isoformat(), openPrice=100)]}], at)

    def test_end_label_forming_fallback_is_explicit_exclusive_end(self):
        current = dict(timestamp=moment(10, 2).isoformat(), openPrice=100, closePrice=104)
        observed = self.observed([[], {"candles": [current]}], moment(10, 1)+timedelta(seconds=5),
                                 simple.Config(timestamp_kind="end"))
        self.assertEqual(observed["barTimestamp"], moment(10, 1).isoformat())
        self.assertEqual(observed["price"], 104)

    def test_naive_candle_labels_fail_and_source_labels_are_auditable(self):
        row = dict(timestamp="2026-10-07T10:00:00", openPrice=100)
        with self.assertRaisesRegex(ValueError, "explicit timezone"):
            simple.normalize_candles([row], moment(), simple.Config())
        row["timestamp"] = "2026-10-07T14:00:00+00:00"
        bar = simple.normalize_candles([row], moment(), simple.Config())[0]
        self.assertEqual(bar["sourceTimestamp"], row["timestamp"])
        self.assertEqual(bar["sourceTimezone"], "UTC")
        self.assertEqual(bar["parsedTimezone"], "America/New_York")
        self.assertEqual(bar["start"], moment())
        self.assertEqual(bar["scanTimestamp"], moment().isoformat())

    def test_unconfirmed_timestamp_semantics_cannot_enable_forming_fallback(self):
        with self.assertRaisesRegex(RuntimeError, "timestamp kind unconfirmed"):
            self.observed([[]], config=simple.Config(timestamp_kind_confirmed=False))
        with patch.dict(simple.os.environ, {}, clear=True):
            self.assertTrue(simple.Config.from_env().timestamp_kind_confirmed)
        with patch.dict(simple.os.environ, {"SIMPLE_TIMESTAMP_KIND_CONFIRMED": "false"}, clear=True):
            self.assertFalse(simple.Config.from_env().timestamp_kind_confirmed)

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

    def test_real_calendar_extended_sessions_early_close_and_missing_calendar_fail(self):
        client = simple.ReadOnlyTossMarketData(lambda force=False: "unused")
        today = dict(
            dayMarket=dict(startTime="2026-11-27T04:00:00-05:00", endTime="2026-11-27T07:00:00-05:00"),
            preMarket=dict(startTime="2026-11-27T07:00:00-05:00", endTime="2026-11-27T09:30:00-05:00"),
            regularMarket=dict(startTime="2026-11-27T09:30:00-05:00", endTime="2026-11-27T13:00:00-05:00"))
        config = simple.Config()
        with patch.object(client, "get", return_value={"today": today}):
            day = client.session(datetime(2026, 11, 27, 5, tzinfo=simple.NY), config)
        self.assertEqual(day["status"], "DAY")
        self.assertEqual(day["cutoff"], "2026-11-27T06:58:00-05:00")
        client.calendar_cache = None
        with patch.object(client, "get", return_value={"today": today}):
            pre = client.session(datetime(2026, 11, 27, 8, tzinfo=simple.NY), config)
        self.assertEqual(pre["status"], "PRE")
        self.assertEqual(pre["cutoff"], "2026-11-27T09:28:00-05:00")
        client.calendar_cache = None
        with patch.object(client, "get", return_value={"today": today}):
            regular = client.session(datetime(2026, 11, 27, 12, tzinfo=simple.NY), config)
        self.assertEqual(regular["status"], "REGULAR")
        self.assertEqual(regular["cutoff"], "2026-11-27T12:50:00-05:00")
        with patch.object(client, "get", return_value={}):
            client.calendar_cache = None
            with self.assertRaises(ValueError):
                client.session(moment(), config)

    def test_active_defaults_and_entry_sessions(self):
        config = simple.Config()
        self.assertEqual(config.min_day_change_pct, 3)
        self.assertEqual(config.min_volume, 50000)
        self.assertEqual(config.min_amount_usd, 500000)
        self.assertEqual(config.max_candidates, 20)
        self.assertEqual(config.impulse_pct, 1)
        self.assertEqual(config.pullback_min_pct, .5)
        self.assertEqual(config.pullback_max_pct, 4)
        self.assertEqual(config.entry_sessions, ("DAY", "PRE", "REGULAR"))
        with patch.dict(simple.os.environ, {"SIMPLE_ENTRY_SESSIONS": "PRE,REGULAR"}, clear=True):
            parsed = simple.Config.from_env()
        self.assertEqual(parsed.entry_sessions, ("PRE", "REGULAR"))
        with self.assertRaises(ValueError):
            simple.Config(entry_sessions=("AFTER",))

    def test_ranking_excludes_bad_metadata_and_shortlists_only(self):
        symbols = ["GOOD", "CHEAP", "LOW", "TQQQ", "PREF", "ETN", "EUR", "INACTIVE"]
        rankings = [dict(symbol=s, rank=i+1, price=dict(lastPrice=10, changeRate=.06), tradingVolume=200000, tradingAmount=2000000) for i, s in enumerate(symbols)]
        rankings[1]["price"]["lastPrice"] = 2
        rankings[2]["price"]["changeRate"] = .02
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
