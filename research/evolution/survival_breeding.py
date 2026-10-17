"""V081: profit pool first, otherwise capital-preservation parents. Research only."""
from collections import Counter
from dataclasses import replace
import math
from .genome import crossover, mutate
from .population import Bot
from .profit_only import reproduce_profit_only

POLICY = 'v081_survival'


def valid_window(e):
    return (e.get('evaluationValid') is True and e.get('windowComplete') is True and
            e.get('windowSessions') == 5 and e.get('validDays') == 5 and
            all(isinstance(e.get(k), (int, float)) and math.isfinite(e[k]) for k in
                ('windowPnl', 'fitness', 'windowMaxDrawdown')))


def parent_candidates(evaluations, epsilon):
    valid=[e for e in evaluations if valid_window(e)]
    profitable=[e for e in valid if e['windowPnl']>epsilon]
    chosen=profitable if profitable else sorted(valid,key=lambda e:(-e['windowPnl'],e['windowMaxDrawdown'],-e['fitness'],e['botId']))[:2]
    return valid,profitable,chosen


def actual_mutation(genome, rng, config):
    child, count = mutate(genome, rng, config)
    if child.digest == genome.digest:
        # A bounded, real perturbation even with mutation_probability=0 or a
        # clipped Gaussian draw. Parents are immutable; no zero-strength retry loop.
        index = rng.randrange(len(genome.weights))
        weights = list(genome.weights)
        lo, hi = config.gene_bounds['weight']
        value = rng.uniform(lo, hi)
        if value == weights[index]: value = hi if value != hi else lo
        weights[index] = value
        child = replace(genome, weights=tuple(weights)).validate(config)
        count = 1
    return child, count


def reproduce_survival(bots, evaluations, config, rng, generation, birth_date):
    lookup = {b.bot_id:b for b in bots}
    if len(lookup) != len(bots) or len({e['botId'] for e in evaluations}) != len(evaluations):
        raise ValueError('DUPLICATE_BOT_EVALUATION')
    valid,profitable,chosen = parent_candidates(evaluations,config.epsilon)
    if any(e['botId'] not in lookup for e in valid): raise ValueError('UNKNOWN_PARENT_ID')
    if not valid: raise ValueError('NO_VALID_FIVE_SESSION_EVALUATIONS: data invalid, not a loss generation')
    if profitable:
        children, meta = reproduce_profit_only(bots, valid, replace(config, selection_policy='v08_profit_only'), rng, generation, birth_date)
        meta.update(selectionPolicy=POLICY, breedingMode='PROFIT_ONLY', preservationParents=False,
                    validEvaluations=len(valid), profitableBotCount=len(profitable), profitableBotRatio=len(profitable)/len(bots))
        return children, meta
    ranked = chosen
    children=[]
    for i in range(config.population_size):
        first=lookup[ranked[i % len(ranked)]['botId']]
        ids=[first.bot_id]; genes=first.genome
        if len(ranked)==2:
            second=lookup[ranked[(i+1)%2]['botId']]
            genes=crossover(genes,second.genome,rng);ids.append(second.bot_id)
        genes,count=actual_mutation(genes,rng,config)
        children.append(Bot(f'g{generation:04d}-b{i:04d}',genes,generation,ids,birth_date,
            mutation_count=count,origin='PRESERVATION_MUTATION',lifetime_initial_capital=config.initial_capital,
            lifetime_equity=config.initial_capital))
    contributions=Counter(p for c in children for p in c.parent_ids)
    return children,dict(selectionPolicy=POLICY,breedingMode='CAPITAL_PRESERVATION',preservationParents=True,
        parentPoolSize=len(ranked),parentIds=[e['botId'] for e in ranked],validEvaluations=len(valid),
        profitableBotCount=0,profitableBotRatio=0.,eliteCount=0,randomRecovery=False,
        mutationCount=sum(c.mutation_count for c in children),mutatedChildren=len(children),
        crossoverCount=len(children) if len(ranked)==2 else 0,offspringCount=len(children),
        uniqueGenomes=len({c.genome.digest for c in children}),maxParentContribution=1/len(ranked),
        bestWindowReturn=max(e['windowPnl']/config.initial_capital for e in valid),
        worstWindowReturn=min(e['windowPnl']/config.initial_capital for e in valid),
        meanWindowReturn=sum(e['windowPnl']/config.initial_capital for e in valid)/len(valid),
        meanMdd=sum(e['windowMaxDrawdown'] for e in valid)/len(valid),meanFitness=sum(e['fitness'] for e in valid)/len(valid),
        parentSelection=[dict(botId=e['botId'],netPnl=e['windowPnl'],maxDrawdown=e['windowMaxDrawdown'],fitness=e['fitness'],
            children=contributions[e['botId']],reason='CAPITAL_PRESERVATION_RANK',eliteCount=0,
            mutationCount=sum(c.mutation_count for c in children if e['botId'] in c.parent_ids)) for e in ranked])
