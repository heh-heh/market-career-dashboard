"""Tiny provenance regression fixtures; never historical simulations."""
import csv
import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import backtest_v4_strategies as v4
import build_v4_data_manifest as validator


def short_session(first, last):
    # Deliberately tiny, explicit session for unit tests (not a production
    # weekday fallback). Real holiday/early-close integration is tested below.
    return {"2024-07-03": (datetime(2024, 7, 3, 9, 30, tzinfo=v4.NY),
                           datetime(2024, 7, 3, 9, 33, tzinfo=v4.NY))}


def row(minute, **overrides):
    return dict(timestamp=f"2024-07-03T09:{minute:02}:00-04:00", open=100,
                high=102, low=99, close=101, volume=1000, **overrides)


class ManifestValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root/"TQQQ.csv.gz"
        self.manifest_path = self.root/"manifest.json"

    def write(self, rows, path=None):
        path = path or self.path
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "wt", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def build(self, **kwargs):
        options = dict(verified_by="synthetic-reviewer",
                       verification_note="Synthetic fixtures only; no production claims",
                       confirm_split_consistency=True,
                       confirm_point_in_time_eligibility=True,
                       confirm_symbol_identity=True, calendar_provider=short_session)
        options.update(kwargs)
        return validator.build_manifest(self.root, ["TQQQ"], "start", "split-adjusted", **options)

    def save(self, manifest):
        self.manifest_path.write_text(json.dumps(manifest))

    def accept(self):
        return v4.validate_data_manifest(self.manifest_path, {"TQQQ"}, "start", data_dir=self.root)

    def test_exact_duplicate_reported_and_accepted(self):
        self.write([row(30), row(30), row(31), row(32)])
        manifest = self.build()
        meta = manifest["symbols"]["TQQQ"]
        self.assertTrue(manifest["validationPassed"])
        self.assertEqual((meta["rows"], meta["duplicates"], meta["exactDuplicates"], meta["conflictingDuplicates"]), (4, 1, 1, 0))
        self.assertEqual(meta["observedRegularMinutes"], 3)
        self.save(manifest)
        self.accept()

    def test_conflicting_duplicate_fails(self):
        conflict = row(30)
        conflict["close"] = 100.5
        self.write([row(30), conflict, row(31), row(32)])
        manifest = self.build()
        self.assertFalse(manifest["validationPassed"])
        self.assertEqual(manifest["symbols"]["TQQQ"]["conflictingDuplicates"], 1)
        self.save(manifest)
        with self.assertRaisesRegex(ValueError, "PASS"):
            self.accept()

    def test_invalid_ohlc_fails(self):
        invalid = row(31)
        invalid["high"] = 100
        self.write([row(30), invalid, row(32)])
        manifest = self.build()
        self.assertFalse(manifest["validationPassed"])
        self.assertEqual(manifest["symbols"]["TQQQ"]["ohlcViolations"], 1)

    def test_invalid_duplicate_rows_still_counted(self):
        invalid = dict(row(30), high=100)
        self.write([row(30), invalid, invalid, row(31)])
        meta = self.build()["symbols"]["TQQQ"]
        self.assertEqual(meta["duplicates"], 2)
        self.assertEqual(meta["conflictingDuplicates"], 2)
        self.assertEqual(meta["ohlcViolations"], 2)
        self.assertFalse(meta["validationPassed"])

    def test_missing_regular_minute_is_diagnostic_not_automatic_failure(self):
        self.write([row(30), row(32)])
        manifest = self.build()
        meta = manifest["symbols"]["TQQQ"]
        self.assertTrue(manifest["validationPassed"])
        self.assertEqual((meta["expectedRegularMinutes"], meta["observedRegularMinutes"], meta["missingRegularMinutes"]), (3, 2, 1))
        self.assertAlmostEqual(meta["completenessPct"], 200/3)
        self.assertEqual(meta["sessionGapCount"], 1)

    def test_hash_mismatch_fails_before_loading(self):
        self.write([row(30)])
        manifest = self.build()
        manifest["symbols"]["TQQQ"]["sha256"] = "0"*64
        self.save(manifest)
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.accept()

    def test_timestamp_kind_mismatch(self):
        self.write([row(30)])
        self.save(self.build())
        with self.assertRaisesRegex(ValueError, "timestamp_kind"):
            v4.validate_data_manifest(self.manifest_path, {"TQQQ"}, "end", data_dir=self.root)

    def test_unconfirmed_price_adjustment_rejected(self):
        self.write([row(30)])
        manifest = self.build()
        manifest["priceAdjustmentConfirmed"] = False
        self.save(manifest)
        with self.assertRaisesRegex(ValueError, "priceAdjustmentConfirmed"):
            self.accept()
        unknown = validator.build_manifest(self.root, ["TQQQ"], "start", "unknown", calendar_provider=short_session)
        self.assertFalse(unknown["priceAdjustmentConfirmed"])
        self.assertFalse(unknown["validationPassed"])

    def test_valid_manifest_accepted_and_loader_agrees(self):
        self.write([row(30), row(31), row(32)])
        self.save(self.build())
        manifest = self.accept()
        meta = manifest["symbols"]["TQQQ"]
        days, errors = v4.load_1m_data(self.path, "start", expected_sha256=meta["sha256"], naive_timezone=meta["naive_timezone"])
        self.assertFalse(errors)
        self.assertEqual(len(days["2024-07-03"]), 3)
        self.assertEqual(meta["completenessPct"], 100)

    def test_mutated_data_file_rejected(self):
        self.write([row(30)])
        self.save(self.build())
        self.accept()
        self.write([row(30), row(31)])
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.accept()

    def test_mutation_during_validation_fails(self):
        self.write([row(30)])
        digest = v4.file_sha256(self.path)
        with patch.object(validator, "file_sha256", side_effect=[digest, "0"*64]):
            manifest = self.build()
        self.assertFalse(manifest["validationPassed"])
        self.assertIn("Input changed", " ".join(manifest["symbols"]["TQQQ"]["validationErrors"]))

    def test_naive_timezone_requires_declaration_and_supports_explicit_utc(self):
        naive = row(30)
        naive["timestamp"] = "2024-07-03T13:30:00"
        self.write([naive])
        self.assertFalse(self.build()["validationPassed"])
        manifest = self.build(naive_timezone="UTC")
        self.assertTrue(manifest["validationPassed"])
        self.save(manifest)
        self.accept()
        days, _ = v4.load_1m_data(self.path, "start", naive_timezone="UTC")
        self.assertIn(datetime(2024, 7, 3, 9, 30, tzinfo=v4.NY), days["2024-07-03"])

    def test_end_labels_shift_exactly_one_minute_and_reject_inclusive_end(self):
        self.write([row(31), row(32), row(33)])
        meta = validator.inspect_file(self.path, "TQQQ", "end", None, calendar_provider=short_session)
        self.assertEqual(meta["completenessPct"], 100)
        days, _ = v4.load_1m_data(self.path, "end", naive_timezone=None)
        self.assertIn(datetime(2024, 7, 3, 9, 30, tzinfo=v4.NY), days["2024-07-03"])
        invalid = row(30)
        invalid["timestamp"] = "2024-07-03T09:30:59-04:00"
        self.write([invalid])
        meta = validator.inspect_file(self.path, "TQQQ", "end", None, calendar_provider=short_session)
        self.assertEqual(meta["timestampViolations"], 1)

    def test_identity_mismatch_and_quotes_conflict_rejected(self):
        wrong = row(30, ticker="QQQ")
        self.write([wrong])
        self.assertFalse(self.build()["validationPassed"])
        self.write([row(30, bid=100, ask=101), row(30, bid=100, ask=101.1)])
        meta = self.build()["symbols"]["TQQQ"]
        self.assertEqual(meta["quoteConflicts"], 1)
        self.assertFalse(meta["validationPassed"])

    def test_plain_csv_and_ambiguous_sources(self):
        self.path = self.root/"TQQQ.csv"
        self.write([row(30)])
        self.save(self.build())
        self.accept()
        days, _ = v4.load_1m_data(self.path, "start", naive_timezone=None)
        self.assertTrue(days)
        self.write([row(30)], self.root/"TQQQ.csv.gz")
        self.assertFalse(self.build()["validationPassed"])

    def test_external_reviews_cannot_be_inferred_from_price_flag(self):
        self.write([row(30)])
        self.assertFalse(self.build(confirm_split_consistency=False)["validationPassed"])
        self.assertFalse(self.build(confirm_point_in_time_eligibility=False)["validationPassed"])
        self.assertFalse(self.build(confirm_symbol_identity=False)["validationPassed"])
        self.assertFalse(self.build(verified_by="")["validationPassed"])

    def test_declared_failed_checks_cannot_be_bypassed_by_pass_flag(self):
        self.write([row(30)])
        manifest = self.build()
        manifest["symbols"]["TQQQ"]["conflictingDuplicates"] = 1
        self.save(manifest)
        with self.assertRaisesRegex(ValueError, "conflictingDuplicates"):
            self.accept()

    def test_out_of_order_rows_reported_and_not_silently_rewritten(self):
        self.write([row(32), row(30), row(31)])
        manifest = self.build()
        meta = manifest["symbols"]["TQQQ"]
        self.assertTrue(manifest["validationPassed"])
        self.assertFalse(meta["monotonicTimestampOrdering"])
        self.assertEqual(meta["outOfOrderTransitions"], 1)
        self.assertEqual(meta["rawFirstTimestamp"], row(30)["timestamp"])

    def test_real_calendar_early_close_and_holiday_exclude_overnight(self):
        # Three rows only; actual XNYS July 3 early-close and July 4 holiday.
        self.write([row(30), dict(row(30), timestamp="2024-07-03T13:00:00-04:00"),
                    dict(row(30), timestamp="2024-07-04T09:30:00-04:00")])
        meta = validator.inspect_file(self.path, "TQQQ", "start", None)
        self.assertEqual(meta["expectedRegularMinutes"], 210)
        self.assertEqual(meta["observedRegularMinutes"], 1)
        self.assertEqual(meta["missingRegularMinutes"], 209)
        self.assertEqual(meta["outsideRegularSessionMinutes"], 2)

    def test_real_calendar_single_day_and_holiday_only(self):
        self.write([row(30)])
        meta = validator.inspect_file(self.path, "TQQQ", "start", None)
        self.assertEqual(meta["expectedRegularMinutes"], 210)
        self.assertEqual(meta["observedRegularMinutes"], 1)
        self.assertEqual(v4.exchange_sessions("2024-07-04", "2024-07-04"), {})

    def test_standalone_cli_writes_pass_and_fail_with_correct_exit_status(self):
        data = self.root/"input"
        data.mkdir()
        csv_path = data/"TQQQ.csv.gz"
        self.write([row(30), row(31)], csv_path)
        command = [sys.executable, str(Path(validator.__file__)), "--data-dir", str(data),
                   "--out", str(self.manifest_path), "--symbols", "TQQQ",
                   "--timestamp-kind", "start", "--price-adjustment", "split-adjusted",
                   "--verified-by", "synthetic-reviewer", "--verification-note", "Synthetic fixture only",
                   "--confirm-split-consistency", "--confirm-point-in-time-eligibility", "--confirm-symbol-identity"]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Overall: PASS", result.stdout)
        manifest = v4.validate_data_manifest(self.manifest_path, {"TQQQ"}, "start", data_dir=data)
        self.assertEqual(manifest["symbols"]["TQQQ"]["rows"], 2)
        with self.assertRaisesRegex(ValueError, "required symbol QQQ"):
            v4.validate_data_manifest(self.manifest_path, {"TQQQ", "QQQ"}, "start", data_dir=data)
        self.write([row(30), dict(row(30), close=100.5)], csv_path)
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Overall: FAIL", result.stdout)
        self.assertIn("conflictingDuplicates=1", result.stdout)
        self.assertFalse(json.loads(self.manifest_path.read_text())["validationPassed"])


if __name__ == "__main__":
    unittest.main()
