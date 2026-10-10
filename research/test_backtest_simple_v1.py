import csv
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from simple_momentum_v1 import Config, NY
from research.backtest_simple_v1 import run, session_name, setup_from_window


def dt(day, hour, minute):
    return datetime(2026, 10, day, hour, minute, tzinfo=NY)


class SimpleBacktestTests(unittest.TestCase):
    def test_session_partition(self):
        self.assertEqual(session_name(dt(7, 4, 0)), "DAY")
        self.assertEqual(session_name(dt(7, 7, 0)), "PRE")
        self.assertEqual(session_name(dt(7, 9, 30)), "REGULAR")
        self.assertIsNone(session_name(dt(7, 16, 0)))

    def test_valid_setup_matches_simple_v1_shape(self):
        prices = [
            (100,100.8,99.7,100.5),(100.8,101.6,100.5,101.3),
            (101.6,102.4,101.3,102.1),(102.4,103.2,102.1,102.9),
            (103.2,105,103,104.8),(104.8,104.9,104,104.1),
            (104.1,104.2,103.4,103.5),(103.5,103.6,102.8,102.9),
            (102.9,103,102,102.5),(102.7,103.5,102.2,103.4),
        ]
        bars=[]
        for i,(o,h,l,c) in enumerate(prices):
            start=dt(7,9,50)+timedelta(minutes=i)
            bars.append({"start":start,"end":start+timedelta(minutes=1),
                         "open":o,"high":h,"low":l,"close":c,"volume":100000})
        setup,reason=setup_from_window(bars,Config())
        self.assertIsNone(reason)
        self.assertAlmostEqual(setup["recentImpulsePct"],5.0)
        self.assertAlmostEqual(setup["pullbackLow"],102.0)

    def test_tiny_historical_run_creates_subsequent_observation_trade(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            data=root/"data";data.mkdir()
            path=data/"TEST.csv.gz"
            fields=["timestamp","open","high","low","close","volume","currency"]
            rows=[]
            rows.append({
                "timestamp":dt(6,15,59).isoformat(),"open":100,"high":100.1,
                "low":99.9,"close":100,"volume":100000,"currency":"USD",
            })
            prices=[
                (100,100.8,99.7,100.5),(100.8,101.6,100.5,101.3),
                (101.6,102.4,101.3,102.1),(102.4,103.2,102.1,102.9),
                (103.2,105,103,104.8),(104.8,104.9,104,104.1),
                (104.1,104.2,103.4,103.5),(103.5,103.6,102.8,102.9),
                (102.9,103,102,102.5),(102.7,103.5,102.2,103.4),
            ]
            for i,(o,h,l,c) in enumerate(prices):
                rows.append({
                    "timestamp":(dt(7,9,50)+timedelta(minutes=i)).isoformat(),
                    "open":o,"high":h,"low":l,"close":c,"volume":100000,"currency":"USD",
                })
            rows.append({
                "timestamp":dt(7,10,0).isoformat(),"open":103.4,"high":104.2,
                "low":103.3,"close":104.0,"volume":100000,"currency":"USD",
            })
            for i in range(1,17):
                rows.append({
                    "timestamp":(dt(7,10,0)+timedelta(minutes=i)).isoformat(),
                    "open":104.0,"high":104.1,"low":103.9,"close":104.0,
                    "volume":100000,"currency":"USD",
                })
            with gzip.open(path,"wt",encoding="utf-8",newline="") as fp:
                w=csv.DictWriter(fp,fieldnames=fields);w.writeheader();w.writerows(rows)
            result=run(
                data,Config(),state_path=root/"state.json",
                result_path=root/"result.json",trades_csv=root/"trades.csv",log_path=root/"run.log",
            )
            self.assertEqual(result["overall"]["trades"],1)
            trade=result["trades"][0]
            self.assertEqual(trade["exitReason"],"TIME_STOP")
            self.assertEqual(trade["entryLatencySeconds"],60)
            self.assertEqual(trade["executionProxy"],"next-completed-1m-close-after-signal")
            self.assertTrue((root/"result.json").exists())
            state=json.loads((root/"state.json").read_text())
            self.assertEqual(state["phase"],"completed")
            self.assertFalse(state["running"])


if __name__=="__main__":
    unittest.main()
