# V4 context/clock audit — 2026-10-10

## Scope and evidence

No parameter/stop/target/hold/fee/slippage/candidate-universe changes. No full
historical simulation, download, production service change, broker call or
forward-log modification. Frozen S1/S2/S3, V3 and Simple Momentum are untouched.
Reviewed manifest remains mandatory for FINAL; explicit provisional results remain
`PROVISIONAL_UNREVIEWED_DATA`. Diagnostic PASS does not attest price adjustment,
source labels, corporate actions, eligibility, quotes or executability.

Worktree `/workspace/market-career-dashboard-regime`, branch `strategy-engine-v3`.
Starting HEAD `567d35f`; the dirty user's `work` checkout was untouched.
Upstream `1f07ab8` had already introduced explicit provisional research plus
bounded mechanical inspection and frozen setup priority. Integration commit
`be1be80` preserves those upstream changes and the prior collector/lifecycle fixes.
Baseline after integration: **196 Python tests passed**, both admin JS smoke tests
passed. Changes described below are confined to V4 research.

Evidence: supplied `v4-all-backtest-2026-10-10T08-09-47-731Z.zip`, existing result,
state, audit and decision JSONL. The artifact's engine SHA256
`b8a325b81f779e11a8ad47beb6ba2606c950d95d3d62eb0d8053f89ccaa30137`
exactly matches the pre-instrumentation engine, so the traced gates are the gates
used by that run. No historical minute files/progress JSON exist in this workspace;
EC2 has not been accessed. The ZIP is an actual result artifact, not raw candles.

## Verified original result

1,213 XNYS sessions; 6 completed trades (2 wins, 4 losses); 45 signals.

| Strategy | Terminal decision rows | Old context reason | Count | % of terminal rows | Trades |
|---|---:|---|---:|---:|---:|
| IR1 |3,249|CONTEXT_FAILED|2,862|88.089%|3|
| IR2 |5,278|CONTEXT_FAILED|3,650|69.155%|2|
| IR3 |2,408|CLOCK_ELIGIBILITY_CONTEXT_UNAVAILABLE|2,324|96.512%|1|

The detailed log counts, including setup/entry cancellations, are:

| IR1 reason |Count| IR2 reason |Count| IR3 reason |Count|
|---|---:|---|---:|---|---:|
|CONTEXT_FAILED|2862|CONTEXT_FAILED|3650|CLOCK_ELIGIBILITY_CONTEXT_UNAVAILABLE|2324|
|PULLBACK_INVALIDATED|171|EXCESSIVE_PENETRATION|1107|OPENING_CONDITION_FAILED|71|
|PULLBACK_EXPIRED|128|ENTRY_WINDOW_ENDED|334|RISK_COST_GUARD|7|
|ENTRY_WINDOW_ENDED|42|NEW_LOWER_LOW|157|ENTRY_CONTEXT_FAILED|5|
|ARMED_INVALIDATED|41|ENTRY_REWARD_RECOVERY_FAILED|17|SESSION_EXIT|1|
|HARD_STOP|2|RISK_COST_GUARD|6|||
|TIME_STOP|1|RECLAIM_EXPIRED|3|||
|RISK_COST_GUARD|1|CHASE|2|||
|ENTRY_CONTEXT_FAILED|1|HARD_STOP|2|||

IR1's CONTEXT_FAILED splits by known taxonomy into 1,806 technology and 1,056
semiconductor events. Technology failures necessarily represent known market
**direction OR volatility** filters, not missing benchmark bars. Semiconductor
failures may additionally represent SOXX return/VWAP filters or unavailable SOXX
return/VWAP features. IR2 has 2,181 technology +1,469 semiconductor failures;
**all 3,650** come from known market direction/volatility filters, since IR2 does
not use sector gating and unknown context would have another legacy reason.
The exact direction-vs-volatility-vs-sector counts are **not recoverable** from
old logs: cancel-time context values were never saved. No invented split is reported.

## Root cause: IR3's label conflates unrelated gates

