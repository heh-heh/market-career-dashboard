"""Daily survival, lineage and selection; validation/OOS never call reproduction."""
from dataclasses import dataclass, field
import math

from .genome import random_genome, crossover, mutate


@dataclass
class Bot:
    bot_id: str
    genome: object
    generation: int
    parent_ids: list
    birth_date: str
    mutation_count: int = 0
    death_date: str | None = None
    lifetime_days: int = 0
    lifetime_trades: int = 0
    lifetime_equity: float = 100
    lifetime_initial_capital: float = 100
    origin: str = "RANDOM"
    # Financial lifetime is inherited along one ancestor, never pooled from two.
    lifetime_pnl: float = 0

    def record(self):
        return {k: v for k, v in vars(self).items() if k != "genome"} | {
            "genome": self.genome.to_dict(), "genome_hash": self.genome.digest,
            "lifetime_return": self.lifetime_pnl/self.lifetime_initial_capital}


def initial_population(config, rng, birth_date):
    return [Bot(f"g0000-b{i:04d}", random_genome(rng, config), 0, [], birth_date,
                lifetime_equity=config.initial_capital, lifetime_initial_capital=config.initial_capital)
            for i in range(config.population_size)]


def daily_evaluation(bot, account, day, config, *, update_lifetime=True, mark_death=True):
    # A missed session exit cannot fabricate liquidation or become a profitable parent.
    valid = account.position is None
    pnl = account.cash-account.initial_capital if valid else None
    survival = ("SURVIVE" if pnl > config.epsilon else "DEAD" if pnl < -config.epsilon else "NEUTRAL") if valid else "INVALID"
    ret = pnl/config.initial_capital if valid else None
    fitness = (ret-config.drawdown_penalty*account.max_drawdown
               -config.transaction_cost_penalty*account.transaction_cost/config.initial_capital
               -config.excessive_trading_penalty*max(0, len(account.trades)-config.free_trade_count)) if valid else None
    if update_lifetime:
        bot.lifetime_days += 1
        bot.lifetime_trades += len(account.trades)
        if valid:
            bot.lifetime_pnl += pnl
            bot.lifetime_equity = bot.lifetime_initial_capital+bot.lifetime_pnl
        if mark_death and survival == "DEAD":
            bot.death_date = day
    return dict(botId=bot.bot_id, date=day, survival=survival, reproductionEligible=survival == "SURVIVE",
                evaluationValid=valid, dailyPnl=pnl, dailyReturn=ret, fitness=fitness,
                maxDrawdown=account.max_drawdown, trades=len(account.trades),
                transactionCost=account.transaction_cost, turnover=account.turnover,
                winRate=sum(t["pnlUsd"] > 0 for t in account.trades)/len(account.trades) if account.trades else None,
                averageR=sum(t["netR"] for t in account.trades)/len(account.trades) if account.trades else None,
                averageHoldMinutes=sum(t["holdMinutes"] for t in account.trades)/len(account.trades) if account.trades else None,
                lifetimeEquity=bot.lifetime_equity, cancellations=account.cancellations,
                unresolvedPosition=account.position, failureReason=None if valid else "NO_OBSERVABLE_SESSION_EXIT")



def aggregate_window_evaluations(bots, daily_batches, config, *, complete, mark_death=False):
    """Aggregate daily-reset accounts into one selection window.

    Every daily account still starts from initial_capital. Selection uses the sum
    of daily PnL and the mean of causal daily fitness values. Any INVALID day
    makes the whole bot window INVALID so partial data cannot create a parent.
    """
    if not daily_batches:
        return []
    expected = len(daily_batches)
    by_bot = {b.bot_id: [] for b in bots}
    for batch in daily_batches:
        for e in batch:
            if e["botId"] in by_bot:
                by_bot[e["botId"]].append(e)

    result = []
    for bot in bots:
        rows = by_bot[bot.bot_id]
        valid = len(rows) == expected and all(e["evaluationValid"] for e in rows)
        pnl = sum(e["dailyPnl"] for e in rows) if valid else None
        ret = pnl / config.initial_capital if valid else None
        daily_returns = [e["dailyReturn"] for e in rows] if valid else []
        trades = sum(e["trades"] for e in rows)
        costs = sum(e["transactionCost"] for e in rows)
        trades_per_session = trades / expected if expected else 0.0
        losing_sessions = sum(r < 0 for r in daily_returns)
        profitable_sessions = sum(r > 0 for r in daily_returns)
        neutral_sessions = sum(abs(r) <= config.epsilon / config.initial_capital for r in daily_returns)
        losing_ratio = losing_sessions / expected if expected else 0.0
        return_volatility = (
            math.sqrt(sum((r - sum(daily_returns)/len(daily_returns))**2 for r in daily_returns)/len(daily_returns))
            if daily_returns else 0.0
        )
        max_daily_drawdown = max((e["maxDrawdown"] for e in rows), default=0.0)
        if valid and config.fitness_mode == "v04_generalization":
            cost_ratio = costs / config.initial_capital
            excess_trades_per_session = max(0.0, trades_per_session - config.free_trade_count)
            fitness_components = {
                "windowReturn": ret,
                "drawdownPenalty": config.drawdown_penalty * max_daily_drawdown,
                "costPenalty": config.transaction_cost_penalty * cost_ratio,
                "tradingPenalty": config.excessive_trading_penalty * excess_trades_per_session,
                "losingSessionPenalty": config.losing_session_penalty * losing_ratio,
                "volatilityPenalty": config.return_volatility_penalty * return_volatility,
            }
            fitness = (
                fitness_components["windowReturn"]
                - fitness_components["drawdownPenalty"]
                - fitness_components["costPenalty"]
                - fitness_components["tradingPenalty"]
                - fitness_components["losingSessionPenalty"]
                - fitness_components["volatilityPenalty"]
            )
        else:
            fitness_components = None
            fitness = (sum(e["fitness"] for e in rows) / expected) if valid else None
        survival = (
            "SURVIVE" if pnl > config.epsilon
            else "DEAD" if pnl < -config.epsilon
            else "NEUTRAL"
        ) if valid else "INVALID"
        if complete and mark_death and survival == "DEAD":
            bot.death_date = rows[-1]["date"]
        result.append(dict(
            botId=bot.bot_id,
            date=rows[-1]["date"] if rows else None,
            survival=survival,
            reproductionEligible=bool(complete and survival == "SURVIVE"),
            evaluationValid=valid,
            dailyPnl=pnl,          # compatibility alias used by reproduce()
            dailyReturn=ret,       # compatibility alias; this is window cumulative return
            fitness=fitness,
            windowPnl=pnl,
            windowReturn=ret,
            windowSessions=expected,
            windowComplete=bool(complete),
            validDays=sum(bool(e["evaluationValid"]) for e in rows),
            maxDrawdown=max_daily_drawdown,
            trades=trades,
            tradesPerSession=trades_per_session,
            transactionCost=costs,
            costRatio=(costs/config.initial_capital) if valid else None,
            profitableSessions=profitable_sessions,
            losingSessions=losing_sessions,
            neutralSessions=neutral_sessions,
            losingSessionRatio=losing_ratio,
            returnVolatility=return_volatility,
            fitnessMode=config.fitness_mode,
            fitnessComponents=fitness_components,
            turnover=sum(e["turnover"] for e in rows),
            lifetimeEquity=bot.lifetime_equity,
            failureReason=None if valid else "INVALID_SESSION_IN_EVALUATION_WINDOW",
        ))
    return result


