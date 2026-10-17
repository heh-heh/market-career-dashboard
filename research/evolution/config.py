"""Configurable baseline, not fitted parameters."""
from dataclasses import asdict, dataclass, field
import math


@dataclass(frozen=True)
class Config:
    population_size: int = 200
    initial_capital: float = 100.0
    elite_count: int = 10
    parent_pool_size: int = 40
    parent_fraction: float = 0.5
    mutation_probability: float = 0.2  # per gene
    mutation_strength: float = 0.05  # fraction of gene range, Gaussian sigma
    crossover_probability: float = 0.7
    epsilon: float = 1e-6  # USD, selection-window survival
    evaluation_window_sessions: int = 7  # one generation spans this many processed trading sessions
    slippage_bps: float = 2.0
    half_spread_bps: float = 1.0
    drawdown_penalty: float = 0.5
    transaction_cost_penalty: float = 0.25
    excessive_trading_penalty: float = 0.001
    free_trade_count: int = 10
    # V0.4 optional generalization-oriented window fitness. The legacy V0 path
    # remains the default so prior experiments are reproducible.
    selection_policy: str = "legacy"  # opt-in; legacy RNG/eligibility remain unchanged
    fitness_mode: str = "v0"
    losing_session_penalty: float = 0.0
    return_volatility_penalty: float = 0.0
    # V0.7 regime/timing: selection fitness only, never future observation features.
    down_regime_loss_penalty: float = 0.0
    up_regime_loss_penalty: float = 0.0
    entry_adverse_penalty: float = 0.0
    exit_giveback_penalty: float = 0.0
    regime_return_floor: float = 0.001  # 10bp over 30m, not optimized
    regime_noise_multiplier: float = 1.0
    regime_concentration_penalty: float = 0.1
    min_regime_trades: int = 3  # diagnostic adequacy only, not a trade incentive
    entry_quality_minutes: int = 5
    timing_noise_floor: float = 0.001
    parent_max_drawdown: float = 0.05
    max_trades_per_session: int = 0  # 0 disables the structural cap
    entry_cooldown_minutes: int = 0
    min_profitable_sessions_for_parent: int = 0
    stability_fold_sessions: int = 0  # 0 disables V0.6 fold stability gating
    stability_min_positive_folds: int = 0
    cutoff_minutes: int = 5  # before actual XNYS close, including half days
    # V0.1 resource-aware scaling is opt-in so V0 replay remains reproducible by default.
    adaptive_resources: bool = False
    min_population: int = 20
    max_population: int = 1000
    scale_up_factor: float = 1.25
    scale_down_factor: float = 0.75
    memory_soft_limit_pct: float = 70.0
    memory_hard_limit_pct: float = 85.0
    swap_soft_limit_pct: float = 5.0
    swap_hard_limit_pct: float = 20.0
    cpu_soft_limit_pct: float = 85.0
    train: tuple = ("2022-01-01", "2024-12-31")
    validation: tuple = ("2025-01-01", "2025-12-31")
    final_oos: tuple = ("2026-01-01", "2026-12-31")
    # Fixed unit scaling, never full-sample or future normalization.
    return_scale: float = 0.01
    volume_scale: float = 100000.0
    feature_clip: float = 5.0
    gene_bounds: dict = field(default_factory=lambda: {
        "weight": [-2.0, 2.0], "buy_threshold": [0.25, 3.0],
        "sell_threshold": [-3.0, -0.25], "stop_loss_pct": [0.005, 0.03],
        "take_profit_pct": [0.008, 0.06], "max_hold_minutes": [5, 120],
        "position_fraction": [0.1, 1.0],
    })

    indicator_policy_enabled: bool = True  # V082 ablation; shared risk model unchanged
    indicator_aware: bool = False  # isolated V082; omitted from legacy snapshots
    portfolio_max_positions: int = 3
    portfolio_risk_budget: float = 0.02
    portfolio_daily_loss_limit: float = 0.03
    portfolio_min_order: float = 5.0
    portfolio_correlation_limit: float = 0.8
    portfolio_min_dollar_volume: float = 10000.0

    def __post_init__(self):
        if self.selection_policy not in {"legacy", "v08_profit_only", "v081_survival"}:
            raise ValueError("Unknown selection_policy")
        if self.selection_policy in {"v08_profit_only", "v081_survival"} and (self.evaluation_window_sessions != 5 or self.fitness_mode != "v07_regime_timing" or self.adaptive_resources):
            raise ValueError("V08 requires fixed population and existing five-session V07 fitness")
        if self.indicator_aware and self.selection_policy not in {"v081_survival", "v08_profit_only"}:
            raise ValueError("V082 requires survival breeding")
        if type(self.portfolio_max_positions) is not int or not 1 <= self.portfolio_max_positions <= 30:
            raise ValueError("Invalid portfolio cap")
        if not (0 < self.portfolio_risk_budget < 1 and 0 < self.portfolio_daily_loss_limit < 1 and
                0 < self.portfolio_correlation_limit <= 1 and self.portfolio_min_order > 0 and self.portfolio_min_dollar_volume >= 0):
            raise ValueError("Invalid portfolio risk limits")
        from datetime import date
        for x in asdict(self).values():
            if isinstance(x, float) and not math.isfinite(x):
                raise ValueError("Nonfinite config")
        if self.population_size < 1 or self.initial_capital <= 0:
            raise ValueError("Positive population and capital required")
        if any(type(x) is not int for x in (self.population_size, self.elite_count, self.parent_pool_size, self.free_trade_count, self.cutoff_minutes, self.min_population, self.max_population, self.evaluation_window_sessions, self.max_trades_per_session, self.entry_cooldown_minutes, self.min_profitable_sessions_for_parent, self.stability_fold_sessions, self.stability_min_positive_folds)):
            raise ValueError("Counts and cutoff must be integer")
        if min(self.elite_count, self.parent_pool_size, self.free_trade_count) < 0 or self.parent_pool_size < 1:
            raise ValueError("Invalid selection/trading counts")
        if self.evaluation_window_sessions < 1:
            raise ValueError("evaluation_window_sessions must be >= 1")
        if self.min_population < 1 or self.max_population < self.min_population:
            raise ValueError("Invalid adaptive population bounds")
        if self.adaptive_resources and not self.min_population <= self.population_size <= self.max_population:
            raise ValueError("Adaptive initial population must be within min/max bounds")
        if not 1 < self.scale_up_factor <= 2:
            raise ValueError("scale_up_factor must be in (1,2]")
        if not 0.1 <= self.scale_down_factor < 1:
            raise ValueError("scale_down_factor must be in [0.1,1)")
        limits = (self.memory_soft_limit_pct, self.memory_hard_limit_pct, self.swap_soft_limit_pct,
                  self.swap_hard_limit_pct, self.cpu_soft_limit_pct)
        if any(not 0 <= x <= 100 for x in limits):
            raise ValueError("Resource limits must be percentages")
        if self.memory_soft_limit_pct >= self.memory_hard_limit_pct:
            raise ValueError("memory soft limit must be below hard limit")
        if self.swap_soft_limit_pct >= self.swap_hard_limit_pct:
            raise ValueError("swap soft limit must be below hard limit")
        if not 0 < self.parent_fraction <= 1:
            raise ValueError("parent_fraction must be in (0,1]")
        if any(not 0 <= v <= 1 for v in (self.mutation_probability, self.crossover_probability)):
            raise ValueError("Probabilities must be in [0,1]")
        if min(self.epsilon, self.return_scale, self.volume_scale, self.feature_clip) <= 0 or self.cutoff_minutes < 1:
            raise ValueError("Invalid numerical scale/cutoff")
        if self.fitness_mode not in {"v0", "v04_generalization", "v05_structural", "v06_stability", "v07_regime_timing"}:
            raise ValueError("Unknown fitness_mode")
        if min(self.max_trades_per_session, self.entry_cooldown_minutes, self.min_profitable_sessions_for_parent,
               self.stability_fold_sessions, self.stability_min_positive_folds) < 0:
            raise ValueError("Structural trade controls must be nonnegative")
        if self.min_profitable_sessions_for_parent > self.evaluation_window_sessions:
            raise ValueError("Parent profitability requirement exceeds evaluation window")
        if self.fitness_mode == "v07_regime_timing":
            if self.transaction_cost_penalty or self.losing_session_penalty or self.down_regime_loss_penalty or self.up_regime_loss_penalty:
                raise ValueError("V07-R1 net return already includes costs/losses; duplicate penalties must be zero")
            if not 0 < self.parent_max_drawdown < 1:
                raise ValueError("parent_max_drawdown must be in (0,1)")
        if any(type(x) is not int or x < 1 for x in (self.min_regime_trades, self.entry_quality_minutes)):
            raise ValueError("Positive integer regime sample/entry timing window required")
        if min(self.regime_return_floor, self.regime_noise_multiplier, self.timing_noise_floor) <= 0 or self.regime_concentration_penalty < 0:
            raise ValueError("Invalid context/timing scales")
        if self.fitness_mode == "v06_stability":
            if self.stability_fold_sessions < 1:
                raise ValueError("V0.6 requires stability_fold_sessions")
            if self.evaluation_window_sessions % self.stability_fold_sessions:
                raise ValueError("Evaluation window must be divisible by stability fold size")
            fold_count = self.evaluation_window_sessions // self.stability_fold_sessions
            if not 1 <= self.stability_min_positive_folds <= fold_count:
                raise ValueError("Invalid stability_min_positive_folds")
        if min(self.slippage_bps, self.half_spread_bps, self.mutation_strength,
               self.drawdown_penalty, self.transaction_cost_penalty, self.excessive_trading_penalty,
               self.losing_session_penalty, self.return_volatility_penalty, self.down_regime_loss_penalty,
               self.up_regime_loss_penalty, self.entry_adverse_penalty, self.exit_giveback_penalty) < 0:
            raise ValueError("Costs/penalties must be nonnegative")
        if self.slippage_bps + self.half_spread_bps >= 10000:
            raise ValueError("Invalid execution cost")
        periods = [self.train, self.validation, self.final_oos]
        for lo, hi in periods:
            if date.fromisoformat(lo) > date.fromisoformat(hi):
                raise ValueError("Reversed split")
        if not self.train[1] < self.validation[0] <= self.validation[1] < self.final_oos[0]:
            raise ValueError("Splits must be chronological and disjoint")
        required = {"weight", "buy_threshold", "sell_threshold", "stop_loss_pct", "take_profit_pct", "max_hold_minutes", "position_fraction"}
        if set(self.gene_bounds) != required:
            raise ValueError("Unknown/missing gene bounds")
        for name, (lo, hi) in self.gene_bounds.items():
            if not math.isfinite(lo + hi) or lo >= hi:
                raise ValueError(f"Invalid bounds: {name}")
        if self.gene_bounds["stop_loss_pct"][0] <= 0 or self.gene_bounds["stop_loss_pct"][1] >= 1 or self.gene_bounds["take_profit_pct"][0] <= 0:
            raise ValueError("Invalid risk gene bounds")
        if self.gene_bounds["max_hold_minutes"][0] < 1 or self.gene_bounds["max_hold_minutes"][1] > 390:
            raise ValueError("Holding bound must be 1..390")
        if any(type(x) is not int for x in self.gene_bounds["max_hold_minutes"]):
            raise ValueError("Holding gene bounds must be integer")
        if not 0 < self.gene_bounds["position_fraction"][0] < self.gene_bounds["position_fraction"][1] <= 1:
            raise ValueError("Invalid position fraction")

    def split(self, day):
        for name in ("train", "validation", "final_oos"):
            lo, hi = getattr(self, name)
            if lo <= day <= hi:
                return name
        return None

    def snapshot(self):
        result = asdict(self)
        if not self.indicator_aware:
            for key in tuple(result):
                if key in ("indicator_aware", "indicator_policy_enabled") or key.startswith("portfolio_"):result.pop(key)
        if self.selection_policy == "legacy":
            result.pop("selection_policy")  # preserve historical config snapshots
        return result