`run_sessions` advances `T` through XNYS RTH minute **completion** timestamps,
observes every symbol's bar with START `T-1m`, then calculates context and calls
`evaluate` at exactly 15:30 for SPY/QQQ. `evaluate` can return while still IDLE for:

1. Missing own completed bar at T.
2. Entry window/cutoff not valid.
3. Daily eligibility unavailable (61-day history, incomplete prior sessions or
   liquidity/price/ATR filter).
4. `allowed_context` false: missing QQQ/SPY direction features, volatility history,
   **or perfectly available context with own direction !=UP, other direction DOWN,
   or LOW_VOL**.
5. No strictly prior completed 5m ATR.

The caller previously converted **every** such IDLE return into
`CLOCK_ELIGIBILITY_CONTEXT_UNAVAILABLE`. Thus 96.5% is a mixed gate-rejection rate,
not a demonstrated 15:30 coverage or context-unavailability rate. This is a
proven **IMPLEMENTATION BUG in diagnostics**, reproduced with completely valid
context whose own direction is OTHER. No price/execution bug has been established
from this artifact. The change explains the original return; it does not permit
any new entry.

Actual sample: SPY 2021-12-01, IDLE -> CANCELLED at
`2021-12-01T15:30:00-05:00`; fields contain only cancellation timestamp/reason.
This is initial-history warmup (no earlier files in audit), but the log cannot
establish whether missing own bar was the first failing gate that day.

XNYS yields exactly 9 early closes over the supplied period. Therefore
`(1213-9)*2 = 2408`, exactly the artifact's IR3 terminal row count. Early closes
never reached the clock and were omitted from old terminal logs, rather than
creating the 2,324 catch-all failures. New diagnostics expose calendar exclusions
separately. 61 initial sessions cannot be daily-eligible; that alone does not
explain nearly five years of clock failures.

## Code flow and drops

|Stage|Shared/IR1|IR2|IR3|
|---|---|---|---|
|Raw row|CSV/CSV.GZ; schema/finite OHLCV; aware/exact-minute timestamps; duplicate conflicts/disorder fail; exact duplicates reported then ignored|same|same|
|Session|Normalize declared START/END to START in NY; group by NY date; XNYS open/close; release only at end|same|full 390m sessions; early closes excluded|
|Prior context|61 prior complete daily sessions; >=$5; ADV20 >=$50M; positive daily ATR; missing calendar dates append unavailable|same|SPY/QQQ own daily eligibility still required|
|Current context|QQQ/SPY synchronized endpoints, exact 21-minute returns/ER, VWAP prefix, ATR/slope; prior 60 same-clock RV/min40; no future fill|same|same|
|Detail eligibility|top15 stock detail queue; prior volume20/min10; IR1 TREND_UP+NORMAL; SOXX for semis|NORMAL and RANGE/MIXED before breakdown; non-down after breakdown|clock bypasses stock queue; own UP/other not DOWN; NORMAL/HIGH|
|Setup|10:15–14:00; completed contiguous 9 five-minute bars, 3-bar impulse, synchronized RS; freeze A|10:00–11:00; frozen OR15, width/gap; freeze A|15:30 only; opening30 complete, prior return60/min40 std, volume20/min10; Z/RVOL thresholds|
|Setup cancellation|market/vol/sector rechecked **each minute**; structure invalid, pullback expiry/window end|SETUP also cancels on market/vol change; excessive penetration; ARMED cancels on lower low, reclaim expiry|any clock gate rejects once; no retry|
|Trigger|frozen pullback boundary, later completed1m reversal/volume/RS; arm expiry|first/second subsequent complete5m reclaim, no lower low, recovery|same clock snapshot emits signal|
|Pending|frozen setup priority; sort by priority/ADV/time/symbol; per-book capacity|same|openingZ priority|
|Entry|strictly next completed1m close; no gap, latency<=90s; current context recheck; risk-cost/chase/cash/structure guard|also target/reward/recovery guard|stop1ATR, own direction recheck|
|Exit|close observation only; hard stop before trail/target; stale = no fill; 45m/time/stall/trail and cutoff|30m/target/stop/cutoff|15:50/full-session cutoff; observed stop|

