import json
import tempfile
import unittest
from pathlib import Path

from research.evolution.build_shortlist_v07 import build_shortlist
from research.backtest_v4_strategies import exchange_sessions


def holdout_info():
    n=len(exchange_sessions("2024-01-02","2024-12-31"))
    return {"runId":"hold", "phase":"completed", "sourceRunId":"train", "fromDate":"2024-01-02", "toDate":"2024-12-31", "requestedSessions":n, "completedSessions":n}


def holdout_summary(rows):
    n=holdout_info()["requestedSessions"]
    return {"bots":[dict(r,validDays=n) for r in rows],"sessionsRequested":n,"sessionsProcessed":n,"missingSessions":[]}


class V07ShortlistTests(unittest.TestCase):
    def test_shortlist_uses_only_positive_valid_holdout_bots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); train=root/"train"; hold=root/"hold"; out=root/"short"
            train.mkdir(); hold.mkdir()
            pop=[]
            for i in range(5):
                pop.append({"bot_id":f"b{i}","genome_hash":f"h{i}","genome":{},"generation":1})
            (train/"final_population.json").write_text(json.dumps(pop))
            (train/"run.json").write_text(json.dumps({
                "runId":"train","phase":"completed","config":{},"symbols":["AAPL","TQQQ"],
                "completedTrainGenerations":10,"fromDate":"2022-01-03","toDate":"2023-12-29"
            }))
            (hold/"run.json").write_text(json.dumps(holdout_info()))
            rows=[
                {"botId":"b0","genomeHash":"h0","validationReturn":.10,"invalidDays":0,"trades":10,"transactionCost":1},
                {"botId":"b1","genomeHash":"h1","validationReturn":.05,"invalidDays":0,"trades":10,"transactionCost":1},
                {"botId":"b2","genomeHash":"h2","validationReturn":-.01,"invalidDays":0,"trades":10,"transactionCost":1},
                {"botId":"b3","genomeHash":"h3","validationReturn":.20,"invalidDays":1,"trades":10,"transactionCost":1},
                {"botId":"b4","genomeHash":"h4","validationReturn":0.0,"invalidDays":0,"trades":10,"transactionCost":1},
            ]
            (hold/"validation_summary.json").write_text(json.dumps(holdout_summary(rows)))
            build_shortlist(train,hold,out,max_count=1)
            selected=json.loads((out/"final_population.json").read_text())
            meta=json.loads((out/"shortlist.json").read_text())
            self.assertEqual([x["bot_id"] for x in selected],["b0"])
            self.assertEqual(meta["eligiblePositiveBots"],2)
            self.assertEqual(meta["selectedBots"][0]["botId"],"b0")

    def test_shortlist_rejects_holdout_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); train=root/"train"; hold=root/"hold"; out=root/"short"
            train.mkdir(); hold.mkdir()
            (train/"final_population.json").write_text(json.dumps([{"bot_id":"b","genome_hash":"h","genome":{}}]))
            (train/"run.json").write_text(json.dumps({"runId":"train","config":{},"symbols":[]}))
            (hold/"run.json").write_text(json.dumps(holdout_info()))
            (hold/"validation_summary.json").write_text(json.dumps(holdout_summary([
                {"botId":"b","genomeHash":"x","validationReturn":.1,"invalidDays":0}
            ])))
            with self.assertRaisesRegex(ValueError,"genome mismatch"):
                build_shortlist(train,hold,out,40)


if __name__=="__main__":
    unittest.main()
