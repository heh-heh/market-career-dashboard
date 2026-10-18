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
