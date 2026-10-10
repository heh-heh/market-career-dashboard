# Final integration / forward paper execution audit

Local verdict: PASS with runtime gates. No deployment, live orders, historical
simulation or parameter tuning performed. Production candle semantics remain
unverified; Simple entries are disabled until explicitly confirmed.

## Integration

Merge --no-ff (not rebase) origin/strategy-engine-v3 at
2bf3c79e1c6e3c1044f9df6afa69cdb7517f85d5 into codex-regime-v3 based on d347249.
Merge commit:6f468e3. Resolved only server.py/admin.html conflicts by preserving
both V3 and Simple sections, APIs and startup paths.

- paper_trader.py, live_trader.py and strategy_engine.py match that remote HEAD.
- V4 specification/backtester match pre-merge d347249.
- V3 and Simple have separate enabled state, cash/equity, positions, histories,
  state files and authenticated /api/trading/paper/v3 vs /simple-v1 endpoints.
- Equal resolved storage paths fail before Simple initialization. No LIVE ARM.
- Real PaperV3Trader synthetic API lifecycle/restart tested, without live API.
- Original unrelated work branch/files untouched. No force push or push performed.

## Findings and fixes

| Severity | Location | Finding / minimal correction | Bias before correction |
|---|---|---|---|
| HIGH | simple_momentum_v1.py::_enter/process_snapshot | Delayed scans bought a past minute open. Require a subsequent scan's fresh current observation; cancel beyond90s. Persist source/receipt/decision/latency and fill model. | Unknown; favorable retrospective fills possible |
| HIGH | simple_momentum_v1.py::_manage/_exit | Historical stop/open/candle replay could imply execution at a price already gone. Execute only current observed price minus slippage; worse gap fill; hard stop first. | Upward on adverse stop gaps/rebounds |
| HIGH | simple_momentum_v1.py::_manage | Outages could conceal delayed liquidation. Persist DATA_STALE, staleSince/count; never invent fill. Recovery retains affected flag, uses fresh price, no retrospective missed stop. | Unknown |
| HIGH | Config/normalize_candles/observation | Live start/end semantics unresolved. Default runtime confirmation false blocks entry; aware source timestamps mandatory. Completed OHLC cannot be fills. | Unknown; potentially early signal/fill |
| MEDIUM | server.py::simple_paper | Configuring same V3/Simple directory risks account collision. Reject identical resolved directories. | Unknown/account corruption |
| PASS | admin.html/server.py | Both paper sections/APIs/startup paths coexist; existing authentication and live control flow retained. | None introduced |
| PASS | read-only Toss transport | Hard GET-only allowlist includes prices; order/account/arbitrary paths rejected, no broker submit imports. | None introduced |

Current quotes require finite positive lastPrice and explicit source time <=15s
old, never future, with independently stored receipt time. Candle fallback is
only current FORMING closePrice; cannot use a completed close or any old open.
It requires confirmed semantics and warns that intrabar last-trade age is
unverified. Cached/replayed receipt cannot recover a stale position or execute.

## Recorded semantics / limits

Entry:completed trigger -> pending -> first valid subsequent observation plus
configured slippage (default2bps); max decision latency90s from trigger completion.
Exit:fresh current price minus2bps; stop levels are conditions, never fill prices.
Scheduled deadline latency is measured from cutoff/time stop. Price-condition
trigger time is actual detection, not a guessed missed tick. Receipt, quote source
and engine decision timestamps remain separate.

Trail high/MFE/MAE are sampled post-entry observations. The engine deliberately
cannot reconstruct unobserved intrabar ordering; short-lived stop/activation/high
moves between60s scans may be missed. Marked excursionMethod and executionModel
make this limitation auditable. Outage-affected trades remain separately flagged.

Old trade logs remain unchanged. Legacy pending signals are cancelled; legacy
open positions are preserved and flagged with old entry semantics while future
management uses current observations. No paper balances are reset during migration.

## Lightweight verification

- Python compilation:Simple, server, unchanged V3 runner and relevant tests PASS.
- unittest discover -s research -p 'test*.py':104 tests PASS (V4/manifest +
  Simple signal/execution/data safety + real local HTTP paper API integration).
- Synthetic cases include independent ON/OFF/storage/history, V3 trade/restart,
  no historical-open fill, first current price, >90s cancellation, worse stop gap,
  no outage fill, recovery/restart stale flags, cached/future/stale rejection,
  forming close fallback, start/exclusive-end/naive timestamps and no live orders.
- node --check extracted admin script and smoke harness PASS.
- node research/test_admin_paper_smoke.cjs PASS:both sections, independent controls,
  data-stale/timestamp rendering, dedicated endpoints, no live endpoint requests.
- git diff --check PASS; remote V3/live files and original V4 files verified.

## Remaining EC2 runtime checks

1. Verify real candles' source offsets and START vs exclusive END labels; only
   then set SIMPLE_TIMESTAMP_KIND and SIMPLE_TIMESTAMP_KIND_CONFIRMED=true.
2. Verify actual prices response/source timestamp semantics and clock freshness.
   Inspect fallback warnings; a forming label alone cannot certify last-trade age.
3. Confirm valid Toss US calendar, shared OAuth/rate-limit capacity, observed scan
   cadence/entry cancellation rate alongside collector; do not restart collector.
4. Verify distinct writable account storage and restore persisted settings safely.
   An outage can delay exit beyond cutoff; never treat late liquidation as on-time.
5. Confirm deployed UI/authorized API parity. Keep live trading flags unchanged.

Push readiness is relative to fetched remote HEAD:it is an ancestor of the merged
branch, so integration needs no force. No guarantee about subsequent remote
advances; fetch/recheck before any later authorized push.
