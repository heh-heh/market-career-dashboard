"""V0.8.3: top-two genome-only breeding. No parent trading state survives.

Called only from explicitly opted-in V083 research. Requires 50 complete,
valid five-session evaluations. No fallback to arbitrary or invalid bots.
"""
from collections import Counter
import math

from .genome import crossover
from .population import Bot
from .survival_breeding import actual_mutation, valid_window


def select_top_two(evaluations, epsilon):
    size=len(evaluations)
    if (not 2 <= size <= 50 or
            len({e.get("botId") for e in evaluations}) != size or
            not all(valid_window(e) for e in evaluations)):
        raise ValueError("V083_REQUIRES_ALL_VALID_FIVE_SESSION_EVALUATIONS")
    # Primary criterion: actual net 5-session USD P&L. Risk and fitness
    # break ties; never sort on the optimizer's fitness alone.
    ranked = sorted(evaluations,
        key=lambda e: (-e["windowPnl"], e["windowMaxDrawdown"], -e["fitness"], e["botId"]))
    has_profit = any(e["windowPnl"] > epsilon for e in ranked)
    return ranked[:2], has_profit


def reproduce_top_two(bots, evaluations, config, rng, generation, birth_date):
    size=config.population_size
    if not 2 <= size <= 50 or len(bots) != size:
        raise ValueError("V083_POPULATION_OUT_OF_BOUNDS")
    by_id = {b.bot_id: b for b in bots}
    if len(by_id) != size or len({b.genome.digest for b in bots}) < 1:
        raise ValueError("V083_INVALID_OR_DUPLICATE_BOTS")
    selected, has_profit = select_top_two(evaluations, config.epsilon)
    if any(e["botId"] not in by_id for e in selected):
        raise ValueError("V083_UNKNOWN_PARENT")
    first, second = (by_id[e["botId"]] for e in selected)
    children = []
    for idx in range(size):
        # All children are new, including the first two. Parents are not
        # retained as runtime trading bots; only their immutable genes are used.
        parent_ids = [first.bot_id, second.bot_id]
        genes = crossover(first.genome, second.genome, rng)
        genes, changes = actual_mutation(genes, rng, config)
        children.append(Bot(
            f"g{generation:04d}-b{idx:04d}", genes, generation, parent_ids,
            birth_date, mutation_count=changes, origin="V083_TOP_TWO_CROSSOVER_MUTATION",
            lifetime_initial_capital=config.initial_capital,
            lifetime_equity=config.initial_capital,
            lifetime_pnl=0., lifetime_days=0, lifetime_trades=0))
    chosen_ids = [e["botId"] for e in selected]
    values = [e["windowPnl"]/config.initial_capital for e in evaluations]
    return children, {
        "selectionPolicy":"v083_top_two", "breedingMode":"TOP_TWO_NET_PNL",
        "parentIds":chosen_ids, "parentPoolSize":2,
        "parentGenomeHashes":[first.genome.digest,second.genome.digest],
        "parentSelection":[
            {"botId":e["botId"],"rank":i+1,"netPnl":e["windowPnl"],
             "maxDrawdown":e["windowMaxDrawdown"],"fitness":e["fitness"],
             "reason":"V083_NET_PNL_TOP_TWO"}
            for i,e in enumerate(selected)],
        "profitEligibleCount":sum(e["windowPnl"]>config.epsilon for e in evaluations),
        "profitableBotCount":sum(e["windowPnl"]>config.epsilon for e in evaluations),
        "profitableBotRatio":sum(e["windowPnl"]>config.epsilon for e in evaluations)/size,
        "restartRequested":not has_profit,
        "preservationParents":not has_profit,
        "validEvaluations":size, "randomRecovery":False, "eliteCount":0,
        "mutationCount":sum(c.mutation_count for c in children),
        "mutatedChildren":sum(c.mutation_count>0 for c in children),
        "crossoverCount":size, "offspringCount":size,
        "uniqueGenomes":len({c.genome.digest for c in children}),
        "maxParentContribution":0.5,
        "bestWindowReturn":max(values), "worstWindowReturn":min(values),
        "meanWindowReturn":sum(values)/len(values),
        "meanMdd":sum(e["windowMaxDrawdown"] for e in evaluations)/size,
        "meanFitness":sum(e["fitness"] for e in evaluations)/50,
    }
