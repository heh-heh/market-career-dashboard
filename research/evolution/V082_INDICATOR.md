# V0.8.2 Indicator-Aware Trading Intelligence

Research-only opt-in; this is not a profitability or calibrated-probability claim.
V07/V08/V081 configs, historical results, collectors, broker execution and tokens
are unchanged. 2026 Final OOS remains CLOSED. No EC2 deployment or long training
was performed by implementation tests.

## Architecture and compatibility

`run_v082.py` → existing audited chronological available-data runner → isolated
`indicator_engine.py` → shared `MinuteFeatureProvider` + `IndicatorCache` →
per-genome linear scores → independent multi-position `Portfolio` → existing
fractional `Account` fill/close accounting → original V07 fitness and V081
survival breeding. No broker/network import in this research evaluator.

Defaults are **50 bots**, 5 consecutive valid sessions/generation, daily $100,
max 3 positions, unchanged 2bps slippage + 1bps half-spread per side. No automatic
population growth. A reviewed new configuration/code path is required to raise
50; calibration always measures 50. Existing V081 100+ certificates are rejected.
Old genome JSON/hashes/RNG streams remain valid. New optional 15 indicator weights
are omitted from old JSON, initialized by a separately seeded RNG, crossed and
mutated with existing bounded weight rules. Parents are immutable.

The original V081 CLI/config/capacity/web remain available for reproducibility.
V082 uses new paths, config, protocol and pinned source; it never changes an
in-progress V081 run or reuses its measurement certificate.

## Shared causal indicators

Only completed END-labeled regular-session 1-minute bars; no higher-timeframe or
cross-session indicators in this first version. All calculations reset per session.
A gap ends the usable prefix; there is no interpolation or benchmark substitution.

- SMA20/50/200: arithmetic means of trailing closes.
- EMA9/21/50: alpha=2/(N+1), first close seed; MACD=EMA12−EMA26,
  signal EMA9(MACD), histogram=MACD−signal.
- Session VWAP=sum(typical price × volume)/sum(volume).
- RSI14: Wilder gain/loss mean seeded by 14 close changes. Flat=50, zero loss=100.
- StochRSI14: (RSI−min trailing14 RSI)/(max−min); unsmoothed, flat=0.5.
- ATR14: Wilder TR mean; first TR=high−low. ADX14: Wilder means of directional
  movement, DI, then first14 DX mean followed by Wilder smoothing.
- Bollinger20/2: SMA20 ± 2 population standard deviations.
- Volatility ratio: std(last5 returns)/std(previous30 disjoint returns).
- Volume mean: previous20 completed volumes excluding current; relative volume
  is current/that mean, **not historical same-time-of-day RVOL**.
- Price-volume change: current return × (relative volume−1); dollar volume=close×volume.
- No bid/ask: spread=null and UNAVAILABLE_OHLCV. No invented pressure/depth/ticks.

Require SMA200 and all selected rolling quantities. Zero-volume or zero baseline
volatility cannot establish readiness. Consequently the first 199 completed
minutes are warmup (including half-day limitations); this is explicit, not hidden
as an unavailable benchmark. No 2026 bars are accepted by indicator computation.

Policy inputs are six MA distances / 1%, histogram/ATR, centered RSI and StochRSI,
signed ADX/100, ATR% / 1%, Bollinger position, volatility/volume expansion and
price-volume change / 1%, clipped ±5. No full-sample normalization.

Trend=EMA9>21>50 and close>VWAP (inverse for BEAR), otherwise SIDEWAYS.
Momentum sign requires RSI50 and MACD/signal agreement. Opposite nonzero trend
and momentum is a conflict: WAIT for new entries, HOLD for indicator-driven exit;
hard stop/time/session exits still operate. ADX persistence is a proxy, not a
probability. Missing reference/indicator quantities produce BLOCKED.

## Portfolio risk and observable execution

Admission considers current cash, held count, stop-distance/ATR risk, exact prior
31 closes (30 returns) correlation, market regime, completed-minute dollar volume,
mark-to-market exposure, daily peak drawdown, minimum $5 notional, fractional shares,
and explicit 3x TQQQ/SQQQ/SOXL/SOXS gross exposure. No margin or naked shorts.

