# V0.8.3 Spot — zero-billing verification (2026-10-18)

> **Review branch only. No deployment, merge, AWS creation, paid API, long-run training, S3 upload or IAM mutation authorized.**  
> Base reviewed: `strategy-engine-v3` @ `5252b8856ab6bb171e1ac645ed816cd75b811c9c`.

## Confirmed without additional AWS resources

- [PR #2](https://github.com/heh-heh/market-career-dashboard/pull/2), original Codex head `9fda55fef14b3d010e68ada235000d33fd5c2261`, was **already merged** into `strategy-engine-v3` on **2026-10-18 04:49:49 UTC**. This review did not merge or deploy it.
- [Latest inspected strategy-engine CI](https://github.com/heh-heh/market-career-dashboard/actions/runs/39107181596) concluded **success** for `5252b8856a...`. Earlier Codex PR CI also passed; these are **mock/offline tests**, not live Spot performance proof.
- **AWS Tokyo read-only check:** EC2 `DescribeInstances` filtered on project tag `Project=V083-Research-Only` returned **0 matching instances** at review time. This does *not* prove no untagged instances in the account, nor that a Spot capacity request will succeed.
- EC2 Standard Spot vCPU quota: **32**, retrieved by `service-quotas:GetServiceQuota`, `L-34B43A08` (Tokyo). This is quota, not inventory/capacity or a guaranteed launch.
- Source provides double paid consent, train-only one-time worker, stop/reaper, immutable S3-pinned checkpoint references, a bootstrap script and restore tests. These are **source capabilities**, not deployed infrastructure.
- `research/spot/offline_readiness.py` performs an **offline static report only**. It does not import boto3, make HTTP requests, use AWS permissions, execute a job, or modify files. It intentionally **never returns `readyForPaidLaunch=true`** merely because source code and tests exist.

## Release-gate matrix

| Gate | Evidence | Decision |
|---|---|---|
| PR #2 code integrated | GitHub PR #2 merged | **Confirmed** |
| Engine regression CI | GitHub CI passed on inspected branch commit | **Confirmed for that commit** |
| No tagged Spot worker at time checked | EC2 read-only DescribeInstances | **Confirmed, point in time** |
| Account Spot quota ≥ 8 vCPU | AWS quota 32 vCPU | **Confirmed; no capacity guarantee** |
| Versioned private S3 with reviewed bundle V2 | No S3 creation/upload approved in current task | **Not verified — block launch** |
| x86_64 reviewed image and attached instance profile | No IAM/AMI creation approved | **Not verified — block launch** |
| No-ingress SG, compatible subnet and controlled egress | No infra deployment approved | **Not verified — block launch** |
| Timed termination with independently running systemd reaper | No timer install/service start approved | **Not verified — block launch** |
| Operator per-job private config/source/plan hash | No approved private settings inspected here | **Not verified — block launch** |
| Real Spot interruption + deterministic S3 resume | Only synthetic/mocked tests shown | **Not verified — block live training** |
| Actual invoice total including S3/EBS/network/tax | Spot example budget not an invoice cap | **Not verified; separately approve** |

**Verdict: SOURCE_IMPLEMENTED / CI_GREEN, INFRA_UNPROVISIONED_OR_UNVERIFIED, PAID_LAUNCH_BLOCKED.**

## Offline-only developer verification

Run on an existing clean code checkout (no AWS credentials necessary):

```bash
python -m research.spot.offline_readiness --repo .
python -m unittest research.test_v083_spot_offline_readiness -v
# Wider no-provision regression checks, if desired:
python -m unittest research.test_v083_resume research.test_v083_spot_lifecycle research.test_v083_spot_safety research.test_v083_spot_worker
```

The report accepts an **optional** local `--private-config /path/to/file.json` for minimal structural checks. It does not print raw values, credential fields, S3 identifiers, network IDs, secrets or approval tokens. **Do not put real configuration into a public commit.** It does not validate AWS IAM permissions, current prices, data provenance or network reachability; those require a separately authorized preflight and later approved pilot.

## Next executable work that needs no deployment or billing

1. Review the original PR #2 differences and the entire `research/spot/V083_SPOT_READINESS.md` and `infra/` templates for consistency with the actual V0.8.3 campaign.
2. Run locally only the offline readiness report and test suite; fix regressions on **review branches**, not on the running production checkout.
3. Establish a versioned, hash-pinned **documentation-only** operator checklist for creating S3/IAM/AMI/reaper *after* cost/security authorization.
4. Keep default Spot state **off** and UI paid-launch button blocked. Do not merge, deploy or apply any IaC under this request.
5. When the user explicitly authorizes future resource setup, collect updated Tokyo spot prices, S3/EBS/network estimates, and per-job maximum spend before changes; set up infrastructure and run a small paid pilot **only under the separate new authorization**.

## Safety boundaries

No main-branch changes, GitHub Pages deployment, AWS SSM installation, `systemctl` action, S3 requests, EC2 provisioning, IAM edits, broker API, real order, Final OOS opening, or study execution are part of this review. The separate audit branch must remain unmerged until reviewed.


## Follow-up: actual account resources, 2026-10-19 (read-only)

> **This section supersedes the earlier assumption that ALL infrastructure was absent.** The 2026-10-18 review only tested the *instance* tag filter. New account-wide read-only checks observed existing infrastructure. Nothing was created, changed, merged, installed, launched or billed by this follow-up. Resource creation provenance beyond AWS stack timestamps was not investigated.

| Gate / evidence | Live observed state | Release decision |
| --- | --- | --- |
| Tokyo EC2 | Dashboard \`t8i.small\` running, SSM online; separate shorts \`t3.micro\` stopped | Preserve both |
| Spot workers | Zero Spot requests and zero Spot instances account-wide at time checked | No live verification |
| Spot vCPU quota | Standard Spot 32 vCPU | Quota OK; capacity not guaranteed |
| Private S3 | Dedicated V083 research bucket exists; versioning Enabled, AES256 encryption, all four public access block settings enabled, bucket policy exists | Storage **container** exists; version-pinned TRAIN bundle/checkpoints and permissions still unverified |
| Dedicated worker IAM/network | \`v083-spot-worker-infra\` CloudFormation stack \`CREATE_COMPLETE\`; worker EC2 role/profile and security group (0 ingress rules) exist | Verify the exact per-job role/bundle/SG/subnet contracts and security tests before use |
| AMI | No account-owned AMIs reported in Tokyo | **Block paid launch** until reviewed x86_64 worker image/bootstrap proof |
| Controller attached instance profile | Running dashboard instance currently uses \`youtube-shorts-ssm-role\` with \`AmazonSSMManagedInstanceCore\` only. A separate dormant \`market-dashboard-ec2\` role exists with SSM+CloudWatch policies but is **not attached to the dashboard**. | **Block paid launch**; a reviewed controller authorization / profile association is still required. Do not silently repurpose shared roles |
| Independent TTL reaper | Host timer installation/activity not verified by AWS resource metadata | **Block paid launch**; separately inspect on host, stage reviewed installation later |
| AWS Budget | No account budgets returned | Optional guardrail not configured; a budget is an alert, not a hard stop |
| Main/Pages deployment | Spot source is on \`strategy-engine-v3\`, not repository default \`main\`; host overlay/restart not verified | UI/backend production availability **not proven** |
| Cost sample | 2026-10-18 UTC Spot history for \`c6i.2xlarge\` Tokyo 1a ~$0.1977/hr, 1d ~$0.2050/hr | Approximately $1.58–$1.64 **compute-only** at 8h at those samples; re-quote before any future paid approval |

AWS CloudFormation stack creation time was 2026-10-18 04:59 UTC. Its only listed resources were a worker role, worker profile and security group. No reviewed worker image was observed. Existing research bucket exists separately; this read did not inspect object contents or issue S3 uploads.

**Critical no-billing next steps** (do these on separate review branches, not in a production checkout): review the current CF template/actual worker IAM and bucket policy for exact job scoping; check source/AMI/bootstrap contracts statically; reconcile controller/worker role isolation; confirm backend/UI deployment commands without running them; complete offline \`python -m research.spot.offline_readiness --repo .\` and CI. S3 object inventory, IAM changes, AMI creation, server install, reaper activation, any paid API/data movement, Spot creation, long training and production deployment remain separate approvals.

**Updated verdict:** \`PARTIAL_INFRA_EXISTS / CODE_CI_GREEN / CONTROLLER_UNPREPARED / IMAGE_UNVERIFIED / REAPER_UNVERIFIED / PAID_LAUNCH_BLOCKED\`.
