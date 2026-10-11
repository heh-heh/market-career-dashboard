# Evolution Trading AI V0

CPU-only **research/paper simulation**, no neural network, RL, broker credentials,
order endpoint or service launch. Existing V3/V4/Simple/tick collection stay separate.
`RESEARCH_ONLY=True`; all result directories/status records carry `researchOnly=true`.
No profitability claim is made. This establishes an evolutionary replay framework.

## Architecture and modules

Finalized Toss CSV/CSV.GZ → existing audited streaming reader → one NY session
across symbols → `MinuteFeatureProvider` shared read-only NumPy arrays → batched
linear market scores → independent `Account` / bot-state score → pending actions
→ daily evaluations → positive parents → elites / uniform crossover / bounded
Gaussian mutation → next **chronological training** session.

| Module | Responsibility |
|---|---|
| `config.py`, `default_config.json` | Config snapshot, bounds, chronological split validation |
| `features.py` | Extensible `FeatureProvider` protocol, minute implementation and explicit benchmarks |
| `genome.py` | Frozen `Genome`, named JSON weights, stable SHA256, mutation/crossover |
| `account.py` | Fractional long account, immutable replaceable `ExecutionCostModel` |
| `engine.py` | Day replay, array score calculation, per-bot pending actions and diagnostics |
| `population.py` | `Bot`, daily fitness, survival, lineage, selection and recovery |
| `run_evolution_v0.py` | CLI, data guards, daily artifacts, split freeze and final hash check |
| `monitor.py` | Bounded public status summary; reads files only |

The provider and cost interfaces can later accept received tick/orderbook observations.
V0 does **not** implement a tick provider, neural policy, live promotion, resuming a
partially written run, or portfolio optimization. Future rule-based seed factories
can return immutable `Genome` objects without changing V4. V0 seeds are random only.

## Data, timestamps and causality

- Input: exactly one `SYMBOL.csv` or `SYMBOL.csv.gz` per required symbol, containing
  `timestamp,open,high,low,close,volume` and optional identity columns.
- **Exclusive END labels only**: `10:01` means the `[10:00,10:01)` bar, observable
  at `10:01`. Inclusive `:59` labels are rejected by the existing validator.
- Aware source times are converted to `America/New_York`. Naive input requires
  explicit `--naive-timezone UTC|America/New_York`; ambiguous DST folds fail.
- Actual XNYS calendar, including holidays, EST/EDT and early closes; only REGULAR
  minutes are used. DAY/PRE/AFTER ticks and minute bars are not policy inputs.
- Reviewed mode reuses **unchanged** `validate_data_manifest()` and its identity,
  timestamp, split/price-volume, point-in-time, adjustment-basis and hash guards.
- Otherwise **explicit** `--provisional` performs bounded mechanical validation:
  schema, OHLCV, timestamps, monotonicity, symbol identity when supplied, conflicting
  duplicates and file stability. It does not attest adjustment or survivorship.
  A requested missing/invalid reviewed manifest never falls back automatically.
  Such runs are permanently `PROVISIONAL_UNREVIEWED_DATA` /
  `NOT_VALID_FOR_FINAL_PERFORMANCE_CLAIMS`; external review is still required.
- Required references: SPY, QQQ, SOXX plus each symbol's mapped reference. No silent
  reference substitution. Missing files fail clearly; missing minutes do not fill.
- Exact duplicates are counted in the preflight and skipped by the shared reader;
  conflicts/disorder fail. No forward fill, nearest future benchmark, centered
  rolling statistic, whole-sample normalization or daily final volume is used.
- A gap resets contiguous rolling history; an incomplete regular-session VWAP
  prefix disables new features for that symbol for the rest of the day. This
  conservative V0 quality rule and missing counts are explicitly recorded.
  Position management still uses subsequent genuine observed closes.
- Data are loaded once per day, never per bot. Precomputed future rows are stored
  but the engine indexes **only the current completed minute**. Benchmark joins
  are at that exact observable time. All source SHA256s are rechecked after replay;
  changed inputs make the run `error`, `resultsValid=false`, never completed.