- Stop-risk budget: 2% equity less current price-to-stop risk of held lots.
- Proposed risk/share fraction: max(genome stop%, ATR/price) + round-trip cost fraction.
- Notional=min(cash×genome fraction, remaining risk/risk fraction,
  remaining 1x effective-gross-equity budget / instrument leverage).
- Max 3 held lots; drawdown>=3% blocks new entries. It does not magically fill
  all positions at a historical price or force sales just to satisfy a lowered cap.
- Absolute correlation>=0.8 blocks additional correlated long/inverse exposure.
  Missing/flat correlation cannot prove independence and blocks adding that lot.
  Leveraged/inverse shared QQQ/SOXX underlying also blocks concentration.
- Current minute dollar volume below $10k blocks entry. No fake spread measurement.
- BEAR blocks ordinary longs, BULL blocks inverse ETFs. Existing causal SPY/QQQ
  context remains required. SIDEWAYS may allow risk-approved signals.
- At most one new candidate pending; budgets rechecked at execution. The reported
  allowed count is the conservative **candidate-specific** held+admitted count,
  not a promise all future signals fit. Independent candidates can reach 3 over
  successive observations. No forced close when that count later declines.

BUY signal at completed END T → exact next completed-minute CLOSE proxy at T+1m
with adverse costs, never T close or past minute open. Missing/delayed entry
cancels. SELL signals → next observed completed close with adverse costs; gaps
receive recovered price, not theoretical stop. Close-only stop precedes target,
then max hold, sell threshold, cutoff. MFE/MAE use observed closes only.
Unresolved exits or any held observation gap invalidate the daily/window evaluation.

All trades retain entry source candle/features, indicator values/contributions,
exit indicator snapshot/score, reason, timestamps, costs and actual portfolio cash.
Daily metrics contain bounded representative-bot BUY samples (max30), latest
per-symbol analysis (max universe size), and all-bot decision counters. These are
**historical snapshots**, not live recommendations. Full all-bot minute decision
logs are deliberately not duplicated on the 2GB host. Trade provenance covers all bots.

## Evolution, resource protection, artifacts

Original profit candidates / two capital-preservation fallback parents / single
parent mutation / actual changed recovery children are reused without changing
fitness coefficients. Any invalid cohort breaks a 5-session window. No fake zero
results, skipped dates or positive labels. Original ex-post pilot day-completeness
selection bias warnings remain; quality screening is not an executable alpha.

50-bot pilot must complete >=1 valid generation. Calibration runs one warmup plus
**two independent 50-bot measured trials**, retains shared flock descriptors and
never starts full training. CPU<=75%, RAM<=65%, swap<=3%, old hard limits, session
runtime deadline and max(10GiB,10% disk) reserve remain required. A failed sample
stops the whole cohort; no silent shrink or culling. Checkpoint is last committed
5-session generation, not an automatic mid-window resume promise.

Certificates bind V082 version, host+boot, <24h age, seed, config, all evolution
Python hashes, audit plan hash, explicit data-origin label, and three unique completed run file hashes. Old,
mutated, unsafe or stale proof fails closed. One worker at a time through shared
V07 research flock. CPU/thread environment fixed to one BLAS thread at CLI launch.

Default outputs: `/mnt/research/evolution-v082`, controller state
`/mnt/research/v082-web-jobs`, capacity trials under each job; explicit
`--research-mount /mnt/research-extra --require-ebs` supports the extra mount.
No existing results moved. Every run creates a unique output directory.

## Controlled comparisons

Do not compare original100 V081 against new50 V082 and call it indicator alpha.
Use same source hashes, dates, seed, costs, and selection-window masks:

1. V081100 vs V08150 (run existing base runner with unchanged V081 config and
   explicit `--population 50`): population effect only.
2. V08250 `v082_no_indicator_score_config.json` vs normal V08250: indicator
   score contribution under the **same** portfolio, warmup and risk constraints.
