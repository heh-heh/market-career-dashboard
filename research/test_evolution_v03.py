"""Frozen V0.3 validation tests; synthetic fixtures only."""
import contextlib, io, json, tempfile, unittest
from pathlib import Path

from research.backtest_v4_strategies import exchange_sessions
from research.evolution.config import Config
from research.evolution.population import initial_population, load_population_records
from research.evolution.run_evolution_v0 import population_hash
from research.evolution.evaluate_population_v03 import _latest_training_run
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
            (old/"run.json").write_text(json.dumps({"phase":"completed","completedTrainGenerations":2}))
            validation=root/"20260103-validation"; validation.mkdir()
            (validation/"run.json").write_text(json.dumps({"phase":"completed","evaluationMode":"FROZEN_VALIDATION"}))
            newest=root/"20260102-new"; newest.mkdir()
            (newest/"final_population.json").write_text("[]")
            (newest/"run.json").write_text(json.dumps({"phase":"completed","completedTrainGenerations":3}))
            self.assertEqual(_latest_training_run(root),newest)


if __name__=="__main__":
    unittest.main()
