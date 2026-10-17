# V0.8.1 Adaptive Evolution & Survival Breeding

Research only; no orders, collector changes, raw-data repairs or automatic long
training. V07 and V08 configs/results remain unchanged. Version `V081_SURVIVAL_1`,
selection policy `v081_survival`, separate `/mnt/research/evolution-v081` results.
Final OOS CLOSED: training 2022–2023, frozen internal holdout 2024, development
validation 2025. No 2026 market rows are decoded for this experiment.

## Breeding

All complete, valid five-session net-PnL evaluations above the existing epsilon
($0.000001) are candidates. Fitness rank weights, elites, bounded Gaussian
mutation and distinct-parent crossover reuse V08. MDD/profitable-day conditions
never disqualify a profitable parent. Daily $100 reset and existing fitness,
costs (2bp slippage + 1bp half-spread per side) and next-completed-close observation
proxy are unchanged. No same-bar entry or fictitious stop fills.

If none is profitable, sort **valid complete** windows by net PnL descending,
stitched MDD ascending, fitness descending, then bot ID. Use up to two parents.
Two-parent recovery performs distinct-parent crossover for every child and
bounded mutation with at least one actual changed gene; no unchanged elites in
this path. If probabilistic mutation changes nothing, perturb one bounded weight.
With one valid candidate, use single-parent mutation. With zero valid candidates,
raise `NO_VALID_FIVE_SESSION_EVALUATIONS`; never random-recover invalid data or
label it neutral. Normal recovery always produces the configured fixed number
of children, retains genealogy, and records `CAPITAL_PRESERVATION`, parents' PnLs,
contributions, crossovers, actual mutation counts and population diversity.

Financial SURVIVE/DEAD/NEUTRAL classification remains unchanged. V081 separately records primary `profitReproductionEligible` and actual `reproductionEligible`/breeding mode, so a loss-making preservation parent is explicitly auditable.

## Zero-generation investigation

An audit's `eligible` means references are complete and at least one symbol has
a causal tradable prefix. It does **not** promise that a position never encounters
a later missing minute. `run_v07_available.py` invalidates the **whole cohort's**
window on `COHORT_HELD_SYMBOL_OBSERVATION_GAP` or `COHORT_UNRESOLVED_SESSION_EXIT`;
prior pending days are discarded. Calendar breaks, missing SPY/QQQ/SOXX, no usable
prefix and trailing incomplete windows are distinct failures. Ten processed days
are not ten valid selected days. Existing V08 web `worker()` can mark a process
completed before `_verified_run(smoke)` later rejects zero generations.

The reported EC2 `10/10, 0 generations, 7 attempts` alone cannot identify which
condition occurred. No EC2 access or actual source artifacts were available in
this coding session. **Do not treat the synthetic reproduction below as a finding
about the server.** Run the bounded read-only diagnostic against the actual run:

```bash
python -m research.evolution.v081_diagnostics --run-dir /mnt/research/evolution-v08-profit-only/FAILED_RUN --out /mnt/research/v081-zero-diagnosis-UNIQUE.json
```

The V081 web report also reads the existing V08 smoke result, when present, and displays actual zero-generation reason counts without modifying that job. The CLI splits window/session counters (different denominators), invalid bot-days,
missing held-symbol observations and unresolved-symbol samples. Original V08
logs may lack exact gap timestamps; the tool does not invent them. New V081
failures record bounded observed gap timestamps and invalid-account examples.

V081 pilot searches the **same verified audit**, preserving calendar order, for
5–10 consecutive eligible days without partial tradable-prefix symbols. It keeps
the original universe, benchmarks and execution/missingness rules. It does not
choose only profitable symbols or skip gaps while pretending continuity. Whole-
day completeness screening is **ex-post data selection bias**, explicitly written
in `pilot_selection.json`; it is not an unconditional performance claim. If no
five-day block exists, fail with classified exclusion counts. No source repair,
interpolation or automatic threshold relaxation. A pilot passes only with at
least one actual completed generation. Final annual validation adequacy gates
remain unchanged and may still report PARTIAL_UNVERIFIED.

A deterministic synthetic regression creates seven held-symbol gaps in the first
ten sessions: unchanged V08 gets zero generations/seven invalid attempts. V081
finds a later complete block in the **same unchanged files**, completes a generation,
and records the selection bias. This proves the mechanism, not EC2 root cause.

## Capacity measurement

The web flow separates **100-bot pilot → explicit calibration → operator-approved
training from scratch**. Calibration never invokes full training or holdout.

Each candidate size (100,150,225,337,505,757,1000 by default) runs one warmup plus
two independent measured 5-session pilots. Only the largest **actually tested**
sequential population passing all measurements is recommended. No extrapolated
maximum is launched. Zero valid generations, missing samples, excessive day
runtime or resource pressure cannot become a passing result. An unsafe 100 stops
calibration. Intermediate failure leaves the last tested safe recommendation.

Targets: host CPU <=75%, RAM <=65%, swap <=3%. The sampler runs throughout the
replay, records averages/peaks/RSS/CPU seconds/process and system I/O/free disk,
and samples other visible jobs' RSS and interval CPU contributions without logging
command lines. Optional process inspection is time-bounded and infrequent; host safety counters are committed first so /proc enumeration cannot starve the guard. Host totals include collectors/API/other work. Shared features are
computed once per day; no per-bot CSV reloads or GPU. Stop callbacks execute each
minute. A configurable 900-second processing budget per session and bounded child
wall timeout also constrain slow I/O. Disk free retains max(10GiB,10%). Existing
hard RAM/swap guards remain. No OS settings/services are changed or killed.