- Mechanical preflight hashes/scans finalized files, potentially their full history,
  even for a 5-day smoke. It does not train on those preceding/future sessions.

## Feature schema

For completed close `C_t` and consecutive exact one-minute observations:

| Names | Definition / units | Lookback |
|---|---|---|
| `return_1m/5m/15m/30m` | `C_t/C_(t-n)-1`, fraction | n minutes |
| `momentum_5m/15m/30m` | Same simple return; output aliases, **not duplicated policy weights** | n |
| `current_volume` | Completed-bar shares | 1 bar |
| `rolling_volume_mean` | Mean of **previous** 20 volumes, excluding current | 20 |
| `volume_ratio` | Current volume / that mean (zero baseline → 0) | 20 |
| `volatility_5m/15m/30m` | Population std of last n consecutive one-minute returns | n |
| `distance_from_vwap` | `C_t / VWAP_t - 1`; typical-price×volume running session sums | completed prefix |
| `distance_from_recent_high/low` | `C_t / max(H)` or `/ min(L) - 1` | last 30 completed bars |
| `spy_return/qqq_return/sector_return` | Exact-time reference 5m return (sector = SOXX) | 5 |
| `benchmark_momentum/volatility` | Mapped reference 15m return / std | 15 |
| `relative_strength/sector_relative_strength` | Symbol 15m return minus reference / SOXX 15m return | 15 |
| `minute_from_regular_open/to_regular_close` | Minutes from actual open / until actual calendar close | current clock |
| `current_position` | 0/1, one long instrument per bot | current account |
| `unrealized_pnl` | Quantity × (observed close − entry fill) | current account |
| `holding_minutes` | Observation time minus actual entry time | current position |
| `available_cash` | USD after costs | current account |

Minimum warmup is **31 contiguous completed bars** to produce the 30m return.
Regular-session VWAP uses `(H+L+C)/3`; it is an OHLCV approximation, not tick VWAP.
Volume ratio is the documented causal fallback; historical same-time-of-day RVOL
is not claimed. Close-only MFE/MAE are explicit and understate unobserved extremes.

Policy normalization is fixed unit scaling, with no fitted population mean/std:
returns/volatilities/distances/RS divided by `return_scale=.01`; volume features
`log1p(volume/100000)`; volume ratio minus 1; time divided by actual session duration;
then clip market features to ±5. Account state uses position 0/1, unrealized PnL /
daily capital, holding / session minutes, cash / daily capital. Scales are config.

| Instruments | Main benchmark | Sector context |
|---|---|---|
| Semiconductor stocks, SOXL/SOXS/SOXX/SMH | SOXX | SOXX |
| Large/high-beta tech, QQQ/TQQQ/SQQQ | QQQ | SOXX |
| SPY / IWM | self | SOXX |

SOXX for tech is a **semiconductor contextual comparison**, not asserted technology
sector identity. Relative returns are plain differences, without fitted beta or
leveraged/inverse adjustment. V0 may buy inverse ETFs as long instruments; there
is no naked short, account leverage, borrowing, pyramiding or averaging down.

## Genome and deterministic policy

`Genome.weights` is an immutable tuple serialized as named policy columns; all 22
non-alias market fields + 4 account fields are weighted (26 weights). Defaults:

| Gene | Bound |
|---|---|
| each weight | [-2, 2] |
| buy / sell threshold | [.25, 3] / [-3, -.25] |
| stop loss fraction / take profit fraction | [.005, .03] / [.008, .06] |
| max hold minutes | integer [5, 120] |
| position fraction of cash | [.1, 1] |

