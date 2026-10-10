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

    def __post_init__(self):
        object.__setattr__(self, "weights", tuple(self.weights))

    def validate(self, config):
        if len(self.weights) != len(POLICY_FEATURES):
            raise ValueError("Weight/feature schema mismatch")
        for name, value in [("weight", x) for x in self.weights] + [(n, getattr(self, n)) for n in GENES]:
            lo, hi = config.gene_bounds[name]
            if not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError(f"Out-of-bounds gene {name}: {value}")
        if not isinstance(self.max_hold_minutes, int):
            raise ValueError("Holding time must be integer minutes")
        return self

    def to_dict(self):
        d = asdict(self)
        d["weights"] = dict(zip(POLICY_FEATURES, self.weights))
        return d

    @classmethod
    def from_dict(cls, d):
        if set(d["weights"]) != set(POLICY_FEATURES):
            raise ValueError("Unknown/missing weight names")
        return cls(tuple(d["weights"][n] for n in POLICY_FEATURES), **{n: d[n] for n in GENES})

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, allow_nan=False).encode()).hexdigest()


def random_genome(rng, config):
    values = {n: rng.uniform(*config.gene_bounds[n]) for n in GENES}
    values["max_hold_minutes"] = round(values["max_hold_minutes"])
    return Genome(tuple(rng.uniform(*config.gene_bounds["weight"]) for _ in POLICY_FEATURES), **values).validate(config)


def crossover(a, b, rng):
    return Genome(tuple(x if rng.random() < .5 else y for x, y in zip(a.weights, b.weights)),
                  **{n: getattr(a if rng.random() < .5 else b, n) for n in GENES})


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
                    **{n: gene(getattr(parent, n), n) for n in GENES})
    return child.validate(config), count
