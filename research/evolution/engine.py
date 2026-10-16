"""Population replay. Futures in the precomputed matrix are never indexed early."""
from datetime import datetime
from collections import Counter
import numpy as np

from .account import Account, ExecutionCostModel
from .features import MinuteFeatureProvider, POLICY_BASE, benchmark_for_symbol


def evaluate_day(bots, rows, opening, closing, symbols, config, *, data_policy="strict"):
    if data_policy not in ("strict", "causal_available"):
        raise ValueError("Unknown data policy")
    from research.backtest_v4_strategies import MINUTE
    from .regime_timing import MODE, CONTEXT_FEATURES
    candidate = config.fitness_mode == MODE
    if data_policy == "causal_available" and not candidate:
        raise ValueError("causal_available is isolated to V07-R1 research fitness")
    provider = MinuteFeatureProvider(config).prepare(rows, opening, closing, symbols)
    accounts = [Account(config.initial_capital, ExecutionCostModel(config.slippage_bps, config.half_spread_bps)) for _ in bots]
    if candidate:
        complete = all(provider.duration == len(provider.bars.get(s, {})) for s in rows) and all(
            s in rows for s in set(symbols) | {"SPY", "QQQ", "SOXX"})
        if data_policy == "causal_available":
            # Quality verdict only: never used as a future symbol-selection mask.
            complete = all(provider.duration == len(provider.bars.get(s, {})) for s in ("SPY", "QQQ", "SOXX"))
        for account in accounts:
            account.research_timing = True
            account.research_data_complete = complete
    context_weights = np.array([b.genome.context_weights or (0.,)*len(CONTEXT_FEATURES) for b in bots])
    weights = np.array([b.genome.weights for b in bots])
    symbol_index = {s: i for i, s in enumerate(provider.symbols)}
    cutoff = closing-config.cutoff_minutes*MINUTE
    decisions = Counter()
    for ti, t in enumerate(provider.times):
        # Shared market computation once per minute, not once per bot/file.
        base_scores = provider.matrix[ti] @ weights[:, :len(POLICY_BASE)].T
        if candidate and provider.market[t]["valid"]:
            base_scores = base_scores + np.asarray(provider.market[t]["policyValues"]) @ context_weights.T
        for bi, (bot, account) in enumerate(zip(bots, accounts)):
            filled = False
            pending = account.pending
            if pending and t > datetime.fromisoformat(pending["signalTimestamp"]):
                bar = provider.bars.get(pending["symbol"], {}).get(t)
                signal_t = datetime.fromisoformat(pending["signalTimestamp"])
                if pending["side"] == "BUY" and (t != signal_t+MINUTE or bar is None or t >= cutoff):
                    account.cancellations["MISSING_OR_LATE_ENTRY_OBSERVATION"] = account.cancellations.get("MISSING_OR_LATE_ENTRY_OBSERVATION", 0)+1
                    account.pending = None
                elif bar:
                    if pending["side"] == "BUY":
                        if candidate:
                            observation = provider.raw.get((pending["symbol"], t), {})
                            pending = dict(pending, entryRegime=provider.market[t]["regime"],
                                entryContextTimestamp=t.isoformat(),
                                entryVolatility15m=observation.get("volatility_15m", 0.),
                                entryVwapDistance=observation.get("distance_from_vwap", 0.),
                                entrySourceCandle=dict(start=bar.start.isoformat(), end=bar.end.isoformat(), close=bar.c),
                                entryMarketContext={r: {k: provider.raw.get((r,t),{}).get(k) for k in
                                    ("return_5m", "return_15m", "return_30m", "volatility_30m", "distance_from_vwap", "vwap_slope_5m", "volume_ratio")}
                                    for r in ("SPY", "QQQ")})
                        account.buy(pending["symbol"], bar.c, t, bot.genome.position_fraction, pending, bot.genome)
                    else:
                        account.sell(bar.c, t, pending["reason"], pending["signalTimestamp"])
                    account.pending = None
                    filled = True
                else:
                    account.cancellations["STALE_EXIT_OBSERVATION"] = account.cancellations.get("STALE_EXIT_OBSERVATION", 0)+1
            if account.position:
                symbol = account.position["symbol"]
                bar = provider.bars.get(symbol, {}).get(t)
                if bar is None:
                    if data_policy == "causal_available":
                        account.research_data_complete = False  # sticky even after recovery/exit
                        account.cancellations["HELD_SYMBOL_OBSERVATION_GAP"] = account.cancellations.get("HELD_SYMBOL_OBSERVATION_GAP", 0)+1
                    continue  # no stale/fabricated execution or price-forward-fill
                account.mark(bar.c, t)
                if account.pending:
                    continue
                p = account.position
                held = (t-datetime.fromisoformat(p["entryTimestamp"])).total_seconds()/60
                si = symbol_index[symbol]
                score = float(base_scores[si, bi] + np.dot(account.state_features(t, provider.duration), weights[bi, len(POLICY_BASE):]))
                # Close observations only, adverse cost; stop first, no candle-high/low fills.
                reason = ("STOP_LOSS" if bar.c <= p["stopPrice"] else
                          "TAKE_PROFIT" if bar.c >= p["targetPrice"] else
                          "MAX_HOLD" if held >= bot.genome.max_hold_minutes else
                          "SELL_THRESHOLD" if provider.valid[ti, si] and score <= bot.genome.sell_threshold else
                          "SESSION_EXIT" if t >= cutoff-MINUTE else None)
                if reason:
                    account.pending = dict(side="SELL", symbol=symbol, signalTimestamp=t.isoformat(), reason=reason)
                    decisions[reason] += 1
            elif not account.pending and not filled and t < cutoff-MINUTE:
                # V0.5 structural anti-overtrading guard. These are execution
                # constraints, not fitted signals: once the daily cap is reached,
                # or the post-exit cooldown is active, no new BUY can be emitted.
                if config.max_trades_per_session and len(account.trades) >= config.max_trades_per_session:
                    continue
                if config.entry_cooldown_minutes and account.trades:
                    last_exit = datetime.fromisoformat(account.trades[-1]["exitTimestamp"])
                    if (t-last_exit).total_seconds() < config.entry_cooldown_minutes*60:
                        continue
                valid = provider.valid[ti]
                if not np.any(valid):
                    continue
                state_score = float(np.dot(account.state_features(t, provider.duration), weights[bi, len(POLICY_BASE):]))
                scores = np.where(valid, base_scores[:, bi]+state_score, -np.inf)
                si = int(np.argmax(scores))  # stable sorted-symbol tie break
                if scores[si] >= bot.genome.buy_threshold:
                    symbol = provider.symbols[si]
                    bar = provider.bars[symbol][t]
                    account.pending = dict(side="BUY", symbol=symbol, signalTimestamp=t.isoformat(), score=float(scores[si]),
                                           sourceCandle=dict(start=bar.start.isoformat(), end=bar.end.isoformat(),
                                                             open=bar.o, high=bar.h, low=bar.l, close=bar.c, volume=bar.v),
                                           features=dict(provider.raw[symbol, t]), benchmark=benchmark_for_symbol(symbol)[0])
                    if candidate:
                        account.pending.update(signalRegime=provider.market[t]["regime"],
                                               entryQualityMinutes=config.entry_quality_minutes)
                    decisions["BUY"] += 1
    return accounts, dict(provider.diagnostics, dataPolicy=data_policy, decisions=dict(decisions), executionModel="NEXT_COMPLETED_MINUTE_CLOSE_PROXY",
                          observationExtrema="Completed close marks only; high/low never used for trade exits or MFE/MAE")
