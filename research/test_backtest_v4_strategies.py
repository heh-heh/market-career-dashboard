"""Tiny deterministic V4 audit fixtures; no historical files or simulations.

Run: python -m unittest discover -s research -p test_backtest_v4_strategies.py -v
"""
import csv
import gzip
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import backtest_v4_strategies as v4


def stamp(hour, minute=0):
    return datetime(2026, 10, 7, hour, minute, tzinfo=v4.NY)


def bar(end, close=100.2, low=99.6, high=100.3, volume=100, opening=None, minutes=1):
    return v4.Bar(end-minutes*v4.MINUTE, end, close if opening is None else opening,
                  high, low, close, volume)


CTX = dict(MARKET_UP=True, SECTOR_UP=True, MILD=True, RECOVERY=True,
           SECTOR_NOT_BEAR=True, QQQ_NONNEGATIVE=True, references={})


def fixture(strategy="S2", symbol="TQQQ"):
    opening, closing = stamp(9, 30), stamp(16)
    f = SimpleNamespace(opening=opening, closing=closing, rows={}, errors={}, bars={},
                        atrs={i: 1.0 for i in range(-1, 79)}, vwaps={},
                        data=SimpleNamespace(eligibility=lambda _: (None, {})),
                        rvol=lambda *_: dict(value=None, mode="UNAVAILABLE"))
    refs = {}
    for name in ("QQQ", "SPY", "SOXX"):
        ends = [opening+(i+1)*5*v4.MINUTE for i in range(78)]
        snaps = {i: dict(index=i, timestamp=T, UP=True, BEAR=False,
                         distance=1, slope=0.1, return5=0.01) for i, T in enumerate(ends)}
        refs[name] = SimpleNamespace(opening=opening, ends=ends, snapshots=snaps, first_error_end=None)
    s = v4.StrategyState(strategy, symbol, "2026-10-07", f, refs, None)
    return s


def arm_s2():
    s = fixture()
    s.state = "ARMED"
    s.event = dict(A=1, orl=100, orh=102, orm=101, failure_low=99.6,
                   breakdown_end=stamp(9, 55), breakdown_index=4, breakdown_volume=100,
                   setup_time=stamp(9, 50), armed_time=stamp(9, 55), anchor_start=stamp(9, 30))
    return s


def s2_close(s, age, reclaim):
    T = stamp(9, 55)+age*5*v4.MINUTE
    current = bar(T, close=100.2 if reclaim else 100.0)
    j = s.event["breakdown_index"]+age
    s.f.bars[j] = bar(T, close=current.c, minutes=5)
    s.f.vwaps[T] = 100.1
    s.f.rows[T-v4.MINUTE] = current
    s.on_close(current)
    return current


def position(strategy="S1"):
    s = fixture(strategy)
    s.state = "MANAGING"
    s.trade = dict(entryTimestamp=stamp(10).isoformat(), entryPrice=101*(1+v4.SLIP),
                   rawEntryPrice=101, stopPrice=100, targetPrice=103,
                   riskPerShare=101*(1+v4.SLIP)-100, exitPrice=None,
                   grossR=None, netR=None, mfeR=0.0, maeR=0.0)
    return s


def write_csv(path, labels, opens=None, bad_last=False):
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for i, T in enumerate(labels):
            o = opens[i] if opens is not None else 100
            w.writerow([T.isoformat(), o, o+1, o+2 if bad_last and i == len(labels)-1 else o-1, o, 100])


