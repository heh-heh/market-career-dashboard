# V0.7 available-data research experiment

Separate from both original seven-session V0.7 and the strict five-session R1.
The original configs, artifacts, collectors and trading services are unchanged.
This experiment changes **data evaluation policy only**, not genes, features,
fitness coefficients, trade thresholds, costs, stop/target or execution prices.
2026 Final OOS remains CLOSED. No automatic deployment or historical training.

## Three stages

1. `audit_available_data.py` reads all 30 symbols from universe_v3 by default,
   2022–2025, existing finalized SYMBOL.csv or CSV.GZ. One session in memory,
   streaming per-symbol/day evidence. Missing sources/sessions are reported;
   ambiguous CSV variants, schema/OHLCV/quote errors,
   conflicting duplicates, unknown timezone and changing sources fail closed.
   A source file with no rows in a year is not a permanent symbol blacklist.
   Currency/adjustment and point-in-time provenance remain unreviewed.
2. `select_v07_windows.py` verifies evidence hashes and creates a separate plan:
   all XNYS sessions, missing/usable symbols, dependency reasons, non-overlapping
   five-session windows, potential rolling windows and first **ten consecutive**
   eligible TRAIN dates for a bounded smoke. No performance-based date selection.
3. `run_v07_available.py` uses that plan and the unchanged R1 config, with an
   explicit `causal_available` execution-data policy. The old runner and old
   campaign keep their strict behavior, including their existing completeness guard.

Outputs cannot be written inside raw data. Audit/selection output directories
must be new; training creates a unique run directory. Neither original source
nor old result files are rewritten, repaired, interpolated, renamed or deleted.
The source resolver rejects symlinks escaping the source root. Missing sources
appearing later or source/audit/plan changes require a new audit; no hash refresh
or automatic downgrade of a reviewed manifest is performed.

## Exact availability semantics and causality

The required completed observations remain SPY, QQQ and SOXX, including the
existing symbol/sector benchmark mapping. There is no substitute for a missing
reference. Complete reference sessions and at least one potentially usable
symbol prefix make a day eligible for conditional research evaluation.

A usable prefix means >=31 contiguous regular-session completed closes from open,
with positive cumulative volume, because the unchanged feature provider needs a
30-minute return and session VWAP. Missing minutes are never filled. If a symbol
has a gap, existing VWAP-prefix logic disables its new signals for the rest of
that day. It may become fully usable again the next trading day.

**The daily `completeSymbols` list is diagnostic and is NOT passed as a tradable
mask to the policy.** All requested symbols' actual bars are passed to the feature
provider. At each decision, the existing causal validity mask uses only completed
prefix data. A late-day gap cannot suppress an earlier otherwise valid signal.
No future price, next-day information, quote or regime is introduced.

If a missing minute occurs while any bot holds that symbol, its account is marked
invalid, permanently for that evaluation day. Any later exit uses the first
available real close with the old adverse cost model; it does not invent a missed
stop fill. Trades and cancellations remain in rejected-attempt artifacts.

If any participating account is invalid, the **whole population's in-progress
window** is discarded so survivors cannot gain an advantage by silently dropping
other bots' bad outcomes. Also discard a window interrupted by a missing reference
or no usable symbol. Keep the same genomes and restart the next window on the next
scheduled session; do not increment the selected generation number. Commit daily
lifetime PnL/trades/days only after five consecutive valid cohort days. Trailing
1–4 days do not reproduce or pollute lifetime equity.

Each attempt has a unique directory and explicit `windows.jsonl` outcome. Here
`skippedGenerationAttempts` counts attempts terminated by a data-invalid day
(including an invalid first day); `excludedDataSessions` counts those rejected
calendar days; `interruptedPartialWindows` counts interruptions after >=1 pending
valid day. These are **not** an estimate that every rejected day represents five
lost sessions. `completedTrainGenerations` counts only valid five-day selections.

The plan's window counts are **potential**, not a guarantee: an actual held-symbol
gap can invalidate a planned window and change subsequent window boundaries.
If no ten-session consecutive planned range exists, `--smoke` fails with
INSUFFICIENT_CONSECUTIVE_SMOKE_DATA. It never relaxes criteria or joins distant days.
Explicit shorter bounded research ranges remain possible but are labelled partial.

