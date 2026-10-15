"""Fractional long-only virtual account and replaceable adverse execution costs."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecutionCostModel:
    slippage_bps: float = 2
    half_spread_bps: float = 1

    def fill(self, price, side):
        import math
        if not math.isfinite(price) or price <= 0 or side not in ("BUY", "SELL"):
            raise ValueError("Invalid simulated execution")
        return price*(1 + (1 if side == "BUY" else -1)*(self.slippage_bps+self.half_spread_bps)/10000)


@dataclass
class Account:
    initial_capital: float
    costs: ExecutionCostModel
    cash: float = field(init=False)
    position: dict | None = None
    realized_pnl: float = 0
    transaction_cost: float = 0
    trades: list = field(default_factory=list)
    peak_equity: float = field(init=False)
    max_drawdown: float = 0
    turnover: float = 0
    last_price: float | None = None
    pending: dict | None = None
    cancellations: dict = field(default_factory=dict)

    def __post_init__(self):
        self.cash = self.peak_equity = self.initial_capital

    @property
    def equity(self):
        return self.cash + (self.position["quantity"]*self.last_price if self.position else 0)

    @property
    def unrealized_pnl(self):
        return self.position["quantity"]*(self.last_price-self.position["entryFillPrice"]) if self.position else 0

    def mark(self, price, t):
        self.last_price = price
        if self.position:
            p = self.position
            if price > p["highestObservedPrice"]:
                p["highestObservedPrice"], p["mfeTimestamp"] = price, t.isoformat()
            if price < p["lowestObservedPrice"]:
                p["lowestObservedPrice"], p["maeTimestamp"] = price, t.isoformat()
        self.peak_equity = max(self.peak_equity, self.equity)
        self.max_drawdown = max(self.max_drawdown, (self.peak_equity-self.equity)/self.peak_equity)

    def buy(self, symbol, price, t, fraction, signal, genome):
        if self.position or not 0 < fraction <= 1:
            raise ValueError("No leverage, shorting or pyramiding")
        fill = self.costs.fill(price, "BUY")
        notional = self.cash*fraction
        quantity = notional/fill
        self.cash -= notional
        self.transaction_cost += quantity*(fill-price)
        self.turnover += quantity*price
        self.position = dict(symbol=symbol, quantity=quantity, entryFillPrice=fill,
                             entryMarketPrice=price, entryTimestamp=t.isoformat(),
                             signalTimestamp=signal["signalTimestamp"], sourceCandle=signal["sourceCandle"],
                             features=signal["features"], decisionScore=signal["score"],
                             benchmark=signal["benchmark"], decisionReason="LINEAR_BUY_THRESHOLD",
                             stopPrice=fill*(1-genome.stop_loss_pct), targetPrice=fill*(1+genome.take_profit_pct),
                             highestObservedPrice=price, lowestObservedPrice=price,
                             mfeTimestamp=t.isoformat(), maeTimestamp=t.isoformat(),
                             executionModel="NEXT_COMPLETED_MINUTE_CLOSE_PROXY", session="REGULAR")
        from datetime import datetime
        self.position["entryLatencySeconds"] = (t-datetime.fromisoformat(signal["signalTimestamp"])).total_seconds()
        features = signal["features"]
        spy, qqq = features.get("spy_return", 0), features.get("qqq_return", 0)
        self.position["marketRegime"] = "UP" if spy > 0 and qqq > 0 else "DOWN" if spy < 0 and qqq < 0 else "MIXED_OR_FLAT"
        # Separate V0.7 confirmation: only completed SPY/QQQ 5m AND 30m
        # observations at entry. This field is an audit label, not a future input.
        spy30, qqq30 = features.get("spy_return_30m"), features.get("qqq_return_30m")
        self.position["confirmedMarketRegime"] = (
            "UP" if spy30 is not None and qqq30 is not None and min(spy, qqq, spy30, qqq30) > 0
            else "DOWN" if spy30 is not None and qqq30 is not None and max(spy, qqq, spy30, qqq30) < 0
            else "MIXED_OR_FLAT"
        )
        self.mark(price, t)

    def sell(self, price, t, reason, signal_timestamp):
        if not self.position:
            raise ValueError("Cannot sell without position")
        p = self.position
        self.mark(price, t)
        fill = self.costs.fill(price, "SELL")
        quantity = p["quantity"]
        pnl = quantity*(fill-p["entryFillPrice"])
        self.cash += quantity*fill
        self.realized_pnl += pnl
        self.transaction_cost += quantity*(price-fill)
        self.turnover += quantity*price
        from datetime import datetime
        entry_t = datetime.fromisoformat(p["entryTimestamp"])
        trade = dict(p, exitTimestamp=t.isoformat(), exitSignalTimestamp=signal_timestamp,
                     exitMarketPrice=price, exitFillPrice=fill, exitReason=reason, pnlUsd=pnl,
                     returnPct=100*(fill/p["entryFillPrice"]-1),
                     mfePct=max(0, 100*(p["highestObservedPrice"]/p["entryFillPrice"]-1)),
                     maePct=min(0, 100*(p["lowestObservedPrice"]/p["entryFillPrice"]-1)),
                     holdMinutes=(t-entry_t).total_seconds()/60,
                     entryToMFEMinutes=(datetime.fromisoformat(p["mfeTimestamp"])-entry_t).total_seconds()/60,
                     entryToMAEMinutes=(datetime.fromisoformat(p["maeTimestamp"])-entry_t).total_seconds()/60,
                     costUsd=quantity*((p["entryFillPrice"]-p["entryMarketPrice"])+(price-fill)),
                     netR=pnl/(quantity*(p["entryFillPrice"]-p["stopPrice"])))
        trade["exitLatencySeconds"] = (t-datetime.fromisoformat(signal_timestamp)).total_seconds()
        trade["missingExecutionObservations"] = max(0, int(trade["exitLatencySeconds"]/60)-1)
        trade["grossPnlUsd"] = quantity*(price-p["entryMarketPrice"])
        self.trades.append(trade)
        self.position = None
        self.mark(price, t)
        return trade

    def state_features(self, t, duration):
        from datetime import datetime
        held = (t-datetime.fromisoformat(self.position["entryTimestamp"])).total_seconds()/60 if self.position else 0
        return (float(self.position is not None), self.unrealized_pnl/self.initial_capital,
                held/duration, self.cash/self.initial_capital)
