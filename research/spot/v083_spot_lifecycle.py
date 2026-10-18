"""V0.8.3 on-demand EC2 Spot controller (no provisioning on import).

No Spot worker exists until explicitly approved by an administrator AND a
locally provisioned, reviewed config is present. Does not create buckets,
IAM roles, instance profiles, images, networks or permanent EC2 instances.

Only the dedicated train phase can launch a *single, one-time* Spot worker.
Requires a signed-off immutable S3 bundle (including audited training data)
prepared separately. Spot instance *terminates* on completion or failure.
This module is not automatically run by system startup, report or UI polling.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REGION = "ap-northeast-1"
ALLOWED_TYPES = {
    "c6i.2xlarge": 8,
    "c7i.2xlarge": 8,
    "m7i.2xlarge": 8,
    "c6i.4xlarge": 16,
}
DEFAULT_CONFIG = Path("/mnt/research/spot-control/v083-spot-config.json")
JOB_RE = re.compile(r"v083-[a-f0-9]{16}")
SHA_RE = re.compile(r"[a-f0-9]{64}")
ID_RE = re.compile(r"[a-z0-9._/-]+")
AWS_ID_RE = re.compile(r"(ami-[a-f0-9]{8,17}|subnet-[a-f0-9]{8,17}|sg-[a-f0-9]{8,17})")
ROOT = Path(__file__).resolve().parents[2]


def load_config(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("SPOT_CONFIG_REQUIRED: a private, reviewed config is missing")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "enabled", "region", "instance_type", "ami_id", "subnet_id",
        "security_group_id", "instance_profile_arn", "bucket", "bundle_key",
        "bundle_sha256", "bundle_version_id", "job_id", "campaign_job", "source_commit",
        "population", "max_hours", "max_spot_price_usd_hour", "max_compute_usd",
        "final_oos_gate"
    }
    if not required.issubset(cfg) or cfg["enabled"] is not True:
        raise ValueError("SPOT_DISABLED_OR_CONFIG_INCOMPLETE")
    if cfg["region"] != REGION or cfg["instance_type"] not in ALLOWED_TYPES:
        raise ValueError("SPOT_REGION_OR_INSTANCE_NOT_ALLOWLISTED")
    for name, prefix in (("ami_id", "ami-"), ("subnet_id", "subnet-"), ("security_group_id", "sg-")):
        if not isinstance(cfg[name], str) or not AWS_ID_RE.fullmatch(cfg[name]) or not cfg[name].startswith(prefix):
            raise ValueError("SPOT_INVALID_" + name.upper())
    arn = cfg["instance_profile_arn"]
    if not isinstance(arn, str) or not re.fullmatch(r"arn:aws:iam::[0-9]{12}:instance-profile/[A-Za-z0-9+=,.@_/-]+", arn):
        raise ValueError("SPOT_INSTANCE_PROFILE_INVALID")
    if not isinstance(cfg["bucket"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", cfg["bucket"]):
        raise ValueError("SPOT_BUCKET_INVALID")
    key = cfg["bundle_key"]
    if not isinstance(key, str) or not ID_RE.fullmatch(key) or not key.startswith("v083/bundles/") or ".." in key.split("/") or not key.endswith(".tar.gz"):
        raise ValueError("SPOT_BUNDLE_KEY_INVALID")
    if not isinstance(cfg["bundle_version_id"], str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", cfg["bundle_version_id"]):
        raise ValueError("SPOT_IMMUTABLE_BUNDLE_VERSION_REQUIRED")
    if not SHA_RE.fullmatch(str(cfg["bundle_sha256"])):
        raise ValueError("SPOT_BUNDLE_SHA256_REQUIRED")
    if not re.fullmatch(r"[a-f0-9]{40}", str(cfg["source_commit"])):
        raise ValueError("SPOT_SOURCE_COMMIT_REQUIRED")
    if not JOB_RE.fullmatch(str(cfg["job_id"])):
        raise ValueError("SPOT_JOB_ID_INVALID")
    if not re.fullmatch(r"job-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}",str(cfg["campaign_job"])):
        raise ValueError("SPOT_CAMPAIGN_JOB_INVALID")
    if type(cfg["population"]) is not int or not 2 <= cfg["population"] <= 50:
        raise ValueError("SPOT_POPULATION_MUST_BE_2_TO_50")
    if type(cfg["max_hours"]) not in (int, float) or not 0 < cfg["max_hours"] <= 8:
        raise ValueError("SPOT_MAX_HOURS_MUST_BE_0_TO_8")
    for attr in ("max_spot_price_usd_hour", "max_compute_usd"):
        if type(cfg[attr]) not in (int, float) or not 0 < cfg[attr] <= 5:
            raise ValueError("SPOT_INVALID_COST_LIMIT_" + attr)
    if cfg["max_spot_price_usd_hour"] * cfg["max_hours"] > cfg["max_compute_usd"]:
        raise ValueError("SPOT_COST_CAP_INCOMPATIBLE")
    if cfg["final_oos_gate"] != "CLOSED":
        raise ValueError("SPOT_FINAL_OOS_MUST_BE_CLOSED")
    return cfg


def job_prefix(cfg):
    return "v083/runs/" + cfg["job_id"]


def display_plan(cfg):
    return {
        "status": "DISABLED_BY_DEFAULT_UNTIL_APPROVED",
        "region": cfg["region"], "instance_type": cfg["instance_type"],
        "vcpus": ALLOWED_TYPES[cfg["instance_type"]],
        "population": cfg["population"],
        "spot_only": True, "spot_behavior": "one-time / terminate",
        "max_wall_hours": cfg["max_hours"],
        "max_spot_usd_hour": cfg["max_spot_price_usd_hour"],
        "max_compute_usd": cfg["max_compute_usd"],
        "maximum_compute_spend_at_bid": round(cfg["max_spot_price_usd_hour"] * cfg["max_hours"], 4),
        "other_charges": "EBS/S3/network/IPv4/tax are additional",
        "final_oos_gate": "CLOSED",
        "data_ready": "NOT_CHECKED_IN_OFFLINE_PLAN",
        "existing_host_untouched": True,
        "automatic_replacement": False,
        "terminate_when_worker_exits": True,
    }


def make_user_data(cfg):
    # No shell snippets or commands accepted from config. All values validated
    # by load_config. The archive must be trusted and pinned by S3 version + SHA.
    bucket = cfg["bucket"]
    version = cfg["bundle_version_id"]
    bundle = cfg["bundle_key"]
    prefix = job_prefix(cfg)
    sha = cfg["bundle_sha256"]
    seconds = int(cfg["max_hours"] * 3600)
    return f"""#!/bin/bash