## Selection bias, validation, comparison

Full-session reference completeness is known **after the session**, so dropping
reference-deficient days is an ex-post research sample filter. Discarding windows
based on held-symbol gaps also makes sample selection policy-dependent. Missing
provider data may correlate with volatility, halts, IPO history and market regime.
The requested contemporary universe does not prove point-in-time eligibility.

Thus the output is conditional missing-data research, **not** a claim of an
executable forward availability filter, unbiased annual performance or superior
strategy returns. Record all excluded dates and compare pre/post selection regime
shares, including CONTEXT_UNAVAILABLE warmup/missing-reference minutes. No 2025
performance is used to choose periods, filters, thresholds or genomes.

- TRAIN: only 2022–2023. Reproduction after five valid consecutive XNYS sessions.
- Internal holdout: 2024, frozen population, no reproduction.
- Development validation: 2025, frozen population, no reproduction.
- Requested dates >=2026 fail before source market preflight. Mixed-year CSVs
  are byte-read for integrity hashes and timestamp-scanned for ordering, but 2026
  OHLCV is never numerically decoded, used as features or evaluated.

Frozen evaluation is available with `--source-run NEW_TRAIN_RUN --from-date ...
--to-date ...`. Missing/invalid days are retained as exclusions, not relabelled
as complete annual evaluation. `validationReturn` is null for an incomplete annual
sample; `observedSubsetReturn` is explicitly the available-subset arithmetic PnL
per $100 daily-reset account. `annualCoverageComplete` requires the entire year's
XNYS calendar, no excluded dates and complete requested source universe. An
additional per-bot trade adequacy floor (default30, explicitly recorded; no claim
of statistical significance) is required for `annualValidationComplete`.
Otherwise status remains PARTIAL_UNVERIFIED. Even an annual mechanically complete
run stays UNVERIFIED_PROVISIONAL_DATA, with CLOSED Final OOS and no automatic
legacy shortlist/campaign promotion. This is an isolated research driver, not a
replacement for `run_v07_campaign.py` or its annual holdout selection requirements.

`baselineComparability.dataSelectionComparable` reports whether missing-data
selection affected the requested evaluation. It does not assert that an unseen
old run used the same seed/population/config. Optional `--baseline-run LEGACY_RUN`
compares dates, seed, universe, full source hashes, costs/config, execution model,
data origin and source validity. A seven-session config or different dates yields
NOT_COMPARABLE. No baseline path -> sameConditions=null / NEEDS_PAIRED_BASELINE
(or NOT_COMPARABLE if data selection differed). Baselines are never overwritten.

Tests on complete synthetic data show the new driver produces the same final
**genome hashes** as the strict R1 driver under matching seed/config/dates; lifetime
values match numerically. This is implementation parity, not historical evidence.

## Artifacts

Audit directory:

- `audit.json`: all symbols, exact complete/missing/partial dates, minute/session
  completeness ratios, file hashes, source errors and regime counts.
- `symbol_sessions.jsonl`: every symbol/day, first/last END labels, expected/actual
  minute counts, missing-minute ranges, prefix availability and dependencies.
  Ranges have inclusive firstEnd/lastEnd labels and count; one-minute expansion
  reconstructs every missing timestamp without a giant per-minute JSON array.
- `sessions.jsonl`: daily evaluation eligibility, reference failures, causal-prefix
  symbols, partial-prefix symbols, complete symbols and broad-market regime counts.

Selection directory: `selection_plan.json`, including train windows, rejection
counts, skipped/partial windows, smoke range, per-split data/regime proportions,
source/audit hashes and explicit selection-bias warnings.

Run directory:

- `run.json`, `status.json`, `generations.jsonl`: config/code/plan provenance,
  status, generation counts, data-invalid attempts, selected/observed trade counts.
- `evaluation_sessions.json`: days actually simulated, including rejected attempts.
- `used_sessions.json`: only days committed to a full five-day selection, or valid
  frozen subset days. `excluded_sessions.jsonl`: all discarded dates and reasons.
