# Evolution V0.7-R1: five-session regime/timing experiment

Research only. No broker calls, collector changes, deployments, or historical
training were performed for this change. **2026 Final OOS = CLOSED.**

## Source audit and compatibility

Inspected `run_v07_campaign.py`, `v07_holdout_config.json`, `run_evolution_v0.py`,
`population.py`, `engine.py`, `features.py`, `account.py`, `genome.py`, `config.py`,
`evaluate_population_v03.py` and their existing tests. The three partial commits
1669075 / 62172a6 / 812c81c added configuration and audit labels but did not connect
`v07_regime_timing` to window fitness or parent-consistency gating. This completes
that opt-in mode, rather than changing `v05_structural` scoring.

Original V0.7 configuration is unchanged: daily $100 reset accounts, seven-session
selection window, >=4 profitable sessions, `v05_structural`, daily cap 3, cooldown
30 minutes. Defaults of the existing campaign still select this baseline.
Original entry/exit prices, stop/take-profit/sell/max-hold genes, normalization,
policy weight names, mutation/crossover and legacy genome hashes are preserved.

New files:

- `v07_5d_control_config.json`: five sessions, >=3 profitable sessions, old policy
  and fitness; isolates the generation-period effect.
- `v07_5d_regime_timing_config.json`: five sessions, >=3 profitable sessions,
  new optional context genes and research objective.
- `regime_timing.py`: causal market classifier and post-trade/window diagnostics.
- `data_access.py`: bounded numeric-price decoding; source integrity validation.
- `audit_v07_data.py`: streaming, read-only coverage and storage audit.
- `compare_v07_experiments.py`: descriptive comparison of separate training arms.

`ceil(5 * 4/7) = 3` preserves the old minimum positive-day fraction. A generation
requires **five consecutive scheduled XNYS sessions**, including holiday/early
close awareness, not five conveniently available days. Missing days/minutes are
recorded, not filled: an incomplete required source session invalidates the bot
window and prevents parent selection. No reproduction on a trailing partial
window. All positions remain intraday; five days is not a holding period.

## Causal context and genome extension

At completed END-label timestamp T, for both SPY and QQQ:

- `r_n = close(T)/close(T-n minutes)-1`, n=5,15,30, contiguous minutes only.
- `sigma30 = population_std(last 30 one-minute returns)`.
- `VWAP = cumulative typical_price * volume / cumulative volume` since open.
- `d = close/VWAP-1`; `s = VWAP(T)/VWAP(T-5 minutes)-1`.
- `ER30 = abs(close(T)-close(T-30))/sum(abs(one-minute price changes))`.
- `VR = current_volume/mean(previous 20 completed volumes)`; current excluded
  from denominator. No historical full-session volume is used.

BULL: both references have r5>0, r15>0, d>0, s>0 and
r30 >= max(0.001, sigma30*sqrt(30)). BEAR is the exact negative counterpart.
Otherwise SIDEWAYS. Missing reference context is explicitly `valid=false`;
no reference substitution, backfill or future-fill. A positive 5m rebound in a
negative 15/30m market cannot be BULL. Initial slope warmup is SIDEWAYS until an
actual previous VWAP exists. The classifier labels the broad market, so a
profitable SQQQ/SOXS long can be recorded in BEAR. It does not reverse orders or
force exposure. Cash during unfavorable periods is allowed.

Existing symbol policy already uses returns, volume ratio, volatility, VWAP
position, market/sector returns and relative strength (15m symbol return minus
benchmark return). Six optional, JSON-named context weights add:

| Field | Normalized value |
|---|---|
| market_bull / market_bear | respective regime indicator, 0 or 1 |
| market_return_30m | mean SPY/QQQ r30 / fixed return_scale |
| market_vwap_distance | mean reference d / fixed return_scale |
| market_volume_ratio | mean reference VR - 1 |
| market_efficiency | mean reference ER30 |

All clipped by the existing fixed feature clip; no future normalization. Policy
score is old score plus dot(context weights, context values), only in candidate
mode. The current cash/position state and existing BUY/HOLD/SELL logic remain.
`context_weights` is absent in old JSON: old files load unchanged and use zero
context contribution. Empty extensions are omitted on serialization, preserving
legacy hashes. New random candidates have six bounded [-2,2] weights, drawn from a separate
hash-seeded stream so the original initial genes match across A/B arms with the
same seed; Gaussian
mutation/uniform crossover support these without changing parents in place.
This is a linear-policy hypothesis, not demonstrated regime-specific edge.

