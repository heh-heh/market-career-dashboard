# V0.8.3 Dedicated EC2 Spot Research Worker — Design & Cost Plan

> **Design review / cost estimate only.** 2026-10-18, Asia/Tokyo (`ap-northeast-1`).  
> **NOT IMPLEMENTED:** No Spot worker, new EBS, S3 bucket, VPC resource, instance profile, IAM role, AWS Budget, or research job has been created by this plan.  
> **Changes in this commit:** documentation only. Existing 2026 Final OOS gate remains CLOSED; existing V0.8.2/0.8.3 data, real accounts, and AWS daemon remain untouched.

## Executive decision

Keep the existing `t8i.small` control/web/data host running as-is, and allow a **separate, opt-in, transient Linux x86-64 Spot instance** to execute long V0.8.3 backtests. Initial evaluation: `c6i.2xlarge` (8 vCPU / 16 GiB), fallbacks `c7i.2xlarge` and `m7i.2xlarge` (8 vCPU / 32 GiB), later compare `c6i.4xlarge` (16 vCPU / 32 GiB). Use at most one worker and no automatic scale-up until verified.

**Critical blocker:** Current V0.8.3 writes generation `checkpoint.json`, RNG state, final population, run/status/lineage files, but its entry point has **no validated checkpoint resume mode**. A Spot interruption can otherwise force a full restart. Implement and test resumability **before paid Spot runs**.

## Confirmed current infrastructure (AWS API read-only on 2026-10-18)

- Existing EC2 `t8i.small`, `ap-northeast-1a`, state `running`, instance profile attached.
- Three attached gp3 EBS volumes: root **60 GiB**, other **256 GiB**, other **2,048 GiB**. Root and 2,048-GiB disk are not marked encrypted; 256 GiB disk is encrypted. This is existing configuration only; this project will not alter it.
- **Do not create or snapshot a second 2-TiB EBS** merely to provide the worker with source data. gp3 at an indicative Tokyo $0.096/GiB-month would be ~**$196.61/month** for a full 2,048-GiB duplicate, without snapshot overhead.
- V0.8.3 operator range **2–50 bots** and exactly **2 genetic parents**. The latest engine advances forward even through zero-profit generations. Existing 45-bot failed job and checkpoint must not be silently resumed under changed evolution semantics.
- Engine uses Python minute/day-based research simulation and host CPU/RAM/swap guard, not a GPU-trained giant neural network. **More vCPUs alone do not ensure proportional speedup.** First measure 8-vCPU utilization and per-day wall time; parallelism requires an independent deterministic refactor.

## Checked sample Spot prices (actual AWS DescribeSpotPriceHistory, Linux, 2026-10-18)

| Region / AZ | Instance | vCPU/RAM | USD/hour |
|---|---|---|---:|
| Tokyo 1a | c6i.2xlarge | 8 / 16 GiB | 0.1964 |
| Tokyo 1c | c6i.2xlarge | 8 / 16 GiB | 0.2067 |
| Tokyo 1d | c6i.2xlarge | 8 / 16 GiB | 0.2065 |
| Tokyo 1a | c7i.2xlarge | 8 / 16 GiB | 0.2230 |
| Tokyo 1a | m7i.2xlarge | 8 / 32 GiB | 0.2190 |
| Tokyo 1a | c6i.4xlarge | 16 / 32 GiB | 0.3694 |
| Tokyo 1a | c7i.4xlarge | 16 / 32 GiB | 0.4768 |

**Examples (compute charge only, assume this sampled rate stays unchanged):**

| Machine | 10h | 40h | 80h |
|---|---:|---:|---:|
| c6i.2xlarge (1a) | $1.96 | $7.86 | $15.71 |
| c7i.2xlarge (1a) | $2.23 | $8.92 | $17.84 |
| c6i.4xlarge (1a) | $3.69 | $14.78 | $29.55 |

Spot is variable, region/AZ-specific, and can become unavailable. Prices are examples **not fixed quotes or a guarantee of performance**. Compare availability/price before each approved launch, without violating a maximum bid/budget.