- `attempt_NNNN_DATE/`: original-style metrics, trades, cancellations and population
  snapshots. `selection.selected=false` means pending/uncommitted, not a child
  population; committed windows are authoritative in the following files.
- `window_NNNN.json`, `windows.jsonl`: full window dates, fitness, parent reasons,
  selected/invalid/partial outcomes. `lineage.jsonl`, `final_population.json` and
  `last_evaluated_population.json`: offspring and evaluated population stay distinct.
- Frozen runs: `validation_summary.json` with sample coverage/adequacy and subset
  returns; source population hashes are checked and source files stay unchanged.

Existing monitor gains read-only availability status fields and recognizes the
new process name. There are no UI layout, service, authentication or trading changes.

## Safe EC2 commands (manual, sequential)

No EC2 connection was available in this task. Actual 2022–2025 completeness and
AAPL/TQQQ 2024 recovery remain **unverified**. Commands below run in the existing
research Python environment after a separately reviewed code update. Do not
force-pull a dirty operating checkout. Do not stop collectors or generate a fake
manifest. If the finalized CSVs are still mutating, use a separately prepared
stable snapshot; the tool will fail on hash changes rather than stopping collection.

Use a new output location and enforce the separately mounted research EBS:

```bash
cd /home/ubuntu/market-career-dashboard
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
V07_DATA_JOB="/mnt/research/v07-availability-$(date -u +%Y%m%dT%H%M%SZ)"
```

1. Entire universe / 2022–2025 audit (no training):

```bash
nice -n 10 python research/evolution/audit_available_data.py --data-dir /home/ubuntu/market-career-dashboard/data/toss_1m --timestamp-kind end --from-date 2022-01-01 --to-date 2025-12-31 --out-dir "$V07_DATA_JOB/audit" --require-ebs
```

2. Automatic five-session window selection (no training):

```bash
nice -n 10 python research/evolution/select_v07_windows.py --audit-dir "$V07_DATA_JOB/audit" --out-dir "$V07_DATA_JOB/selection" --require-ebs
```

3. Only20 bots / ten consecutive TRAIN calendar sessions:

```bash
nice -n 10 python research/evolution/run_v07_available.py --selection-plan "$V07_DATA_JOB/selection/selection_plan.json" --smoke --population 20 --seed 42 --results-dir "$V07_DATA_JOB/smoke" --require-ebs
```

If the source uses naive timestamps, add `--naive-timezone` to the audit only after
verifying its real source interpretation. END semantics never change. Omit
`--symbols` for all30; a subset is allowed for diagnostics without deleting others.
Always inspect raw completeness, eligible days, excluded reasons and bias flags;
an audit command completing does **not** mean the raw universe is complete.

Resource checks preserve >=max(10GiB,10%) disk free and use existing RAM/swap hard
limits. Audit checks disk periodically; execution checks before each day. Streaming
retains one source session and at most five batches of bot diagnostics, not years
of arrays or CSV copies per bot. No GPU/new dependency. Commands use one BLAS
thread and lower CPU priority. This is controlled stop on resource pressure, not
a resume implementation; rerun to a new unique directory after reviewing the cause.

## Recorded local validation (synthetic only)

The exact three CLI stages were exercised on30 synthetic symbol sources, ten
trading sessions and20 bots. Audit3.26s, selection0.85s, training16.02s, two selected
generations, ten committed days, no skipped attempts, all source hashes unchanged.
Maximum child RSS104.8MiB; source CSVs10.8MB, run artifacts approximately3.5MB.
These are Codex measurements, not EC2 capacity promises or historical performance.

Additional regressions cover missing sources/days/minutes, non-permanent symbol
eligibility, reference failures, no future symbol mask, held-gap recovery without
fabricated fills, whole-window/lifetime rollback, DST/early close, immutable source
hashes, plan tampering, OOS rejection, partial holdout and low-sample annual gates.


Local release validation: all597 Python tests passed (108.6s), including21 new
availability regressions; all14 existing JavaScript smoke tests passed. Python
compile checks, all three CLI `--help` commands and the actual synthetic three-stage
CLI smoke passed. No EC2 dataset was available, so no actual2022–2025 completeness,
2024 repair status, historical return or EBS deployment result is claimed.