## Entry/exit timing and objective

Signals use completed minute data. A pending BUY fills at the **next completed
minute close**, with 2bp slippage +1bp half-spread; this deliberately retains the
existing observable-close proxy, not an earlier historical open. A missing/late
entry observation cancels the entry. SELL uses the next available observed close
with adverse costs, including gap-through-stop prices. No high/low-based fills,
no same-close retroactive execution. Unresolved session exits invalidate evaluation.

Legacy `marketRegime` / `confirmedMarketRegime` labels originate from the signal
snapshot; these are retained. New `signalRegime` is separate from `entryRegime`,
which is calculated at the actual completed entry observation. Trades include
`entryContextTimestamp`, `entryMarketContext` (SPY/QQQ source features) and
`entrySourceCandle`. Thus signal, execution and regime can be audited separately.

After a trade closes, compute using observed completed closes only:

- `earlyAdverse = max(0, (entryFill-min_close_first_5min)/entryFill)`.
- `giveback = max(0, MFE_fraction-max(net_trade_return,0))`.
- `allowance = max(0.001, entry_sigma15*sqrt(5)) + roundtrip_cost_rate`.
- `A = max(0, earlyAdverse-allowance)` for net winning trades; otherwise zero.
- `G = max(0, giveback-allowance)` for net winning trades; otherwise zero.
- late-chase diagnostic: entry VWAP distance > allowance; no extra chase penalty.

Early low continues to update only through five minutes after entry. MFE/MAE,
entry-to-MFE/MAE times, hold duration, execution latency and final net PnL are
preserved. No post-exit prices are inspected. No post-trade score becomes an
input to an already executed signal. Losing trade MAE/giveback are reported but
not charged again by the timing terms. A temporary adverse move alone is not a
failure: the allowance accounts for volatility/costs and selection considers final
net results. These are preferences, not calibrated estimates of execution alpha.

For a valid five-session window, define:

- R = sum(daily net PnL)/100 (daily-reset arithmetic, not compounded account).
- D = peak-to-trough drawdown of the stitched `$100 + cumulative realized PnL`
  path across daily-reset accounts. Unlike max daily DD, includes multi-day losses.
- N = completed trades / session count; S = std(daily net returns).
- C = abs(max(0,BULL net PnL)-max(0,BEAR net PnL))/100, **only** when each regime
  has >=3 completed trades; otherwise C=0 and `INSUFFICIENT_REGIME_SAMPLES`.

```
fitness = R - 0.5*D - 0.0015*max(0,N-2) - 0.25*S
            - 0.1*mean(A) - 0.1*mean(G) - 0.1*C
```

All coefficients/scales live in Config/experiment JSON. These are unoptimized
coarse starting choices. Net R already deducts both sides' transaction costs:
`transaction_cost_penalty=0`; `losing_session_penalty=0` and regime loss penalties
are zero. Config rejects re-enabling these duplicate loss/cost penalties in this
mode. Drawdown is path risk, dispersion is day-to-day instability, recovered
adverse and giveback describe different parts of a winning trade's path. Regime
concentration penalizes unequal *positive contributions*, not BEAR losses twice.
All components and raw regime PnLs/costs are saved for later attribution.

Parent eligibility: full valid window, positive net PnL > epsilon, >=3 positive
days (daily epsilon applied), trades>0, positive fitness and stitched DD <=5%.
The DD limit is a coarse research selection guard, not a trade stop. Parents rank
by fitness, ties bot ID; existing elite/mutation/crossover mechanics remain.
No-trade bots remain NEUTRAL and cannot reproduce, even if their zero score beats
a losing bot. More trades receive no bonus; structural cap/cooldown remain.
Regime adequacy is diagnostic, not a requirement to manufacture BEAR trades.
If no eligible parents exist, existing RANDOM_RECOVERY is recorded explicitly;
dead/ineligible genomes never become parents.

## Read-only data audit and embargo

This workspace has no EC2 connection, source data, or mounted research EBS.
Actual 2024 AAPL/TQQQ repair status is **UNVERIFIED**. The audit explicitly checks
2024-07-26, 07-29, 07-30, 07-31, 08-01, 08-02 for both symbols.

