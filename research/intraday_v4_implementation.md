# IR V4 implementation and runtime review

IR1/IR2/IR3 implement `IR_SPEC_V1`, not the older frozen S1/S2/S3 backtester.
Rules/defaults are unchanged. No historical results or profitability assertion.

## Timing and conservative interpretations

- Toss labels are START, as instructed: 09:30 completes at 09:31. Five exact
  consecutive minutes 09:30..09:34 are available at 09:35. Gaps never resample
  across missing minutes. No 15-minute indicator is required.
- Reference minute endpoints must match the decision endpoint exactly. There
  is no nearest/as-of substitution or future fill. Missing context blocks entry,
  but does not block a position's own price-based exit.
- ATR is SMA of 14 completed 5-minute TR, prior RTH warmup permitted, excluding
  the overnight gap on each first RTH bar. `A` freezes from the latest 5-minute
  ATR strictly before SETUP; for a SETUP at a five-minute close, use its preceding
  five-minute bar. IR3 at 15:30 uses ATR available at 15:25.
- IR1 uses three completed impulse bars and the preceding six highs. The first
  qualifying pullback freezes its last five-minute high +0.10A and cumulative
  pullback low. Only later one-minute closes can trigger. Cancellation wins.
- IR2 freezes exact 09:30..09:44 OR15 at 09:45. Earliest SETUP is 10:00;
  breakdown must be a later five-minute completion. Reclaim bars are physical
  five-minute intervals 1 and 2 after breakdown; breakdown itself is excluded.
  Any later one-minute low below the frozen failure low cancels, equality allowed.
  A first reclaim without RECOVERY may wait for the second, as specified.
- IR3 opening return is log(09:59 close /09:30 open), known at 10:00. Sample
  standard deviation is prior60 sessions/min40, not today's return. RVOL uses
  prior20 opening-volume intervals/min10. Exactly once at 15:30 use information
  through the 15:29 START candle. The 15:30 START candle is not known until 15:31.
  Early-close sessions do not run IR3.
- Historical execution is the strict next completed-minute **close**, ordinarily
  T+60s. Never the signal/next candle open. A missing next minute cancels pending
  entry, even if a later observation falls within 90s. Price/reference/risk guards
  are rechecked at entry. IR1/IR2 keep their own entry windows; IR3 deadline is 90s.
- Costs: configurable slippage 2bps/side baseline plus1bp/side half-spread proxy.
  Stored CSV bid/ask fields are not treated as certified live quote observations.
  Fees0 are explicitly unverified. No economic PASS is issued.
- Exits check observable closes, hard stop then existing trail then target then
  deadlines then stall. Stops are conditions, not guaranteed fill prices. Gap
  exits use the worse observed price. IR1 trail updates AFTER the stop check.
  OHLC touch counts/ambiguous intrabar counts are diagnostics, never ideal fills.
- Deadline exits use the first actual observation at/after cutoff. Missing prices
  mark DATA_STALE, preserving cash; recovery uses current price and records the
  affected flag. A failed session liquidation is retained, including next-session
  recovery or an UNRESOLVED record; it is never erased or assigned a fake exit.

No strategy is SPEC_BLOCKED. The historical close proxy is a model, not evidence
of executable REST quotes. Actual receipt/source/latency fields stay null;
logical observation timestamps and modeled delays are recorded separately.

## Universe, capacity and diagnostics

Seed classification comes from the named, reviewed instrument groups in the
spec/universe. Only common stocks enter IR1/IR2; only SPY/QQQ enter IR3.
TQQQ/SQQQ/SOXL/SOXS are excluded. Semiconductor IR1 requires SOXX, never a silent
SMH substitute. IR2 does not require sector observations. References are loaded
automatically and cannot become unintended entries when `--symbols` restricts
the entry universe. No instrument metadata/news is fabricated.

All eligibility/price-adjustment attestations remain externally reviewed in the
manifest. The current seed is not survivorship-free historical membership.
Daily bars are reconstructed only from complete prior XNYS sessions; missing
prior sessions block the 61-session eligibility window (conservative handling).
Intraday prefix gaps make VWAP/RVOL unavailable, not neutral. Past60/20 reference
queues advance only after the session, with missing observations retained.

The cheap top15 queue and Borda RVOL/headroom rank follow the specification and
have no fitted expected-return interpretation. Priority freezes at trigger.
Separate $10,000/100USD books, one position/strategy, no shared cash with V3 or
Simple. First SETUP consumes the symbol×strategy×date opportunity. CANCELLED/EXIT
are terminal. Same-time conflicts use frozen score/ADV/symbol, IR3 uses Z/symbol
(all historical spreads unknown). Capacity failures are retained.

