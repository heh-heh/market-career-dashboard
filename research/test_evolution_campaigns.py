import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research.evolution import run_v04_campaign


class CampaignProfileTests(unittest.TestCase):
    def test_campaign_preserves_profile_evaluation_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cfg=root/"v06.json"
            cfg.write_text(json.dumps({
                "fitness_mode":"v06_stability",
                "evaluation_window_sessions":28,
            }))
            train_dir=root/"train"; train_dir.mkdir()
            val_dir=root/"val"; val_dir.mkdir()
            captured={}
            def fake_train(args):
                captured["train"]=list(args)
                return train_dir
            def fake_validation(args):
                captured["validation"]=list(args)
                return val_dir
            with mock.patch.object(run_v04_campaign,"train_main",side_effect=fake_train),                  mock.patch.object(run_v04_campaign,"validation_main",side_effect=fake_validation),                  mock.patch.object(run_v04_campaign,"analyze",return_value={
                     "verdict":"TEST","finalOosAllowed":False,"positiveBots":0,
                     "meanValidationReturn":0.0,"medianValidationReturn":0.0,
                     "trainValidationSpearman":0.0,"tradesValidationPearson":0.0,
                 }):
                run_v04_campaign.main([
                    "--data-dir",str(root/"data"),
                    "--results-dir",str(root/"results"),
                    "--config",str(cfg),
                    "--population","20","--max-population","300",
                ])
            args=captured["train"]
            i=args.index("--evaluation-window-sessions")
            self.assertEqual(args[i+1],"28")


if __name__=="__main__":
    unittest.main()
