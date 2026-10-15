"""Opt-in V07-R1 context and post-trade objectives; never reads future observations."""
import math
import statistics

MODE = "v07_regime_timing"
REGIMES = ("BULL", "BEAR", "SIDEWAYS")
CONTEXT_FEATURES = ("market_bull", "market_bear", "market_return_30m",
                    "market_vwap_distance", "market_volume_ratio", "market_efficiency")


def market_context(spy, qqq, config):
    """Both exact-time completed references are required. Rebounds remain SIDEWAYS.

    Direction requires agreement across 5/15/30m returns AND VWAP; the 30m
    return must exceed one realized 30m noise unit. No daily final values.
    """
    if spy is None or qqq is None:
        return dict(regime="SIDEWAYS", valid=False, reason="MARKET_CONTEXT_MISSING")
    refs = (spy, qqq)
    noise = [max(config.regime_return_floor,
                 config.regime_noise_multiplier*x["volatility_30m"]*math.sqrt(30)) for x in refs]
    def direction(sign):
        return all(sign*x["return_30m"] >= n
                   and all(sign*x[f"return_{h}m"] > 0 for h in (5, 15))
                   and sign*x["distance_from_vwap"] > 0
                   and sign*x["vwap_slope_5m"] > 0 for x, n in zip(refs, noise))
    regime = "BULL" if direction(1) else "BEAR" if direction(-1) else "SIDEWAYS"
    values = (float(regime == "BULL"), float(regime == "BEAR"),
              statistics.mean(x["return_30m"] for x in refs)/config.return_scale,
              statistics.mean(x["distance_from_vwap"] for x in refs)/config.return_scale,
              statistics.mean(x["volume_ratio"] for x in refs)-1,
              statistics.mean(x["efficiency_30m"] for x in refs))
    return dict(regime=regime, valid=True, reason="COMPLETED_SPY_QQQ_AGREEMENT",
                policyValues=tuple(max(-config.feature_clip, min(config.feature_clip, v)) for v in values))


def trade_quality(trade, config):
    """Completed CLOSE observations only, measured AFTER sale, never policy inputs.

    Do not charge the terminal loss again: entry recovery and exit giveback
    penalties apply only to profitable trades, above a volatility+cost allowance.
    Losing-trade quality remains diagnostic. Late entry is a diagnostic, not an
    extra penalty duplicating adverse excursion.
    """
    entry = trade["entryFillPrice"]
    allowance = max(config.timing_noise_floor,
                    trade.get("entryVolatility15m", 0)*math.sqrt(config.entry_quality_minutes))
    allowance += 2*(config.slippage_bps+config.half_spread_bps)/10000
    early = max(0, (entry-trade.get("earlyLowestObservedPrice", entry))/entry)
    giveback = max(0, trade["mfePct"]/100-max(0, trade["returnPct"]/100))
    profitable = trade["pnlUsd"] > config.epsilon
    return dict(earlyAdverse=early, profitGiveback=giveback, noiseAllowance=allowance,
                recoveredAdverseExcess=max(0, early-allowance) if profitable else 0,
                profitableGivebackExcess=max(0, giveback-allowance) if profitable else 0,
                lateChase=trade.get("entryVwapDistance", 0) > allowance,
                entryRegime=trade.get("entryRegime", "SIDEWAYS"))


def day_diagnostics(account, config):
    regimes = {r: dict(trades=0, pnlUsd=0.0, costUsd=0.0) for r in REGIMES}
    qualities = []
    for trade in account.trades:
        quality = trade_quality(trade, config)
        trade["timingQuality"] = quality
        qualities.append(quality)
        bucket = regimes[quality["entryRegime"]]
        bucket["trades"] += 1
        bucket["pnlUsd"] += trade["pnlUsd"]
        bucket["costUsd"] += trade["costUsd"]
    return dict(regimePerformance=regimes,
                timing=dict(trades=len(qualities),
                    recoveredAdverseExcess=sum(q["recoveredAdverseExcess"] for q in qualities),
                    profitableGivebackExcess=sum(q["profitableGivebackExcess"] for q in qualities),
                    earlyAdverse=sum(q["earlyAdverse"] for q in qualities),
                    profitGiveback=sum(q["profitGiveback"] for q in qualities),
                    lateChaseCount=sum(q["lateChase"] for q in qualities)),
                equityPath=[x[1] for x in account.research_marks],
                dataComplete=account.research_data_complete)


def window_objective(rows, config):
    regimes = {r: dict(trades=0, pnlUsd=0.0, costUsd=0.0) for r in REGIMES}
    timing = dict(trades=0, recoveredAdverseExcess=0., profitableGivebackExcess=0.,
                  earlyAdverse=0., profitGiveback=0., lateChaseCount=0)
    offset = 0.
    peak = config.initial_capital
    mdd = 0.
    for row in rows:
        for equity in row.get("equityPath", [config.initial_capital, config.initial_capital+row["dailyPnl"]]):
            stitched = offset+equity
            peak = max(peak, stitched)
            mdd = max(mdd, (peak-stitched)/peak)
        offset += row["dailyPnl"]
        for regime in REGIMES:
            for key in regimes[regime]:
                regimes[regime][key] += row["regimePerformance"][regime][key]
        for key in timing:
            timing[key] += row["timing"][key]
    returns = [r["dailyReturn"] for r in rows]
    ntrades = max(1, timing["trades"])
    covered = all(regimes[r]["trades"] >= config.min_regime_trades for r in ("BULL", "BEAR"))
    # Penalize unequal positive regime contributions, not losses already in net PnL.
    concentration = (abs(max(0, regimes["BULL"]["pnlUsd"])-max(0, regimes["BEAR"]["pnlUsd"]))/config.initial_capital
                     if covered else 0.)
    components = dict(windowReturn=sum(returns),
        drawdownPenalty=config.drawdown_penalty*mdd,
        costPenalty=0.,  # costs are already deducted exactly once in windowReturn
        tradingPenalty=config.excessive_trading_penalty*max(0., timing["trades"]/len(rows)-config.free_trade_count),
        consistencyPenalty=config.return_volatility_penalty*statistics.pstdev(returns),
        entryTimingPenalty=config.entry_adverse_penalty*timing["recoveredAdverseExcess"]/ntrades,
        exitTimingPenalty=config.exit_giveback_penalty*timing["profitableGivebackExcess"]/ntrades,
        regimeConcentrationPenalty=config.regime_concentration_penalty*concentration)
    fitness = components["windowReturn"]-sum(v for k, v in components.items() if k != "windowReturn")
    for r in REGIMES:
        regimes[r]["sampleAdequate"] = regimes[r]["trades"] >= config.min_regime_trades
    return dict(fitness=fitness, fitnessComponents=components, windowMaxDrawdown=mdd,
                winRate=sum((r.get("winRate") or 0)*r["trades"] for r in rows)/timing["trades"] if timing["trades"] else None,
                averageHoldMinutes=sum((r.get("averageHoldMinutes") or 0)*r["trades"] for r in rows)/timing["trades"] if timing["trades"] else None,
                regimePerformance=regimes, timing=timing, regimeEvidence="ADEQUATE" if covered else "INSUFFICIENT_REGIME_SAMPLES")


def public_evaluation(row):
    return {k: v for k, v in row.items() if k != "equityPath"}
