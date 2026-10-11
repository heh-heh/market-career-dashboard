"""Frozen V0.3 validation tests; synthetic fixtures only."""
import contextlib, io, json, tempfile, unittest
from pathlib import Path

from research.backtest_v4_strategies import exchange_sessions
from research.evolution.config import Config
from research.evolution.population import initial_population, load_population_records
from research.evolution.run_evolution_v0 import population_hash
from research.evolution.evaluate_population_v03 import _latest_training_run, main as validation_main
from research.test_evolution_v0 import write_fixtures
import random


class FrozenPopulationTests(unittest.TestCase):
    def test_saved_population_round_trip_preserves_hash(self):
        c=Config(population_size=4)
        bots=initial_population(c,random.Random(42),"2024-06-03")
        records=[b.record() for b in bots]
        loaded=load_population_records(records,c)
        self.assertEqual(population_hash(bots),population_hash(loaded))
        self.assertEqual([b.bot_id for b in bots],[b.bot_id for b in loaded])

    def test_genome_hash_tamper_is_rejected(self):
        c=Config(population_size=1)
        row=initial_population(c,random.Random(1),"2024-06-03")[0].record()
        row["genome_hash"]="bad"
        with self.assertRaisesRegex(ValueError,"Genome hash mismatch"):
            load_population_records([row],c)

    def test_latest_training_run_ignores_validation_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            old=root/"20260101-old"; old.mkdir()
            (old/"final_population.json").write_text("[]")
            (old/"run.json").write_text(json.dumps({"phase":"completed","completedTrainGenerations":2,"smoke":False}))
            validation=root/"20260103-validation"; validation.mkdir()
            (validation/"run.json").write_text(json.dumps({"phase":"completed","evaluationMode":"FROZEN_VALIDATION"}))
            newest=root/"20260102-new"; newest.mkdir()
            (newest/"final_population.json").write_text("[]")
            (newest/"run.json").write_text(json.dumps({"phase":"completed","completedTrainGenerations":3,"smoke":False}))
            self.assertEqual(_latest_training_run(root),newest)

    def test_frozen_validation_end_to_end_has_no_evolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=root/"data"; results=root/"results"; source=results/"20250101-training"
            dates=list(exchange_sessions("2025-01-02","2025-01-03"))
            write_fixtures(data,dates)
            c=Config(population_size=4)
            bots=initial_population(c,random.Random(42),"2024-12-31")
            source.mkdir(parents=True)
            (source/"run.json").write_text(json.dumps({
                "phase":"completed","runId":"source-train","completedTrainGenerations":20,
                "smoke":False,"fromDate":"2024-06-03","toDate":"2024-12-31",
                "config":c.snapshot(),"symbols":["AAPL","TQQQ"],
            }))
            population=[b.record() for b in bots]
            (source/"final_population.json").write_text(json.dumps(population))
            before=(source/"final_population.json").read_bytes()
            argv=[
                "--source-run",str(source),"--data-dir",str(data),"--results-dir",str(results),
                "--from-date",dates[0],"--to-date",dates[-1],"--provisional","--data-origin","synthetic",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                out=validation_main(argv)
            info=json.loads((out/"run.json").read_text())
            state=json.loads((out/"status.json").read_text())
            summary=json.loads((out/"validation_summary.json").read_text())
            self.assertEqual(info["phase"],"completed")
            self.assertEqual(info["evaluationMode"],"FROZEN_VALIDATION")
            self.assertEqual(info["sourceRunId"],"source-train")
            self.assertTrue(info["sourcePopulationVerified"])
            self.assertEqual(state["mutationCount"],0)
            self.assertEqual(state["crossoverCount"],0)
            self.assertEqual(state["parentPoolSize"],0)
            self.assertEqual(state["resourceDecision"],"FROZEN_VALIDATION")
            self.assertEqual(summary["population"],4)
            self.assertEqual(len(summary["bots"]),4)
            self.assertEqual(before,(source/"final_population.json").read_bytes())
            self.assertEqual(population_hash(load_population_records(population,c)),
                             population_hash(load_population_records(json.loads((source/"final_population.json").read_text()),c)))


if __name__=="__main__":
    unittest.main()