`score = dot(normalized market + account features, genome weights)`.
Flat → highest eligible symbol score ≥ buy threshold → pending BUY; otherwise HOLD.
Sorted symbol order breaks ties. One position and one pending action per bot.
Holding → stop, take profit, max hold, or score ≤ sell threshold → pending SELL.
Session exit is independent of missing policy features. Priorities are stop, TP,
max hold, score, session. No new order is emitted on a just-filled exit minute.
Re-entry after a completed trade is permitted; the fitness trading penalty records
and penalizes excess activity. There is no parameter grid or winner search.

## Execution and account

- At signal time T, the decision candle is already completed; entry is the next
  **completed-minute close observation at T+1m**, never signal close or past open.
  Missing that exact next observation cancels a pending BUY; it never fills later.
- Each fill is adverse by `(slippage_bps + half_spread_bps)/10000 = .0003`:
  BUY `observed_close × 1.0003`; SELL `observed_close × .9997`.
  Fractional quantity = `(cash × position_fraction) / entry_fill`; no borrowing.
  These are OHLCV observation proxies, not claims of executable quotes or zero fees.
- Close observation detects stop/TP; pending exit uses the **subsequent** genuine
  close. A missed exit waits for real data, records latency and missing observations,
  and never fills at a theoretical stop. Gap fills can be worse than the stop.
  Candle high/low never choose optimistic intrabar paths, exits or MFE/MAE.
- Forced-exit decision occurs one minute before `close − 5m` to permit a following
  observation by the cutoff. Late recovery may delay that exit and is visible.
  If no exit observation returns by session end, the position is preserved in the
  day's audit record: `INVALID / NO_OBSERVABLE_SESSION_EXIT`, no fabricated fill,
  no reproduction and no profit attributed to lifetime. It is quarantined, not
  carried into the next $100 evaluation account. INVALID is data quality, not DEAD.
- Every trade stores bot/genome identity, source candle, signal/entry/exit timestamps,
  market/fill prices, quantities, stop/target, score/features/benchmark, regime
  sign diagnostics, costs, PnL/R, close-MFE/MAE timestamps and holding/latency.

## Survival, fitness and reproduction

Every bot receives a **new independent $100 account each trading day**.
For valid closed accounts with ε = `1e-6 USD`:
PnL > ε → SURVIVE / parent eligible; PnL < −ε → DEAD; |PnL| ≤ ε → NEUTRAL.
Incomplete-liquidation INVALID accounts are separately counted/excluded.

```
daily_return = daily_net_pnl / initial_capital
fitness = daily_return
        - 0.5 * maximum_daily_equity_drawdown_fraction
        - 0.25 * (buy_and_sell_execution_cost_usd / initial_capital)
        - 0.001 * max(0, completed_trades - 10)
```

Net PnL already includes execution costs; the extra cost term is deliberate activity
regularization. All coefficients, tolerance and bounds are in `default_config.json`.
Equity drawdown uses chronological marked equity with initial $100 included.
Daily metrics include trades, win rate, average R, cost, turnover and hold duration.

Positive valid bots are ranked by fitness (ID tie-break); parent pool is the top
`min(40, ceil(positive_count × .5))`. Preserve up to 10 elites without mutation;
restore total population (default 200) with sampled parents. With probability .7,
use two distinct parents and **uniform per-gene crossover**, then mutation.
Each gene mutates with probability .2, Gaussian σ = .05 × its bound width, clipped
to bounds (integer hold time rounded). Parent tuples are never changed in place.
Mutation count counts changed genes, not attempted mutations or descendant bots.
If no positive parent exists, create an explicitly labeled **RANDOM_RECOVERY**
population with no parent IDs; DEAD/NEUTRAL bots never enter the pool.

IDs/generation/parents/birth/death, current birth mutation count, lifetime days/trades,
lifetime equity/return and SHA256 track lineage. Birth date is reproduction date
(at the end of the previous evaluation day). Elites inherit that unchanged genome's
financial lifetime; mutated/crossed offspring start a new financial lifetime, and
retain parent IDs. Lifetime equity = starting capital + sum of valid **daily unit**
PnL; it is additive, not a compounded reinvestment account or daily fitness capital.

## Splits and reproducibility

