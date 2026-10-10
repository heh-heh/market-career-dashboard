# V4 clock/context audit

This instrumentation is research-only. It does not alter thresholds, stop/target rules, hold time, slippage, execution, portfolio sizing, reviewed-manifest requirements, V3, Simple Momentum V1, collectors, or broker-order paths.

## Root cause addressed

The old runner converted every IR3 `IDLE` result at 15:30 into `CLOCK_ELIGIBILITY_CONTEXT_UNAVAILABLE`. An `IDLE` result can also be the expected result of a valid market-direction or volatility filter, so the old aggregate could not be interpreted as data-missing coverage.

The runner now classifies the immediate cause without changing the decision. IR1/IR2 context cancellation also uses atomic reason codes instead of the generic `CONTEXT_FAILED`.

IR3 `CLOCK_DIRECTION_FILTER_FAILED` now has a diagnostic-only sub-breakdown. It records overlapping component failures for the selected index's 20-minute return sign, close-vs-VWAP relation, ER threshold, and the other index being DOWN. Exact intersections are also counted as signatures, with bounded raw-value examples. The parent rejection reason and every strategy threshold remain unchanged.

## Causality

Minute labels are normalized to START semantics. At decision time 15:30 ET, the most recent completed one-minute bar is the bar that starts at 15:29 and ends at 15:30. A 15:30 START bar is not observable until 15:31.

## Short audit before any full rerun

```bash
.venv/bin/python research/audit_v4_clock_context.py \
  --symbols SPY,QQQ,SOXX,AAPL,NVDA \
  --timestamp-kind start \
  --from-date 2022-03-01 \
  --to-date 2022-03-11 \
  --out /tmp/v4_clock_audit.json

cat /tmp/v4_clock_audit.json
```

Run the short audit and a bounded V4 rerun before considering a full 2021-2026 run. Trade count is not a success criterion; causal correctness and reason attribution are.