set -Eeuo pipefail
export AWS_DEFAULT_REGION={REGION}
umask 077
JOB=/opt/v083-one-shot
mkdir -p "$JOB"
finish() {{
  rc=$?
  printf '%s\\n' "$rc" > "$JOB/exit-code.txt"
  aws s3 cp "$JOB/exit-code.txt" "s3://{bucket}/{prefix}/exit-code.txt" --region {REGION} || true
  aws s3 sync "$JOB/output/" "s3://{bucket}/{prefix}/output/" --region {REGION} --no-progress || true
  sync || true
  shutdown -h now
}}
trap finish EXIT
aws s3api get-object --bucket {bucket} --key {bundle} --version-id {version} "$JOB/bundle.tar.gz" --region {REGION} >/dev/null
echo "{sha}  $JOB/bundle.tar.gz" | sha256sum -c -
python3 - "$JOB/bundle.tar.gz" "$JOB" <<'PY_SAFE_ARCHIVE'
import pathlib,sys,tarfile
archive=pathlib.Path(sys.argv[1]);dest=pathlib.Path(sys.argv[2]).resolve()
with tarfile.open(archive,'r:gz') as tar:
    for m in tar.getmembers():
        name=pathlib.PurePosixPath(m.name)
        if name.is_absolute() or '..' in name.parts or m.issym() or m.islnk() or m.isdev() or m.isfifo():
            raise SystemExit('SPOT_UNSAFE_ARCHIVE')
        if not (m.isdir() or m.isfile()):
            raise SystemExit('SPOT_INVALID_ARCHIVE_ENTRY')
    tar.extractall(dest,filter='data')
