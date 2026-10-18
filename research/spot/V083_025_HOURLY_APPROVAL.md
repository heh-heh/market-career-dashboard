# V0.8.3 Spot: $0.25/hour planning approval (2026-10-19)

> This is an **operator cost condition**, not a hard invoice limit or authorization to provision an EC2 worker before safety checks pass. Existing dashboard, AWS storage, trading permissions, and 2026 Final OOS remain untouched.

## Approved planning envelope

| Category | Target |
| --- | ---: |
| One-time `c6i.2xlarge` Spot worker | 8 vCPU / 16 GiB |
| Spot **maximum bid** | **$0.20/hour** |
| Reserved budget for EBS + S3 + network + public IPv4 + incidental charges | **$0.05/hour** |
| **Aggregate planning ceiling** | **$0.25/hour** |
| Reviewed single-job runtime | **up to 8 hours** |
| Eight-hour **planned aggregate** | **up to $2.00** |

Offline estimate example: compute $1.60, temporary encrypted EBS $0.12, S3 $0.08, network $0.10, public IPv4 $0.04, miscellaneous $0.06. These are *budget allocations*, not verified metered AWS rates. Unused budget allocations do not automatically cover unexpected costs or other account workloads.

AWS Spot price history at 2026-10-18 20:00 UTC showed `c6i.2xlarge` Tokyo 1a at $0.1977/hour and Tokyo 1d around $0.205/hour. Thus the $0.20 bid may permit **1a only** at that sample; if the market price rises, the worker may be interrupted or the launch may not fulfill. Do **not** raise the bid to $0.25 to force capacity. Recheck real-time price, supportable AZ, and campaign performance before each authorized launch.

## Fail-closed implementation in this review branch

- Configuration refuses `max_spot_price_usd_hour > 0.20`.
- A reviewed `cost_estimate` must cover all five cost categories. Aggregate estimate and permitted budget must not exceed `0.25 × max_hours`; standard cost validation remains in force.
- The web controller lowers the total approval ceiling when a smaller `maxHours` is requested. If fixed conservative overhead estimates then exceed the shorter envelope, launch remains blocked until *a new private reviewed cost estimate* is prepared.
- This applies at `plan/prepare` validation, `issue_approval`, and `launch` before `RunInstances`.
- Tests are offline/mocked only; no AWS runner, bundle upload, server restart or compute launch in this review.
- A budget and max Spot price do **not** constrain all AWS charges. S3 object retention and requests, EBS lifecycle or delayed termination, routing, public IPv4, and taxes can exceed estimates. A single per-hour invoice hard cap cannot be guaranteed by these controls.
- Existing *baseline* `max_spot_price_usd_hour=0.25` launch configs will now fail closed until reviewed and changed. Do not silently rewrite private server configs.

## Live read-only preflight (AWS 2026-10-19)

- Dashboard `t8i.small` remains running. Host read-only SSM audit: `v083_spot_control.py` and `v083_web_jobs.py` **present**, dashboard service active, `/mnt/research/spot-control/v083-spot-config.json` **absent**, and `v083-spot-reaper.timer` **inactive/disabled**.
- A dedicated versioned AES256 private V083 S3 bucket and existing `v083-spot-worker-infra` IAM role/profile/no-ingress SG exist. **S3 object versions under `v083/`: none** at time checked.
- **Zero account-owned x86_64 AMIs**, **zero Spot worker requests**, and **no VPC endpoints** found in Tokyo. Existing subnet uses Internet Gateway and maps public IPv4 on launch; a future worker must have reviewed egress/access and include IPv4 charge if needed.
- Dashboard EC2 still uses `youtube-shorts-ssm-role` (SSM role), **not** the separate `market-dashboard-ec2` profile. Controller EC2/PassRole permissions and least privilege have **not** been provisioned or checked as authorized.
- AWS Budgets returned no budgets; no email recipient or billing alarm has been configured. No automatic launch replacement or On-Demand fallback is allowed.
- Existing user-visible `main` GitHub Pages `admin.html` does **not** contain the V083 Spot UI, while `strategy-engine-v3` does. Deploying the frontend and backend is a separate step, not accomplished by a pull request.

## Launch gate (all mandatory)

1. Independently review cost of preparing and retaining the AMI, artifact retention and all cloud services. An AMI creation may produce storage cost even without Spot running; do not assume this is covered by the per-hour Spot budget.
2. Build or verify a pinned x86_64 image containing cloud-init, Python environment, AWS CLI, shutdown/timeout, and isolated train-only dependencies. No broker credentials.
3. Produce a sanitized 2022–2023 TRAIN-only V2 bundle from valid provenance; reject 2026/OOS; upload to existing private S3 **only after data-transfer/storage approval** with SHA256 + immutable VersionId.
4. Review and configure minimal *dedicated* controller IAM and exact worker policy binding for the immutable job ID; verify no-ingress SG, subnet, network costs, and restricted S3 access.
5. Stage independent reaper timer, validate service state and terminate authorization, and ensure it does not touch dashboard/shorts or legacy resources.
6. Generate private job config tied to exact image, bundle hash/version, source/campaign, population (2–50), runtime and cost. Keep launch switch OFF until all gates are proven.
7. Verify tests/CI, then obtain explicit per-job paid launch confirmation using existing web `prepare → approve → start` gates. Confirm instance actually terminates and result checkpoint is durable.

**State: code review prepared, AWS launch NOT authorized by code changes alone.** No IAM, AMI, S3 uploads, EC2 launch, reaper installation, `main` merge or production deployment occurred in this branch.