**Storage/network approximation:** Tokyo gp3 ~$0.096/GiB-month: temporary 80 GiB root/scratch is ~$7.68 if kept for a full month, or ~$0.42 if provisioned for only 40/730 hours and then deleted. S3 Standard ~$0.025/GB-month: 20 GB ~$0.50/month plus S3 requests. One public IPv4, if allocated: $0.005/hour (~$0.20/40h); prefer no inbound ports and avoid NAT Gateway hourly/data-processing costs. Actual invoice also depends on S3/API/SSM/CloudWatch/egress/cross-AZ, tax and volume lifetime. The existing AWS instance+volumes remain as separate sunk/ongoing costs. The root/scratch volume should be encrypted, `DeleteOnTermination=true`, and nonessential caches disposable; all durable results live in a controlled S3 prefix.

At 40 compute hours using `c6i.2xlarge`, rough additional cost assuming **80 GiB temporary EBS deleted after run**, **20 GB S3 retained one month**, **one public IPv4** and no paid NAT: ~$7.86 + $0.42 + $0.50 + $0.20 = **$8.98**, before S3 requests/logs/other fees/tax. If EBS remains the full month instead, estimate ~$16.24 under identical assumptions. No resource was launched to verify this bill.

## Target architecture (data plane and control plane separated)

```text
GitHub Pages admin UI
   │ authenticated job request / report only
   ▼
Existing t8i.small controller, ap-northeast-1
   │ prepare signed & hashed *approved* research dataset manifest
   │ write source pin + population + seed + cost cap + OOS=CLOSED
   ▼
Private S3 dedicated research prefixes (versioned checkpoints)
   ├── datasets/<dataset-hash>/  (only required 2022–2023 instruments)
   ├── runs/<run-id>/manifest.json
   ├── runs/<run-id>/checkpoints/<gen>/...
   ├── runs/<run-id>/results/...
   └── runs/<run-id>/status.json
   ▲                  ▲
   │ read data + post atomic checkpoint/result, scoped IAM role
   │
EC2 Spot compute worker (desired count 0 by default)
   ├── Python x86-64 pinned source and hashed requirements
   ├── V083 --resume-from implemented and tested first
   ├── no broker keys, no trading API, no 2026 OOS
   ├── 2–50 bots initially (scale only after source review)
   ├── resource accounting and max wall-time TTL
   └── on interruption or shutdown: flush checkpoint → S3 → exit
```

### Control and job lifecycle

1. Explicit admin authorization for **new job**: population, history date range, dataset/source hash, seed, Spot instance class, **maximum cost and runtime**.
2. Compile a job manifest with immutable `job_id`, unique campaign source commit, `selectionPlanSha256`, configuration hash, dataset file hashes, model/engine version and `finalOosGate=CLOSED`.
3. Controller exports **only authorized TRAIN subset** to S3; confirm manifest sizes/checksums. No replication/snapshot of whole existing 2-TiB EBS; no direct attachment of the EBS to two EC2 instances.
4. After explicit approval, launch one one-time Spot worker (or manually scale a **cost-limited** ASG 0→1). Spot request interruptions should terminate worker; durable state stored off-instance. **Automatic replacement OFF by default** until resume/TTL/cost cap are proven. Do not continuously replace Spot machines without budget boundaries.
5. Bootstrap pinned runtime via prebuilt AMI/container or versioned artifact; SSM preferred over SSH. The Spot instance role may only Get allowed dataset prefixes and Put its own run/checkpoint prefix. Disable all broker API access and production server write access.
6. Run test/pilot/calibration according to same data-selection/OOS constraints, then a manually authorized full job. On each **5-valid-session generation** (and before orderly shutdown), atomically write a complete checkpoint and audit log, upload to S3 with checksum and commit marker, update status.
7. Interruption notice / rebalance signal: stop accepting new work, request graceful checkpoint, upload logs. AWS may allow only two minutes and cannot guarantee delivery. Design for abrupt shutdown with the *previous committed* checkpoint still intact.
8. On next separately approved launch, verify `run_id`, source/config/data hashes, seed, state schema, total completed date, RNG state, population, complete five-day cohort and output lineage; then resume on **the next unprocessed session**. Deny resume from incompatible/missing inputs and never duplicate finished sessions or lineage.
9. When finished, upload results, signed metrics/manifest, update read-only status for admin, terminate Spot worker and delete disposable EBS on termination. Apply S3 lifecycle retention only after preserving verified result artifacts.

