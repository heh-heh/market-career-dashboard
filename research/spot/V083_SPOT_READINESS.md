# V0.8.3 Spot readiness — research only

## What changed

* Corrected bootstrap's worker source path and text/binary logging failure.
* Version-2 bundles pin every source/data/audit file before bundled code import.
  Old bundles must be rebuilt; they are intentionally rejected.
* V083 training commits generation zero and every completed five-session generation.
  `--resume-from` restores genome, RNG, lifetime accounts and counters into a NEW
  output run. Only committed ledger byte prefixes are copied. Code/config/data-plan
  hashes must match. Partial next-generation evaluations are replayed, not counted twice.
* Worker uploads immutable SHA256/VersionId-addressed checkpoint manifests throughout
  training. Final completion requires successful durable upload. A new job may restore
  an explicitly pinned previous-job commit with a separately reviewed version-read grant.
* Global controller flock + durable allocation journal + project-wide EC2 pagination
  prevent duplicate allocation. Unknown creation is never automatically retried.
* One-time Spot only; no On-Demand fallback, fleet replacement or autonomous relaunch.
  Default deadline is eight hours: instance-local shutdown, child timeout with upload
  reserve, plus independent existing-host TTL reaper. Only proven research-tagged Spot
  instances can be stopped. Existing production instances are excluded.
* Web flow separates **prepare → approve actual charges → approve instance creation**.
  All endpoints retain existing admin authentication. Disabled/unreviewed configuration
  cannot enable launch. Private config changes invalidate outstanding consent.
* Live status reports real training progress/checkpoint refs; no fabricated performance.
  Stopping is not treated as terminated until verified; final result must be durable.

## HTTP contract

Authenticated GET `/api/research/v083/spot/plan`, `/status`.
Authenticated POST `/api/research/v083/spot/prepare`, `/approve`, `/start`, `/stop`.
No user paths, shell commands, arbitrary instance types or OOS dates are accepted.
The legacy console's combined paid launch shortcut is disabled.

`plan` and `prepare` are local-only. After an approved launch, EC2 status is read-only;
S3 live-status polling additionally requires private `monitor_reads_approved: true`
because S3 requests can be billable. Defaults do not grant this permission.

## Deployment prerequisites — NOT applied by development

No AWS resources or IAM/service changes were applied. No EC2/data upload/long training
was requested during development. Standard-runner public-repository CI is permitted
only after checking the repository visibility and workflow runner. AWS profile discovery
failed locally (`ProfileNotFound`); account configuration/quotas/prices remain unverified.

Before enabling paid launch, an operator must separately approve/review:

1. Existing account/VPC/subnet and Tokyo Spot vCPU quota; c6i.2xlarge is the baseline.
2. Verify the optional SDK on the controller: `python -c "import boto3"`; install
   `research/spot/requirements.txt` only in the reviewed environment, never silently
   change a running API environment. A dedicated secret-free x86_64 AMI with `/opt/v083/venv/bin/python`, AWS CLI,
   cloud-init/systemd and existing dependencies. Building/storing AMI may incur fees.
3. Versioned private S3 bucket and reviewed train-only V2 bundle. Existing data remains
   read-only. Source files containing 2026 rows are rejected, not copied or rewritten.
   Staging a sanitized research copy is a separate reviewed operation if required.
4. Review-only `infra/worker.json`: dedicated job-scoped role/profile and no-ingress SG.
   Do not apply it automatically or expand the operating server's permissions silently.
   Controller needs narrowly scoped EC2 create/tag/describe/terminate and PassRole for
   that exact profile, plus IAM/S3 preflight and explicitly approved monitoring reads.
5. Install reviewed `systemd/v083-spot-reaper.{service,timer}` independently of dashboard
   request/worker processes, verify its executable path/environment/permissions, then
   explicitly approve enabling it. Development did not restart/install any service.
6. Review private `/mnt/research/spot-control/v083-spot-config.json`: exact bundle version,
   SHA, source revision, new job ID, campaign/population, IAM policy hash, cost estimates.
   Require successful local V083 smoke and source evidence. Change population via a new
   campaign; do not rewrite existing campaign/artifacts.
7. Only after approval, set V083_SPOT_LAUNCH_ENABLED=YES,
   V083_SPOT_REAPER_READY=YES and V083_SPOT_REAPER_ENABLED=YES in the reviewed controller
   environment. These are prerequisites, never settings auto-enabled by the UI.

The reaper is independent of the training process, but an unavailable controller host
plus failed guest shutdown can delay termination. Cloud-side scheduled watchdog requires
separate resource/cost approval and is **not deployed**. An active timer alone cannot
prove IAM/network correctness. A paid pilot is still required before production readiness.

## Costs and limits

The example is a budget scenario, NOT an AWS quote: $0.25/hour bid × 8 hours = $2.00
compute; five example additional cost reserves of $0.10 give $2.50 estimated total
under a $3.00 reviewed budget. Replace every reserve with account/region-specific
storage, request, transfer, IPv4/NAT and tax estimates before approval. Current Spot
price/availability and AMI costs were not verified. Eight-hour budget is not a hard
invoice cap: termination delay and retained versioned storage can exceed it.

Worker transport caps attempted upload payload at 8GiB and PUT attempts at 100,000;
limits fail closed, not infinite retries. They do not cap GET/HEAD, storage retention,
transfer or tax. No automatic raw-data or artifact deletion exists.

## Local validation commands (no AWS)

```bash
python -m unittest research.test_v083_spot_lifecycle research.test_v083_spot_safety research.test_v083_spot_worker research.test_v083_spot_control research.test_v083_resume
python -m unittest discover -s research -p 'test_*.py'
node research/test_v083_spot_web_smoke.cjs
```

Synthetic data/mocked AWS only; never label these as EC2 training results.
GitHub API confirmed `private=false` and the research workflow uses standard
`ubuntu-latest`. Official billing documentation states this combination is free:
https://docs.github.com/en/billing/concepts/product-billing/github-actions
No paid/larger runner or external paid service is authorized. CI results must be
reported separately from local checks; never infer success merely from a push.

## Safe preparation commands — do not launch or alter operating checkout

Use an existing clean tools checkout and fetch only; never pull/reset/clean/stash the
locally modified production checkout. Review installer `--help`, then its check mode.
`--apply` / API restart are NOT approved by this development task and were not executed.
Bundle preparation below creates a separate local archive and does not upload it:

```bash
python -m research.spot.prepare_v083_spot_bundle --help
python -m research.spot.v083_spot_lifecycle --help
python research/evolution/run_v083.py --help
python scripts/install_v083_admin_api_overlay.py --help
```

Actual paid launch remains behind both web confirmations and all operator preconditions.
An interrupted run needs a new job ID, newly reviewed bundle/profile, explicit previous
commit reference and a new cost/creation approval; automatic replacement is prohibited.

## Remaining verification

Live AWS identity/quotas, real AMI/bootstrap/network/IAM, durable uploads under actual
interruption, real-price estimate, timer watchdog and browser-to-EC2 authentication
must be checked in an approved pilot. No claim is made that a button can launch before
these prerequisites. Existing V082/V083 policy rules, live execution, collectors,
raw data, paper history and 2026 CLOSED boundary are preserved.

The optional legacy V082 browser fixture now explicitly opens its archived/inert
runtime and serves past-research.js; production archive visibility is unchanged.
