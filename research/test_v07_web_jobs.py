"""No AWS or broker connections: test fixed V0.7 detached web console."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import v07_web_jobs as jobs


class FakeProcess:
    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.kw = kw


class V07WebJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "jobs"
        self.root.mkdir()
        self.guard = patch.object(jobs, "_guard", lambda: None)
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.patchroot = patch.object(jobs, "ROOT", self.root)
        self.patchroot.start()
        self.addCleanup(self.patchroot.stop)

    def test_command_allowlist_rejects_shell_and_unknown_steps(self):
        for parts in (["v07", "rm"], ["v07", "audit", "--delete"],
                      ["v07", "prepare;rm"], ["v07", "stop"]):
            with self.subTest(parts=parts):
                with self.assertRaisesRegex(ValueError, "V07_COMMAND_NOT_ALLOWED"):
                    jobs.console(parts)
        self.assertIn("v07 prepare", jobs.console(["v07", "help"])[1])
        self.assertIn("연구 작업 없음", jobs.console(["v07", "status"])[1])

    def test_prepare_detaches_and_survives_http_response(self):
        with patch.object(jobs.subprocess, "Popen", side_effect=FakeProcess) as spawn:
            result = jobs.console(["v07", "prepare"])
            self.assertIn("STARTED prepare", result[1])
            self.assertEqual(spawn.call_count, 1)
            args, kwargs = spawn.call_args
            self.assertEqual(args[0][-3:-1], ["--worker", "prepare"])
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["stdin"], jobs.subprocess.DEVNULL)
            self.assertNotIn("bash", args[0])
            job = jobs._current()
            self.assertIsNotNone(job)
            self.assertEqual(jobs._state(job, "prepare")["phase"], "starting")
            with self.assertRaisesRegex(ValueError, "V07_BUSY"):
                jobs.console(["v07", "audit"])
            self.assertEqual(spawn.call_count, 1)

    def test_order_and_no_duplicate_stage(self):
        with patch.object(jobs.subprocess, "Popen", side_effect=FakeProcess):
            jobs.start("prepare")
            job = jobs._current()
            jobs._write(job / "prepare.json", {"phase": "completed"})
            with self.assertRaisesRegex(ValueError, "V07_STAGE_ORDER"):
                jobs.start("smoke")
            jobs.start("audit")
            jobs._write(job / "audit.json", {"phase": "completed"})
            jobs.start("select")
            jobs._write(job / "select.json", {"phase": "completed"})
            jobs.start("smoke")
            with self.assertRaisesRegex(ValueError, "V07_STAGE_ALREADY_STARTED"):
                jobs.start("smoke")

    def test_failure_and_restart_status_persist_without_http_state(self):
        with patch.object(jobs.subprocess, "Popen", side_effect=FakeProcess):
            jobs.start("prepare")
            job = jobs._current()
            jobs._write(job / "prepare.json", {"phase": "failed", "exitCode": 1, "error": "FETCH_FAILED"})
            text = jobs.status()
            self.assertIn("failed", text)
            self.assertIn("FETCH_FAILED", text)
            with self.assertRaisesRegex(ValueError, "V07_STAGE_ORDER"):
                jobs.start("audit")
            # A new job does not overwrite prior research artifacts.
            jobs.start("prepare")
            self.assertNotEqual(jobs._current(), job)
            self.assertTrue((job / "prepare.json").exists())

    def test_log_tail_is_bounded(self):
        log = self.root / "example.log"
        log.write_text("a" * 40000 + "\nLAST_MESSAGE\n")
        tail = jobs._tail(log)
        self.assertIn("LAST_MESSAGE", tail)
        self.assertLessEqual(len(tail), 12000)

    def test_worker_fail_closed_and_writes_error_state(self):
        job = self.root / "job-20261016T012345Z-abcabcabcabc"
        job.mkdir()
        jobs._write(self.root / "current.json", {"name": job.name})
        with patch.object(jobs, "_run_fixed", side_effect=ValueError("MISSING_CSV")):
            jobs.worker("audit", job)
        st = jobs._state(job, "audit")
        self.assertEqual(st["phase"], "failed")
        self.assertEqual(st["exitCode"], 1)
        self.assertIn("MISSING_CSV", st["error"])

    def test_server_routes_are_only_authenticated_console(self):
        script = (Path(__file__).resolve().parents[1] / "server.py").read_text()
        self.assertIn('if parts and parts[0]=="v07":', script)
        self.assertIn("from v07_web_jobs import console as v07_console", script)
        block = script[script.index('if path=="/api/admin/console":'):]
        self.assertLess(block.index("if not authorized(self):"), block.index("run_admin_console(command)"))
        self.assertIn('start_new_session=True', Path(jobs.__file__).read_text())


if __name__ == "__main__":
    unittest.main()