### Interrupt/recovery risk and acceptance tests

Current `checkpoint.json` is **not a complete restart contract**. Define a `checkpoint-v2` that captures:
- Full 2–50-bot genomes + risk/trade history; deterministic RNG state and next session pointer.
- Last *committed* completed generation, selected five-day window, pending partial window treatment (start at last committed boundary for first version), and all ledger integrity checks.
- Exact source, dataset, selection plan, indicator settings, fees/slippage, timestamps and calendar hashes.
- Atomic S3 checkpoint bundle, inventory and checksums; never trust a partially uploaded bundle.
- Idempotent result/lineage writes with immutable generation ID; no duplicate sessions or invented fills after resume.

Minimum tests:
- Baseline uninterrupted synthetic 10+ sessions vs forced worker interruption immediately after generation checkpoint; **identical** lineage, final population, equity and return.
- Mid-session kill → restore from previous safe boundary → no duplicated 5-day window or double-counted trades.
- Spot two-minute notice and **zero-notice kill**, stale/missing/corrupt checkpoint, wrong manifest, source mismatch, 2026 OOS path denial.
- Different CPU counts preserve deterministic results, if bot-level parallel execution is introduced.
- Verify the 75% CPU / 65% RAM / 3% swap guards report and fail safely without randomly changing genomes or data.
- V0.8.2 existing pinned worker and current authenticated administrator routes remain operational and unchanged.

### Capacity and performance testing

**Stage 1:** 8 vCPU, 16 GiB, 45 then 50 bots under fixed inputs. Record `seconds/session`, `CPU core utilization`, `RSS peak`, `EBS I/O`, `cost/completed-generation` and Spot interruptions. Compare against current 2-vCPU baseline; never extrapolate linear 4× speedup from 8 vs 2 CPUs. Single-thread Python can be bottlenecked at 1 core.

**Stage 2:** If profiler shows serial loops, implement deterministic bot-level multiprocess evaluation (stable seed by `run_id+generation+bot_id`, parent reproduction in a serial commit section, sorted stable aggregation). Validate bit-for-bit equivalent metrics or explicitly document justified stochastic differences.

**Stage 3:** Compare c6i.2xlarge and c6i.4xlarge only if measured throughput makes larger instance cost-effective. Do **not** raise the current V0.8.3 50-bot software cap silently; increasing 50 requires separate source/test/spec change. GPU not in MVP.

### Networking and security

- Remain in Tokyo Region, allow multiple Spot AZs for capacity where data is accessed via S3 rather than an AZ-bound EBS.
- No inbound 22/8000/8080 on worker; no public web endpoint. SSM/HTTPS-only administration; IAM role per job, not long-lived API keys.
- S3 encryption, Block Public Access, narrow bucket/key-prefix policies, optional VPC Gateway Endpoint. If private subnet has no NAT/SSM endpoints, bootstrap/connectivity must be designed explicitly; a NAT Gateway or interface endpoints incur charges. A public subnet with a public IPv4 and **zero inbound security rules** is a lower-setup-cost possible pilot, but requires a deliberate security review.
- S3 write can only target `runs/<run-id>/`; cannot alter dataset or another run's checkpoint; existing instance can see summarized results.
- Validate requests with administrator authentication, anti-CSRF where applicable, server-side allowlist of instance types, per-job monthly/hourly caps, and reviewable IAM policies.
- **No AWS credentials in Git, logs or client JavaScript.** No production trading endpoints in worker environment.

### Cost and runaway prevention

