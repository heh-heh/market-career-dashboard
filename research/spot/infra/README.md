# V083 infrastructure — NOT DEPLOYED

## Private S3 storage template (not deployed)

`storage.json` is an **optional review-only** CloudFormation template for a
**new, globally unique** private S3 bucket. It sets bucket versioning to
`Enabled`, AES256 default encryption, all four public-access blocks, bucket-owner
enforced ownership, TLS-only access, and **Retain** on deletion/replacement.
It does **not** create a bucket merely by existing in GitHub; applying a stack
requires separate AWS resource and cost approval. A reviewed existing bucket
meeting the same constraints may be reused instead.

Versioning retains old data and can grow storage cost. This template intentionally
has **no automatic object deletion, replication, access-logging bucket or lifecycle
expiration**: a retention policy must be reviewed together with checkpoint
restoration and cost before activation. A stack delete will not delete the bucket
or its versioned contents; explicit owner cleanup is a separate paid/sensitive action.

`research/test_v083_spot_infra.py` checks these safety invariants offline and
verifies `worker.json` reuses (rather than recreates) the bucket. Neither
CloudFormation template is deployed in the present task.

These are review-only templates. No resource has been created by this change.
Deploying IAM/security-group changes needs security approval; bucket/EBS/AMI,
network, EventBridge/Lambda and compute may incur charges and require separate
cost approval. Never deploy automatically from a dashboard request.

`worker.json` creates a dedicated instance profile with a per-job inline policy
and a no-ingress security group, using an **existing versioned private bucket**.
Change JobId only through a reviewed new stack/change set. Worker cannot call
EC2/SSM/SecretsManager or broker APIs; do not attach trading credentials, user
secrets, arbitrary additional policies or public web servers to the image.
Run controller read-only preflight to record exact canonical JSON policy SHA256
and instance profile ARN. No blanket AdministratorAccess/PowerUserAccess.

AMI contract:
* Reviewed x86_64 Linux image, cloud-init, GNU timeout, systemd/shutdown, AWS CLI.
* `/opt/v083/venv/bin/python` with pinned repository dependencies and boto3.
* No broker keys, production .env, SSH private keys, AWS static credentials,
  Token Broker sockets or SSM remote-command agent credentials in image/bundle.
* Root encrypted gp3 volume deleted on termination; instance shutdown behavior
  must be terminate, IMDSv2 mandatory; no persistent Spot/ASG/On-Demand fallback.
* HTTPS egress must reach regional S3/EC2 metadata path as appropriate. Prefer
  existing S3 endpoint routing. Do not create NAT/public IPv4 without cost review.

Independent deadline protection:
1. Launcher boot sets shutdown deadline; child timeout reserves upload time.
2. Existing-host `research/spot/systemd/v083-spot-reaper.timer` checks job-tag TTL
   independently of worker. Requires explicit operator installation, IAM review
   and active timer before launch. It never launches or retries compute.
3. Reaper controller must remain available during approved run. Simultaneous
   host outage/IAM/network failure remains a cost risk: no AWS design provides a
   mathematically hard billing cap. Cloud-side scheduled Lambda/EventBridge
   watchdog is recommended only after separate resource/cost approval, not
   silently created here. Reaper expiry should include the shutdown/upload grace
   period in estimate. Existing production collectors are never stopped.

Durability: each completed five-day COMMITTED checkpoint captures ledger byte
prefixes and uploads content-addressed, SHA256+VersionId pinned objects before
an immutable commit manifest. Live status is a hint; committed manifests are
truth. Upload failure stops training; catastrophic interruption can lose work
since the last successfully uploaded commit, never invent it. A successor needs
new paid launch approval and a separately reviewed read grant to the old job
prefix; no automatic replacement/retry. Do not broaden the new worker's normal
write prefix. Run --resume-reference exact commit ref; source/plan/code hashes
must match and each artifact prefix is verified. The controller allows only the explicitly configured old prefix with GetObjectVersion
for restoration, and requires its exact policy hash to be reviewed again. Extra
old-prefix writes or unversioned reads are blocked. Local offline restore is available.

Example cost review must include: Spot bid×wall hours + termination grace,
80GiB gp3 duration, bundle/results versioned storage, S3 request/transfer,
network/NAT/IPv4 and tax. Content-addressing avoids repeated identical uploads,
but per-generation snapshots and status every 15 seconds still incur requests.
Never state a total hard cap from compute-only estimate.

Worker durability transport has conservative per-worker attempted-upload limits:
8GiB payload and 100,000 PUTs. Exceeding either fails closed and preserves local
artifacts until the instance deadline; it does not silently spend indefinitely.
These are not a complete AWS billing cap (HEAD/GET/retention/transfer/tax remain).
Large runs may stop at the quota; increase only with a separate reviewed budget.
