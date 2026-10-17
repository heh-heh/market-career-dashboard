# V0.8.3 — Two-Parent Restartable Indicator Evolution (Research-Only)

**Status:** implementation on `strategy-engine-v3`; **not deployed to EC2**, not trained, no profitability claim. Existing V0.8.2 campaigns and bot roster remain intact.

## Why

The 2026-10-17 EC2 V0.8.2 full-train attempt was stopped by an intentional **75% host CPU hard guard** after 376 sessions and 51 completed 5-day generations: sampled CPU peak **87.3%**, average **52.57%**, RAM peak **47%** and swap ~**0.2%**. Two subsequent 50-bot pilots hit **87.0%** and **96.1%** peak host CPU. This was not a GitHub Actions failure and was not evidence of an improved trading policy. The 51 completed-generation windows contained 21 with **zero bots net-profitable for their respective 5 sessions**; some other generations did have positive P&L. No annual return or genuine out-of-sample result is established.

The existing checkpoint and outputs are **read-only V0.8.2 historical artifacts**. V0.8.3 never imports or upgrades their populations or capacity certificate.

## Rules

1. 50 actively simulated bots and max **three concurrent portfolio positions per bot**, retaining V0.8.2 indicators, causal observation rules, slippage and accounting.
2. Every **five valid consecutive trading sessions** rank all 50 by **realized net USD P&L**; lower five-day maximum drawdown, higher fitness and bot ID break ties in that order. If the cohort is not 50 complete valid evaluations, **fail closed** rather than treating invalid data as a loss.
3. **Only the two top-ranked bots are gene donors**, even when exactly one of 50 is profitable. Never carry old lifetime P&L, positions, trade state, or elite bots into the next generation. Generate 50 entirely new, positively mutated crossover children, starting each with a new independent daily $100 simulation account. Do not assume diversity or superior performance without testing.
4. Archive `attempt_*`, `window_*.json`, `lineage.jsonl`, prior generation metrics, selection reasons and hashes. Non-selected parent bot objects cease to be active when the in-memory population reference is replaced; disk evidence is **not deleted**.
5. If **none** of the 50 produced more than `epsilon = 1e-6 USD` over the closed 5-day window, still choose top-two by capital preservation, breed children, and **rewind the training cursor to the first permitted 2022–2023 date**. This is an **in-sample replay** of previously seen data, **not a new forward trading period**.
6. Rewinds are **bounded: default 2**, configurable `--max-restarts 0..10`. Once exhausted, the run stops with `V083_RESTART_LIMIT_REACHED`, while preceding day/gen snapshots and last checkpoint remain. No silent unbounded retry.
7. Same historic data rewound with newly selected genes is an overt form of repeated in-sample exposure; do **not** use those results to claim generalization or statistical significance. Fresh untouched holdout validation remains mandatory. Final 2026 OOS **CLOSED**.
8. Optional CPU pacing (`--cpu-pacing-ms`, default 25ms per completed trading minute) reduces attempted CPU duty cycle by voluntarily waiting between safe checks. **It never changes the 75% CPU / 65% RAM / 3% swap sampled upper bounds.** Host CPU includes concurrent workloads, so spikes may still abort. Resource calibration is required before EC2 training.

## Separation and code

- `run_v083.py`: explicit opt-in front end, validates population/config, bounded restarts, source/capacity before training, keeps V0.8.2 runner unchanged.
- `v083_top_two.py`: top-two gene-only parent selection, fresh mutating children, no old live parents.
- `v083_pacing.py`: cooperative pacing **without relaxing** existing `ResourceMonitor` stop conditions.
- `v083_top_two_config.json`: V0.8.3 copy of V0.8.2 indicator/portfolio settings, but `elite_count=0` and `parent_pool_size=2`. Default evolutionary mutation/crossover probabilities are **not** otherwise changed.
- `calibrate_v083.py`: separate warmup + two measured host runs, and separate V083 capacity hash/type checks. Never accepts V082 certificates.
- `run_v07_available.py`: **gated by `args.v083_mode` only** for restart/reproduction; every older version continues its existing one-pass logic. This is a shared-file code change, so unit/CI regression is mandatory.
- `research/test_evolution_v083.py`: determinism, two parents only, no-profit restart, invalid data and hard guard tests.

## Example isolated CLI (for a **reviewed** local machine)

```bash
# Explicit user-driven 50-bot pilot only, using your already audited selection plan:
python research/evolution/run_v083.py --selection-plan /path/to/selection_plan.json \
  --pilot --population 50 --seed 42 --max-restarts 2 \
  --results-dir /separate/research/evolution-v083 --data-origin toss

# Before approving long training, after a pilot, explicitly calibrate current machine:
python research/evolution/calibrate_v083.py --selection-plan /path/to/selection_plan.json \
  --out /separate/research/v083-capacity-UNIQUE --seed 42

# Full 2022–2023 research ONLY after a fresh three-run V083 capacity certificate,
# confirmed host headroom, reviewed source and explicit human approval:
python research/evolution/run_v083.py --selection-plan /path/to/selection_plan.json \
  --from-date 2022-01-01 --to-date 2023-12-31 --population 50 \
  --capacity-report /separate/research/v083-capacity-UNIQUE/capacity.json \
  --max-restarts 2 --seed 42 --results-dir /separate/research/evolution-v083
```

The example assumes local Python dependencies, dataset mounts and audit plan have already been prepared. The **EC2 instance is not certified for V0.8.3 yet**. No AWS restart, capacity upgrade, real broker integration, production deployment, or unattended full training is part of this implementation.

## Open engineering questions

- Should repeated failure over 2 restarts lead to a manual strategy diversity intervention rather than replaying identical historical windows? Under the present rule it **pauses**, by design.
- Should parent selection be based on net P&L alone or risk-adjusted P&L subject to risk caps? The current design follows the requested net P&L ranking with risk tie-break only.
- Should the model use previously finalized 51-generation V0.8.2 checkpoint? **No**: reproducing a different selection policy from that checkpoint invalidates a clean V0.8.3 baseline. New 0.8.3 experiments require a separate initial population.