PY_SAFE_ARCHIVE
cd "$JOB"
mkdir -p output
timeout --signal=TERM --kill-after=45s {seconds} /opt/v083/venv/bin/python "$JOB/bundle/research/spot/v083_spot_worker.py" --workspace "$JOB" --job-id {cfg["job_id"]} --population {cfg["population"]} --source-commit {cfg["source_commit"]}
"""


def _api(client=None, service="ec2"):
    if client is not None:
        return client
    # No Boto3 import or credentials required to review offline plan.
    import boto3
    return boto3.client(service, region_name=REGION)


def matching_instances(ec2, cfg):
    response = ec2.describe_instances(Filters=[
        {"Name": "tag:V083ResearchJob", "Values": [cfg["job_id"]]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped", "shutting-down"]},
    ])
    return [i for block in response.get("Reservations", []) for i in block.get("Instances", [])]


def verify_bundle(s3, cfg):
    head = s3.head_object(Bucket=cfg["bucket"], Key=cfg["bundle_key"], VersionId=cfg["bundle_version_id"])
    if head.get("Metadata", {}).get("sha256") != cfg["bundle_sha256"] or head.get("ContentLength", 0) <= 0:
        raise ValueError("SPOT_BUNDLE_HASH_OR_SIZE_MISMATCH")
    # Do not accept a version with an unreviewed / unpinned source.
    if head.get("Metadata", {}).get("source-commit") != cfg["source_commit"]:
        raise ValueError("SPOT_BUNDLE_SOURCE_MISMATCH")


def launch(cfg, *, ec2=None, s3=None, approval=False):
    if approval is not True:
        raise ValueError("SPOT_EXPLICIT_LAUNCH_APPROVAL_REQUIRED")
    if os.environ.get("V083_SPOT_LAUNCH_ENABLED") != "YES":
        raise ValueError("SPOT_OPERATOR_ENABLE_SWITCH_REQUIRED")
    if os.environ.get("V083_SPOT_REAPER_READY") != "YES":
        raise ValueError("SPOT_INDEPENDENT_TTL_REAPER_REQUIRED")
    timer = subprocess.run(["/bin/systemctl","is-active","--quiet","v083-spot-reaper.timer"],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
    if timer.returncode != 0:
        raise ValueError("SPOT_REAPER_TIMER_NOT_ACTIVE")
    ec2 = _api(ec2)
    s3 = _api(s3, "s3")
    current = matching_instances(ec2, cfg)
    if current:
        raise ValueError("SPOT_DUPLICATE_OR_EXISTING_JOB_BLOCKED")
    verify_bundle(s3, cfg)
    # The IAM instance profile is managed outside this module. Its bucket
    # permissions must be least privilege; no broker secrets/roles.
    deadline = datetime.now(timezone.utc) + timedelta(hours=cfg["max_hours"])
    params = dict(
        ImageId=cfg["ami_id"], InstanceType=cfg["instance_type"],
        MinCount=1, MaxCount=1,
        SubnetId=cfg["subnet_id"], SecurityGroupIds=[cfg["security_group_id"]],
        IamInstanceProfile={"Arn": cfg["instance_profile_arn"]},
        InstanceMarketOptions={"MarketType": "spot", "SpotOptions": {
            "SpotInstanceType": "one-time", "InstanceInterruptionBehavior": "terminate",
            "MaxPrice": str(cfg["max_spot_price_usd_hour"]),
        }},
        InstanceInitiatedShutdownBehavior="terminate",
        MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
        DisableApiTermination=False,
        EbsOptimized=True,
        BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {
            "VolumeSize": 80, "VolumeType": "gp3", "Encrypted": True,
            "DeleteOnTermination": True}}],
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Project", "Value": "V083-Research-Only"},
            {"Key": "V083ResearchJob", "Value": cfg["job_id"]},
            {"Key": "TTL_UTC", "Value": deadline.isoformat()},
            {"Key": "OOS", "Value": "CLOSED"},
            {"Key": "AutoReplace", "Value": "DISABLED"},
        ]}],
        UserData=make_user_data(cfg),
        ClientToken=cfg["job_id"],
    )
    try:
        response = ec2.run_instances(**params)
    except Exception:
        # A timed-out create can still have succeeded; the idempotency token
        # prevents second allocation. Never attempt automatic repeat.
        raise
    ids = [i["InstanceId"] for i in response["Instances"]]
    if len(ids) != 1:
        raise RuntimeError("SPOT_UNEXPECTED_INSTANCE_COUNT")
    return {"status": "REQUESTED_ONE_TIME_SPOT", "instance_id": ids[0],
            "job_id": cfg["job_id"], "deadline_utc": deadline.isoformat(),
            "automatic_relaunch": False}


def status(cfg, *, ec2=None):
    active = matching_instances(_api(ec2), cfg)
    return {"job_id": cfg["job_id"], "instances": [
        {"id": i["InstanceId"], "state": i["State"]["Name"]} for i in active
    ]}


def terminate(cfg, *, ec2=None, approval=False):
    if approval is not True:
        raise ValueError("SPOT_EXPLICIT_STOP_APPROVAL_REQUIRED")
    client = _api(ec2)
    found = matching_instances(client, cfg)
    ids = [i["InstanceId"] for i in found if i["State"]["Name"] not in ("shutting-down", "terminated")]
    if not ids:
        return {"terminated": [], "note": "no active Spot worker"}
    # Only instances carrying this immutable job tag can be terminated.
    client.terminate_instances(InstanceIds=ids)
    return {"terminated": ids}


def reap(cfg, *, ec2=None, current_time=None):
    """Independently scheduled 5-minute TTL termination. No launch capability.

    Must be installed as a separate systemd timer or cron on the existing host.
    Reads only tagged instances for the fixed job ID in the private config.
    """
    if os.environ.get("V083_SPOT_REAPER_ENABLED") != "YES":
        raise ValueError("SPOT_REAPER_SWITCH_REQUIRED")
    client = _api(ec2)
    current_time = current_time or datetime.now(timezone.utc)
    expired = []
    for i in matching_instances(client, cfg):
        if i["State"]["Name"] not in ("pending", "running", "stopping"):
            continue
        tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
        if tags.get("V083ResearchJob") != cfg["job_id"]:
            continue
        try:
            deadline = datetime.fromisoformat(tags["TTL_UTC"])
        except (ValueError, KeyError):
            # Fail closed on a tag that has been removed or corrupted:
            # instances without a valid budget clock require intervention.
            expired.append(i["InstanceId"])
            continue
        if deadline.tzinfo is None or deadline <= current_time:
            expired.append(i["InstanceId"])
    if expired:
        client.terminate_instances(InstanceIds=expired)
    return {"expired_terminated": expired, "checked_at": current_time.isoformat()}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("plan", "status", "launch", "stop", "reap"))
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--approve-launch", action="store_true")
    p.add_argument("--approve-stop", action="store_true")
    a = p.parse_args(argv)
    try:
        cfg = load_config(a.config)
        if a.command == "plan": result = display_plan(cfg)
        elif a.command == "status": result = status(cfg)
        elif a.command == "launch": result = launch(cfg, approval=a.approve_launch)
        elif a.command == "stop": result = terminate(cfg, approval=a.approve_stop)
        else: result = reap(cfg)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        p.exit(2, "SPOT BLOCKED: " + str(error) + "\n")


if __name__ == "__main__":
    main()