- Initial `desiredWorkerCount=0`, one-time launch only after human click and budget confirmation.
- Initial proposal: **one worker, max 8 hours wall clock per approval, max $5 Spot-compute allowance**; check estimated 8h ~$1.57 (sample c6i.2xlarge price), then measure; shorten first run if desired. Budgets e.g. $10 and $20 alerts are *secondary*.
- Hard cap implemented with worker local deadline + independent EC2 auto-terminate watchdog/EventBridge timeout, not by AWS Budgets email alone. Validate that unexpected instance termination does not remove durable checkpoints. Stop workers at job completion or on safety gate failure.
- Explicitly track `worker_compute_usd`, EBS provisioned GiB-hours, S3 GB-month, S3 requests, IPv4 and optional network endpoints separately.
- Disallow unattended fallbacks to On-Demand or a pricier instance type, scheduled recurring launches, persistent ASG desired=1, and unlimited replacement.

### Staged implementation (no launch before authorizing each stage)

| Phase | Code/tasks (free to draft/review; running/creating resources later may cost) | Release gate |
|---|---|---|
| **P0 — dry design** | This document, repo audit, dataset size inventory, fixed cost/security constraints and Spot availability check | No AWS mutation |
| **P1 — checkpoint-v2** | Implement `--resume-from`, RNG/ledger/lineage/time validation, synthetic kill/restart tests | Uninterrupted vs resumed matches |
| **P2 — packaging** | Dataset selective export, immutable manifest + hash, S3 upload/downloader code and unit tests with mocked S3 | OOS CLOSED and manifest integrity |
| **P3 — IaC dry run** | CloudFormation/CDK/Terraform template: optional S3+IAM+SG+Spot/launch template+watchdog, no apply | Human review of plan, actual costs and IAM |
| **P4 — paid minimal pilot** | After explicit user approval: enable storage/bootstrap, 8-vCPU Spot **limited 5–10 sessions**, 5–10 bots first | Measured efficiency, data match, security |
| **P5 — interruption drills** | Worker kill/no-notice interruption and restart/resume validation; TTL termination and cleanup | Lossless last completed generation |
| **P6 — 45/50-bot full work** | New frozen calibration + human-approved training; compare 8 and 16 vCPU only if needed | Verified cost/completed generation |
| **P7 — integrated UI** | Submit job / show Spot availability, price, live progress, estimates, cost cap, pause/stop in existing admin | Auth, explicit approval, no auto spend |

**Current gate:** P0 only. P1–P3 require implementation and review but need not create billable AWS resources. P4 onward **must receive an explicit new authorization**.

## Sources and limits

- Actual EC2/Spot metadata in the owner's account returned by AWS DescribeInstances, DescribeVolumes and DescribeSpotPriceHistory on 2026-10-18 (all **read-only**).
- Source: [V083 forward-only research engine](https://github.com/heh-heh/market-career-dashboard/blob/strategy-engine-v3/research/evolution/run_v07_available.py) and [V083 CLI](https://github.com/heh-heh/market-career-dashboard/blob/strategy-engine-v3/research/evolution/run_v083.py) — current checkpoint writes but no validated resume.
- Spot interruption warning and best practices: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-best-practices.html and https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/prepare-for-interruptions.html
- Direct EC2↔S3 transfers in same region: https://aws.amazon.com/about-aws/global-infrastructure/global-network/faqs/
- Tokyo EBS gp3 pricing estimate ~$0.096/GiB-month; Tokyo S3 Standard ~$0.025/GB-month. Verify in [AWS Pricing Calculator](https://calculator.aws/) before provision; prices and taxes may change.
- Public IPv4 $0.005/hour: https://aws.amazon.com/vpc/pricing/ .
- AWS Budgets alerts are **not hard real-time instance shutdowns**; use hard TTL and explicit launch control.

**Authorization boundary:** This file and the checks used to prepare it are **not an instruction to provision/scale/run anything in AWS**. Wait for explicit user confirmation and cost limits before any resource creation.
