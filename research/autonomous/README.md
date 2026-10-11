# Autonomous AI Research V0.1 / Evolution V0.8

Research only. No broker client, trading control, live ARM, collector changes or
automatic main deployment. Initial implementation registers **one** structural
experiment (V0.8); it is not a general LLM agent or arbitrary shell executor.
Later ideas require another explicitly implemented, reviewed planner/template.
`--max-iterations N` is an upper bound, not permission to repeat 2025 mining.
The loop stops with `STOP_HYPOTHESIS_REQUIRED` after V0.8.

## Pipeline and scientific control

Authenticated Export catalog + Evolution summary → Summary Export → latest V0.7
development run's linked `V07_FULL_RESEARCH_BUNDLE` → verify ZIP size, exact file
set and every SHA256 → immutable cache → identify V0.7 protocol baseline → compare
stored versions → quarterly hypothesis → plan/safety gates → isolated V0.8 config
→ all Python tests and JavaScript smoke tests → fixed training/holdout checkpoints
→ V0.8 shortlist → detached frozen 2025 evaluation → diagnostics/comparison/report.

**Training and internal holdout are reused, not retrained.** The baseline's exact
2022-01-03–2023-12-29 population and verified 2024-01-02–2024-12-31 daily holdout
are the control. New random/adaptive training could change genomes, population
and resource scaling, confounding the sole changed variable (shortlist selection).
If immutable baseline lineage or daily artifacts are missing, the experiment
fails closed; there is no fabricated/reconstructed data and no implicit retraining.
The independent existing training CLI remains available for a separately planned
future experiment. Existing V0.5–V0.7 implementations/configs are unchanged.

Quarter return = sum(valid daily net PnL in quarter) / **$100 daily capital**,
using the actual baseline capital config. This is additive, matching the existing
frozen evaluator, not compounded wealth. Q1–Q4 use calendar quarters in 2024.
Require complete XNYS sessions for the declared holdout period; annual/daily
PnL, trades, costs, invalid-day counts and genome hashes must reconcile.

Select mechanically valid genomes with annual return > 0, at least 3 positive
quarters, median quarter return > 0. Ranking is median quarter return, worst
quarter return, annual return, then bot ID (all descending except ID).
Maximum 40; minimum 1 is an execution feasibility requirement, **not** sufficient
evidence for promotion. No qualifying bot → `INSUFFICIENT_CANDIDATES`, never relax.
Single valid bot/undefined correlations → promotion `INCONCLUSIVE`.

2025 is **Development Validation**, repeatedly observed, not independent OOS.
No 2025 threshold fitting, changing costs, changing symbols, or changing fitness.
Promotion compares mean, median, positive share, best, trades/bot, cost/bot,
train↔validation and holdout↔validation Spearman, and invalid bots. Missing metrics
produce INCONCLUSIVE. Negative mean keeps baseline; metric tradeoffs require
review. `PROMOTE_CANDIDATE` is a recommendation only: never rewrites baseline.
Final OOS gate stays CLOSED for every autonomous outcome.

## Data and execution boundary

No tick data or live market/broker APIs. Only artifact Export endpoints and the
existing offline frozen evaluator are used. Timestamp kind remains `end`; costs,
next observable completed-close execution and all genome trading rules unchanged.
Original market data and artifacts are never written, moved, or removed.
Existing reviewed-manifest guard remains intact. Unreviewed data needs explicit
`--provisional`; results retain `PROVISIONAL_UNREVIEWED_DATA`.

The autonomous loop has **no `--allow-final-oos` option**; an automatic child can
never request 2026. Any selected evaluation/artifact date ≥2026 is rejected with
`FINAL_OOS_ACCESS_BLOCKED`. A bounded date-fenced reader replaces only the new
stage's stream adapter; it stops at the 2025 cutoff **before price conversion**,
and never constructs/evaluates a 2026 symbol or benchmark bar. Naive timestamps
still require the existing explicit declaration. No forward fill.
Full-source SHA256 scans the shared multi-year CSV **opaque bytes** for integrity;
the provisional mechanical OHLCV/quote audit is date-fenced to 2025, and reviewed
mode keeps every original external-provenance requirement. No later price/volume
is numerically parsed, used for normalization, or evaluated. Separate files under
a 2026 partition are refused. Configuration declarations of the reserved 2026
split are metadata, not evaluation permission. A final frozen-model OOS run must
be a separate, explicitly user-approved workflow, outside this loop.

## Authentication, downloads and cache

Use environment only: `RESEARCH_EXPORT_BASE_URL` defaults to
`http://127.0.0.1:8080`; `RESEARCH_EXPORT_TOKEN` is an existing admin session bearer
token (ordinary existing expiry applies). No token/password in configs, commands,
status, reports, logs or commits. No new login endpoint/auth bypass. Non-localhost
HTTP and redirects are rejected. No credential-bearing URL. Training/test child
environments remove this token. Generic HTTP errors omit potentially sensitive
response bodies. No raw server logs exported.
The client requires `researchOnly=true` on catalog/summary and Export jobs.
The backend removes executed Final OOS rows and descendant artifacts (including
generated summary JSON/CSV) before sending them; ordinary admin downloads keep
their existing behavior. An older backend without this boundary is refused.

