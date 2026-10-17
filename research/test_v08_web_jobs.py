"""Offline V08 web manager safety tests; never execute real EC2 workers."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import v08_web_jobs as jobs


class V08WebJobsTests(unittest.TestCase):
    def test_commands_do_not_accept_arbitrary_shell_or_dates(self):
        with self.assertRaisesRegex(ValueError, "V08_COMMAND_NOT_ALLOWED"):
            jobs.console(["v08", "train", "--to-date", "2026-12-31"])
        with self.assertRaisesRegex(ValueError, "V08_COMMAND_NOT_ALLOWED"):
            jobs.console(["v08", "reset"])
        self.assertIn("2026 OOS CLOSED", jobs.console(["v08", "help"])[1])

    def test_report_does_not_create_fake_progress(self):
        with patch.object(jobs, "_storage"), patch.object(jobs, "_job", return_value=None):
            out = jobs.report()
        self.assertIsNone(out["jobId"])
        self.assertEqual(out["runs"], {})
        self.assertEqual(out["finalOosGate"], "CLOSED")

    def test_selection_requires_completed_existing_plan(self):
        with patch.object(jobs.legacy, "_current", return_value=None):
            with self.assertRaisesRegex(ValueError, "SELECTION_PLAN_REQUIRED"):
                jobs._plan()

    def test_duplicate_completed_smoke_fails_before_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job-20261017T121314Z-aabbccddeeff"
            job.mkdir()
            (root / "current.json").write_text(json.dumps({"name": job.name}))
            (job / "smoke.json").write_text(json.dumps({"phase": "completed"}))
            with patch.object(jobs, "ROOT", root), patch.object(jobs, "_storage"), \
                 patch.object(jobs, "_other_active"), patch.object(jobs, "_plan", return_value=root/"plan.json"), \
                 patch.object(jobs.legacy, "_check_service_lifetime"), \
                 patch.object(jobs, "PYTHON", __file__), patch.object(jobs, "RUNNER", __file__), \
                 patch.object(jobs.subprocess, "Popen") as spawn:
                with self.assertRaisesRegex(ValueError, "ALREADY_STARTED"):
                    jobs.start("smoke")
                spawn.assert_not_called()

    def test_download_requires_export_completion(self):
        with patch.object(jobs, "_storage"), patch.object(jobs, "_job", return_value=None):
            with self.assertRaisesRegex(ValueError, "EXPORT_NOT_READY"):
                jobs.download()


if __name__ == "__main__":
    unittest.main()
