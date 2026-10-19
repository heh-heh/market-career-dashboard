# V083 first-launch investigation — actual training verification pending

## Evidence boundary

PR #6 head `bcea1d858bc7c932cbd52906c6ac6608f3f17c18` was inspected first;
its GitHub Strategy engine check succeeded. This follow-up starts from that head.

Operator-reported facts, NOT independently re-read in this Codex session:
Tokyo worker `i-022624f1d0c8d02a1` terminated, no active research Spot or residual
research volume, only exit-code.txt for job `v083-20261019b4d6113b`, followed by
INTERRUPTED and CREATE_UNKNOWN / already-attempted rejection. These facts do NOT
establish which bootstrap command failed or whether the guest or reaper terminated it.
The value of exit-code.txt and cloud-init/console evidence were not supplied.

AWS credential discovery here failed with `ProfileNotFound`; no AWS requests,
instances, IAM changes, uploads, deployment, service restart or long training were
performed. Initial real-launch root cause remains **UNCONFIRMED**.

## Proven code defects and corrections

1. Every controller launch exception became CREATE_UNKNOWN, including a consumed
   job ID rejected before RunInstances. Now deterministic local preflight rejection
   is FAILED without claiming an allocation attempt. Only finite, explicit EC2
   service rejection codes close the allocation journal; network/idempotency
   uncertainty remains blocked.
2. Status explicitly excluded CREATE_UNKNOWN from terminal transitions. Reconciliation
   now requires positive research tags AND ClientToken AND complete paginated EC2
   observations, including terminated instances and a known ID when present. Missing
   instances never prove success. A live member prevents terminal transition.
3. Only the latest job was remembered. Per-job attempted markers now prevent reusing
   an earlier ID even after a later job, preserving legacy journal evidence.
4. Calibration writes `capacity/<unique-run>/capacity.json`, whereas the worker
   searched only `capacity/capacity.json`. The worker now resolves exactly one
   certificate, rejects ambiguity/symlinks, and still requires MEASURED_SAFE and the
   existing train-time resource/hash checks. Synthetic fixtures now use the real
   nested layout. This bug prevented transition after successful calibration;
   it is NOT asserted to be the initial failed instance's observed root cause.
5. Before-worker failure exported only an exit code. Bootstrap now emits bounded,
   fixed-code stage diagnostics to console and `<job-prefix>/bootstrap/status.json`.
   Raw package/command logs, User Data, commands, environment, credentials and market
   records are never exported. Cleanup preserves the original exit code. An upload
   failure cannot prevent shutdown; console retains safe evidence if AWS CLI/IAM
   itself is unavailable.
6. Archive extraction used system `python3` with a version-dependent tarfile filter.
   It now uses the prepared venv Python and manual bounded regular-file extraction,
   rejecting links, traversal, duplicates and disk exhaustion. The old signature is
   a compatibility risk, NOT a remotely proven first-failure cause.
7. An immutable bundle pinned both original job and code. An optional separately
   versioned/hash-pinned **code-only runtime** now permits a fresh job to reuse the
   original data bundle without rewriting its manifest, archive or file hashes.
   Runtime/source identity and original bundle identity are independently verified.

## Preservation and cost boundary

The original 2022–2023 bundle remains unchanged:
`v083/bundles/v083-20261019b4d6113b.tar.gz`, operator-provided SHA256
`843d30993c29446b48f25cee4818fb45e7e2f1e2fd385abe4047def99a146f2d`.
No object version or source commit was guessed. Fetch them from reviewed existing
metadata when request costs/permissions are approved. Worker training remains
2022–2023 only; 2024+ session plans are rejected. 2026 OOS stays CLOSED.

Default m7i-flex.large, 10 bots, 2–50 manual bounds, 8 hours, $0.08/hour bid,
$0.20/hour bid ceiling and $0.25/hour planning envelope remain. Repeated resource
calibration is mandatory. No paid-account upgrade, On-Demand, ASG or automatic retry.
Independent TTL reaper remains necessary; bootstrap diagnostic GET/PUTs have request
costs and must be included in the approved estimate. No hard AWS invoice cap is claimed.

## Operator's read-only evidence command (not executed here)

From a reviewed checkout containing this follow-up and its Python dependencies:

```bash
python -m research.spot.diagnose_v083_spot \
  --config /mnt/research/spot-control/v083-spot-config.json \
  --instance-id i-022624f1d0c8d02a1 \
  --out /mnt/research/v083-first-instance-evidence-20261019.json
```

This permits disabled launch configuration, never changes state/resources, and
returns SHA256/allowlisted error patterns instead of raw User Data/console. Missing
terminated-instance console evidence is reported explicitly. S3 diagnostics require
`--read-s3-diagnostics` **only after approving possible S3 GET charges**. A pattern
alone remains a hypothesis until correlated with stage/exit evidence. User-initiated
termination alone does not distinguish guest shutdown from controller/reaper action.

## Reuse old data with a reviewed new runtime (preparation only)

```bash
python -m research.spot.prepare_v083_runtime --repo . \
  --out /mnt/research/v083-runtime-UNIQUE.tar.gz
```

Requires a clean pinned code revision and a new output filename. No data download,
S3 upload, infrastructure creation or training occurs. Uploading this small runtime
is a separately approved storage/request operation; this task did NOT perform it.

A new private job configuration must bind:

* new `job_id`, new result prefix/approval, reviewed matching campaign population;
* original `bundle_key`, `bundle_sha256`, exact `bundle_version_id` unchanged;
* original `bundle_job_id` and `bundle_source_commit` from verified metadata;
* new runtime `source_commit`, `runtime_key`, `runtime_sha256`, `runtime_version_id`.

Do not fake values or reuse the old job ID. IAM must allow exact pinned bundle and
runtime version reads and ONLY the new output prefix writes. Controller needs exact
bootstrap/live-status reads if monitoring is approved. Updated example policies
are review-only and were not applied. The existing safe installer also deploys the
new bootstrap helper; no production overwrite or restart was executed here.

## Remaining acceptance

**실제 학습 검증 대기.** First obtain real failure evidence, review/deploy the code,
review IAM/runtime/config/TTL, then separately approve one bounded paid pilot. Verify
boot → download → hash/extract → dependencies → repeated calibration → actual first
training generation → durable S3 checkpoint → termination. Current data collection,
trading, Token Broker, historical artifacts and operating EC2 remain untouched.

The reviewed worker inline policy must actually declare PutObject **and**
GetObjectVersion on its new output prefix (versioned HEAD validates durability),
as well as bundle/runtime version reads. Missing declarations now block before
allocation. Extra boundary/SCP/bucket-policy conditions still require actual AWS
review; static scope checks do not prove effective access. Add the review-only
`infra/runtime-read.fragment.example.json` statement to the SAME dedicated inline
policy, not an extra unreviewed policy; recalculate/review its SHA. No policy was
applied here. Bootstrap diagnostic writes explicitly request SSE AES256.
