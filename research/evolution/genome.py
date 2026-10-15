"""Immutable, bounded genomes; uniform crossover; independent Gaussian mutation."""
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math

from .features import POLICY_FEATURES

GENES = ("buy_threshold", "sell_threshold", "stop_loss_pct", "take_profit_pct", "max_hold_minutes", "position_fraction")


@dataclass(frozen=True)
class Genome:
    weights: tuple
    buy_threshold: float
    sell_threshold: float
    stop_loss_pct: float
    take_profit_pct: float
    max_hold_minutes: int
    position_fraction: float
    context_weights: tuple = ()  # absent in legacy JSON; old hashes remain unchanged

    def __post_init__(self):
        object.__setattr__(self, "weights", tuple(self.weights))
        object.__setattr__(self, "context_weights", tuple(self.context_weights))

    def validate(self, config):
        if len(self.weights) != len(POLICY_FEATURES):
            raise ValueError("Weight/feature schema mismatch")
        from .regime_timing import CONTEXT_FEATURES
        if len(self.context_weights) not in (0, len(CONTEXT_FEATURES)):
            raise ValueError("Context weight schema mismatch")
        for name, value in [("weight", x) for x in self.weights+self.context_weights] + [(n, getattr(self, n)) for n in GENES]:
            lo, hi = config.gene_bounds[name]
            if not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError(f"Out-of-bounds gene {name}: {value}")
        if not isinstance(self.max_hold_minutes, int):
            raise ValueError("Holding time must be integer minutes")
        return self

    def to_dict(self):
        d = asdict(self)
        d["weights"] = dict(zip(POLICY_FEATURES, self.weights))
        from .regime_timing import CONTEXT_FEATURES
        if self.context_weights:
            d["context_weights"] = dict(zip(CONTEXT_FEATURES, self.context_weights))
        else:
            d.pop("context_weights")
        return d

    @classmethod
    def from_dict(cls, d):
        if set(d["weights"]) != set(POLICY_FEATURES):
            raise ValueError("Unknown/missing weight names")
        from .regime_timing import CONTEXT_FEATURES
        context = d.get("context_weights", {})
        if context and set(context) != set(CONTEXT_FEATURES):
            raise ValueError("Unknown/missing context weight names")
        return cls(tuple(d["weights"][n] for n in POLICY_FEATURES), **{n: d[n] for n in GENES},
                   context_weights=tuple(context[n] for n in CONTEXT_FEATURES) if context else ())

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, allow_nan=False).encode()).hexdigest()


def random_genome(rng, config):
    values = {n: rng.uniform(*config.gene_bounds[n]) for n in GENES}
    values["max_hold_minutes"] = round(values["max_hold_minutes"])
    weights = tuple(rng.uniform(*config.gene_bounds["weight"]) for _ in POLICY_FEATURES)
    from .regime_timing import MODE, CONTEXT_FEATURES
    base = Genome(weights, **values)
    # Do not perturb the legacy RNG stream: A/B initial legacy genes match by seed.
    import random
    context_rng = random.Random("V07_CONTEXT:"+base.digest)
    context = tuple(context_rng.uniform(*config.gene_bounds["weight"]) for _ in CONTEXT_FEATURES) if config.fitness_mode == MODE else ()
    return Genome(weights, **values, context_weights=context).validate(config)


def crossover(a, b, rng):
    return Genome(tuple(x if rng.random() < .5 else y for x, y in zip(a.weights, b.weights)),
                  **{n: getattr(a if rng.random() < .5 else b, n) for n in GENES},
                  context_weights=tuple(x if rng.random() < .5 else y for x,y in zip(
                      a.context_weights or (0.,)*len(b.context_weights),
                      b.context_weights or (0.,)*len(a.context_weights))))


def mutate(parent, rng, config):
    count = 0

    def gene(value, name):
        nonlocal count
        if rng.random() >= config.mutation_probability:
            return value
        lo, hi = config.gene_bounds[name]
        new = max(lo, min(hi, value + rng.gauss(0, config.mutation_strength * (hi-lo))))
        if name == "max_hold_minutes":
            new = round(new)
        count += int(new != value)
        return new

    child = replace(parent, weights=tuple(gene(v, "weight") for v in parent.weights),
                    **{n: gene(getattr(parent, n), n) for n in GENES},
                    context_weights=tuple(gene(v, "weight") for v in parent.context_weights))
    return child.validate(config), count