Config baseline: train 2022–2024, validation 2025, final OOS 2026; configurable,
chronologically ordered/disjoint. Evolution/selection runs **only on train days**.
The final training offspring population is frozen and evaluated unchanged in both
validation and OOS, with fresh daily accounts. No validation-selected genome or
OOS-informed mutation. Freeze hashes are checked before and after evaluation.
If a range contains no train day, the frozen population is explicitly an untrained
random baseline. Validation/OOS lifetime PnL is separately recorded in
`evaluation_summary.json` and `splitLifetimeEquity`, without altering train lineage.

One seeded `random.Random(seed)` drives initialization/selection/crossover/mutation;
stable ordered symbols/IDs, immutable features and recorded input/code hashes/config reproduce
financial artifacts with identical inputs/config/seed. Run IDs, wall-clock metadata
and runtime intentionally differ. Python/NumPy/calendar versions and Git dirty state
are also recorded. Cross-platform floating-point BLAS may differ
slightly. No OOS retuning or statistical significance is claimed.

## Outputs and monitor

New unique microsecond-UTC/UUID directory, never raw data or an old research run:

```
research/results/evolution/<run-id>/
  run.json, status.json, generations.jsonl, best_genomes.json, lineage.jsonl
  frozen_population.json        # created at first validation/OOS day
  final_population.json, evaluation_summary.json
  generation_0000/
    population.json, next_population.json, survivors.json, dead.json
    neutral.json, invalid.json, trades.csv, metrics.json, lineage.json
  generation_0001/...
  validation_0001/...
  final_oos_0001/...
```

Completed status `generation` is the last evaluated generation; the separately
stored `nextPopulationGeneration` identifies offspring awaiting their next day.

`best_genomes.json` is a training-only most recent daily winner audit record, never
the validation/OOS selection basis. Full populations persist separately.
Config/source manifest/git SHA/status are atomic JSON writes; daily artifacts are
append-only/new-directory. Replaying a killed run starts a new unique run; old
partial artifacts are preserved. Generated results are gitignored.

`GET /api/research/evolution/status` reads only bounded status metadata and removes
input paths/manifests from the public payload. It exposes the requested generation,
population/alive/dead/neutral/invalid, daily mean/median/best, lifetime equity, trades,
historical date, parent pool, mutation count and latest log. Dead processes display
`interrupted`, not running. No start/control endpoint or LIVE ARM integration.
An independent card below the existing V4 monitor is added to the data-collection
tab. Tick/V4 existing polling remains. Backend and CLI share `EVOLUTION_RESULTS_DIR`
(default repository `research/results/evolution`). Use a persistent directory on
EC2 for both. Deployment / Pages synchronization is a separate operation; this
change is committed/pushed only to `strategy-engine-v3`, never `main`.

## Multi-fold stability selection (V0.6)

V0.6 keeps V0.5 execution controls but changes parent selection inside the training
period. A generation now spans 28 processed trading sessions, split chronologically
into four independent 7-session folds.

A genome can become a parent only when:

- the full 28-session additive PnL is positive
- at least 3 of the 4 seven-session folds are positive
- the median fold return is positive
- all sessions are mechanically valid

The fitness coefficients from V0.5 are unchanged. This is a structural generalization
gate rather than another 2025-fitted penalty adjustment.

Training remains 2022-01-03 through 2024-12-31. 2025 is frozen development validation.
2026 is not evaluated by this campaign.

```bash
python research/evolution/run_v06_campaign.py \
  --data-dir data/toss_1m \
  --results-dir /home/ubuntu/evolution-results \
  --symbols TQQQ,AAPL \
  --population 20 --max-population 300 --seed 42
```

## Structural anti-overtrading campaign (V0.5)

V0.4 soft penalties did not reduce validation turnover, so V0.5 makes the next
change structural instead of increasing penalty coefficients.

V0.5 keeps the same causal features, execution costs and seven-session generation,
but adds:

- maximum 3 completed trades per bot per trading session
- 30 minute cooldown after a completed exit before another entry
- at least 4 profitable sessions out of 7 before a profitable bot can become a parent
- a broader training horizon from 2022-01-03 through 2024-12-31
- 2025 remains frozen validation
- 2026 remains untouched final OOS

The profile is `research/evolution/v05_structural_config.json`.

```bash
python research/evolution/run_v05_campaign.py \
  --data-dir data/toss_1m \
  --results-dir /home/ubuntu/evolution-results \
  --symbols TQQQ,AAPL \
  --population 20 --max-population 300 --seed 42
```

## Generalization fitness and campaign (V0.4)

The V0.3 frozen validation showed strong negative association between validation
performance and trade count, while train/validation rank correlation remained positive.
V0.4 therefore adds a separate, opt-in `v04_generalization` fitness profile rather
than changing the legacy V0 defaults.

The V0.4 seven-session window fitness is:

`window return - drawdown penalty - cost penalty - excess-trading penalty - losing-session penalty - return-volatility penalty`

The repository profile is stored at
`research/evolution/v04_generalization_config.json`. It does not alter execution
costs, feature definitions, timestamp semantics, or the untouched 2026 final OOS split.

A campaign runner executes only:

1. 2024 training
2. 2025 frozen validation
3. generalization diagnostics

It never evaluates 2026.

```bash
python research/evolution/run_v04_campaign.py \
  --data-dir data/toss_1m \
  --results-dir /home/ubuntu/evolution-results \
  --symbols TQQQ,AAPL \
  --population 20 --max-population 300 --seed 42
```

## Frozen validation (V0.3)

V0.3 adds a separate evaluator for a trained population. It loads the exact
`final_population.json` from a completed training run, verifies every genome hash,
and evaluates that fixed population on the configured validation split.

There is no reproduction path in this runner:

- no mutation
- no crossover
- no parent selection
- no adaptive population resizing
- no lifetime-state mutation
- no live/paper broker calls

The validator verifies the source population hash before, during, and after the run.
It also re-hashes all market input files before finalizing.

Use the newest completed training population:

```bash
python research/evolution/evaluate_population_v03.py \
  --source-run latest \
  --data-dir data/toss_1m --timestamp-kind end --provisional \
  --from-date 2025-01-02 --to-date 2025-12-31 \
  --results-dir /home/ubuntu/evolution-results
```

The source run's exact symbol universe and config are reused. A caller cannot silently
change the validation symbol universe. Results are written to a new run directory as
`validation.jsonl`, per-day validation artifacts, and `validation_summary.json`.

## Seven-trading-day generations (V0.2)

The default selection window is now **7 processed trading sessions**.

- every daily simulation account still starts from `$100`
- the same genome remains active for up to 7 processed train sessions
- one losing day does **not** kill the bot immediately
- daily PnL is accumulated across the window
- window PnL < 0 -> DEAD
- window PnL ~= 0 -> NEUTRAL
- window PnL > 0 -> reproduction eligible
- mutation/crossover and adaptive population scaling happen only when the 7-session window completes
- an INVALID day makes the whole selection window INVALID for that bot
- validation/final OOS remain frozen and never trigger reproduction

The runner exposes `--evaluation-window-sessions` for controlled comparisons. The
repository default is 7. A one-session baseline can still be reproduced explicitly
with `--evaluation-window-sessions 1`.

Example:

```bash
python research/evolution/run_evolution_v0.py \
  --data-dir data/toss_1m --timestamp-kind end --provisional \
  --symbols TQQQ,AAPL --population 20 \
  --evaluation-window-sessions 7 \
  --adaptive-resources --min-population 20 --max-population 300 \
  --initial-capital 100 --seed 42 \
  --from-date 2024-06-03 --to-date 2024-08-30 \
  --results-dir /home/ubuntu/evolution-results
```

The dashboard reports the current window as `N / 7`, its best/mean cumulative
window return, and whether selection is still waiting or a generation has completed.