Cache pointer = SHA256 of requested type + relevant catalog metadata. Same
unchanged catalog avoids another compression/download. Persisted pending-job
pointers ensure that restarting a client reuses
an existing matching Export job instead of starting the same compression again.
Extraction directories
are keyed by **manifest SHA256**; all cache hits reverify file hashes and exact
file set. ZIP traversal, duplicates, symlinks, secrets, excess sizes/files and bad
hashes fail closed. Interrupted partial archives/extractions remain for inspection;
retry uses a new temporary directory, never overwrites evidence. Storage limits:
2 GiB compressed / 4 GiB decoded / 50k files / 32 MiB parsed JSON file. Oversized
bundles fail; do not export the raw 1m/tick dataset to this loop.

## Registry, resume and resources

Default registry `/home/ubuntu/evolution-autonomous`; snapshots under
`iteration-0001/`, immutable download cache under `cache/`.
Every iteration records experiment, hypothesis, source manifest/source hashes,
plan/config, quarterly diagnostics, code diff, tests, training/holdout/validation
references, diagnostics, comparison, decision, report JSON/Markdown and Git info.
`dataManifestHash` hashes the stored baseline input manifest; `artifactManifestHash`
identifies the verified export. These are different provenance records.

Atomic status: registry `status.json`. States: queued, downloading, analyzing,
planning, implementing, testing, training, internal_holdout, shortlisting,
development_validation, diagnostics, comparing, completed, failed.
Reuse steps show 100% and record reuse explicitly. Dry-run completes with
`DRY_RUN_ONLY`, no repo change/tests/training/commit.
Actual execution requires a completed dry-run followed by explicit `--resume`;
the first call cannot silently begin a long evaluation.
Normal CLI detaches a worker and returns; worker holds nonblocking `flock` on
`/var/lib/market-career-dashboard/evolution_autonomous.lock`. Reboot releases
kernel locks. `--resume` verifies source/cache/checkpoints and process identity
(boot ID, process start ticks, command hash), never PID alone. Existing active
evaluation is observed, not launched twice. Failed attempts are retained; retry
creates a fresh attempt directory. Completed validation survives a worker crash
before pointer creation. Partial shortlist stays blocked for inspection.
Source-code fingerprints must match on resume and after evaluation; changing
implementation mid-experiment fails closed instead of silently changing results.

Adaptive Resource Guard's current CPU/RAM/swap thresholds are reused for launch
decisions, without scaling the fixed validation population. Pressure →
`PAUSE_NEW_EXPERIMENT`; no collector/dashboard process is stopped. Disk reserve
is max(10 GiB, 10% disk), plus archive/extraction allowance for downloads.
An interrupted worker does not fabricate completion or price fills.

After successful evaluation: save diff, require full tests, commit only the fixed
V0.8 candidate config on `strategy-engine-v3`, normal non-force push. Dirty unrelated
or staged work blocks automatic Git. A failed push preserves commit/results for
retry. No main merge/push. `UI_UPDATE_READY` is deliberately not Pages deployment.

## EC2 commands (no production research was executed in Codex)

```bash
cd /home/ubuntu/market-career-dashboard
git fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3
git status --short
git switch strategy-engine-v3
git merge --ff-only origin/strategy-engine-v3
# Existing administrator login supplies RESEARCH_EXPORT_TOKEN securely to this shell.
export RESEARCH_EXPORT_BASE_URL=http://127.0.0.1:8080
install -d -m 700 /home/ubuntu/evolution-autonomous
# Ensure ubuntu may create its lock in the existing service-state directory.
sudo touch /var/lib/market-career-dashboard/evolution_autonomous.lock
sudo chown ubuntu:ubuntu /var/lib/market-career-dashboard/evolution_autonomous.lock
sudo chmod 600 /var/lib/market-career-dashboard/evolution_autonomous.lock
sudo systemctl restart market-career-dashboard.service
python research/autonomous/run_research_loop.py --dry-run --max-iterations 1
```

After reviewing dry-run report, run **one** actual iteration (not run automatically):

```bash
python research/autonomous/run_research_loop.py --resume --max-iterations 1 \
  --data-manifest research/v4_data_manifest.json
# Or ONLY if explicitly choosing existing mechanical-only research:
# python research/autonomous/run_research_loop.py --resume --max-iterations 1 --provisional
# Add --naive-timezone only when the actual source requires a verified declaration.
cat /home/ubuntu/evolution-autonomous/status.json
```

Monitor `GET /api/research/autonomous/status` is a small public read-only projection
without source paths/logs. The card below Evolution shows current iteration,
hypothesis, progress, outcomes, CLOSED Final OOS and five recent iterations.
Authenticated `autonomous_iteration` Export jobs accept opaque IDs from the Export
catalog, never paths, and include only explicit top-level summary/config/diff
artifacts. Download via existing scoped short-lived ticket. No training-control
web endpoint and no automatic Pages update in this task.

Offline validation: `python -m unittest research.test_autonomous_research`,
`python -m unittest discover -s research -p 'test_*.py'`,
`node research/test_autonomous_monitor_smoke.cjs`.
