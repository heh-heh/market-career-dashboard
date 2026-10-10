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


def daily_evaluation(bot, account, day, config, *, update_lifetime=True):
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
        if survival == "DEAD":
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