Inputs: existing finalized SYMBOL.csv[.gz], using the existing source resolver.
Source identity, finite OHLCV/quotes, ordering, conflicts and timezone declaration
are checked. Exact duplicate rows are counted, conflicting rows fail. XNYS
regular minutes are checked per source including SPY/QQQ/SOXX; samples are bounded.
Naive timestamps fail unless source timezone is explicitly declared. END labels
mean [T-1minute,T), converted to America/New_York. No missing minute is invented.
Any missing regular prefix invalidates existing VWAP signal availability for the
remainder of that session; that existing conservative feature rule is retained.

Training/validation CLIs reject requested dates >=2026 **before market preflight**,
even if a custom config relabels them. Both use bounded streaming price decoding:
2026 OHLCV never becomes numeric bars/features/evaluation. Mixed-year source files
are necessarily byte-read for hashes and timestamp-scanned for ordering; this is
integrity inspection, not research use of 2026 prices. No isolated 2026 files are
selected. The untouched reviewed-manifest validator still governs reviewed data;
mechanical audit produces no fake review flags and remains PROVISIONAL.

Source hashes are checked before/after audit and after evaluation. Active
collector mutation fails the research run; it does not stop/restart the collector.
Use an existing finalized snapshot if the source is still changing. Source paths,
SHA256, config snapshot, code hashes, commit, seed, timestamp basis and execution
model are recorded in a uniquely created run directory; previous artifacts are
never overwritten. Output cannot be inside raw data. Audit/comparison commands
also refuse existing output filenames.

## Safe EC2 commands (manual, after code is available)

No automatic deployment or training. Do not force-pull a dirty operating checkout.
Use the already installed environment and a separately reviewed code update; no
token/service changes. The following runs serially with one BLAS thread. Replace
`.venv/bin/python` with the existing research Python path if different.

Read-only audit, mounted output required (no training; checks all pre-2026 data):

```bash
cd /home/ubuntu/market-career-dashboard && .venv/bin/python research/evolution/audit_v07_data.py --data-dir /home/ubuntu/market-career-dashboard/data/toss_1m --symbols AAPL,TQQQ --timestamp-kind end --from-date 2022-01-01 --to-date 2025-12-31 --require-ebs --out "/mnt/research/evolution-v07-5d-audit-$(date -u +%Y%m%dT%H%M%SZ).json"
```

Select all 30 symbols for an audit using `--symbols` with the exact list from
`universe_v3.json`; an omitted `--symbols` on the training CLI uses all 30.
The audit JSON reports input/output mount devices, available space, explicit
known-missing dates and benchmark completeness. `--require-ebs` verifies a distinct
`/mnt/research` mount, not the AWS volume ID; verify the actual 256GB volume with
`findmnt -T /mnt/research` and `lsblk -f`. An audit failure is not permission to repair.

Bounded first smoke on real data, after audit (no full campaign):

```bash
cd /home/ubuntu/market-career-dashboard && OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 10 .venv/bin/python research/evolution/run_evolution_v0.py --data-dir /home/ubuntu/market-career-dashboard/data/toss_1m --config research/evolution/v07_5d_regime_timing_config.json --timestamp-kind end --symbols AAPL,TQQQ --population 20 --seed 42 --from-date 2023-06-01 --to-date 2023-06-14 --provisional --results-dir /mnt/research/evolution-v07-5d-smoke
```

This explicit 10-session range executes two 5-day generations. The existing
`--smoke` flag is even more bounded (first seven sessions, one complete generation
plus a partial window); it does not fabricate candles. If timestamps are naive,
add `--naive-timezone` **only after verifying the source declaration**.

For an A/B/C comparison, repeat that bounded command serially, changing only
config/output root to `v07_holdout_config.json` / `...-baseline7` and
`v07_5d_control_config.json` / `...-control5`, with the same seed, dates, population,
symbols and costs. Do not enable adaptive scaling for this controlled comparison.
Then (paths are the unique run directories printed by each invocation):

```bash
.venv/bin/python research/evolution/compare_v07_experiments.py --runs /mnt/research/BASELINE7/RUN_ID /mnt/research/CONTROL5/RUN_ID /mnt/research/CANDIDATE5/RUN_ID --out /mnt/research/v07-5d-comparison-UNIQUE.json
```

