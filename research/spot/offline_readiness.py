"""Offline-only V083 Spot release gate report.

Does not import boto3, use HTTP, query AWS, mutate config, provision instances,
or start training. This is intentionally NOT an AWS readiness certification.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat

REQUIRED_SOURCE = (
    "research/evolution/run_v083.py",
    "research/evolution/v083_resume.py",
    "research/spot/prepare_v083_spot_bundle.py",
    "research/spot/v083_spot_lifecycle.py",
    "research/spot/v083_spot_worker.py",
    "research/spot/worker_storage.py",
    "v083_spot_control.py",
    "research/spot/systemd/v083-spot-reaper.timer",
    "research/spot/systemd/v083-spot-reaper.service",
)
REQUIRED_TESTS = (
    "research/test_v083_resume.py",
    "research/test_v083_spot_safety.py",
    "research/test_v083_spot_lifecycle.py",
    "research/test_v083_spot_control.py",
    "research/test_v083_spot_worker.py",
    "research/test_v083_spot_installer.py",
)


def _safe_regular_file(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
        return stat.S_ISREG(mode) and not path.is_symlink() and path.stat().st_size > 0
    except OSError:
        return False


def inspect_offline(repo: Path, private_config: Path | None = None) -> dict:
    """Check source and optional local config, never remote/account state."""
    repo = repo.resolve()
    sources = {name: _safe_regular_file(repo / name) for name in REQUIRED_SOURCE}
    tests = {name: _safe_regular_file(repo / name) for name in REQUIRED_TESTS}
    policy = {"sourceAndTestsPresent": all(sources.values()) and all(tests.values())}
    config_status = "NOT_PROVIDED"
    config_guards = {"privateConfigPresent": False, "disabledByDefault": None,
                     "closedOos": None, "trainOnlyBudget": None}
    if private_config is not None:
        private_config = Path(private_config)
        if not _safe_regular_file(private_config):
            config_status = "MISSING_OR_UNSAFE_FILE"
        else:
            config_guards["privateConfigPresent"] = True
            try:
                raw = private_config.read_bytes()
                if len(raw) > 1024 * 1024:
                    raise ValueError("CONFIG_TOO_LARGE")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("CONFIG_NOT_OBJECT")
                config_guards["disabledByDefault"] = value.get("enabled") is False
                config_guards["closedOos"] = value.get("final_oos_gate") == "CLOSED"
                limit = value.get("max_hours")
                count = value.get("population")
                cap = value.get("max_compute_usd")
                config_guards["trainOnlyBudget"] = (
                    type(limit) in (int, float) and 0 < limit <= 8
                    and type(count) is int and 2 <= count <= 50
                    and type(cap) in (int, float) and 0 < cap <= 5
                )
                config_status = "LOCAL_STRUCTURE_CHECKED_ONLY"
            except (ValueError, UnicodeError, OSError, TypeError):
                config_status = "INVALID_LOCAL_JSON"
    blockers = []
    if not policy["sourceAndTestsPresent"]:
        blockers.append("MISSING_SOURCE_OR_TEST_FILES")
    if config_status != "LOCAL_STRUCTURE_CHECKED_ONLY":
        blockers.append("PRIVATE_JOB_CONFIGURATION_NOT_VERIFIED")
    elif not config_guards["closedOos"] or not config_guards["trainOnlyBudget"]:
        blockers.append("CONFIG_OOS_OR_COST_POLICY_INVALID")
    # Never claim readiness from files alone. The following require a separate
    # approved AWS IAM/network/S3/AMI review, which cannot be inferred offline.
    blockers.extend((
        "AWS_IDENTITY_AND_SCOPED_IAM_NOT_VERIFIED",
        "S3_VERSIONED_BUNDLE_AND_HASHES_NOT_VERIFIED",
        "AMI_NETWORK_AND_REAPER_RUNTIME_NOT_VERIFIED",
        "INTERRUPTION_RESUME_NOT_LIVE_TESTED",
        "PAID_SPOT_LAUNCH_NOT_APPROVED",
    ))
    return {
        "project": "V083_SPOT", "mode": "OFFLINE_NO_AWS_NO_BILLING",
        "readOnly": True, "createsResources": False, "runsTraining": False,
        "sourceFiles": sources, "testFiles": tests,
        "localSourcePresent": policy["sourceAndTestsPresent"],
        "privateConfigStatus": config_status,
        "privateConfigGuards": config_guards,
        "awsAccountState": "NOT_READ_BY_THIS_TOOL",
        "actualCost": "NOT_ESTIMATED_FROM_LOCAL_FILES",
        "readyForPaidLaunch": False, "blockers": blockers,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--private-config", type=Path, default=None,
                        help="Optional local file only. Values are never printed.")
    args = parser.parse_args(argv)
    print(json.dumps(inspect_offline(args.repo, args.private_config),
                     ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
