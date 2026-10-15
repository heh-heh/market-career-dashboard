"""One shared immutable feature matrix per session; exact completed-minute alignment."""
from typing import Protocol
import numpy as np

BASE_FEATURES = (
    "return_1m", "return_5m", "return_15m", "return_30m",
    "momentum_5m", "momentum_15m", "momentum_30m",
    "current_volume", "rolling_volume_mean", "volume_ratio",
    "volatility_5m", "volatility_15m", "volatility_30m",
    "distance_from_vwap", "distance_from_recent_high", "distance_from_recent_low",
    "spy_return", "qqq_return", "sector_return", "benchmark_momentum", "benchmark_volatility",
    "relative_strength", "sector_relative_strength", "minute_from_regular_open", "minute_to_regular_close",
)
STATE_FEATURES = ("current_position", "unrealized_pnl", "holding_minutes", "available_cash")
# Momentum equals simple return: expose aliases but never duplicate policy columns.
POLICY_BASE = tuple(n for n in BASE_FEATURES if not n.startswith("momentum_"))
POLICY_FEATURES = POLICY_BASE + STATE_FEATURES
SEMI = frozenset("NVDA AMD MU INTC AVGO MRVL AMAT LRCX KLAC TSM QCOM SOXL SOXS SOXX SMH".split())
TECH = frozenset("AAPL MSFT META AMZN GOOGL TSLA PLTR NFLX COIN MSTR TQQQ SQQQ QQQ".split())


def benchmark_for_symbol(symbol):
    if symbol in SEMI:
        return "SOXX", "SOXX"
    if symbol in TECH:
        return "QQQ", "SOXX"  # sector RS is semiconductor context, not tech sector identity
    if symbol in ("SPY", "IWM"):
        return symbol, "SOXX"
    raise ValueError(f"No V0 benchmark mapping for {symbol}")


class FeatureProvider(Protocol):
    """Future TickFeatureProvider must preserve this causal observation boundary."""
    def prepare(self, rows, opening, closing, symbols): ...


class MinuteFeatureProvider:
    def __init__(self, config):
        self.config = config

    def prepare(self, rows, opening, closing, symbols):
        from research.backtest_v4_strategies import MINUTE
        self.symbols = tuple(sorted(symbols))
        self.times = tuple(opening + (i+1)*MINUTE for i in range(int((closing-opening).total_seconds()/60)))
        self.duration = len(self.times)
        self.bars = {}
        self.raw = {}
        # Every local formula receives only this prefix of contiguous completed bars.
        for symbol, minutes in rows.items():
            ends = {b.end: b for b in minutes.values() if opening <= b.start < closing}
            self.bars[symbol] = ends
            history = []
            cum_pv = cum_v = 0.0
            prefix_valid = True
            for t in self.times:
                b = ends.get(t)
                if b is None:
                    history = []  # no return across missing minute / no forward fill
                    prefix_valid = False
                    continue
                history.append(b)
                cum_pv += ((b.h+b.l+b.c)/3)*b.v
                cum_v += b.v
                if len(history) > 31:
                    history.pop(0)
                if len(history) < 31 or not prefix_valid or cum_v <= 0:
                    continue
                prices = np.array([x.c for x in history], dtype=float)
                returns = prices[1:]/prices[:-1]-1
                f = {f"return_{n}m": b.c/history[-n-1].c-1 for n in (1, 5, 15, 30)}
                f.update({f"momentum_{n}m": f[f"return_{n}m"] for n in (5, 15, 30)})
                f.update({f"volatility_{n}m": float(np.std(returns[-n:], ddof=0)) for n in (5, 15, 30)})
                vm = float(np.mean([x.v for x in history[-21:-1]]))  # excludes current volume
                f.update(current_volume=b.v, rolling_volume_mean=vm, volume_ratio=b.v/vm if vm > 0 else 0,
                         distance_from_vwap=b.c/(cum_pv/cum_v)-1,
                         distance_from_recent_high=b.c/max(x.h for x in history[-30:])-1,
                         distance_from_recent_low=b.c/min(x.l for x in history[-30:])-1,
                         minute_from_regular_open=(t-opening).total_seconds()/60,
                         minute_to_regular_close=(closing-t).total_seconds()/60,
                         vwap=cum_pv/cum_v, close=b.c)
                self.raw[symbol, t] = f
        self.matrix = np.zeros((len(self.times), len(self.symbols), len(POLICY_BASE)), dtype=float)
        self.valid = np.zeros((len(self.times), len(self.symbols)), dtype=bool)
        self.diagnostics = {"missingRegularMinutes": {s: self.duration-len(self.bars.get(s, {})) for s in sorted(rows)},
                            "eligibleObservations": 0, "contextUnavailable": 0,
                            "vwapGapPolicy": "No new signals after any missing regular-session prefix minute"}
        for ti, t in enumerate(self.times):
            for si, s in enumerate(self.symbols):
                benchmark, sector = benchmark_for_symbol(s)
                refs = [self.raw.get((r, t)) for r in ("SPY", "QQQ", sector, benchmark)]
                own = self.raw.get((s, t))
                if own is None or any(x is None for x in refs):
                    self.diagnostics["contextUnavailable"] += 1
                    continue
                spy, qqq, sec, bench = refs
                own.update(spy_return=spy["return_5m"], qqq_return=qqq["return_5m"],
                           spy_return_30m=spy["return_30m"], qqq_return_30m=qqq["return_30m"],
                           sector_return=sec["return_5m"],
                           benchmark_momentum=bench["return_15m"], benchmark_volatility=bench["volatility_15m"],
                           relative_strength=own["return_15m"]-bench["return_15m"],
                           sector_relative_strength=own["return_15m"]-sec["return_15m"])
                self.matrix[ti, si] = [self.normalized(n, own[n]) for n in POLICY_BASE]
                self.valid[ti, si] = True
                self.diagnostics["eligibleObservations"] += 1
        self.matrix.setflags(write=False)
        self.valid.setflags(write=False)
        return self

    def normalized(self, name, value):
        c = self.config
        if name in ("current_volume", "rolling_volume_mean"):
            value = np.log1p(value/c.volume_scale)
        elif name == "volume_ratio":
            value -= 1
        elif name.startswith("minute_"):
            value /= self.duration
        else:
            value /= c.return_scale
        return float(np.clip(value, -c.feature_clip, c.feature_clip))