class AlignmentAudit(unittest.TestCase):
    def test_start_and_exclusive_end_are_equivalent(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d)/"start.gz", Path(d)/"end.gz"
            starts = [stamp(10)+i*v4.MINUTE for i in range(6)]
            write_csv(a, starts)
            write_csv(b, [T+v4.MINUTE for T in starts])
            rows_a, errors_a = v4.load_1m_data(a, "start")
            rows_b, errors_b = v4.load_1m_data(b, "end")
            self.assertEqual(rows_a, rows_b)
            self.assertFalse(errors_a or errors_b)
            completed = v4.build_5m_bars(rows_b["2026-10-07"], stamp(10), stamp(10, 10), {})
            self.assertEqual(completed[0].end, stamp(10, 5))
            self.assertEqual(completed[0].v, 500)

    def test_inclusive_second_end_labels_fail_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"data.gz"
            write_csv(path, [stamp(10)+timedelta(seconds=59)])
            with self.assertRaisesRegex(ValueError, "minute boundary"):
                v4.load_1m_data(path, "end")

    def test_five_minute_not_visible_early_and_no_future_reference(self):
        ends = [stamp(10, 5), stamp(10, 10)]
        f = SimpleNamespace(ends=ends, snapshots={0: {"id": "past"}, 1: {"id": "future"}})
        for minute in range(5):
            self.assertIsNone(v4.align_completed_timeframe(f, stamp(10, minute)))
        self.assertEqual(v4.align_completed_timeframe(f, stamp(10, 5))["id"], "past")
        self.assertEqual(v4.align_completed_timeframe(f, stamp(10, 9))["id"], "past")
        f.snapshots.pop(0)
        self.assertIsNone(v4.align_completed_timeframe(f, stamp(10, 9)))
        self.assertEqual(v4.align_completed_timeframe(f, stamp(10, 10))["id"], "future")

    def test_missing_raw_minute_never_bridges_buckets(self):
        rows = {stamp(10)+i*v4.MINUTE: bar(stamp(10)+(i+1)*v4.MINUTE) for i in range(10) if i != 2}
        result = v4.build_5m_bars(rows, stamp(10), stamp(10, 10), {})
        self.assertNotIn(0, result)
        self.assertEqual(result[1].start, stamp(10, 5))

    def test_early_close_boundary(self):
        rows = {stamp(12, 55)+i*v4.MINUTE: bar(stamp(12, 55)+(i+1)*v4.MINUTE) for i in range(10)}
        result = v4.build_5m_bars(rows, stamp(12, 55), stamp(13), {})
        self.assertEqual(list(result), [0])
        self.assertEqual(result[0].end, stamp(13))
        s = position()
        s.force = stamp(12, 50)
        s.on_open(bar(stamp(12, 51), close=102, low=101, high=103, opening=102))
        self.assertEqual(s.trade["exitReason"], "FORCE_EXIT")

    def test_future_session_rows_do_not_change_past_features(self):
        opening = stamp(9, 30)
        rows = {opening+i*v4.MINUTE: bar(opening+(i+1)*v4.MINUTE, close=100, low=99, high=101) for i in range(25)}
        data = SimpleNamespace(sessions={"2026-10-07": (opening, stamp(9, 55))},
                               days={"2026-10-07": rows}, errors={}, warmup=lambda _: ([1]*14, 100))
        first = v4.compute_common_features(data, "2026-10-07")
        rows[stamp(9, 54)] = bar(stamp(9, 55), close=1000, low=99, high=2000, volume=10**9)
        second = v4.compute_common_features(data, "2026-10-07")
        self.assertEqual(first.snapshots[3], second.snapshots[3])

    def test_daily_eligibility_never_uses_today_close_or_volume(self):
        data = v4.SymbolData.__new__(v4.SymbolData)
        dates = [(stamp(9, 30)-timedelta(days=k)).date().isoformat() for k in range(60, -1, -1)]
        data.sessions = {d: (datetime.fromisoformat(d).replace(hour=9, minute=30, tzinfo=v4.NY), None) for d in dates}
        data.daily = {d: dict(c=100, v=1_000_000) for d in dates}
        data.daily_tr = {d: 2 for d in dates}
        data.days = {dates[-1]: {stamp(9, 30): bar(stamp(9, 31), close=101, low=100, high=102)}}
        data.errors = {}
        before = data.eligibility(dates[-1])
        data.daily[dates[-1]] = dict(c=10**6, v=10**9)
        data.daily_tr[dates[-1]] = 10**6
        self.assertEqual(before, data.eligibility(dates[-1]))
        self.assertIsNone(before[0])

    def test_older_symbol_history_preserved_without_changing_fold_dates(self):
        evaluation = {"2026-10-07": (stamp(9, 30), stamp(16))}
        expanded = {"2026-10-06": (), **evaluation}
        with patch.object(v4, "exchange_sessions", return_value=expanded) as calendar:
            history = v4.include_symbol_history({"2026-10-06": {}}, {}, evaluation)
            calendar.assert_called_once_with("2026-10-06", "2026-10-07")
        self.assertEqual(history, expanded)
        self.assertEqual(list(evaluation), ["2026-10-07"])


