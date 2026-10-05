#!/usr/bin/env python3
"""
Safe trading core for Market & Career Dashboard.

Live trading is intentionally disabled by default. Paper trading is available
for validating UI and strategy logic before any live order path is enabled.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import threading


@dataclass
class Signal:
    symbol: str
    action: str
    reason: str
    fast_sma: float | None = None
    slow_sma: float | None = None
    rsi: float | None = None


def closes_from(candles):
    rows = []
    for candle in candles or []:
        value = candle.get("closePrice", candle.get("close"))
        if value is None:
            continue
        rows.append((str(candle.get("timestamp", "")), float(value)))
    rows.sort(key=lambda x: x[0])
    return [v for _, v in rows]


def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values, period=14):
    if len(values) <= period:
        return None
    gains, losses = [], []
    for i in range(len(values) - period, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


class Strategy:
    def evaluate(self, symbol, candles):
        closes = closes_from(candles)
        if len(closes) < 20:
            return Signal(symbol, "HOLD", "not enough candles")
        fast = sma(closes, 5)
        slow = sma(closes, 20)
        current_rsi = rsi(closes, 14)
        if fast > slow * 1.002 and (current_rsi is None or current_rsi < 70):
            return Signal(symbol, "BUY", "5-SMA above 20-SMA with RSI below overbought zone", fast, slow, current_rsi)
        if fast < slow * 0.998 and (current_rsi is None or current_rsi > 30):
            return Signal(symbol, "SELL", "5-SMA below 20-SMA with RSI above oversold zone", fast, slow, current_rsi)
        return Signal(symbol, "HOLD", "trend/RSI conditions are neutral", fast, slow, current_rsi)


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    avg_price: float = 0.0


class PaperBroker:
    def __init__(self, initial_cash=10_000_000):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.positions = {}
        self.trades = []
        self._next_trade_id = 1
        self.lock = threading.Lock()

    def reset(self):
        with self.lock:
            self.cash = self.initial_cash
            self.positions.clear()
            self.trades.clear()
            self._next_trade_id = 1

    def trade(self, symbol, side, quantity, price):
        symbol = str(symbol).upper().strip()
        side = str(side).upper().strip()
        quantity = int(quantity)
        price = float(price)
        if not symbol or side not in {"BUY", "SELL"} or quantity <= 0 or price <= 0:
            raise ValueError("invalid paper order")
        with self.lock:
            p = self.positions.setdefault(symbol, Position(symbol))
            notional = quantity * price
            if side == "BUY":
                if notional > self.cash:
                    raise ValueError("insufficient paper cash")
                total = p.avg_price * p.quantity + notional
                p.quantity += quantity
                p.avg_price = total / p.quantity
                self.cash -= notional
            else:
                if p.quantity < quantity:
                    raise ValueError("insufficient paper position")
                p.quantity -= quantity
                self.cash += notional
                if p.quantity == 0:
                    p.avg_price = 0.0
            row = {
                "id": self._next_trade_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "notional": notional,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._next_trade_id += 1
            self.trades.append(row)
            return row

    def snapshot(self, prices=None):
        prices = prices or {}
        with self.lock:
            holdings = []
            market_value = 0.0
            for p in self.positions.values():
                if p.quantity <= 0:
                    continue
                current = float(prices.get(p.symbol, p.avg_price))
                value = p.quantity * current
                market_value += value
                holdings.append({
                    **asdict(p),
                    "current_price": current,
                    "market_value": value,
                    "unrealized_pnl": (current - p.avg_price) * p.quantity,
                })
            equity = self.cash + market_value
            return {
                "mode": "paper",
                "initial_cash": self.initial_cash,
                "cash": self.cash,
                "market_value": market_value,
                "equity": equity,
                "total_pnl": equity - self.initial_cash,
                "return_pct": (equity / self.initial_cash - 1.0) * 100.0,
                "holdings": holdings,
                "trades": list(reversed(self.trades)),
            }


strategy = Strategy()
paper = PaperBroker()
