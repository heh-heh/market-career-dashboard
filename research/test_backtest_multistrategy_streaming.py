import csv
import gzip
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from research.backtest_multistrategy_v1 import iter_symbol_days

NY = ZoneInfo("America/New_York")


class StreamingReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "TEST.csv.gz"

    def write_rows(self, rows):
        with gzip.open(self.path, "wt", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=["timestamp","open","high","low","close","volume"])
            writer.writeheader()
            writer.writerows(rows)

    def row(self, ts, px=100.0):
        return {
            "timestamp": ts.isoformat(),
            "open": px,
            "high": px + 0.2,
            "low": px - 0.2,
            "close": px + 0.05,
            "volume": 1000,
        }

    def test_yields_one_regular_session_at_a_time(self):
        rows = []
        for day in (5, 6):
            start = datetime(2026, 1, day, 9, 29, tzinfo=NY)
            for i in range(4):
                rows.append(self.row(start + timedelta(minutes=i), 100 + day + i / 10))
        self.write_rows(rows)
        days = list(iter_symbol_days(self.path))
        self.assertEqual([d for d, _ in days], ["2026-01-05", "2026-01-06"])
        self.assertEqual([len(v) for _, v in days], [3, 3])

    def test_decreasing_timestamp_is_refused(self):
        a = datetime(2026, 1, 5, 9, 31, tzinfo=NY)
        b = datetime(2026, 1, 5, 9, 30, tzinfo=NY)
        self.write_rows([self.row(a), self.row(b)])
        with self.assertRaisesRegex(RuntimeError, "not monotonic"):
            list(iter_symbol_days(self.path))


if __name__ == "__main__":
    unittest.main()