class ExecutionAndStateAudit(unittest.TestCase):
    def test_A_completed_five_minute_signal_enters_only_next_open(self):
        s = arm_s2()
        # 09:59 open precedes the 09:55..09:59 reclaim bar's completion.
        current = bar(stamp(10), opening=100.2)
        s.on_open(current)
        self.assertIsNone(s.trade)
        s2_close(s, 1, True)
        self.assertEqual(s.state, "TRIGGERED")
        self.assertIsNone(s.trade)
        s.on_open(bar(stamp(10, 1), opening=100.2))
        self.assertEqual(s.trade["entryTimestamp"], stamp(10).isoformat())
        self.assertEqual(s.trade["entryPrice"], 100.2*(1+v4.SLIP))

    def test_B_stop_beats_target_with_slippage(self):
        s = position()
        current = bar(stamp(10, 1), close=102, low=99, high=104, opening=101)
        s.on_open(current)
        s.on_intrabar(current)
        self.assertEqual(s.trade["exitReason"], "STOP")
        self.assertEqual(s.trade["exitPrice"], 100*(1-v4.SLIP))
        self.assertLess(s.trade["netR"], s.trade["grossR"])

    def test_stop_gap_beats_scheduled_exit_and_target_gap_uses_target(self):
        s = position()
        s.pending_exits.add("FORCE_EXIT")
        s.on_open(bar(stamp(10, 1), close=99, low=98, high=104, opening=99))
        self.assertEqual(s.trade["exitReason"], "STOP_GAP")
        self.assertEqual(s.trade["exitPrice"], 99*(1-v4.SLIP))
        s = position()
        current = bar(stamp(10, 1), close=104, low=103.5, high=105, opening=104)
        s.on_open(current)
        s.on_intrabar(current)
        self.assertEqual(s.trade["rawExitPrice"], 103)

    def test_C_s1_pivot_does_not_follow_subsequent_high(self):
        s = fixture("S1")
        T = stamp(10, 36)
        s.state = "ARMED"
        s.event = dict(A=1, origin=99, end_close=103, advance=4, impulse_high=104,
                       pull_low=101.3, pivot=101.5, armed_time=stamp(10, 35),
                       setup_time=stamp(10, 30), setup_index=11, anchor_start=stamp(10, 15))
        for k in range(1, 21):
            t = T-v4.MINUTE-k*v4.MINUTE
            s.f.rows[t] = bar(t+v4.MINUTE, close=101, low=100, high=102)
        current = bar(T, close=101.8, low=101.2, high=103.8, volume=100)
        s.f.rows[current.start] = current
        s.f.vwaps[T] = 100.5
        s.on_close(current)
        self.assertEqual(s.state, "TRIGGERED")
        self.assertEqual(s.event["pivot"], 101.5)
        self.assertEqual(s.event["diagnostics"]["triggerVolumeRatio"], 1.0)

    def test_D_s2_first_following_bar_valid(self):
        s = arm_s2()
        s2_close(s, 1, True)
        self.assertEqual(s.state, "TRIGGERED")
        self.assertEqual(s.event["diagnostics"]["barsSinceBreakdown"], 1)

    def test_E_s2_second_following_bar_valid(self):
        s = arm_s2()
        s2_close(s, 1, False)
        self.assertEqual(s.state, "ARMED")
        s2_close(s, 2, True)
        self.assertEqual(s.state, "TRIGGERED")
        self.assertEqual(s.event["diagnostics"]["barsSinceBreakdown"], 2)

    def test_F_s2_third_following_bar_invalid(self):
        s = arm_s2()
        s2_close(s, 1, False)
        s2_close(s, 2, False)
        self.assertEqual(s.state, "CANCELLED")
        s2_close(s, 3, True)
        self.assertEqual(s.state, "CANCELLED")
        self.assertNotIn("trigger_time", s.event)

    def test_s2_breakdown_bar_not_a_reclaim_bar_and_second_low_wins(self):
        s = arm_s2()
        s2_close(s, 0, True)
        self.assertEqual(s.state, "ARMED")
        s = arm_s2()
        T = stamp(10)
        current = bar(T, low=99.5)
        s.f.bars[5] = current
        s.f.vwaps[T] = 100.1
        s.on_close(current)
        self.assertEqual(s.state, "CANCELLED")
        self.assertEqual(s.transitions[-1]["reason"], "SECOND_LOWER_LOW")

    def test_s2_or_stays_fixed(self):
        s = arm_s2()
        s.f.rows[stamp(9, 30)] = bar(stamp(9, 31), close=200, low=190, high=210)
        s2_close(s, 1, True)
        self.assertEqual((s.event["orl"], s.event["orh"]), (100, 102))

    def test_G_s3_box_frozen_and_prior_ten_volume_excludes_trigger(self):
        s = fixture("S3")
        T, j = stamp(11, 10), 19
        s.state = "ARMED"
        s.event = dict(A=1, bh=101, bl=100.5, setup_index=17,
                       setup_time=stamp(11), armed_time=stamp(11, 5),
                       compression=.6, box_width=.5, anchor_start=stamp(10, 30))
        for k in range(j-10, j+1):
            end = s.f.opening+(k+1)*5*v4.MINUTE
            s.f.bars[k] = bar(end, close=100, low=99.5, high=100.5, minutes=5)
        for k, c in ((17, 101), (18, 101.1), (19, 101.2)):
            end = s.f.bars[k].end
            s.f.bars[k] = bar(end, close=c, low=101, high=105, volume=150 if k == j else 100, minutes=5)
        s.benchmark = SimpleNamespace(bars={k: bar(b.end, close=200, low=199, high=201, minutes=5) for k, b in s.f.bars.items()})
        s.f.vwaps[T] = 100.9
        current = bar(T, close=101.2, low=101, high=105, volume=150)
        s.f.rows[current.start] = current
        s.on_close(current)
        self.assertEqual(s.state, "TRIGGERED")
        self.assertEqual((s.event["bh"], s.event["bl"]), (101, 100.5))
        self.assertEqual(s.event["diagnostics"]["expansionVolumeRatio"], 1.5)
        self.assertEqual(len(s.event["diagnostics"]["relativeStrengthHold"]), 3)

    def test_H_missing_current_reference_cancels_instead_of_future_fill(self):
        s = arm_s2()
        s.references["QQQ"].snapshots.pop(5)  # 10:00 missing; 10:05 exists.
        s2_close(s, 1, True)
        self.assertEqual(s.state, "CANCELLED")
        self.assertEqual(s.transitions[-1]["reason"], "REFERENCE_UNAVAILABLE")

    def test_I_locks_isolated_by_symbol_strategy_and_day(self):
        states = [fixture("S1", "TQQQ"), fixture("S2", "TQQQ"), fixture("S1", "NVDA")]
        states[0].event = dict(setup_time=stamp(10))
        states[0].cancel(stamp(10, 1), "CONTEXT_FAILED")
        states[0].on_close(bar(stamp(10, 2)))
        self.assertEqual([s.state for s in states], ["CANCELLED", "IDLE", "IDLE"])
        self.assertEqual(fixture("S1", "TQQQ").state, "IDLE")

    def test_missing_next_open_cancels_no_late_fill(self):
        s = arm_s2()
        s2_close(s, 1, True)
        s.on_open(bar(stamp(10, 2), opening=100.2))
        self.assertEqual(s.state, "CANCELLED")
        self.assertIsNone(s.trade)
        self.assertEqual(s.transitions[-1]["reason"], "MISSING_NEXT_OPEN")