Of 45 original signals, 39 entry rejections reconcile exactly:
14 RISK_COST_GUARD +17 ENTRY_REWARD_RECOVERY_FAILED +6 ENTRY_CONTEXT_FAILED +2 CHASE.
Signal-to-trade conversion is 6/45 =13.33%; this later bottleneck is separate from
clock or setup context failures. Thresholds and conservative close proxy are kept.

### Completeness propagation

`Session.observe` sets prefix_complete=False after **any** missing RTH minute;
VWAP/slope/RVOL remain unavailable for the remainder of that session even when
15:29/15:30 exists. `Session.finish` appends daily=None unless all expected RTH
minutes are present. `History.eligibility` rejects if any of its last61 sessions
is None. A single incomplete day therefore affects the following61 sessions.
This is **DATA QUALITY / INSUFFICIENT HISTORY plus an existing strict completeness
policy**, not proof of bad timestamps and not changed in this task. The written
spec says >=61 prior completed sessions; it does not explicitly resolve whether
skipping incomplete calendar days should ever be permitted. Relaxing that policy
would require a separate specification decision and is deliberately not done.

## Clock/time audit A–O

|Possibility|Finding|
|---|---|
|A target15:30 missing|Actual coverage unavailable in old audit. At decision15:30 required START is **15:29**, not15:30. New tool reports both.|
|B START vs END|Runner consistently normalizes declared labels. ZIP declares START; current official OpenAPI documents exclusive END and collector stores returned labels directly. Actual EC2 export/version provenance unresolved; do not silently switch.|
|C/D DST/naive|Aware labels convert to NY; naive fails without explicit declaration. EST/EDT and both DST boundaries tested; no naive-aware clock comparison found.|
|E session date|day_stream uses normalized NY start.date(), not UTC date.|
|F/G calendar|XNYS actual open/close; full390/early210. 9 early closes exactly reconcile2,408 clock rows.|
|H/O lookup precision|Exact normalized aware datetime at minute end; seconds/:59 rejected; no future/nearest fill. Missing endpoint produces specific reason.|
|I benchmark|Both SPY/QQQ must have context at the same completed endpoint; early session gap also invalidates VWAP for rest of day.|
|J caches|Histories keyed symbol, rv/volume by session offset; snapshots/minutes keyed aware end. Prior daily/same-clock data update only after finish. No wrong-key evidence found.|
|K history|61-day eligibility, RV60/min40, opening60/min40 and volume20/min10 can independently reject. Now recorded separately.|
|L reader|Streams every parsed row; explicit RTH filter. All24 actual input audits end at2026-10-01 10:59 ET, so the last day is partial; not evidence of reader truncation across all earlier days.|
|M/N off-by-one|observe START T-1 before eligibility/evaluate at T. START15:29 -> completion15:30 -> next observation15:31. START15:30 never leaks into15:30 decision.|

### Actual data facts versus unverified coverage

24 audited inputs report zero duplicate/conflicting/OHLC/timestamp/disorder
violations. SPY:1,228,445 rows; QQQ:1,329,405; SOXX:779,169. First labels are
2021-12-01 early extended session; all end2026-10-01 10:59-04:00. First/last offsets
show EST/EDT; the artifact did not save an offset distribution or full calendar
coverage. `coverageCheck=deferred_to_causal_runner` means **no coverage numbers**
were actually written. 15:30 coverage, whole-prefix completeness, same-clock
benchmark availability and their atomic rejection frequencies require EC2 files.
A clean mechanical audit does not mean all expected RTH minutes exist.
The final-date SPY/QQQ own inputs end before the required15:29 bar: the two
2026-10-01 clock rejects can therefore be positively classified as
`CLOCK_TARGET_BAR_MISSING` from EOF bounds and their actual decision rows.
The other2,322 legacy clock failures cannot be atomically reconstructed.
The old funnel also records11,430 DAILY_HISTORY_UNAVAILABLE over27,899 selected
symbol/session observations (40.97%), distinct from setup-context cancellations.