## Adaptive resource runner (V0.1)

Longer research runs can opt into host-aware population scaling with
`--adaptive-resources`. The initial population is still explicit; scaling happens only
after a completed **train** session. Validation/final OOS populations are frozen.

Default guardrails:

- start/min population: 20 when requested by the caller
- maximum population: 1000 (override with `--max-population`)
- normal resources: next population = current x 1.25
- RAM >= 70%, swap >= 5%, or CPU >= 85%: HOLD
- RAM >= 85% or swap >= 20%: SCALE_DOWN x 0.75
- never scales below `min_population` or above `max_population`
- records CPU, RAM, swap, process RSS, session runtime, safe/peak/next population
- resource pressure never changes genomes during validation/OOS

Adaptive mode is intentionally **not bit-for-bit reproducible across hosts**, because
population size depends on observed system load. The seed still controls genome
randomness for a given scaling path.

Example bounded research run:

```bash
python research/evolution/run_evolution_v0.py \
  --data-dir data/toss_1m --timestamp-kind end --provisional \
  --symbols TQQQ,AAPL --population 20 --adaptive-resources \
  --min-population 20 --max-population 500 \
  --initial-capital 100 --seed 42 \
  --from-date 2024-06-03 --to-date 2024-06-28 \
  --results-dir /home/ubuntu/evolution-results
```

Swap is an OOM safety net, not a performance tier. The runner never creates swap
itself. On an EC2 host, `scripts/install_evolution_swap.sh` can create one guarded
`/swapfile` (default 4 GiB) and set `vm.swappiness=10`. It refuses to overwrite an
existing inactive file.

```bash
sudo bash scripts/install_evolution_swap.sh
```

## Manual bounded EC2 smoke

No new data collection/download, live order, service restart or full training:

```bash
python research/evolution/run_evolution_v0.py \
  --data-dir data/toss_1m --timestamp-kind end \
  --data-manifest research/v4_data_manifest.json \
  --population 20 --initial-capital 100 --seed 42 \
  --from-date 2024-06-03 --to-date 2024-06-07 \
  --results-dir /var/lib/market-career-dashboard/evolution --smoke
```

The manifest must genuinely pass the existing reviewed contract and END semantics.
If unavailable, for **development only**, replace the manifest argument with
`--provisional` (and explicit naive timezone if source requires it). That command
still mechanically audits every required finalized file, never forges review flags.
Collecting/mutating exports cannot finalize. `--symbols TQQQ,AAPL` can bound a
partial sanity run; SPY/QQQ/SOXX must still exist. `--smoke` caps population at 32
and the first 5 XNYS dates. No date arguments without smoke → fail, avoiding an
accidental five-year run. `--data-origin synthetic` is for test fixtures only.

Set backend `EVOLUTION_RESULTS_DIR` to the same persistent path when separately
deploying the read-only monitor. No change to collector/live/paper systemd services.

## Validation and limitations

Tests: feature future mutation/past stability, exact END semantics, benchmark gaps,
rolling baseline exclusion, DST/early close; genome JSON/bounds/immutability/seed;
fractional accounts and bilateral costs; delayed execution/gap exits/no high-low
ordering; deaths/neutrals/positive parents/elite preservation/all-dead recovery;
multiple generations, train/validation/OOS freeze, artifact reproducibility, source
hash mutation rejection, malformed timestamps/duplicates, read-only API/UI and no
live imports. See `research/test_evolution_v0.py` and monitor smoke test.

Actual Toss data and EC2 connectivity are absent in this workspace. The local CLI
smoke uses **explicit synthetic data**, seven XNYS dates, 20 bots, two candidate
instruments plus three references (9,750 fixture rows). It tests wiring only;
repeated rising fixture days are not an economic experiment. No historical
profitability, full-universe runtime or EC2 capacity claim can follow from it.
Real END-label evidence, adjustment/survivorship review, fee/quote executability,
missing-session impact and independent OOS are still required before long training.
