import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from research.run_v4_sequence import Sequence


class FakeManager:
    def __init__(self):
        self.v3_calls = 0
        self.started = []
        self.engine_calls = {}

    def status(self, engine):
        if engine == "v3":
            self.v3_calls += 1
            return {
                "running": self.v3_calls == 1,
                "phase": "running" if self.v3_calls == 1 else "completed",
                "progress": 55 if self.v3_calls == 1 else 100,
                "resultAvailable": self.v3_calls > 1,
            }
        count = self.engine_calls.get(engine, 0)
        self.engine_calls[engine] = count + 1
        if count == 0:
            return {"running": True, "phase": "running", "progress": 50, "resultAvailable": False}
        return {"running": False, "phase": "completed", "progress": 100, "resultAvailable": True}

    def active_job(self):
        return None

    def start(self, engine):
        self.started.append(engine)
        return {"running": True}


class SequenceTests(unittest.TestCase):
    def test_runs_only_ir1_ir2_ir3_after_v3(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = FakeManager()
            state = Path(tmp) / "state.json"
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            seq = Sequence(manager, state, poll_seconds=1)
            with patch("research.run_v4_sequence.time.sleep"), patch.dict(
                "os.environ", {"V4_DATA_MANIFEST": str(manifest)}
            ):
                seq.run()
            self.assertEqual(manager.started, ["v4-ir1", "v4-ir2", "v4-ir3"])
            self.assertNotIn("v4-all", manager.started)
            self.assertIn('"phase": "completed"', state.read_text(encoding="utf-8"))

    def test_refuses_interrupted_v3(self):
        manager = Mock()
        manager.status.return_value = {"running": False, "phase": "interrupted", "resultAvailable": False}
        with tempfile.TemporaryDirectory() as tmp:
            seq = Sequence(manager, Path(tmp) / "state.json", poll_seconds=1)
            with self.assertRaisesRegex(RuntimeError, "V3 must complete successfully"):
                seq.wait_for_v3()


if __name__ == "__main__":
    unittest.main()