## Additive diagnostics and tool

`intraday_v4_diagnostics.py` explains existing boolean gates, preserving gate
priority. Examples: `CONTEXT_MARKET_DIRECTION_FILTER`, `CONTEXT_VOLATILITY_FILTER`,
`CONTEXT_SECTOR_RETURN_FILTER`, `CONTEXT_BENCHMARK_BAR_MISSING`,
`CONTEXT_BENCHMARK_SESSION_PREFIX_INCOMPLETE`,
`CONTEXT_BENCHMARK_ATR_WARMUP_INSUFFICIENT`, `CONTEXT_VOLATILITY_HISTORY_INSUFFICIENT`.
Clock counterparts include `CLOCK_TARGET_BAR_MISSING`,
`CLOCK_DAILY_HISTORY_INSUFFICIENT`, `CLOCK_DAILY_SESSION_INCOMPLETE`,
`CLOCK_OWN_DIRECTION_FILTER`, `CLOCK_REFERENCE_DIRECTION_FILTER`,
`CLOCK_BENCHMARK_BAR_MISSING`, `CLOCK_PRIOR_ATR_UNAVAILABLE`; opening history is
split into missing range/insufficient returns/undefined std/volume history/baseline.
Entry-context failures also carry atomic reason + old category.

Results gain `diagnostics.ir1/ir2/ir3`:
exclusive terminal `rejectReasons`, count/percent breakdown, maximum5 samples per
reason, separate repeated `observationRejectReasons` and bounded observation
samples, context check accepted/rejected counts, unique symbol/strategy/session
stage funnel with conversion rates. Calendar exclusions have separate counts.
Old `funnel` categories remain compatibility totals; **do not sum legacy and new
reasons as distinct events**. Samples explicitly record decision/start/observable
 times, eligibility/prior history counts, reference values, prefix-gap evidence;
no future timestamps are consulted. Post-hoc next-neighbor coverage appears only
in the audit tool and is labeled non-causal/non-signal evidence.

IR1/IR2 funnel: daily-eligible sessions -> available market context -> admitted
pattern evaluations -> setup -> trigger -> signal -> execution observation -> entry.
IR3: calendar-eligible sessions -> clock -> own target/history/ATR available ->
benchmark context -> strategy evaluation -> condition pass -> signal -> observation
-> entry. Raw observations are separate; no comparing minute counts with sessions.

`audit_v4_clock_context.py` reads finalized CSV/CSV.GZ, not mutable SQLite staging.
Memory is one day across selected symbols plus bounded rolling histories/samples;
a temporary SQLite index contains only **one row per symbol/session** for duplicate
counts, never millions of candles. Each session is written immediately to JSONL.
It reports requested six clock labels, normalized starts/source labels, expected
and observed minutes, missing/duplicates/disorder, timezone declaration/offsets,
early close, benchmark context, nearest neighbors and per-symbol/overall totals.
Short-range context warmup uses previous61 calendar sessions; it never simulates
orders. Disorder/conflicts/naive/OHLC/identity errors fail before context replay.
Outputs cannot overwrite inputs. No manifest review flags are generated/changed.

## Before/after short check

**Actual short-range rerun not executed:** no EC2 candles/access. Instead compare
original engine/runner snapshots against instrumented versions on the **same five
synthetic full-session days**, with identical seeded past history/parameters and
one neutral day, missing benchmark endpoint and early prefix gap. This is a
correctness fixture, not market performance evidence.

|Metric|Before|After|
|---|---:|---:|
|Evaluated dates|5|5|
|Setup/signal|2/2|2/2|
|Entry/exit|1/1|1/1|
|Old clock catch-all compatibility total|8|8|

New primary split of those8: OWN_DIRECTION_FILTER2, BENCHMARK_BAR_MISSING1,
TARGET_BAR_MISSING1, BENCHMARK_SESSION_PREFIX_INCOMPLETE1,
DAILY_SESSION_INCOMPLETE3. Pending capacity rejects1 separately.
All old funnel totals and all trade records/fills/PnL match original exactly
in the independent dictionary comparison. Entry109.025 ->109.0577075 at15:31;
exit109.5 ->109.46715 at15:50. New unique-session IR3 stage counts
`10 ->10 ->6 ->4 ->4 ->2 ->2 ->2 ->1`; the fixture is pinned in regression tests.
No increase in trade count is the objective or result.

