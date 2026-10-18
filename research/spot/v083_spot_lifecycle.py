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
import contextlib
import fcntl
import math
import tempfile
import urllib.parse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REGION = "ap-northeast-1"
# Operator-approved planning ceiling: $0.25/h TOTAL, not an AWS invoice hard cap.
# Reserve >= $0.05/h for EBS, S3, network, IPv4 and incidental fees.
APPROVED_PLANNING_USD_HOUR = 0.25
APPROVED_MAX_SPOT_BID_USD_HOUR = 0.20
ALLOWED_TYPES = {
    "c6i.2xlarge": 8,
    "c7i.2xlarge": 8,
    "m7i.2xlarge": 8,
    "c6i.4xlarge": 16,
}
DEFAULT_CONFIG = Path("/mnt/research/spot-control/v083-spot-config.json")
JOB_RE = re.compile(r"v083-[a-f0-9]{16}")
SHA_RE = re.compile(r"[a-f0-9]{64}")
VERSION_RE = re.compile(r"[A-Za-z0-9._+/=-]{1,1024}")
ID_RE = re.compile(r"[a-z0-9._/-]+")
AWS_ID_RE = re.compile(r"(ami-[a-f0-9]{8,17}|subnet-[a-f0-9]{8,17}|sg-[a-f0-9]{8,17})")
PROJECT = "V083-Research-Only"
CONTROL_DIR = Path("/mnt/research/spot-control")
ROOT = Path(__file__).resolve().parents[2]


