"""Offline-only readiness report: no boto3, AWS, paid calls, or file writes."""
import json
from pathlib import Path
import tempfile
import unittest

from research.spot.offline_readiness import (
    inspect_offline, REQUIRED_SOURCE, REQUIRED_TESTS,
)


class OfflineReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "checkout"
        self.repo.mkdir()

    def populate(self):
        for relative in (*REQUIRED_SOURCE, *REQUIRED_TESTS):
            dest = self.repo / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("test fixture only", encoding="utf-8")

    def test_missing_files_never_claims_ready(self):
        result = inspect_offline(self.repo)
        self.assertFalse(result["localSourcePresent"])
        self.assertFalse(result["readyForPaidLaunch"])
        self.assertFalse(result["createsResources"])
        self.assertEqual(result["awsAccountState"], "NOT_READ_BY_THIS_TOOL")
        self.assertIn("MISSING_SOURCE_OR_TEST_FILES", result["blockers"])

    def test_complete_file_layout_is_still_not_production_ready(self):
        self.populate()
        result = inspect_offline(self.repo)
        self.assertTrue(result["localSourcePresent"])
        self.assertFalse(result["readyForPaidLaunch"])
        self.assertIn("AMI_NETWORK_AND_REAPER_RUNTIME_NOT_VERIFIED", result["blockers"])
        self.assertEqual(len(result["sourceFiles"]), len(REQUIRED_SOURCE))
        self.assertEqual(len(result["testFiles"]), len(REQUIRED_TESTS))

    def test_private_config_does_not_leak_identifiers_or_secrets(self):
        self.populate()
        private = self.repo / "secret-settings.json"
        secret = "secret-bucket-and-profile-not-for-output"
        private.write_text(json.dumps({
            "enabled": False, "population": 10, "max_hours": 8,
            "max_compute_usd": 2, "final_oos_gate": "CLOSED",
            "secretValue": secret,
        }))
        report = inspect_offline(self.repo, private)
        self.assertEqual(report["privateConfigStatus"], "LOCAL_STRUCTURE_CHECKED_ONLY")
        self.assertTrue(report["privateConfigGuards"]["disabledByDefault"])
        self.assertTrue(report["privateConfigGuards"]["closedOos"])
        self.assertTrue(report["privateConfigGuards"]["trainOnlyBudget"])
        self.assertNotIn(secret, json.dumps(report))
        self.assertFalse(report["readyForPaidLaunch"])

    def test_unsafe_or_wrong_config_is_not_treated_as_approved(self):
        self.populate()
        private = self.repo / "settings.json"
        private.write_text(json.dumps({
            "enabled": True, "population": 1, "max_hours": 40,
            "max_compute_usd": 10, "final_oos_gate": "OPEN",
        }))
        report = inspect_offline(self.repo, private)
        self.assertFalse(report["privateConfigGuards"]["closedOos"])
        self.assertFalse(report["privateConfigGuards"]["trainOnlyBudget"])
        self.assertIn("CONFIG_OOS_OR_COST_POLICY_INVALID", report["blockers"])
        private.write_text("not JSON")
        self.assertEqual(inspect_offline(self.repo, private)["privateConfigStatus"],
                         "INVALID_LOCAL_JSON")

    def test_symlinks_are_rejected_even_when_target_is_real(self):
        self.populate()
        path = self.repo / REQUIRED_SOURCE[0]
        path.unlink()
        path.symlink_to(self.repo / REQUIRED_SOURCE[1])
        self.assertFalse(inspect_offline(self.repo)["localSourcePresent"])
        private = self.repo / "private.json"
        actual = self.repo / "actual.json"
        actual.write_text("{}")
        private.symlink_to(actual)
        self.assertEqual(inspect_offline(self.repo, private)["privateConfigStatus"],
                         "MISSING_OR_UNSAFE_FILE")

    def test_no_aws_sdk_import_is_required(self):
        # This import and this report can run with the Python stdlib alone.
        self.assertEqual(inspect_offline(self.repo)["mode"], "OFFLINE_NO_AWS_NO_BILLING")


if __name__ == "__main__":
    unittest.main()