Results include return/R/USD statistics, chronological tied-exit drawdown,
sampled MFE/MAE/time-to-extremes, strategy/symbol/year/month/session/regime/exit
breakdowns and unresolved exposure. Sharpe-like consistency includes zero-trade
sessions. Frozen 12/3/3 rolling descriptive folds include a pre-OOS session
embargo; fewer than3 complete OOS folds is NOT_EVALUABLE. No optimization,
robustness classification, control-ablation or bootstrap is run automatically.
Those later research experiments remain separate work.

IR1 stock/IR3 index universes have no same-symbol duplicate signals. Their
trade-day co-occurrence is reported as such, not simultaneous signal overlap.

## Runtime audit and manual execution

This Codex workspace has no `data/toss_1m/`; **actual EC2 audit not performed**.
Tests use only small labelled synthetic fixtures. No data was downloaded.

Runtime requires the existing reviewed manifest. It verifies SHA256, START
semantics, timezone, source identity, price/volume split consistency and
point-in-time eligibility. The validator's attestations must reflect real review;
do not set them merely to make a job start. This implementation does not change
or bypass `build_v4_data_manifest.py` or the old V4 manifest contract.

The IR run reuses `inspect_file` to publish per-symbol row/timestamp/UTC-offset,
duplicate/OHLC/negative-volume/nonpositive-price counts, XNYS interval completeness
and session coverage. Conflicts/invalid values/disorder/hash changes fail closed.
Missing minutes are diagnostic and block affected features/entries as specified.
Hash checks before/after the streaming run reject collector re-exports without
stopping/modifying the collector. SQLite staging is not an input; CSV/CSV.GZ only.

```bash
python research/backtest_intraday_v4.py --strategy ir3 \
  --data-dir data/toss_1m --manifest research/v4_data_manifest.json \
  --out /var/lib/market-career-dashboard/backtest_v4_ir3_result.json \
  --state /var/lib/market-career-dashboard/backtest_v4_ir3_state.json \
  --trades-csv /var/lib/market-career-dashboard/backtest_v4_ir3_trades.csv \
  --log /var/lib/market-career-dashboard/backtest_v4_ir3.log \
  --data-audit /var/lib/market-career-dashboard/backtest_v4_ir3_data_audit.json \
  --decisions /var/lib/market-career-dashboard/backtest_v4_ir3_decisions.jsonl
```

For a bounded sanity evaluation add `--symbols QQQ --from-date YYYY-MM-DD
--to-date YYYY-MM-DD`. Earlier history is read solely for causal warmup. A true
tiny synthetic smoke (70 rows across two symbols) is in the tests, not presented
as real market performance.

## API/UI compatibility

The integration base is current remote `ddc9c81`. Its authenticated generic
registry, dedicated backtest tab and `btEngineSelect` are reused, including all
newer remote UI/API work. Four IR engines extend the existing selected-engine
model; no second API or duplicate UI sections are introduced.
Existing Simple endpoints are retained; old Simple/V3 research scripts and both
forward runners are byte-unchanged. Only their orchestration/display is extended.

Engine IDs: `simple-v1`, `v3`, `v4-ir1`, `v4-ir2`, `v4-ir3`, `v4-all`.
GET `/api/backtest/engines`, GET `/api/backtest/status?engine=...`, POST
`/api/backtest/start` with ONLY `{"engine":"v4-ir3"}`, GET
`/api/backtest/download?engine=...` all require existing admin authentication.
No arbitrary command/argument API. Lock plus PID/manual-process detection prevents
concurrent API historical jobs. Starting via the legacy Simple endpoint also
uses this guard. External independent manual launches cannot be controlled by an
HTTP lock; operators must not start a simultaneous unmanaged job.

Paths default to `/var/lib/market-career-dashboard/backtest_v4_{ir1,ir2,ir3,all}_*`.
`BACKTEST_DATA_DIR` can relocate managed files; `V4_DATA_MANIFEST` can select an
already reviewed manifest. ZIP includes this engine's result/trades/state/log/
audit and decision file, no secrets/environment/auth files. State writes are
atomic; process death displays INTERRUPTED. There is no partial-session restart
replay: restart starts a fresh historical run. Completed results remain readable
after service restart. Progress counts real audited files/evaluated sessions.
IR run IDs bind state/audit/result; a failed new run cannot display or download
an older run's statistics as its own.

After deployment, use the existing tab to run one engine at a time. No automatic
baseline, live orders, ARM changes, service restart or historical optimization is
part of this change. Remaining runtime work: real data manifest/audit, metadata/
corporate-action evidence, fee bounds, complete chronological evaluation and
independent forward execution confirmation.
