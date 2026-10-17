# V0.8 Profit-Only Reproduction

Research only. Existing V07 configs/results, execution, feature definitions,
fitness coefficients and available-data continuity rules are unchanged.
Opt-in `selection_policy=v08_profit_only`; legacy snapshots omit this new field.

## Eligibility and ranking

Each daily account resets to $100. Five *consecutive valid* sessions accumulate
realized net cash PnL after the existing 2bp slippage + 1bp half-spread per side.
A complete window with all five valid days and `windowPnl > epsilon` qualifies;
`epsilon=0.000001 USD` treats floating-point noise as neutral. Fitness, MDD and
profitable-day gates do **not** reject profitable V08 parents. All qualifying
parents enter the pool, ignoring legacy parent_fraction / parent_pool_size.

Sort by unchanged V07 fitness descending, then stitched window MDD ascending,
profitable sessions descending and bot ID. Rank weights N,N-1,...,1 give every
candidate positive probability, including negative-fitness profitable parents.
Preserve up to ten elites. Sample two *distinct* weighted parents for crossover;
with one parent, preserve one elite and mutate its remaining descendants without
self-crossover. With zero eligible parents only, generate 100 random genomes.
Mutation/crossover implementation and gene bounds are unchanged. Mutation is
probabilistic: a mutation child can retain its parent's genes if no gene draws fire.

`window_*.json.selection` records all parent net PnLs/rank weights, offspring
contributions, mutated gene counts and elite counts. A crossover child counts
once for each contributing parent; per-parent contributions therefore need not
sum to 100. `maxParentContribution` uses total parent links as denominator.
`uniqueGenomes` is distinct child genome hashes, not a statistical diversity claim.
Status exposes the most recently completed window under `reproduction`.

## Run preparation (no automatic long training)

Use the existing completed **read-only** data audit and selection plan. The plan
must cover the requested dates; source hashes and continuity are checked again.
No unreviewed manifest becomes reviewed. Data remain provisional; 2026 is blocked.

```bash
# First bounded smoke: 100 bots, ten planned consecutive training sessions.
python research/evolution/run_v08_profit_only.py --selection-plan /mnt/research/PLAN/selection_plan.json --smoke --seed 42 --require-ebs

# Same data, initial genomes, seed, 100 bots, costs and fitness: selection-only A/B.
python research/evolution/compare_v08_profit_only.py --selection-plan /mnt/research/PLAN/selection_plan.json --from-date 2022-01-03 --to-date 2022-01-14 --results-dir /mnt/research/v08-paired --seed 42 --require-ebs

# Explicit training only after inspecting smoke; NOT launched by this change.
python research/evolution/run_v08_profit_only.py --selection-plan /mnt/research/PLAN/selection_plan.json --from-date 2022-01-03 --to-date 2023-12-29 --seed 42 --require-ebs

# Frozen evaluation, no reproduction: repeat separately for each A/B source.
python research/evolution/run_v08_profit_only.py --selection-plan /mnt/research/PLAN/selection_plan.json --source-run /mnt/research/evolution-v08-profit-only/RUN --from-date 2024-01-01 --to-date 2024-12-31 --results-dir /mnt/research/v08-holdout --require-ebs
python research/evolution/run_v08_profit_only.py --selection-plan /mnt/research/PLAN/selection_plan.json --source-run /mnt/research/evolution-v08-profit-only/RUN --from-date 2025-01-01 --to-date 2025-12-31 --results-dir /mnt/research/v08-development --require-ebs
```

Replace PLAN/RUN with real paths. Do not assume incomplete annual evaluation is
valid; existing coverage/sample adequacy gates remain. Source-run config is
frozen; do not tune using 2025. Final OOS CLOSED. A small synthetic test does not
establish performance or actual EC2 resource needs.

The fixed population does not auto-scale. Before V08 training, measured CPU,
RAM and swap must be below configured soft thresholds (85/70/5 percent);
RAM/swap hard limits remain enforced daily. Disk guard keeps max(10GiB,10%).
On pressure the new run fails closed; collectors/services are never stopped.
One session's shared features serve all bots; no per-bot CSV reloads or GPU.

## Read-only monitor

Admin → AI → research retains V07 controls and adds a distinct V08 card.
`GET /api/research/evolution/v08/status` reads only
`EVOLUTION_V08_RESULTS_DIR` (default `/mnt/research/evolution-v08-profit-only`).
It shares the existing research-monitor feature gate. No start endpoint added;
no browser action starts V08. Missing backend/data shows unavailable, never made-up
returns. Local code is ready; EC2 backend deployment is a separate operator action.

## Evaluation

Compare `comparison.json` only when `comparable=true`; masks, initial conditions,
code hashes and selected dates must match. Later different held-position gaps can
make cohorts non-comparable. Parent policy is the only intended configuration
difference; a historical 20-bot run is not this 100-bot control. Read per-window
returns/MDD/fitness, parent count, recovery, concentration and diversity, then
independently frozen 2024/2025 summaries. No best-bot-only promotion or claims.
