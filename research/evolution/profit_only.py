"""V08 selection only: eligibility from closed, valid net-PnL windows.

Positive rank weights keep *every* profitable candidate selectable even when
its unchanged V07 fitness is negative. Fitness is primary; MDD and profitable
sessions break ties. No qualification gate, parent_fraction or pool-size cap.
"""
import math
import statistics
from collections import Counter
from .genome import crossover, mutate, random_genome


def reproduce_profit_only(bots, evaluations, config, rng, generation, birth_date):
    from .population import Bot
    lookup = {b.bot_id: b for b in bots}
    if len(lookup) != len(bots) or len({e['botId'] for e in evaluations}) != len(evaluations):
        raise ValueError('DUPLICATE_BOT_EVALUATION')
    eligible = []
    for e in evaluations:
        if not (e.get('evaluationValid') and e.get('windowComplete') and
                e.get('windowSessions') == 5 and e.get('validDays') == 5):
            continue
        pnl = e.get('windowPnl')
        if pnl is None or not math.isfinite(pnl) or pnl <= config.epsilon:
            continue
        if e['botId'] not in lookup or not math.isfinite(e['fitness']):
            raise ValueError('INVALID_PROFIT_PARENT_RECORD')
        eligible.append(e)
    eligible.sort(key=lambda e: (-e['fitness'], e.get('windowMaxDrawdown', e['maxDrawdown']),
                                  -e['profitableSessions'], e['botId']))
    parents = [lookup[e['botId']] for e in eligible]
    weights = list(range(len(parents), 0, -1))
    elites = parents[:min(config.elite_count, config.population_size)]
    children = []
    for i in range(config.population_size):
        if i < len(elites):
            ancestor = elites[i]
            genes, ids, changes, origin = ancestor.genome, [ancestor.bot_id], 0, 'ELITE'
        elif parents:
            ancestor = rng.choices(parents, weights=weights, k=1)[0]
            genes, ids, origin = ancestor.genome, [ancestor.bot_id], 'MUTATION'
            if len(parents) > 1 and rng.random() < config.crossover_probability:
                others = [(p, w) for p, w in zip(parents, weights) if p.bot_id != ancestor.bot_id]
                second = rng.choices([p for p, w in others], weights=[w for p, w in others], k=1)[0]
                genes = crossover(genes, second.genome, rng)
                ids.append(second.bot_id)
                origin = 'CROSSOVER_MUTATION'
            genes, changes = mutate(genes, rng, config)
        else:
            ancestor = None
            genes, ids, changes, origin = random_genome(rng, config), [], 0, 'RANDOM_RECOVERY'
        inherited = origin == 'ELITE'
        children.append(Bot(f'g{generation:04d}-b{i:04d}', genes, generation, ids, birth_date,
            mutation_count=changes, origin=origin, lifetime_initial_capital=config.initial_capital,
            lifetime_equity=ancestor.lifetime_equity if inherited else config.initial_capital,
            lifetime_pnl=ancestor.lifetime_pnl if inherited else 0,
            lifetime_days=ancestor.lifetime_days if inherited else 0,
            lifetime_trades=ancestor.lifetime_trades if inherited else 0))
    offspring = Counter(p for c in children for p in c.parent_ids)
    total_links = sum(offspring.values())
    valid = [e for e in evaluations if e.get('evaluationValid') and e.get('windowComplete')]
    returns = [e['windowReturn'] for e in valid]
    return children, dict(selectionPolicy=config.selection_policy, parentPoolSize=len(parents),
        parentIds=[p.bot_id for p in parents], eliteCount=len(elites),
        mutationCount=sum(c.mutation_count for c in children),
        mutatedChildren=sum(c.mutation_count > 0 for c in children),
        crossoverCount=sum(len(c.parent_ids) == 2 for c in children), randomRecovery=not parents,
        offspringCount=len(children), uniqueGenomes=len({c.genome.digest for c in children}),
        maxParentContribution=max(offspring.values(), default=0)/total_links if total_links else None,
        profitableBotRatio=len(parents)/len(bots),
        meanWindowReturn=statistics.mean(returns) if returns else None,
        worstWindowReturn=min(returns) if returns else None,
        bestWindowReturn=max(returns) if returns else None,
        meanMdd=statistics.mean(e.get('windowMaxDrawdown', e['maxDrawdown']) for e in valid) if valid else None,
        meanFitness=statistics.mean(e['fitness'] for e in valid) if valid else None,
        parentSelection=[dict(botId=e['botId'], netPnl=e['windowPnl'], fitness=e['fitness'],
            rankWeight=w, children=offspring[e['botId']],
            mutationCount=sum(c.mutation_count for c in children if e['botId'] in c.parent_ids),
            eliteCount=sum(c.origin == 'ELITE' and e['botId'] in c.parent_ids for c in children),
            reason='POSITIVE_VALID_NET_WINDOW') for e, w in zip(eligible, weights)])