Comparison checks source hashes, seed, dates, universe, population and execution
costs. Reports daily population means/medians, trades, costs, DD and quality.
It never selects a winner or fits parameters. Window fitness scales differ across
5/7 sessions; use daily net returns and subsequent frozen validations. Candidate
changes policy context **and** objective: compare with control, but do not attribute
any difference exclusively to one component without a later separate ablation.

Full campaign command is provided for a **later deliberate run**, not executed:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 10 .venv/bin/python research/evolution/run_v07_campaign.py --experiment five-day --data-dir /home/ubuntu/market-career-dashboard/data/toss_1m --results-dir /mnt/research/evolution-v07-5d-results --population 20 --max-population 50 --symbols AAPL,TQQQ --seed 42
```

Same chronological procedure: Train 2022–23 -> freeze -> Internal Holdout 2024 ->
positive/valid annual-return shortlist (max40, unchanged V07 rule) -> freeze ->
2025 Development Validation -> diagnostics. The 2024 step **is model selection**,
not pristine OOS. 2025 remains development validation; do not iteratively fit its
numbers. This campaign requires a separate research mount for candidate output.
Legacy `--experiment baseline` retains its existing seven-session configuration.
No 2026 path or automatic Final OOS promotion exists in either command.

## Monitoring and capacity

No dashboard layout/API/auth changes. Existing monitor consumes the same status
keys: window length/completion, generation, alive/dead/neutral, mean/median return,
parent/mutation counts and progress. New bounded `researchDiagnostics` and
`finalOosGate` are available through its allowlist. Per-day metrics and completed
window files include regime trades/PnL/cost, timing, win rate/hold time, stitched DD,
component scores and parent-selection reasons/genome hashes. Private equity paths
are held for at most five sessions and omitted from exported per-day metrics.

One process, one shared session feature matrix, no per-bot CSV rereads. Default
candidate 20 bots, full campaign adaptive ceiling 50 (explicitly configurable),
legacy ceiling unchanged. Adaptive Resource Guard remains; candidate additionally
stops before a session on RAM/swap hard pressure or free disk below max(10GiB,10%).
It preserves the last completed artifacts and records ERROR/RESOURCE_GUARD_PAUSED;
this is a controlled stop, **not automatic resume**. No running services are touched.

Synthetic measurements on this Codex machine (not EC2, not market performance):
10 days / 20 bots / 2 tradable +3 reference instruments: baseline7 2.36s,
control5 2.29s, candidate5 2.64s. Candidate completed two generations with parent
pools 3 then 10 and mutation counts 139 then 74; no random-recovery fallback.
Candidate artifacts 4,016,964 bytes (107 files); whole-process peak RSS 83.6MiB.
The same source hash audit passed and inputs remained unchanged. One full 30-symbol session /20 bots: 1.45s, peak RSS
103.2MiB. CSV hashing/I/O and actual long-run artifacts can dominate real EC2 runs.
Start at20, measure the monitor's RSS/runtime, and retain headroom for collectors;
these measurements are not a promise for a 2GB host. No extra Python dependencies.


## Verification recorded for this change

- Before changes: 554 existing Python tests passed (54.8s).
- Release: 575 Python tests passed (66.3s), including 21 new V07-R1 tests.
- All 14 existing admin/collector/paper/export JavaScript smoke tests passed.
- `compileall`, five CLI `--help` checks and `git diff --check` passed.
- Actual script entrypoint smoke and data-audit CLI passed on tiny explicitly
  synthetic CSVs. Ten-session benchmark completed two five-session generations;
  separate seven-session `--smoke` completed one window plus a partial window.
- Old/no-context genomes produce identical execution prices/PnL under candidate
  accounting when legacy genes match; legacy config and serialized hashes remain
  compatible. Initial legacy genes match across experiment arms with the same seed.
- Tested BULL/BEAR/SIDEWAYS, bearish rebound, future perturbation invariance,
  missing exact reference/no fill, END labels, naive-timezone refusal, missing
  scheduled days, risk/fitness/zero-activity gates, timing-cost reconciliation,
  mutation/crossover compatibility, frozen validation, source preservation,
  resource guards and 2026 rejection before source preflight.
- Actual EC2 coverage, AAPL/TQQQ repair state, adjusted-price provenance and
  dedicated EBS mapping remain runtime checks. No historical performance, market
  superiority, or production deployment is claimed.
