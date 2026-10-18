# V0.8.3 — Forward-Only Two-Parent Evolution (Research-only)

**Policy revision:** 2026-10-18 · `strategy-engine-v3`. The engine must never send broker orders or open 2026 Final OOS. The 75% host CPU / 65% RAM / 3% swap limits remain hard stop conditions.

## Why the forward-only change

The archived 45-bot V0.8.3 campaign `job-20261018T003934Z-e0670ea61647` completed its 10-day smoke test and 45-bot capacity trial; its full 2022–2023 training stopped after **7 generations and 43 total processed trading days**, because it reached **2/2 allowed no-profit date rewinds**. The *actual measured train CPU peak was 55.8%*, so this specific failure **was not** resource pressure. The old artifacts and checkpoint remain unchanged, including the historical `V083_RESTART_LIMIT_REACHED` failure.

The user requested that **no-profit generations no longer rewind the historical training date**. This new source policy applies to **new campaigns only**, not by rewriting or silently resuming the old failed 45-bot campaign.

## New generation policy

1. User selects **2–50 bots** in the admin UI. The chosen count remains fixed from smoke through calibration, train and later frozen evaluation. Existing job records are never overwritten when selecting a new count.
2. Each complete consecutive **five valid 2022–2023 trading sessions** yields one cohort evaluation. **All** selected bots must have valid complete windows; missing/invalid results block reproduction instead of manufacturing losses.
3. Rank bots by **net 5-day USD P&L**, then by lower MDD, higher fitness, then deterministic bot ID. **Only the best two** are genetic parents. A single profitable bot does not prevent selecting two gene donors.
4. Even when **no bot** has net P&L greater than `epsilon`, select the **least unprofitable** two bots, record `zeroProfitGeneration=true` and `preservationParents=true`, and create the next generation.
5. Breed exactly the *selected number* of **new mutated crossover children**, resetting account and cumulative trade state; only the parent gene material is inherited. Prior bots are excluded from the active population. Original bot metrics, parent selection, `window_*.json`, `lineage.jsonl` and generated checkpoints are preserved on disk.
6. **Advance to the next chronological trading session.** Never re-open a previously visited five-day window, even if the cohort was entirely unprofitable. No `v083_restarts.jsonl` file will be produced by the new forward-only engine. Any unexpected legacy `restartRequested=true` flag aborts the run as a policy violation.
7. **One pass through the selected calendar**, followed by an incomplete-tail exclusion if applicable. New runs record `evolutionPolicy=V083_FORWARD_ONLY_NO_PROFIT_TOP_TWO`, `noProfitGenerations`, `restarts=0`, `totalProcessedSessions`. Older campaigns retain their historical evidence and metadata rather than being backfilled.
8. This procedure **does not make an unprofitable strategy profitable**. Repeated no-profit windows might still select poor parents; independent 2024 holdout and 2025 development validation are required, under the existing gates. **2026 Final OOS CLOSED** always.

## Source and safety boundary

| File | Responsibility |
|---|---|
| `v083_top_two.py` | Two actual net-P&L gene donors, `zeroProfitGeneration` and `nextWindowPolicy=FORWARD_ONLY`, no rewind flag |
| `run_v07_available.py` | Strict chronological iteration for every version; only `args.v083_mode` enables new V0.8.3 breeding; fail-closed for an unexpected rewind request |
| `run_v083.py` | 2–50-population opt-in CLI, dedicated results and mandatory capacity certificate for full training; obsolete `--max-restarts` argument removed |
| `v083_pacing.py` | Cooperative pauses with existing host-resource guards untouched |
| `calibrate_v083.py` | Three separately measured capacity trials; population and code/config hashes must match full run |
| `v083_web_jobs.py` | Authenticated web controller; new job gets pinned source and separate storage; no unattended retry |
| `v083-web.js` | Clear difference between historical restart-limit failures and new forward-only policy |

**Older versions V0.7, V0.8, V0.8.1 and V0.8.2 stay unchanged in behavior.** The source is shared via a gated V0.8.3 branch, and existing runs use pinned source trees.

## Experiment prerequisites

Before launching a full new campaign, first perform an explicit new smoke test and calibration **against the updated pinned source**. The previously successful 45-bot calibration is historical and **cannot certify changed source code** (the code hash has changed). Verify fresh peak CPU <75%, memory <65%, swap <3%, uninterrupted data windows, and source hashes. Only then manually approve the full 2022–2023 training.

```bash
# Admin GUI: AI -> 연구 -> 봇 개체 수 [2..50] -> 새 실험
# Equivalent authenticated admin console command (NOT a shell/broker command):
v083 new 10
# On completion: manually calibrate, then (if certified) manually train
v083 calibrate
v083 train
```

No new experimental study, smoke run, capacity calibration, cloud instance upgrade or live-market order should occur merely from merging or deploying this code.

## Acceptance tests

- Zero-profit 5-day population selects top two, generates a distinct full population, and **continues forward**.
- Deterministic ten-session synthetic run visits each session **once**, produces exactly two completed generations, no rewind artifacts, preserves checkpoint lineage and chronological dates.
- Invalid/partial evaluation never triggers fallback breeding or calendar replay.
- Unexpected `restartRequested` signal fails closed instead of looping.
- Old V0.8.2 engine integration, source pins and Final OOS gates remain intact.
- New source requires a matching **fresh** measured capacity certificate for full training.

**Research risks:** raw P&L ranking incentivizes risk; generational selection is not statistical profitability proof. Record MDD, risk, and longitudinal generalization separately.
