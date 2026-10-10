#!/usr/bin/env python3
"""Persistent paper-trading runner driven by the live V3 strategy engine.

This module never submits broker orders. It consumes real Toss market data
through a caller-supplied market snapshot function, simulates fills, and keeps
an append-only decision/trade record for later strategy analysis.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


class PaperV3Trader:
    def __init__(self, root, market_snapshot):
        self.root = Path(root)
        self.market_snapshot = market_snapshot
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        # Forward sample gate: preserve all V3 research logs but keep NEW entries
        # paused by default after the first 25-trade sample failed acceptance.
        # This affects paper V3 only; it never changes live trading flags.
        self.new_entries_enabled = os.getenv("PAPER_V3_NEW_ENTRIES_ENABLED", "false").lower() == "true"

        configured = os.getenv("PAPER_V3_DIR", "/var/lib/market-career-dashboard/paper_v3")
        self.data_dir = Path(configured)
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.data_dir = self.root / "data" / "paper_v3"
            self.data_dir.mkdir(parents=True, exist_ok=True)

        self.state_path = self.data_dir / "state.json"
        self.trades_path = self.data_dir / "trades.jsonl"
        self.decisions_path = self.data_dir / "decisions.jsonl"

        self.enabled = False
        self.cash = self.initial_cash
        self.realized_pnl = 0.0
        self.position = None
        self.closed_count = 0
        self.win_count = 0
        self.loss_count = 0
        self.recent_trades = []
        self.last_candidates = []
        self.last_scan_at = None
        self.last_market_at = None
        self.last_error = None
        self.last_action = None
        self.session = "UNKNOWN"
        self.session_message = ""
        self.last_mark = None
        self._load_state()
        if self.enabled and not self.new_entries_enabled:
            self.enabled = False
            self.last_action = "V3_NEW_ENTRIES_PAUSED_AFTER_FORWARD_REVIEW"
            self._save_state()

    @property
    def initial_cash(self):
        return max(100.0, _f(os.getenv("PAPER_INITIAL_CASH_USD", "10000"), 10000.0))

    @property
    def order_usd(self):
        return max(10.0, _f(os.getenv("PAPER_ORDER_USD", "100"), 100.0))

    @property
    def slippage_bps(self):
        return max(0.0, min(100.0, _f(os.getenv("PAPER_SLIPPAGE_BPS", "2"), 2.0)))

    @property
    def scan_interval(self):
        # V3 decisions are primarily built from completed 5-minute bars.
        # Five minutes also avoids needlessly competing with the history
        # collector for the Toss candle endpoint.
        return max(60, int(_f(os.getenv("PAPER_SCAN_INTERVAL_SEC", "300"), 300)))

    @property
    def max_hold_seconds(self):
        return max(300, int(_f(os.getenv("PAPER_MAX_HOLD_SEC", "1800"), 1800)))

    @property
    def entry_sessions(self):
        raw = os.getenv("PAPER_ENTRY_SESSIONS", "REGULAR")
        return {x.strip().upper() for x in raw.split(",") if x.strip()}

    def _load_state(self):
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.enabled = bool(raw.get("enabled", False))
            self.cash = _f(raw.get("cash"), self.initial_cash)
            self.realized_pnl = _f(raw.get("realizedPnl"), 0.0)
            self.position = raw.get("position") if isinstance(raw.get("position"), dict) else None
            self.closed_count = int(raw.get("closedTrades") or 0)
            self.win_count = int(raw.get("wins") or 0)
            self.loss_count = int(raw.get("losses") or 0)
            self.recent_trades = list(raw.get("recentTrades") or [])[-100:]
            self.last_scan_at = raw.get("lastScanAt")
            self.last_market_at = raw.get("lastMarketAt")
            self.last_action = raw.get("lastAction")
        except Exception as exc:
            self.last_error = "paper state load failed: " + str(exc)

    def _save_state(self):
        payload = {
            "version": 1,
            "engine": "strategy-engine-v3",
            "enabled": self.enabled,
            "initialCash": self.initial_cash,
            "cash": self.cash,
            "realizedPnl": self.realized_pnl,
            "position": self.position,
            "closedTrades": self.closed_count,
            "wins": self.win_count,
            "losses": self.loss_count,
            "recentTrades": self.recent_trades[-100:],
            "lastScanAt": self.last_scan_at,
            "lastMarketAt": self.last_market_at,
            "lastAction": self.last_action,
            "updatedAt": datetime.now(UTC).isoformat(),
        }
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _append_jsonl(path, obj):
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _log_decisions(self, market):
        now = datetime.now(UTC).isoformat()
        for c in market.get("candidates") or []:
            f = c.get("features") or {}
            self._append_jsonl(self.decisions_path, {
                "recordedAt": now,
                "marketTimestamp": c.get("timestamp") or f.get("timestamp"),
                "session": market.get("session"),
                "symbol": c.get("symbol"),
                "price": c.get("price"),
                "signal": c.get("signal"),
                "bestStrategy": c.get("bestStrategy"),
                "bestDirection": c.get("bestDirection"),
                "strategyActive": c.get("strategyActive"),
                "bestStrategyScore": c.get("bestStrategyScore"),
                "finalScore": c.get("finalScore"),
                "stockScore": c.get("stockScore"),
                "stopPrice": c.get("stopPrice"),
                "targetPrice": c.get("targetPrice"),
                "targetPct": c.get("targetPct"),
                "riskScale": c.get("riskScale"),
                "reason": c.get("reason"),
                "features": {
                    "atr14": f.get("atr14"),
                    "atrPct": f.get("atrPct"),
                    "rsi14": f.get("rsi14"),
                    "rvol": f.get("rvol"),
                    "er12": f.get("er12"),
                    "vwap": f.get("vwap"),
                    "vwapSlope": f.get("vwapSlope"),
                    "zVwap": f.get("zVwap"),
                    "aboveVwap": f.get("aboveVwap"),
                },
            })

    def _entry_fill(self, market_price):
        return float(market_price) * (1.0 + self.slippage_bps / 10000.0)

    def _exit_fill(self, market_price):
        return float(market_price) * (1.0 - self.slippage_bps / 10000.0)

    def _open_position(self, candidate):
        market_price = _f(candidate.get("price"))
        if market_price <= 0:
            return
        fill = self._entry_fill(market_price)
        budget = min(self.order_usd, self.cash)
        if budget < 10.0:
            self.last_error = "paper cash insufficient"
            return
        qty = budget / fill
        if qty <= 0:
            return
        notional = qty * fill
        stop = _f(candidate.get("referenceMid") or candidate.get("stopPrice"))
        target = _f(candidate.get("targetPrice"))
        target_pct = _f(candidate.get("targetPct"))
        if target <= fill and target_pct > 0:
            target = fill * (1.0 + target_pct / 100.0)
        if target <= fill:
            risk = max(0.0, fill - stop)
            target = fill + 1.5 * risk if risk > 0 else fill * 1.01

        now = datetime.now(UTC).isoformat()
        self.cash -= notional
        self.position = {
            "symbol": candidate.get("symbol"),
            "strategy": candidate.get("bestStrategy"),
            "direction": "LONG",
            "quantity": qty,
            "entryPrice": fill,
            "entryMarketPrice": market_price,
            "entryNotional": notional,
            "entryTime": time.time(),
            "entryTimestamp": now,
            "signalTimestamp": candidate.get("timestamp") or (candidate.get("features") or {}).get("timestamp"),
            "stopPrice": stop if stop > 0 else None,
            "targetPrice": target,
            "finalScore": candidate.get("finalScore"),
            "strategyScore": candidate.get("bestStrategyScore"),
            "reason": candidate.get("reason"),
        }
        self.last_mark = market_price
        self.last_action = "PAPER_BUY %s @ %.4f" % (self.position["symbol"], fill)

    def _close_position(self, market_price, reason):
        p = self.position
        if not p:
            return
        market_price = _f(market_price)
        if market_price <= 0:
            return
        fill = self._exit_fill(market_price)
        qty = _f(p.get("quantity"))
        proceeds = qty * fill
        pnl = proceeds - _f(p.get("entryNotional"))
        ret = (fill / max(_f(p.get("entryPrice")), 1e-12) - 1.0) * 100.0
        self.cash += proceeds
        self.realized_pnl += pnl
        self.closed_count += 1
        if pnl > 0:
            self.win_count += 1
        elif pnl < 0:
            self.loss_count += 1

        trade = {
            **p,
            "exitTimestamp": datetime.now(UTC).isoformat(),
            "exitPrice": fill,
            "exitMarketPrice": market_price,
            "exitReason": reason,
            "pnlUsd": pnl,
            "returnPct": ret,
            "slippageBpsPerSide": self.slippage_bps,
        }
        self._append_jsonl(self.trades_path, trade)
        self.recent_trades.append(trade)
        self.recent_trades = self.recent_trades[-100:]
        self.last_action = "PAPER_SELL %s @ %.4f (%s)" % (p.get("symbol"), fill, reason)
        self.position = None
        self.last_mark = market_price

    def _manage_position(self, market):
        if not self.position:
            return
        sym = str(self.position.get("symbol") or "")
        managed = market.get("managed") or next(
            (x for x in market.get("candidates") or [] if str(x.get("symbol") or "") == sym),
            None,
        )
        if not managed:
            return
        price = _f(managed.get("price"))
        if price <= 0:
            return
        self.last_mark = price
        stop = _f(self.position.get("stopPrice"))
        target = _f(self.position.get("targetPrice"))
        reason = None

        if stop > 0 and price <= stop:
            reason = "v3_stop"
        elif target > 0 and price >= target:
            reason = "v3_target"
        elif self.position.get("entryTime") and time.time() - _f(self.position.get("entryTime")) >= self.max_hold_seconds:
            ma5 = _f(managed.get("ma5"), price)
            entry = _f(self.position.get("entryPrice"))
            if price < entry * 1.003 and price < ma5:
                reason = "v3_time_stop"

        now_ny = datetime.now(NY)
        if reason is None and (now_ny.hour > 15 or (now_ny.hour == 15 and now_ny.minute >= 50)):
            reason = "session_cutoff"

        if reason:
            self._close_position(price, reason)

    def scan(self, force=False):
        with self.lock:
            self.last_scan_at = datetime.now(UTC).isoformat()
            self.last_error = None
            try:
                managed_symbol = self.position.get("symbol") if self.position else None
                market = self.market_snapshot(managed_symbol)
                self.session = str(market.get("session") or "UNKNOWN")
                self.session_message = str(market.get("sessionMessage") or "")
                self.last_market_at = market.get("timestamp") or self.last_scan_at
                self.last_candidates = list(market.get("candidates") or [])[:20]
                self._log_decisions(market)

                # Risk management continues for an already-open paper position
                # even after the V3 entry switch has been turned off.
                if self.position:
                    self._manage_position(market)

                now_ny = datetime.now(NY)
                before_cutoff = (now_ny.hour, now_ny.minute) < (15, 50)
                if (self.enabled and self.new_entries_enabled and not self.position
                        and self.session in self.entry_sessions and before_cutoff):
                    buy = next((x for x in self.last_candidates if x.get("signal") == "BUY"), None)
                    if buy:
                        self._open_position(buy)
                elif self.enabled and not self.new_entries_enabled:
                    self.enabled = False
                    self.last_action = "V3_NEW_ENTRIES_PAUSED_AFTER_FORWARD_REVIEW"
                elif self.enabled and not before_cutoff and not self.position:
                    self.last_action = "V3_ENTRY_BLOCKED_AFTER_1550_ET"

                if not self.enabled and not self.position and not force:
                    self._save_state()
                    return self.status()

                self._save_state()
            except Exception as exc:
                self.last_error = str(exc)
                try:
                    self._save_state()
                except Exception:
                    pass
            return self.status()

    def set_enabled(self, enabled):
        with self.lock:
            requested = bool(enabled)
            if requested and not self.new_entries_enabled:
                self.enabled = False
                self.last_action = "V3_NEW_ENTRIES_PAUSED_AFTER_FORWARD_REVIEW"
                self.last_error = "V3 new paper entries are paused after forward-sample review"
            else:
                self.enabled = requested
                self.last_action = "PAPER_AUTO_ON" if self.enabled else "PAPER_AUTO_OFF"
                self.last_error = None
            self._save_state()
            return self.status()

    def status(self):
        with self.lock:
            mark = self.last_mark
            unrealized = 0.0
            market_value = 0.0
            open_position = dict(self.position) if self.position else None
            if self.position:
                entry = _f(self.position.get("entryPrice"))
                qty = _f(self.position.get("quantity"))
                mark = _f(mark, entry)
                market_value = qty * mark
                unrealized = (mark - entry) * qty
                open_position["markPrice"] = mark
                open_position["unrealizedPnlUsd"] = unrealized
                open_position["unrealizedPct"] = (mark / max(entry, 1e-12) - 1.0) * 100.0

            equity = self.cash + market_value
            total_pnl = equity - self.initial_cash
            return {
                "mode": "paper",
                "engine": "strategy-engine-v3",
                "dataSource": "Toss real market data / simulated fills only",
                "enabled": self.enabled,
                "newEntriesEnabled": self.new_entries_enabled,
                "entryPauseReason": None if self.new_entries_enabled else "25-trade forward sample failed acceptance; logs retained for analysis",
                "entryCutoffEt": "15:50",
                "session": self.session,
                "sessionMessage": self.session_message,
                "scanIntervalSec": self.scan_interval,
                "orderUsd": self.order_usd,
                "slippageBpsPerSide": self.slippage_bps,
                "entrySessions": sorted(self.entry_sessions),
                "initialCashUsd": self.initial_cash,
                "cashUsd": self.cash,
                "marketValueUsd": market_value,
                "equityUsd": equity,
                "realizedPnlUsd": self.realized_pnl,
                "unrealizedPnlUsd": unrealized,
                "totalPnlUsd": total_pnl,
                "returnPct": (equity / self.initial_cash - 1.0) * 100.0,
                "closedTrades": self.closed_count,
                "wins": self.win_count,
                "losses": self.loss_count,
                "winRate": (self.win_count / self.closed_count * 100.0) if self.closed_count else 0.0,
                "position": open_position,
                "candidates": self.last_candidates[:10],
                "recentTrades": list(reversed(self.recent_trades[-30:])),
                "lastScanAt": self.last_scan_at,
                "lastMarketAt": self.last_market_at,
                "lastAction": self.last_action,
                "lastError": self.last_error,
                "storage": {
                    "state": str(self.state_path),
                    "trades": str(self.trades_path),
                    "decisions": str(self.decisions_path),
                },
            }

    def run_loop(self):
        while not self.stop_event.is_set():
            if self.enabled or self.position:
                try:
                    self.scan()
                except Exception as exc:
                    with self.lock:
                        self.last_error = str(exc)
            self.stop_event.wait(self.scan_interval)

    def start(self):
        threading.Thread(target=self.run_loop, daemon=True, name="paper-v3-trader").start()
