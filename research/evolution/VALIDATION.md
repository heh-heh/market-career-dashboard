# V0 framework validation — 2026-10-10 UTC

Environment: Python 3.12, NumPy 2.5, exchange_calendars 4.13, CPU only.
No EC2 connection or actual Toss minute dataset was available. No historical
download, full training, parameter search, live order or service restart occurred.

## Checks performed

- `python -m py_compile research/evolution/*.py research/test_evolution_v0.py research/test_simple_momentum_api.py server.py`: PASS.
- `python research/evolution/run_evolution_v0.py --help`: PASS.
- `python -m unittest discover -s research -p 'test_*.py'`: **313 tests PASS**, 20.896 seconds
  (281 existing + 32 Evolution tests). Existing tests include V4, Simple/V3 API,
  paper execution, collectors and manifest validation.
- Five offline JavaScript checks PASS:
  `test_evolution_monitor_smoke.cjs`, `test_admin_backtest_smoke.cjs`,
  `test_admin_paper_smoke.cjs`, `test_data_collection_smoke.cjs`,
  `test_v4_frequency_monitor_smoke.cjs`.
- `git diff --check`: PASS.

The before-change suite exposed a wall-clock-dependent existing V3 test: its
synthetic REGULAR snapshot did not override the runner's real NY entry cutoff,
so a run after 15:50 had no expected position. Only the test clock was fixed to
11:00 ET. V3/Simple implementation, authentication and entry/exit rules are unchanged.

## Explicitly synthetic CLI smoke

Fixture: five full XNYS sessions, 2024-06-03 through 2024-06-07, TQQQ/AAPL
candidates with SPY/QQQ/SOXX references, 9,750 END-labeled rows. The tiny fixture
generator is in `research/test_evolution_v0.py`, not a historical data collector.
Inputs and generated artifacts were stored under `/tmp`, outside the repository.

```
python research/evolution/run_evolution_v0.py \
  --data-dir /tmp/evolution-v0-smoke-data --symbols TQQQ,AAPL \
  --timestamp-kind end --population 20 --initial-capital 100 --seed 42 \
  --from-date 2024-06-03 --to-date 2024-06-07 \
  --results-dir /tmp/evolution-v0-smoke-results \
  --provisional --smoke --data-origin synthetic
```

Result: completed, five evaluated generations (0–4), 20 bots maintained every day,
406 simulated completed trades, 1 DEAD / 18 NEUTRAL / 81 SURVIVE evaluations;
371 changed mutation genes and 33 crossover offspring. Each daily evaluation had
fresh $100 capital. Realized/unrealized/cost accounting, lineage, generation JSONs,
CSV provenance and unchanged input hashes were verified.

Latest replay + artifact-write runtime: **1.392 seconds**. Timer starts after input
preflight, so this excludes the mechanical audit/hash scan. It is a local small
fixture measurement, **not** a full-universe or EC2 performance estimate.

Separate integration tests use 2 train / 1 validation / 2 OOS fixture days and
verify that financial/lineage artifacts reproduce with seed 42 and the frozen
population never mutates in validation/OOS. Source mutation rejects finalization.
All-negative populations recover via fresh random genomes, not deceased parents.
Missing exit observations become INVALID, not fabricated profitable survivors.

Synthetic rising/repeated days have no evidentiary value for trading performance.
Every smoke result is marked `synthetic`, `PROVISIONAL_UNREVIEWED_DATA` and
`NOT_VALID_FOR_FINAL_PERFORMANCE_CLAIMS`. Source/code hashes, pre-commit Git dirty
state, config and runtime versions were recorded; wall-clock metadata/run IDs differ.

## Still awaiting real data validation

Run the bounded EC2 command in README against finalized read-only exports, using
the genuine reviewed END-label manifest, or explicit mechanical-only provisional
mode. Validate real timestamp/adjustment provenance, data gaps and execution fees
before longer training. Independent final OOS remains required. Read-only monitor
code is prepared; backend/Pages deployment has not been performed.
