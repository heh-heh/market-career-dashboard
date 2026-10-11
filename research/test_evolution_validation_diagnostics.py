import json, tempfile, unittest
from pathlib import Path

from research.evolution.analyze_validation_v04 import analyze


class GeneralizationDiagnosticTests(unittest.TestCase):
    def test_analyze_joins_by_bot_and_emits_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); src=root/"src"; val=root/"val"; src.mkdir(); val.mkdir()
            (src/"final_population.json").write_text(json.dumps([
                {"bot_id":"a","genome_hash":"h1","lifetime_return":.1,"lifetime_days":7},
                {"bot_id":"b","genome_hash":"h2","lifetime_return":.2,"lifetime_days":7},
            ]))
            (val/"run.json").write_text(json.dumps({
                "runId":"v","sourceRunId":"s","sourceRunDir":str(src),
                "sourceTrainFrom":"2024-01-01","sourceTrainTo":"2024-12-31",
                "fromDate":"2025-01-01","toDate":"2025-12-31"
            }))
            (val/"validation_summary.json").write_text(json.dumps({"bots":[
                {"botId":"a","genomeHash":"h1","validationReturn":-.4,"pnlUsd":-40,"trades":100,"transactionCost":2,"validDays":250,"invalidDays":0},
                {"botId":"b","genomeHash":"h2","validationReturn":.1,"pnlUsd":10,"trades":50,"transactionCost":1,"validDays":250,"invalidDays":0},
            ]}))
            d=analyze(val)
            self.assertEqual(d["positiveBots"],1)
            self.assertEqual(d["negativeBots"],1)
            self.assertFalse(d["finalOosAllowed"])
            self.assertTrue((val/"generalization_diagnostics.json").exists())

    def test_hash_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); src=root/"src"; val=root/"val"; src.mkdir(); val.mkdir()
            (src/"final_population.json").write_text(json.dumps([{"bot_id":"a","genome_hash":"h1"}]))
            (val/"run.json").write_text(json.dumps({"sourceRunDir":str(src)}))
            (val/"validation_summary.json").write_text(json.dumps({"bots":[{"botId":"a","genomeHash":"x","validationReturn":0,"pnlUsd":0,"trades":0,"transactionCost":0,"validDays":1,"invalidDays":0}]}))
            with self.assertRaisesRegex(ValueError,"genome mismatch"):
                analyze(val)


if __name__=="__main__":
    unittest.main()
