"""Offline tests for redacted persisted event history; no external connections."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from research import collection_activity as activity


class CollectionActivityTests(unittest.TestCase):
    def test_allowlist_and_persistence_across_readers(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(activity.emit(d, "MODE_CHANGED", mode="MARKET_CLOSED",
                                          code="PREOPEN_GUARD", token="secret-text",
                                          message="Bearer secret-text"))
            self.assertTrue(activity.emit(d, "CANDLE_PAGE", symbol="AAPL",
                                          page=4, added=100, stored=900))
            rows = activity.recent(d)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["mode"], "MARKET_CLOSED")
            self.assertEqual(rows[1]["stored"], 900)
            self.assertNotIn("token", json.dumps(rows))
            self.assertNotIn("secret-text", json.dumps(rows))
            self.assertIn("+00:00", rows[0]["at"])

    def test_public_code_is_strict_and_no_raw_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(activity.emit(d, "CANDLE_ERROR", symbol="TQQQ",
                                          code="Bearer secret", path="/private/token.sock"))
            self.assertEqual(activity.recent(d)[0], {"kind": "CANDLE_ERROR",
                                                      "at": activity.recent(d)[0]["at"],
                                                      "symbol": "TQQQ"})
            with self.assertRaisesRegex(ValueError, "UNKNOWN_COLLECTION_EVENT"):
                activity._safe_event("RAW_HTTP_ERROR", code="TOKEN")

    def test_rotation_retains_recent_history_and_bounded_response(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(activity, "MAX_BYTES", 450):
                for i in range(23):
                    self.assertTrue(activity.emit(d, "CANDLE_PAGE", symbol="AAPL",
                                                  page=i, added=1, stored=i))
            self.assertTrue((Path(d) / activity.PREVIOUS).exists())
            rows = activity.recent(d, 500)
            self.assertLessEqual(len(rows), 120)
            self.assertEqual(rows[-1]["page"], 22)
            self.assertGreater(rows[-1]["page"], rows[0]["page"])

    def test_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            victim = root / "keep.txt"
            victim.write_text("preserved")
            (root / activity.CURRENT).symlink_to(victim)
            self.assertFalse(activity.emit(root, "MODE_CHANGED", mode="PAUSED"))
            self.assertEqual(victim.read_text(), "preserved")
            self.assertEqual(activity.recent(root), [])

    def test_invalid_lines_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / activity.CURRENT
            path.write_text('{"kind":"MODE_CHANGED","at":"not-time","code":"XXX"}\n'
                            '{"kind":"CANDLE_PAGE","at":"2026-10-12T04:00:00Z",'
                            '"symbol":"<bad>","code":"Bearer abc","added":30}\n')
            rows = activity.recent(d)
            self.assertEqual(len(rows), 1)
            self.assertNotIn("symbol", rows[0])
            self.assertNotIn("code", rows[0])
            self.assertEqual(rows[0]["added"], 30)


if __name__ == "__main__":
    unittest.main()