class ProvenanceAndMetricAudit(unittest.TestCase):
    def manifest(self, path, basis="split_adjusted_ohlcv"):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        meta = dict(sha256=digest, price_basis=basis, timestamp_kind="start",
                    timestamp_semantics="minute_start", naive_timezone="America/New_York",
                    split_consistency_verified=True, point_in_time_eligibility_verified=True,
                    validationPassed=True, priceAdjustmentConfirmed=True, priceAdjustment="split-adjusted",
                    symbolIdentityConfirmed=True, conflictingDuplicates=0, ohlcViolations=0,
                    timestampViolations=0, quoteViolations=0, symbolViolations=0)
        return dict(version=1, verified_by="synthetic-test", verification_note="Synthetic fixture only, not production provenance",
                    validationPassed=True, priceAdjustmentConfirmed=True, priceAdjustment="split-adjusted", timestampKind="start", calendar="XNYS",
                    symbols={s: dict(meta, symbol=s) for s in ("TQQQ", "QQQ", "SPY")})

    def test_boolean_without_manifest_rejected(self):
        with self.assertRaisesRegex(ValueError, "data-manifest"):
            v4.validate_data_manifest(None, {"TQQQ"}, "start")

    def test_manifest_mixed_bases_and_missing_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path, manifest_path = Path(d)/"data.gz", Path(d)/"manifest.json"
            write_csv(path, [stamp(10)])
            manifest = self.manifest(path)
            manifest["symbols"]["QQQ"]["price_basis"] = "unadjusted_split_free_ohlcv"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "inconsistent|Mixed"):
                v4.validate_data_manifest(manifest_path, {"TQQQ", "QQQ"}, "start")
            manifest = self.manifest(path)
            manifest["symbols"]["TQQQ"]["split_consistency_verified"] = False
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "unverified"):
                v4.validate_data_manifest(manifest_path, {"TQQQ"}, "start")
            manifest = self.manifest(path)
            manifest["symbols"]["TQQQ"]["point_in_time_eligibility_verified"] = False
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "as-traded"):
                v4.validate_data_manifest(manifest_path, {"TQQQ"}, "start")

    def test_manifest_timestamp_mismatch_and_changed_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path, manifest_path = Path(d)/"data.gz", Path(d)/"manifest.json"
            write_csv(path, [stamp(10)])
            manifest = self.manifest(path)
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "timestamp_kind"):
                v4.validate_data_manifest(manifest_path, {"TQQQ"}, "end")
            validated = v4.validate_data_manifest(manifest_path, {"TQQQ"}, "start")
            digest = validated["symbols"]["TQQQ"]["sha256"]
            v4.load_1m_data(path, "start", expected_sha256=digest)
            write_csv(path, [stamp(10)], opens=[101])
            with self.assertRaisesRegex(ValueError, "SHA256"):
                v4.load_1m_data(path, "start", expected_sha256=digest)

    def test_file_change_during_read_refused_without_interrupting_collector(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"data.gz"
            write_csv(path, [stamp(10)])
            digest, before = hashlib.sha256(path.read_bytes()).hexdigest(), path.stat()
            changed = SimpleNamespace(st_size=before.st_size+1, st_mtime_ns=before.st_mtime_ns+1, st_ino=before.st_ino)
            with patch.object(Path, "stat", side_effect=[before, changed]):
                with self.assertRaisesRegex(ValueError, "changed while reading"):
                    v4.load_1m_data(path, "start", expected_sha256=digest)

    def test_aware_only_declaration_rejects_naive_source(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"data.gz"
            write_csv(path, [stamp(10).replace(tzinfo=None)])
            with self.assertRaisesRegex(ValueError, "timezone-aware"):
                v4.load_1m_data(path, "start", naive_timezone=None)

    def test_conflicting_open_cannot_be_resurrected_by_third_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"data.gz"
            write_csv(path, [stamp(10)]*3, opens=[100, 101, 100])
            rows, errors = v4.load_1m_data(path, "start")
            self.assertNotIn(stamp(10), rows["2026-10-07"])
            self.assertIn(stamp(10), errors["2026-10-07"])

    def test_invalid_later_hlc_does_not_erase_known_open(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"data.gz"
            write_csv(path, [stamp(10)], bad_last=True)
            rows, errors = v4.load_1m_data(path, "start")
            self.assertEqual(rows["2026-10-07"][stamp(10)].o, 100)
            self.assertIn(stamp(10), errors["2026-10-07"])

    def test_metrics_chronology_pf_drawdown_and_unresolved_scope(self):
        values = [(3, -2), (1, -1), (4, 2), (2, 3)]
        trades = [dict(exitTimestamp=stamp(10, minute).isoformat(), symbol="TQQQ", strategy="S1", netR=r) for minute, r in values]
        m = v4.metrics(trades)
        self.assertEqual(m["winRate"], .5)
        self.assertEqual(m["expectancyR"], .5)
        self.assertEqual(m["profitFactorR"], 5/3)
        self.assertEqual(m["maxDrawdownR"], 2)
        self.assertEqual(m["averageWinR"], 2.5)
        self.assertEqual(m["averageLossR"], 1.5)
        m = v4.metrics(trades + [dict(netR=None)])
        self.assertFalse(m["performanceValid"])
        self.assertEqual(m["unresolvedTrades"], 1)
        self.assertEqual(m["metricScope"], "RESOLVED_TRADES_ONLY")
        m = v4.metrics([dict(trades[0], netR=1)])
        self.assertIsNone(m["profitFactorR"])
        self.assertEqual(m["profitFactorStatus"], "NO_LOSSES")

    def test_three_folds_oos_disjoint_train_past_only(self):
        days = [f"2026-01-{i:02d}" for i in range(1, 21)]
        folds = v4.chronological_folds([], days)["folds"]
        self.assertEqual(len(folds), 3)
        for i, f in enumerate(folds):
            self.assertLess(f["trainEnd"], f["oosStart"])
            if i:
                self.assertLess(folds[i-1]["oosEnd"], f["oosStart"])

    def test_overlap_same_day_is_label_not_temporal_duplicate(self):
        decisions = [dict(date="2026-10-07", symbol="TQQQ", strategy="S1", triggerTimestamp=stamp(10).isoformat()),
                     dict(date="2026-10-07", symbol="TQQQ", strategy="S3", triggerTimestamp=stamp(14).isoformat())]
        row = v4.overlap_report(decisions, [])["dailyS1S3"][0]
        self.assertEqual(row["classification"], "BOTH")
        self.assertEqual(row["absoluteTriggerDifferenceMinutes"], 240)
        self.assertFalse(v4.overlap_report(decisions, [])["duplicatePairs"])

    def test_benchmark_mapping(self):
        for symbol, expected in (("TQQQ", ("QQQ", 3)), ("SQQQ", ("QQQ", -3)),
                                 ("SOXL", ("SOXX", 3)), ("SOXS", ("SOXX", -3)),
                                 ("NVDA", ("SOXX", 1)), ("TSLA", ("QQQ", 1))):
            self.assertEqual(v4.benchmark_for_symbol(symbol), expected)


if __name__ == "__main__":
    unittest.main()