3. V08150 vs V08250: bundled portfolio+indicator change, not isolated attribution.

`python research/evolution/compare_v082.py --baseline OLD --candidate NEW --out NEW.json`
checks pair conditions and reports config changes, window return/MDD/fitness,
costs, trades and resource telemetry. No automatic winner or tuning.
Ablation still computes indicators for the same readiness and risk budget, and
keeps the same conflict filter: it isolates learned indicator **score** only.

## Safe operator commands (no automatic deployment)

GitHub Pages contains V082 UI; backend must be installed separately. On EC2 do not
pull/reset/stash the dirty operating repository. Use existing tools checkout:

```bash
TOOLS=/mnt/research/collection-deploy-tools-20261012; git -c safe.directory="$TOOLS" -C "$TOOLS" fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3 && git -c safe.directory="$TOOLS" -C "$TOOLS" show origin/strategy-engine-v3:scripts/install_v082_admin_api_overlay.py > /mnt/research/install-v082-api.py && sudo python3 /mnt/research/install-v082-api.py --check
```

Only after READY and no active research / outside regular session:
`sudo python3 /mnt/research/install-v082-api.py --apply`.
Installer preserves local server changes, backs up files/SHA, blocks active
V07/V08/V081/V082 research, verifies reviewed KillMode=process (never changes it),
and has automatic rollback on API health failure. No collector restart. V081 and
V082 endpoints remain separate. No unattended long training or live orders.

For isolated CLI on the **pinned V082 source** with its existing virtualenv:

```bash
PY=/home/ubuntu/market-career-dashboard/.venv/bin/python
APP=/mnt/research/v082-source-<COMMIT12>
PLAN=/mnt/research/<existing-audit-selection>/selection_plan.json
$PY "$APP/research/evolution/run_v082.py" --selection-plan "$PLAN" --pilot --population 50 --seed 42 --require-ebs --results-dir /mnt/research/evolution-v082
$PY "$APP/research/evolution/calibrate_v082.py" --selection-plan "$PLAN" --out /mnt/research/v082-capacity-UNIQUE --require-ebs --seed 42
```

Do not run simultaneously with existing research. Web AI→研究 has separate V082
buttons for pilot, repeated resource verification, **explicit full-train approval**,
2024 frozen holdout, 2025 development validation, ZIP. Unsupported server protocol
V082_WEB_1 disables all action buttons. 2026 has no button/automatic access.

Explicit full-train command, only after fresh successful certificate and approval:

```bash
$PY "$APP/research/evolution/run_v082.py" --selection-plan "$PLAN" --population 50 --seed 42 --capacity-report /mnt/research/v082-capacity-UNIQUE/capacity.json --from-date 2022-01-01 --to-date 2023-12-31 --require-ebs --results-dir /mnt/research/evolution-v082
```

Actual EC2 50-bot repeated CPU/RAM/swap measurement, real source completeness,
latest deployed protocol, mount and reviewed service lifetime remain runtime checks.
Synthetic local throughput does not certify `t8i.small` safety or profitability.
Synthetic calibration fixtures must explicitly use `--data-origin synthetic`; this
label propagates to all child trials and is checked against the certificate.

## Local validation evidence (synthetic, not EC2)

A real V082 CLI pilot on five synthetic sessions and two tradable symbols plus
SPY/QQQ/SOXX shared references completed one 50-bot generation, selected all five
days, and wrote isolated artifacts. Wall time ~4.54s; sampled RSS peak ~89.25MiB,
host CPU peak 41.6%, host RAM peak4.1%, swap0. This much larger Codex machine is
not t8i.small; those percentages must not be used as a capacity certificate.

A tiny one-session warmup + two measured repeats (same seed/data/cost assumptions)
initially measured median process CPU seconds: V081100 ~0.388, V08150 ~0.313,
V08250 no-score ~0.510, V08250 ~0.602. Indicator/portfolio work adds overhead:
**there is no demonstrated total CPU improvement versus V081**. Absolute times,
trade activity and startup warmup differ; this is a throughput smoke check only.
Use the mandatory three-trial EC2 measurement before approving full research.