def reproduce(bots, evaluations, config, rng, generation, birth_date):
    lookup = {b.bot_id: b for b in bots}
    eligible = sorted([e for e in evaluations if e["reproductionEligible"] and e["evaluationValid"] and e["dailyPnl"] > config.epsilon],
                      key=lambda e: (-e["fitness"], e["botId"]))
    count = min(config.parent_pool_size, math.ceil(len(eligible)*config.parent_fraction))
    parents = [lookup[e["botId"]] for e in eligible[:count]]
    elites = parents[:min(config.elite_count, config.population_size)]
    children = []
    mutations = 0
    for i in range(config.population_size):
        if i < len(elites):
            ancestor = elites[i]
            genes, parent_ids, changed, origin = ancestor.genome, [ancestor.bot_id], 0, "ELITE"
        elif parents:
            ancestor = rng.choice(parents)
            parent_ids = [ancestor.bot_id]
            genes, origin = ancestor.genome, "MUTATION"
            if len(parents) > 1 and rng.random() < config.crossover_probability:
                second = rng.choice([p for p in parents if p.bot_id != ancestor.bot_id])
                genes = crossover(genes, second.genome, rng)
                parent_ids.append(second.bot_id)
                origin = "CROSSOVER_MUTATION"
            genes, changed = mutate(genes, rng, config)
        else:
            ancestor = None
            genes, parent_ids, changed, origin = random_genome(rng, config), [], 0, "RANDOM_RECOVERY"
        mutations += changed
        # Elite retains its lifetime; changed genes start a new financial lifetime.
        inherited = origin == "ELITE"
        children.append(Bot(f"g{generation:04d}-b{i:04d}", genes, generation, parent_ids, birth_date,
                            mutation_count=changed,
                            lifetime_days=ancestor.lifetime_days if inherited else 0,
                            lifetime_trades=ancestor.lifetime_trades if inherited else 0,
                            lifetime_equity=ancestor.lifetime_equity if inherited else config.initial_capital,
                            lifetime_initial_capital=config.initial_capital,
                            lifetime_pnl=ancestor.lifetime_pnl if inherited else 0, origin=origin))
    return children, dict(parentPoolSize=len(parents), eliteCount=len(elites), mutationCount=mutations,
                          crossoverCount=sum(c.origin == "CROSSOVER_MUTATION" for c in children),
                          randomRecovery=not parents, parentIds=[p.bot_id for p in parents])


def load_population_records(records, config):
    """Rebuild an immutable genome population from a saved research artifact."""
    from .genome import Genome
    bots = []
    seen = set()
    for row in records:
        bot_id = str(row["bot_id"])
        if bot_id in seen:
            raise ValueError(f"Duplicate bot_id in population artifact: {bot_id}")
        seen.add(bot_id)
        genome = Genome.from_dict(row["genome"]).validate(config)
        bot = Bot(
            bot_id=bot_id,
            genome=genome,
            generation=int(row.get("generation", 0)),
            parent_ids=list(row.get("parent_ids") or []),
            birth_date=str(row.get("birth_date") or ""),
            mutation_count=int(row.get("mutation_count", 0)),
            death_date=row.get("death_date"),
            lifetime_days=int(row.get("lifetime_days", 0)),
            lifetime_trades=int(row.get("lifetime_trades", 0)),
            lifetime_equity=float(row.get("lifetime_equity", config.initial_capital)),
            lifetime_initial_capital=float(row.get("lifetime_initial_capital", config.initial_capital)),
            origin=str(row.get("origin") or "LOADED"),
            lifetime_pnl=float(row.get("lifetime_pnl", 0.0)),
        )
        if row.get("genome_hash") and row["genome_hash"] != genome.digest:
            raise ValueError(f"Genome hash mismatch for {bot_id}")
        bots.append(bot)
    if not bots:
        raise ValueError("Population artifact is empty")
    return bots