## EC2 staged commands (not executed here)

Keep collection, existing outputs, paper state and services untouched. Use stable
finalized exports. First reproduce the artifact's declared START interpretation;
this does **not** certify that START is correct. Resolve source provenance before
changing to END or making final performance claims.

```bash
python research/audit_v4_clock_context.py --data-dir data/toss_1m \
  --symbols SPY,QQQ,SOXX,AAPL,TSLA,PLTR,NVDA \
  --timestamp-kind start --from-date 2022-03-01 --to-date 2022-03-11 \
  --out /var/lib/market-career-dashboard/v4_clock_20220301_11.json
```

For actual per-strategy context counts on that same small range, use separate
output names; explicit provisional mode preserves the current unreviewed status:

```bash
python research/backtest_intraday_v4.py --strategy all --data-dir data/toss_1m \
  --timestamp-kind start --provisional \
  --from-date 2022-03-01 --to-date 2022-03-11 \
  --out /var/lib/market-career-dashboard/v4_context_20220301_11_result.json \
  --state /var/lib/market-career-dashboard/v4_context_20220301_11_state.json \
  --log /var/lib/market-career-dashboard/v4_context_20220301_11.log \
  --data-audit /var/lib/market-career-dashboard/v4_context_20220301_11_data_audit.json \
  --decisions /var/lib/market-career-dashboard/v4_context_20220301_11_decisions.jsonl \
  --trades-csv /var/lib/market-career-dashboard/v4_context_20220301_11_trades.csv
```

If a reviewed manifest exists, the runner continues to validate it and never
ignores an invalid manifest via --provisional. File hashes/stability checks remain
unchanged. The above small run reads earlier input only as causal warmup; it does
not rerun the five-year strategy evaluation. Compare identical inputs and starting
cash to the pre-instrumentation implementation; compare legacy categories/fills,
not the newly named reasons. Do not restart the server to run diagnostic scripts.

**Full rerun: defer.** First obtain the bounded data audit and short actual run;
verify normalized15:29/15:30 coverage, prefix gaps, prior61 completeness and atomic
filter/data breakdowns. An instrumentation-only change is not evidence that a
five-year rerun will improve trades or performance. No economic PASS is claimed.

## Validation

Commands used (local synthetic/offline tests only):

```bash
/tmp/v4-manifest-venv/bin/python -m unittest discover -s research -p 'test_*.py'
/tmp/v4-manifest-venv/bin/python -m py_compile research/backtest_intraday_v4.py research/intraday_v4_engine.py research/intraday_v4_diagnostics.py research/audit_v4_clock_context.py research/test_v4_clock_context.py
/tmp/v4-manifest-venv/bin/python research/audit_v4_clock_context.py --help
/tmp/v4-manifest-venv/bin/python research/backtest_intraday_v4.py --help
node research/test_admin_backtest_smoke.cjs
node research/test_admin_paper_smoke.cjs
```

New tests: full session clock/trigger, missing START15:30 versus actual required
START15:29, missing benchmark without future fill, persistent prefix gap,
insufficient/partial daily history and61-session propagation, volatility warmup,
IR1/2 market/sector reasons, boolean equivalence across stages/regimes,
atomic cancellation, EST/EDT and spring/autumn DST boundaries, early close,
deterministic isolated/bounded reason counters, exact duplicates, disorder,
naive timestamp rejection/explicit declaration, mechanical failure before replay,
streaming coverage and five-day frozen trade/funnel regression.

All **217 Python tests pass** (196 prior +21 new), both admin smoke checks pass,
compilation and help checks pass. No test failure was hidden or disabled. Existing
V3/Simple/paper/collector/manifest tests remained in the suite. Market data coverage
and real short-range before/after counts remain runtime checks, not local claims.
