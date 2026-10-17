import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import v082_bot_roster as roster


def population(g):
    return [
        dict(bot_id=f"g{g:04d}-b{i:04d}", generation=g,
             origin="ELITE" if i == 0 else "MUTATION",
             parent_ids=[f"g{g-1:04d}-b0000"] if g else [],
             birth_date="2023-01-01", mutation_count=i,
             genome_hash="abcdef", lifetime_pnl=2.5,
             lifetime_return=0.025, lifetime_equity=102.5,
             lifetime_trades=7, lifetime_days=5,
             genome=dict(weights={"return_1m":0.1}, indicator_weights={"rsi":0.3},
                         buy_threshold=1.0, stop_loss_pct=0.01))
        for i in range(50)
    ]


def assessments(g):
    return [
        dict(botId=f"g{g:04d}-b{i:04d}", survival="SURVIVE" if i < 2 else "DEAD",
             evaluationValid=True, windowComplete=True, windowSessions=5, validDays=5,
             windowPnl=1.2-i/10, windowReturn=(1.2-i/10)/100,
             windowMaxDrawdown=0.03, fitness=0.02, trades=5,
             winRate=0.5, transactionCost=0.1)
        for i in range(50)
    ]


class BotRosterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "v082-web-jobs"
        self.results = Path(self.temp.name) / "evolution-v082"
        self.root.mkdir()
        self.results.mkdir()
        self.job = self.root / "job-20261017T123456Z-aabbccddeeff"
        self.job.mkdir()
        self.folder = self.results / "run-1"
        self.folder.mkdir()
        self.state = {"phase":"completed","runDir":str(self.folder)}
        self.mod = types.SimpleNamespace(
            ROOT=self.root, RESULTS=self.results,
            _job=lambda: self.job,
            _state=lambda j, stage: self.state if stage == "smoke" else {"phase":"not_started"})
        self.patch = patch.dict(sys.modules, {"v082_web_jobs":self.mod})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write(self, name, contents):
        path = self.folder / name
        path.write_text(json.dumps(contents))
        return path

    def fixtures(self):
        self.write("status.json",dict(version="V082_INDICATOR_1",finalOosGate="CLOSED",
                                      phase="completed",runId="demo",completedTrainGenerations=2,
                                      completedSessions=10,dataOrigin="toss"))
        self.write("final_population.json",population(2))
        self.write("last_evaluated_population.json",population(1))
        self.write("window_0001.json",dict(
            dates=["2023-01-02","2023-01-03","2023-01-04","2023-01-05","2023-01-06"],
            bots=assessments(1),
            selection=dict(parentIds=["g0001-b0000","g0001-b0001"],
                           breedingMode="PROFIT_ONLY",parentPoolSize=2)))

    def test_50_bots_and_current_generation_distinct_from_evaluated(self):
        self.fixtures()
        result=roster.snapshot()
        self.assertEqual(result["protocol"],"V082_BOTS_1")
        self.assertEqual(len(result["currentBots"]),50)
        self.assertEqual(len(result["evaluatedBots"]),50)
        self.assertEqual(result["currentBots"][0]["generation"],2)
        self.assertEqual(result["currentBots"][0]["status"],"NOT_EVALUATED_IN_LAST_WINDOW")
        self.assertIsNone(result["currentBots"][0]["latestEvaluation"])
        self.assertEqual(result["currentBots"][0]["inheritedLifetime"]["pnlUsd"],2.5)
        self.assertTrue(result["evaluatedBots"][0]["selectedParent"])
        self.assertFalse(result["evaluatedBots"][2]["selectedParent"])

    def test_no_job_does_not_create_data(self):
        self.mod._job=lambda: None
        result=roster.snapshot()
        self.assertIsNone(result["jobId"])
        self.assertEqual(result["currentBots"],[])
        self.assertEqual(result["evaluatedBots"],[])

    def test_path_escape_is_not_loaded(self):
        self.fixtures()
        self.state["runDir"]=str(self.root)
        result=roster.snapshot()
        self.assertFalse(result["currentBots"])
        self.assertFalse(result["evaluatedBots"])

    def test_malicious_artifact_symlink_rejected(self):
        self.fixtures()
        p=self.folder/"final_population.json"
        p.unlink()
        p.symlink_to(self.root/"current.json")
        with self.assertRaisesRegex(ValueError,"BOT_ARTIFACT_SYMLINK"):
            roster.snapshot()

    def test_population_over_50_rejected(self):
        self.fixtures()
        arr=population(2)
        arr.append({**arr[-1], "bot_id":"g0002-b0050"})
        self.write("final_population.json",arr)
        with self.assertRaisesRegex(ValueError,"BOT_POPULATION_SIZE_INVALID"):
            roster.snapshot()

    def test_console_read_only(self):
        self.fixtures()
        code, payload = roster.console(["bots","report"])
        self.assertEqual(code,0)
        self.assertIn("V082_BOTS_1",payload)
        with self.assertRaises(ValueError):
            roster.console(["bots","trade"])


if __name__ == "__main__":
    unittest.main()