def load_config(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("SPOT_CONFIG_REQUIRED: a private, reviewed config is missing")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return validate_config(cfg)


def validate_config(cfg):
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
    if not isinstance(cfg["bundle_version_id"], str) or not VERSION_RE.fullmatch(cfg["bundle_version_id"]):
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
        if type(cfg[attr]) not in (int, float) or not math.isfinite(cfg[attr]) or not 0 < cfg[attr] <= 5:
            raise ValueError("SPOT_INVALID_COST_LIMIT_" + attr)
    if cfg["max_spot_price_usd_hour"] > APPROVED_MAX_SPOT_BID_USD_HOUR + 1e-9:
        raise ValueError("SPOT_BID_EXCEEDS_APPROVED_HOURLY_RESERVE")
    if cfg["max_spot_price_usd_hour"] * cfg["max_hours"] > cfg["max_compute_usd"]:
        raise ValueError("SPOT_COST_CAP_INCOMPATIBLE")
    if cfg["final_oos_gate"] != "CLOSED":
        raise ValueError("SPOT_FINAL_OOS_MUST_BE_CLOSED")
    resume_prefix = cfg.get("resume_prefix")
    resume_ref = cfg.get("resume_reference")
    if resume_prefix is not None or resume_ref is not None:
        if not isinstance(resume_prefix, str) or not re.fullmatch(r"v083/runs/v083-[a-f0-9]{16}", resume_prefix) or resume_prefix == "v083/runs/" + cfg["job_id"]:
            raise ValueError("SPOT_RESUME_OLD_JOB_PREFIX_REQUIRED")
        if not isinstance(resume_ref, dict) or set(resume_ref) != {"key", "versionId", "sha256"}:
            raise ValueError("SPOT_IMMUTABLE_RESUME_REFERENCE_REQUIRED")
        key = resume_ref["key"]
        if not isinstance(key, str) or not key.startswith(resume_prefix + "/commits/") or not ID_RE.fullmatch(key) or ".." in key.split("/") or not key.endswith(".json"):
            raise ValueError("SPOT_RESUME_COMMIT_KEY_INVALID")
        if not isinstance(resume_ref["versionId"], str) or not VERSION_RE.fullmatch(resume_ref["versionId"]) or not SHA_RE.fullmatch(str(resume_ref["sha256"])):
            raise ValueError("SPOT_RESUME_VERSION_AND_HASH_REQUIRED")
    # Optional in an offline legacy plan, mandatory before any paid action.
    if "cost_estimate" in cfg:
        cost_breakdown(cfg)
    return cfg


def config_digest(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def cost_breakdown(cfg):
    estimate = cfg.get("cost_estimate", {})
    keys = ("ebs_usd", "s3_usd", "network_usd", "ipv4_usd", "other_usd")
    if not all(type(estimate.get(k)) in (int, float) and math.isfinite(estimate[k]) and estimate[k] >= 0 for k in keys):
        raise ValueError("SPOT_REVIEWED_ADDITIONAL_COST_ESTIMATE_REQUIRED")
    cap = cfg.get("max_total_usd")
    if type(cap) not in (int, float) or not math.isfinite(cap) or cap <= 0:
        raise ValueError("SPOT_TOTAL_COST_BUDGET_REQUIRED")
    compute = cfg["max_spot_price_usd_hour"] * cfg["max_hours"]
    total = compute + sum(estimate[k] for k in keys)
    approved_envelope = APPROVED_PLANNING_USD_HOUR * cfg["max_hours"]
    if cap > approved_envelope + 1e-9:
        raise ValueError("SPOT_BUDGET_EXCEEDS_APPROVED_HOURLY_TOTAL")
    if total > approved_envelope + 1e-9:
        raise ValueError("SPOT_HOURLY_TOTAL_ESTIMATE_EXCEEDS_APPROVAL")
    if total > cap + 1e-9:
        raise ValueError("SPOT_TOTAL_ESTIMATE_OVER_BUDGET")
    return {"compute_usd": compute, **{k: estimate[k] for k in keys},
            "estimated_total_usd": total, "budget_usd": cap,
            "approved_hourly_target_usd": APPROVED_PLANNING_USD_HOUR,
            "max_approved_spot_bid_usd_hour": APPROVED_MAX_SPOT_BID_USD_HOUR,
            "approved_envelope_total_usd": approved_envelope,
            "hard_invoice_cap": False,
            "warning": "Approval is a planning target, NOT an AWS invoice limit; actual storage, S3 requests, tax and termination delays can exceed it."}


def issue_approval(cfg, *, expires_at=None):
    """Create server-side receipt ONLY after authenticated explicit cost consent.

    Receipt is private controller state, never accepted directly from a browser.
    It pins every launch field, source/data bundle, population and budget.
    """
    cost_breakdown(cfg)
    now = datetime.now(timezone.utc)
    expires = expires_at or now + timedelta(minutes=10)
    if expires.tzinfo is None or not now < expires <= now + timedelta(minutes=15):
        raise ValueError("SPOT_APPROVAL_EXPIRY_INVALID")
    return {"job_id": cfg["job_id"], "config_sha256": config_digest(cfg),
            "expires_at": expires.isoformat(), "paid_launch_approved": True}


def verify_approval(cfg, record):
    if not isinstance(record, dict) or record.get("paid_launch_approved") is not True or record.get("job_id") != cfg["job_id"] or record.get("config_sha256") != config_digest(cfg):
        raise ValueError("SPOT_PER_JOB_COST_APPROVAL_REQUIRED")
    try:
        expiry = datetime.fromisoformat(record["expires_at"])
        now = datetime.now(timezone.utc)
        if expiry.tzinfo is None or not now < expiry <= now + timedelta(minutes=15):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("SPOT_APPROVAL_EXPIRED_OR_INVALID")


@contextlib.contextmanager
def controller_lock(control_dir=None):
    root = Path(control_dir or CONTROL_DIR)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = root / "launch.lock"
    if lock.is_symlink():
        raise ValueError("SPOT_CONTROL_SYMLINK_BLOCKED")
    with lock.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield root


def _write_registry(root, value):
    target = root / "launch-registry.json"
    if target.is_symlink():
        raise ValueError("SPOT_CONTROL_SYMLINK_BLOCKED")
    fd, temp = tempfile.mkstemp(dir=root, prefix=".registry-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, sort_keys=True); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        if os.path.exists(temp): os.unlink(temp)


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
        "hourly_total_planning_target_usd": APPROVED_PLANNING_USD_HOUR,
        "hourly_spot_bid_ceiling_usd": APPROVED_MAX_SPOT_BID_USD_HOUR,
        "max_compute_usd": cfg["max_compute_usd"],
        "maximum_compute_spend_at_bid": round(cfg["max_spot_price_usd_hour"] * cfg["max_hours"], 4),
        "other_charges": "EBS/S3/network/IPv4/tax are additional",
        "cost_breakdown": cost_breakdown(cfg) if "cost_estimate" in cfg else None,
        "hard_invoice_cap": False,
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
    seconds = max(1, int(cfg["max_hours"] * 3600) - 300)
    resume_setup = ""
    resume_args = ""
    if cfg.get("resume_reference"):
        validate_config(cfg)
        resume_setup = "cat > \"$JOB/resume-reference.json\" <<'V083_RESUME_REFERENCE'\n" + json.dumps(cfg["resume_reference"], sort_keys=True) + "\nV083_RESUME_REFERENCE\n"
        resume_args = f" --resume-reference \"$JOB/resume-reference.json\" --resume-prefix {cfg['resume_prefix']}"
    return f"""#!/bin/bash
set -Eeuo pipefail
export AWS_DEFAULT_REGION={REGION}
umask 077
JOB=/opt/v083-one-shot
mkdir -p "$JOB"
finish() {{
  rc=$?
  printf '%s\\n' "$rc" > "$JOB/exit-code.txt"
  timeout 45 aws s3 cp "$JOB/exit-code.txt" "s3://{bucket}/{prefix}/exit-code.txt" --region {REGION} || true
  # Artifact uploads belong only to the bounded, versioned worker transport.
  sync || true
  shutdown -h now
}}
trap finish EXIT
# Set the deadline after installing cleanup, including deadline setup failures.
shutdown -h +{max(1, int(cfg["max_hours"] * 60))}
aws s3api get-object --bucket {bucket} --key {bundle} --version-id={version} "$JOB/bundle.tar.gz" --region {REGION} >/dev/null
echo "{sha}  $JOB/bundle.tar.gz" | sha256sum -c -
python3 - "$JOB/bundle.tar.gz" "$JOB" <<'PY_SAFE_ARCHIVE'
import pathlib,sys,tarfile,shutil
archive=pathlib.Path(sys.argv[1]);dest=pathlib.Path(sys.argv[2]).resolve()
if archive.stat().st_size > 24 * 1024**3:
    raise SystemExit('SPOT_BUNDLE_ARCHIVE_TOO_LARGE')
with tarfile.open(archive,'r:gz') as tar:
    members=[];names=set();total=0
    for m in tar:
        name=pathlib.PurePosixPath(m.name)
        if name.is_absolute() or '..' in name.parts or m.issym() or m.islnk() or m.isdev() or m.isfifo():
            raise SystemExit('SPOT_UNSAFE_ARCHIVE')
        if not (m.isdir() or m.isfile()) or not name.parts or name.parts[0] != 'bundle':
            raise SystemExit('SPOT_INVALID_ARCHIVE_ENTRY')
        canonical=str(name)
        if canonical in names or (dest/name).exists():
            raise SystemExit('SPOT_DUPLICATE_OR_EXISTING_ARCHIVE_ENTRY')
        names.add(canonical);members.append(m);total+=m.size
        if len(members)>200000 or total>30*1024**3:
            raise SystemExit('SPOT_ARCHIVE_EXPANSION_LIMIT')
    disk=shutil.disk_usage(dest)
    if disk.free-total<max(10*1024**3,disk.total//10):
        raise SystemExit('SPOT_ARCHIVE_DISK_RESERVE_REQUIRED')
    tar.extractall(dest,members=members,filter='data')
PY_SAFE_ARCHIVE
cd "$JOB"
mkdir -p output
{resume_setup}
timeout --signal=TERM --kill-after=45s {seconds} /opt/v083/venv/bin/python "$JOB/bundle/source/research/spot/v083_spot_worker.py" --workspace "$JOB" --job-id {cfg["job_id"]} --population {cfg["population"]} --source-commit {cfg["source_commit"]} --bucket {bucket} --prefix {prefix} --max-seconds {seconds}{resume_args}
"""


def _api(client=None, service="ec2"):
    if client is not None:
        return client
    # No Boto3 import or credentials required to review offline plan.
    import boto3
    return boto3.client(service, region_name=REGION)


def matching_instances(ec2, cfg=None, *, project_wide=False):
    filters = [{"Name": "tag:Project", "Values": [PROJECT]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped", "shutting-down"]}]
    if cfg is not None and not project_wide:
        filters.append({"Name": "tag:V083ResearchJob", "Values": [cfg["job_id"]]})
    found, token, seen = [], None, set()
    while True:
        args = {"Filters": filters}
        if token: args["NextToken"] = token
        response = ec2.describe_instances(**args)
        found.extend(i for block in response.get("Reservations", []) for i in block.get("Instances", []))
        token = response.get("NextToken")
        if not token: break
        if not isinstance(token, str) or token in seen:
            raise ValueError("SPOT_PAGINATION_INVALID")
        seen.add(token)
    return found


def managed_spot(instance, job_id=None):
    tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
    return (instance.get("InstanceLifecycle") == "spot" and tags.get("Project") == PROJECT
            and JOB_RE.fullmatch(tags.get("V083ResearchJob", "")) is not None
            and (job_id is None or tags.get("V083ResearchJob") == job_id)
            and tags.get("OOS") == "CLOSED" and tags.get("AutoReplace") == "DISABLED")


def preflight_readonly(ec2, s3, iam, cfg):
    """AWS reads only; never invoked by plan/status polling. No resource creation.

    Security/IAM review binds separately provisioned infrastructure to this job.
    Policy verification uses an exact reviewed policy fingerprint; AWS Policy
    Simulator is intentionally not treated as proof of absence of all privileges.
    """
    review = cfg.get("security_review", {})
    required = ("iam_policy_sha256", "reviewed_profile_arn", "no_broker_secrets", "least_privilege_confirmed")
    if not all(k in review for k in required) or review.get("no_broker_secrets") is not True or review.get("least_privilege_confirmed") is not True or review.get("reviewed_profile_arn") != cfg["instance_profile_arn"] or not SHA_RE.fullmatch(str(review.get("iam_policy_sha256"))):
        raise ValueError("SPOT_SECURITY_REVIEW_REQUIRED")
    sg = ec2.describe_security_groups(GroupIds=[cfg["security_group_id"]]).get("SecurityGroups", [])
    if len(sg) != 1 or sg[0].get("IpPermissions"):
        raise ValueError("SPOT_SECURITY_GROUP_MUST_HAVE_NO_INGRESS")
    images = ec2.describe_images(ImageIds=[cfg["ami_id"]]).get("Images", [])
    if len(images) != 1 or images[0].get("State") != "available" or images[0].get("Architecture") != "x86_64" or images[0].get("ImageId") != cfg["ami_id"]:
        raise ValueError("SPOT_AMI_NOT_VERIFIED")
    if images[0].get("RootDeviceType") != "ebs":
        raise ValueError("SPOT_AMI_EBS_ROOT_REQUIRED")
    subnet = ec2.describe_subnets(SubnetIds=[cfg["subnet_id"]]).get("Subnets", [])
    if len(subnet) != 1 or subnet[0].get("VpcId") != sg[0].get("VpcId"):
        raise ValueError("SPOT_SUBNET_SECURITY_GROUP_MISMATCH")
    profile_name = cfg["instance_profile_arn"].split("/")[-1]
    profile = iam.get_instance_profile(InstanceProfileName=profile_name)["InstanceProfile"]
    if profile.get("Arn") != cfg["instance_profile_arn"] or len(profile.get("Roles", [])) != 1:
        raise ValueError("SPOT_INSTANCE_PROFILE_NOT_VERIFIED")
    role = profile["Roles"][0]["RoleName"]
    # Dedicated worker profile: exactly one inline policy and no managed policy.
    attached = iam.list_attached_role_policies(RoleName=role)
    if attached.get("IsTruncated") or attached.get("AttachedPolicies"):
        raise ValueError("SPOT_UNREVIEWED_MANAGED_IAM_POLICY")
    listed = iam.list_role_policies(RoleName=role)
    if listed.get("IsTruncated"):
        raise ValueError("SPOT_IAM_POLICY_PAGINATION_UNREVIEWED")
    names = listed.get("PolicyNames", [])
    if len(names) != 1:
        raise ValueError("SPOT_EXACT_REVIEWED_INLINE_POLICY_REQUIRED")
    policy = iam.get_role_policy(RoleName=role, PolicyName=names[0])["PolicyDocument"]
    if isinstance(policy, str): policy = json.loads(urllib.parse.unquote(policy))
    digest = hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not isinstance(policy, dict):
        raise ValueError("SPOT_IAM_POLICY_DOCUMENT_INVALID")
    statements = policy.get("Statement", [])
    if isinstance(statements, dict): statements = [statements]
    resume_read = f"arn:aws:s3:::{cfg['bucket']}/{cfg['resume_prefix']}/*" if cfg.get("resume_prefix") else None
    allowed = {"s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:AbortMultipartUpload", "s3:ListBucket", "s3:GetBucketVersioning"}
    if not statements: raise ValueError("SPOT_EMPTY_IAM_POLICY")
    for statement in statements:
        actions = statement.get("Action", [])
        if isinstance(actions, str): actions = [actions]
        resources = statement.get("Resource", [])
        if isinstance(resources, str): resources = [resources]
        if statement.get("Effect") != "Allow" or statement.get("NotAction") or statement.get("NotResource") or not actions or not set(actions) <= allowed:
            raise ValueError("SPOT_IAM_ACTION_SCOPE_UNSAFE")
        allowed_resources = {f"arn:aws:s3:::{cfg['bucket']}/{cfg['bundle_key']}", f"arn:aws:s3:::{cfg['bucket']}/{job_prefix(cfg)}/*"}
        if resume_read and any(r == resume_read for r in resources):
            if actions != ["s3:GetObjectVersion"] or resources != [resume_read]:
                raise ValueError("SPOT_RESUME_READ_VERSION_ONLY_REQUIRED")
            continue
        if actions == ["s3:ListBucket"] or actions == ["s3:GetBucketVersioning"]:
            if resources != [f"arn:aws:s3:::{cfg['bucket']}"]:
                raise ValueError("SPOT_IAM_BUCKET_SCOPE_UNSAFE")
            if actions == ["s3:ListBucket"]:
                prefix = statement.get("Condition", {}).get("StringLike", {}).get("s3:prefix")
                if prefix not in (job_prefix(cfg) + "/*", [job_prefix(cfg) + "/*"]):
                    raise ValueError("SPOT_IAM_LIST_PREFIX_REQUIRED")
        elif not resources or not set(resources) <= allowed_resources:
            raise ValueError("SPOT_IAM_RESOURCE_SCOPE_UNSAFE")
        if any(a in ("s3:PutObject", "s3:AbortMultipartUpload") for a in actions) and resources != [f"arn:aws:s3:::{cfg['bucket']}/{job_prefix(cfg)}/*"]:
            raise ValueError("SPOT_IAM_WRITE_SCOPE_UNSAFE")
    if digest != review["iam_policy_sha256"]:
        raise ValueError("SPOT_IAM_POLICY_CHANGED")
    if s3.get_bucket_versioning(Bucket=cfg["bucket"]).get("Status") != "Enabled":
        raise ValueError("SPOT_BUCKET_VERSIONING_REQUIRED")
    public = s3.get_public_access_block(Bucket=cfg["bucket"])["PublicAccessBlockConfiguration"]
    if not all(public.get(k) is True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")):
        raise ValueError("SPOT_PRIVATE_BUCKET_REQUIRED")
    verify_bundle(s3, cfg)
    return {"security": "VERIFIED_READ_ONLY", "root_device": images[0]["RootDeviceName"]}


def verify_bundle(s3, cfg):
    head = s3.head_object(Bucket=cfg["bucket"], Key=cfg["bundle_key"], VersionId=cfg["bundle_version_id"])
    if head.get("Metadata", {}).get("sha256") != cfg["bundle_sha256"] or not 0 < head.get("ContentLength", 0) <= 24 * 1024**3:
        raise ValueError("SPOT_BUNDLE_HASH_OR_SIZE_MISMATCH")
    # Do not accept a version with an unreviewed / unpinned source.
    if head.get("Metadata", {}).get("source-commit") != cfg["source_commit"]:
        raise ValueError("SPOT_BUNDLE_SOURCE_MISMATCH")


def launch(cfg, *, ec2=None, s3=None, iam=None, approval=False, approval_record=None, control_dir=None):
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
    # Freeze caller-owned configuration before checking approval or creating clients.
    cfg = json.loads(json.dumps(cfg, allow_nan=False))
    validate_config(cfg)
    verify_approval(cfg, approval_record)
    cost_breakdown(cfg)
    ec2 = _api(ec2)
    s3 = _api(s3, "s3")
    iam = _api(iam, "iam")
    with controller_lock(control_dir) as root:
        return _launch_locked(cfg, ec2, s3, iam, root)


def _launch_locked(cfg, ec2, s3, iam, root):
    registry_path = root / "launch-registry.json"
    if registry_path.exists():
        registry = json.loads(registry_path.read_text())
        # Ambiguous creation cannot be retried from a changed mutable config.
        if registry.get("phase") in ("CREATING", "CREATE_UNKNOWN"):
            raise ValueError("SPOT_UNRESOLVED_CREATE_REQUIRES_RECONCILIATION")
        if registry.get("job_id") == cfg["job_id"]:
            raise ValueError("SPOT_JOB_ALREADY_ATTEMPTED_NEW_APPROVAL_AND_JOB_REQUIRED")
    current = matching_instances(ec2, cfg, project_wide=True)
    if current:
        raise ValueError("SPOT_DUPLICATE_OR_EXISTING_JOB_BLOCKED")
    verified = preflight_readonly(ec2, s3, iam, cfg)
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
        BlockDeviceMappings=[{"DeviceName": verified["root_device"], "Ebs": {
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
    params["TagSpecifications"].append({"ResourceType": "volume", "Tags": list(params["TagSpecifications"][0]["Tags"])})
    record = {"job_id": cfg["job_id"], "config_sha256": config_digest(cfg), "phase": "CREATING", "deadline_utc": deadline.isoformat()}
    _write_registry(root, record)
    try:
        response = ec2.run_instances(**params)
    except Exception:
        _write_registry(root, {**record, "phase": "CREATE_UNKNOWN"})
        # A timed-out create can still have succeeded; the idempotency token
        # prevents second allocation. Never attempt automatic repeat.
        raise
    ids = [i["InstanceId"] for i in response["Instances"]]
    if len(ids) != 1:
        _write_registry(root, {**record, "phase": "CREATE_UNKNOWN"})
        raise RuntimeError("SPOT_UNEXPECTED_INSTANCE_COUNT")
    _write_registry(root, {**record, "phase": "REQUESTED", "instance_id": ids[0]})
    return {"status": "REQUESTED_ONE_TIME_SPOT", "instance_id": ids[0],
            "job_id": cfg["job_id"], "deadline_utc": deadline.isoformat(),
            "automatic_relaunch": False}


def status(cfg, *, ec2=None, control_dir=None):
    """Read-only observation, never clears ambiguous creation or launches a job."""
    ec2 = _api(ec2)
    active = matching_instances(ec2, cfg)
    root = Path(control_dir or CONTROL_DIR)
    registry_file = root / "launch-registry.json"
    registry = {}
    if registry_file.is_symlink():
        raise ValueError("SPOT_CONTROL_SYMLINK_BLOCKED")
    if registry_file.is_file():
        registry = json.loads(registry_file.read_text())
    pending_unknown = registry.get("phase") in ("CREATING", "CREATE_UNKNOWN")
    last_instance = None
    identifier = registry.get("instance_id")
    if registry.get("job_id") == cfg["job_id"] and isinstance(identifier,str) and re.fullmatch(r"i-[a-f0-9]{8,17}",identifier):
        response = ec2.describe_instances(InstanceIds=[identifier])
        previous = [i for reservation in response.get("Reservations",[]) for i in reservation.get("Instances",[])]
        if len(previous)==1 and previous[0].get("InstanceId")==identifier and managed_spot(previous[0],cfg["job_id"]):
            last_instance = {"id":identifier,"state":previous[0]["State"]["Name"]}
    return {"job_id": cfg["job_id"], "instances": [
        {"id": i["InstanceId"], "state": i["State"]["Name"], "verified_managed_spot": managed_spot(i,cfg["job_id"])} for i in active
    ], "launchRegistry": registry, "lastInstance": last_instance, "reconciliationRequired": pending_unknown,
        "automatic_relaunch": False,
        "note": "No visible active instance is not proof that a timed-out creation failed. Unknown creates require operator reconciliation."}


def terminate(cfg, *, ec2=None, approval=False):
    if approval is not True:
        raise ValueError("SPOT_EXPLICIT_STOP_APPROVAL_REQUIRED")
    client = _api(ec2)
    found = matching_instances(client, cfg)
    ids = [i["InstanceId"] for i in found if managed_spot(i, cfg["job_id"]) and i["State"]["Name"] not in ("shutting-down", "terminated")]
    if not ids:
        return {"terminated": [], "note": "no active Spot worker"}
    # Only instances carrying this immutable job tag can be terminated.
    client.terminate_instances(InstanceIds=ids)
    return {"terminated": ids}


def reap(cfg, *, ec2=None, current_time=None):
    """Independently scheduled 5-minute TTL termination. No launch capability.

    Must be installed as a separate systemd timer or cron on the existing host.
    Reads every project-tagged Spot job, independent of mutable current-job config.
    """
    if os.environ.get("V083_SPOT_REAPER_ENABLED") != "YES":
        raise ValueError("SPOT_REAPER_SWITCH_REQUIRED")
    client = _api(ec2)
    current_time = current_time or datetime.now(timezone.utc)
    expired = []
    for i in matching_instances(client, cfg, project_wide=True):
        if i["State"]["Name"] not in ("pending", "running", "stopping"):
            continue
        tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
        if not managed_spot(i):
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
    p.add_argument("--approval-file", type=Path, help="Private server-side per-job cost consent receipt")
    a = p.parse_args(argv)
    try:
        cfg = None if a.command == "reap" else load_config(a.config)
        if a.command == "plan": result = display_plan(cfg)
        elif a.command == "status": result = status(cfg)
        elif a.command == "launch": result = launch(cfg, approval=a.approve_launch, approval_record=json.loads(a.approval_file.read_text()) if a.approval_file else None)
        elif a.command == "stop": result = terminate(cfg, approval=a.approve_stop)
        else: result = reap(cfg)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        p.exit(2, "SPOT BLOCKED: " + str(error) + "\n")


if __name__ == "__main__":
    main()