Calibration records every fixed-size run separately, sample count, resource
reports, hash-bound input plan/config/code, source run hashes and host/boot
fingerprint. Incremental RSS/time per bot are measured differences between sizes;
cache/noise can make slopes negative. They are diagnostics, never justification
for skipping load tests. A certificate expires after 24 hours and is rejected on
another host/reboot, changed source/config/code/data or mismatched measurements.
Subsequent changes in host load can still invalidate safety; live guards remain.

Training uses the certified **fixed** population from generation zero, not the
100-bot pilot's evolved population. Pressure stops the whole run; no unevaluated
bots are silently culled or resized. `checkpoint.json` retains the last fully
committed generation, population, RNG state, plan hash and through-date. Partial
windows must not be merged on a restart. There is no automatic in-place resume
or automatic long-training restart in this release.

## CLI

Replace PLAN and RUN with existing artifact paths; these commands are preparation,
not evidence that EC2 has been deployed or tested.

```bash
# Audited 100-bot pilot, at least one valid complete generation.
python research/evolution/run_v081.py --selection-plan /mnt/research/PLAN/selection_plan.json --pilot --seed 42 --require-ebs

# Explicit finite resource calibration; no full training follows automatically.
python research/evolution/calibrate_v081.py --selection-plan /mnt/research/PLAN/selection_plan.json --out /mnt/research/v081-capacity-UNIQUE --max-population 1000 --require-ebs

# AFTER reviewing the certificate: set --population to its measured recommendation.
python research/evolution/run_v081.py --selection-plan /mnt/research/PLAN/selection_plan.json --capacity-report /mnt/research/v081-capacity-UNIQUE/capacity.json --population 225 --from-date 2022-01-01 --to-date 2023-12-31 --results-dir /mnt/research/evolution-v081 --seed 42 --require-ebs

# Frozen evaluation; source config/population retained, no reproduction.
python research/evolution/run_v081.py --selection-plan /mnt/research/PLAN/selection_plan.json --source-run /mnt/research/evolution-v081/RUN --from-date 2024-01-01 --to-date 2024-12-31 --results-dir /mnt/research/v081-holdout --require-ebs
python research/evolution/run_v081.py --selection-plan /mnt/research/PLAN/selection_plan.json --source-run /mnt/research/evolution-v081/RUN --from-date 2025-01-01 --to-date 2025-12-31 --results-dir /mnt/research/v081-development --require-ebs
```

`/mnt/research-extra` support is explicit: add `--research-mount /mnt/research-extra`
and put `--results-dir`/`--out` there. With `--require-ebs`, findmnt must report that
exact mount and a source different from the root filesystem; a mere directory or
symlink is insufficient. Nothing is moved from the old EBS. For web use, the
operator must explicitly configure `V081_RESEARCH_MOUNT=/mnt/research-extra` and a
matching pinned source proof in that jobs root; the default overlay installer
below deliberately targets `/mnt/research` and does not change systemd env.
Administrator maximum is `V081_MAX_POPULATION` (default1000). Calibration writes an isolated immutable `trial_config.json` with that maximum; it never edits the original config. Training automatically uses the certificate-adjacent config unless an explicit matching --config is supplied.

## Web and deployment

AI research has a separate V081 card; original V08 remains in legacy details and
V07 controls/artifacts remain preserved. The new protocol `V081_WEB_1` must be
returned by the authenticated `v081 report` console command before action buttons
enable. Unsupported servers display a specific blocked message. Only backend
reported metrics are shown. The explicit `v081 new` / 새 실험 button starts a new bounded pilot campaign without deleting the prior job. Use this after re-auditing changed source files; retry never silently swaps a campaign plan. Six explicit stages: smoke, calibrate, train, holdout,
validation, export. `v081 train` checks completed smoke/calibration and the immutable
certificate before launching; the runner repeats the deep certificate checks.

Workers are detached, inherit shared V07 research flock descriptors through
calibration subprocesses, and retain identity checks. API-managed starts require
reviewed systemd KillMode=process; this code does not change that policy. API deploy
checks the research locks; manual API restarts must preserve this KillMode policy.
The new authenticated download is `/api/research/v081/download`.

Optional safe **operator-run**, API-only overlay (no EC2 action was run by Codex):

```bash
TOOLS=/mnt/research/collection-deploy-tools-20261012
 git -c safe.directory="$TOOLS" -C "$TOOLS" fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3
 git -c safe.directory="$TOOLS" -C "$TOOLS" show origin/strategy-engine-v3:scripts/install_v081_admin_api_overlay.py > /mnt/research/install-v081-api.py
 sudo python3 /mnt/research/install-v081-api.py --check
# Only after READY and reviewing KillMode=process / no running worker:
 sudo python3 /mnt/research/install-v081-api.py --apply
```

The overlay adds only the console/download hooks and new controller, preserving
local server/auth/UI modifications. It creates a revision-pinned isolated source,
backs up every replaced file under /mnt/research, verifies hashes, holds shared
locks, blocks regular-hours API restart, and rolls back on write/health failure.
Unexpected controller edits or changed anchors block rather than overwrite.
Collectors, tokens and systemd settings are untouched; apply never starts research.
Pages UI publication is separate from EC2 backend installation.

## Validation limits

Synthetic 100-bot/5-day measured pilot: one complete generation, about5.2s total,
peak RSS about82.6MiB, sampled host CPU mean5.0%/peak10.2%, RAM3.0%, swap0% on the
Codex machine. Only three samples: not an EC2 capacity recommendation, not a
profitability estimate, and not a large-universe scaling benchmark. OS I/O byte
counters were zero (cache-backed workspace); do not infer that production disk
I/O is free. Actual EC2 100+ repeated trials, original zero-generation artifacts,
2TB mount verification, annual holdout and development results remain unverified.
