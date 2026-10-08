"""Forward PAPER research with real Toss quotes; no broker/order capability.

Only whitelisted market-data GETs exist. Completed candles define signals;
pending signals resolve at the exact following minute's real open, on a later
poll (maximum 90 seconds lateness). observedAt records REST delivery latency.
No replay of old signals. Position candles may be replayed after a restart.

Intrabar convention: opening gaps first; hard stop before existing trailing
stop. A candle's high raises/activates the trail only AFTER testing old stops.
The new trail applies next minute; if this candle CLOSE already breaches it,
exit at that close. Never assume high occurred before low for a better fill.
Excursions on ambiguous stop candles use only the known open and exit price.
"""
from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import re
import threading
import time as clock
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from toss_rate_limit import group_for_path, wait_for_slot

NAME = "SIMPLE_MOMENTUM_V1"
NY = ZoneInfo("America/New_York")
MINUTE = timedelta(minutes=1)
KNOWN_LEVERAGED = {"TQQQ", "SQQQ", "SOXL", "SOXS", "UPRO", "SPXU", "SSO", "SDS", "QLD", "QID", "TNA", "TZA"}


def iso(dt):
    return dt.isoformat()


def timestamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Toss timestamp requires an explicit timezone offset")
    return dt.astimezone(NY)


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite number")
    return result


@dataclass(frozen=True)
class Config:
    min_price: float = 5
    min_day_change_pct: float = 5
    min_volume: float = 100000
    min_amount_usd: float = 1000000
    max_candidates: int = 15
    window_minutes: int = 10
    impulse_pct: float = 2
    pullback_min_pct: float = 1
    pullback_max_pct: float = 3
    hard_stop_pct: float = 1.2
    trail_activation_pct: float = .8
    trail_distance_pct: float = .6
    time_stop_minutes: int = 15
    order_usd: float = 100
    slippage_bps: float = 2
    initial_cash: float = 10000
    max_entry_delay_sec: float = 90
    force_exit_time: str = "15:50"
    timestamp_kind: str = "start"

    def __post_init__(self):
        for name, value in vars(self).items():
            if isinstance(value, (int, float)) and (not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid Simple V1 setting: {name}")
        if not 1 <= self.max_candidates <= 20 or not 3 <= self.window_minutes <= 30:
            raise ValueError("Simple candidates must be 1..20; impulse window 3..30 minutes")
        if not 0 < self.pullback_min_pct <= self.pullback_max_pct < 100:
            raise ValueError("Invalid pullback range")
        if not all(0 < v < 100 for v in (self.hard_stop_pct, self.trail_activation_pct, self.trail_distance_pct)):
            raise ValueError("Invalid stop/trail percentages")
        if min(self.order_usd, self.initial_cash, self.time_stop_minutes, self.max_entry_delay_sec) <= 0 or self.slippage_bps >= 100:
            raise ValueError("Invalid paper cash/time/slippage settings")
        if self.timestamp_kind not in ("start", "end"):
            raise ValueError("SIMPLE_TIMESTAMP_KIND must be start or exclusive end")
        cutoff = time.fromisoformat(self.force_exit_time)
        if not time(9, 30) < cutoff < time(16):
            raise ValueError("Simple forced exit must be inside the regular session")

    @classmethod
    def from_env(cls):
        names = dict(min_price="MIN_PRICE", min_day_change_pct="MIN_DAY_CHANGE_PCT",
                     min_volume="MIN_TRADING_VOLUME", min_amount_usd="MIN_TRADING_AMOUNT_USD",
                     max_candidates="MAX_CANDIDATES", window_minutes="IMPULSE_WINDOW_MINUTES",
                     impulse_pct="MIN_IMPULSE_PCT", pullback_min_pct="PULLBACK_MIN_PCT",
                     pullback_max_pct="PULLBACK_MAX_PCT", hard_stop_pct="HARD_STOP_PCT",
                     trail_activation_pct="TRAIL_ACTIVATION_PCT", trail_distance_pct="TRAIL_DISTANCE_PCT",
                     time_stop_minutes="TIME_STOP_MINUTES", order_usd="PAPER_ORDER_USD",
                     slippage_bps="SLIPPAGE_BPS", initial_cash="PAPER_INITIAL_CASH_USD",
                     max_entry_delay_sec="MAX_ENTRY_DELAY_SEC", force_exit_time="FORCE_EXIT_TIME",
                     timestamp_kind="TIMESTAMP_KIND")
        defaults = cls()
        return cls(**{k: type(getattr(defaults, k))(os.getenv("SIMPLE_"+v, str(getattr(defaults, k)))) for k, v in names.items()})


class ReadOnlyTossMarketData:
    """Cannot express a POST, account operation, order endpoint or arbitrary URL."""
    ALLOWED = frozenset({"/api/v1/rankings", "/api/v1/stocks", "/api/v1/candles", "/api/v1/market-calendar/US"})

    def __init__(self, token_provider):
        self.token_provider = token_provider
        self.calendar_cache = None

    def get(self, path, params=None):
        if path not in self.ALLOWED:
            raise PermissionError("Simple paper client permits market-data GETs only")
        query = urllib.parse.urlencode(params or {})
        url = "https://openapi.tossinvest.com" + path + ("?"+query if query else "")
        for attempt in range(2):
            token = self.token_provider(force=bool(attempt))
            if not token:
                raise RuntimeError("Toss market-data authentication unavailable")
            request = urllib.request.Request(url, method="GET", headers={"Authorization": "Bearer "+token, "Accept": "application/json"})
            wait_for_slot(group_for_path(path))
            try:
                with urllib.request.urlopen(request, timeout=8) as response:
                    obj = json.loads(response.read())
                return obj.get("result", obj) if isinstance(obj, dict) else obj
            except urllib.error.HTTPError as exc:
                if exc.code != 401 or attempt:
                    # Do not expose provider response bodies/tokens to the UI.
                    raise RuntimeError(f"Toss market-data HTTP {exc.code}") from exc
        raise RuntimeError("Toss market-data authentication failed")

    def session(self, now, config):
        day = now.astimezone(NY).date().isoformat()
        if self.calendar_cache is None or self.calendar_cache[0] != day:
            obj = self.get("/api/v1/market-calendar/US", {"date": day})
            if not isinstance(obj, dict) or "today" not in obj:
                raise ValueError("Toss US calendar response missing today")
            self.calendar_cache = day, obj["today"]
        regular = (self.calendar_cache[1] or {}).get("regularMarket")
        if not regular:
            return dict(status="CLOSED", date=day)
        opening, closing = timestamp(regular["startTime"]), timestamp(regular["endTime"])
        if opening.date().isoformat() != day or closing <= opening:
            raise ValueError("Invalid Toss regular-session bounds")
        cutoff = min(datetime.combine(opening.date(), time.fromisoformat(config.force_exit_time), NY), closing-timedelta(minutes=10))
        return dict(status="REGULAR" if opening <= now < closing else "CLOSED",
                    date=day, open=iso(opening), close=iso(closing), cutoff=iso(cutoff))

    def candidates(self, config):
        obj = self.get("/api/v1/rankings", dict(type="MARKET_TRADING_VOLUME", marketCountry="US",
                                               duration="realtime", excludeInvestmentCaution="true", count="100"))
        rows = obj.get("rankings") if isinstance(obj, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Invalid Toss volume ranking response")
        preliminary = []
        seen = set()
        for item in rows[:100]:
            try:
                symbol = str(item["symbol"]).upper()
                p = item["price"]
                price, change = number(p["lastPrice"]), 100*number(p["changeRate"])
                volume = number(item["tradingVolume"])
                amount = number(item["tradingAmount"]) if item.get("tradingAmount") is not None else price*volume
                if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol) or symbol in seen:
                    continue
                seen.add(symbol)
                if price < config.min_price or change < config.min_day_change_pct or volume < config.min_volume or amount < config.min_amount_usd:
                    continue
                preliminary.append(dict(symbol=symbol, price=price, dayChangePct=change, tradingVolume=volume,
                                        previousClose=price/(1+change/100), previousCloseSource="ranking lastPrice/changeRate",
                                        tradingAmount=amount, tradingAmountSource="ranking" if item.get("tradingAmount") is not None else "price*volume",
                                        candidateRank=item.get("rank")))
            except (KeyError, TypeError, ValueError):
                continue
        if not preliminary:
            return []
        metadata = self.get("/api/v1/stocks", {"symbols": ",".join(x["symbol"] for x in preliminary)})
        if not isinstance(metadata, list):
            raise ValueError("Invalid Toss stock metadata response")
        meta = {str(x.get("symbol", "")).upper(): x for x in metadata}
        out = []
        for item in preliminary:
            info = meta.get(item["symbol"])
            if info is None:
                continue  # Missing identity metadata is not proof of suitability.
            kind = str(info.get("securityType") or "").upper()
            if info.get("status") not in (None, "", "ACTIVE") or info.get("isActive") is False:
                continue
            if info.get("currency") not in (None, "", "USD"):
                continue
            if kind and kind not in {"STOCK", "ETF"} or info.get("isCommonShare") is False and kind != "ETF":
                continue
            if any(info.get(flag) is True for flag in ("isWarrant", "isRight", "isPreferredShare", "isExotic")):
                continue
            if item["symbol"] in KNOWN_LEVERAGED or info.get("isLeveraged") is True or info.get("isInverse") is True:
                continue
            if info.get("leverageRatio") is not None:
                try:
                    if number(info["leverageRatio"]) != 1:
                        continue
                except ValueError:
                    continue
            item.update(name=info.get("name") or info.get("englishName") or item["symbol"], securityType=kind or "UNKNOWN")
            out.append(item)
        out.sort(key=lambda x: (-x["dayChangePct"], -x["tradingAmount"], x["symbol"]))
        return out[:config.max_candidates]

    def candles(self, symbol):
        obj = self.get("/api/v1/candles", {"symbol": symbol, "interval": "1m", "count": "200"})
        rows = obj.get("candles") if isinstance(obj, dict) else None
        if not isinstance(rows, list):
            raise ValueError(f"Invalid Toss candle response for {symbol}")
        return rows


def normalize_candles(rows, now, config):
    """Return completed OHLCV and open-only current bars, never future bars."""
    out = {}
    for row in rows:
        start = timestamp(row["timestamp"])
        if config.timestamp_kind == "end":
            start -= MINUTE
        if start.second or start.microsecond:
            raise ValueError("Candle labels must be exact minute boundaries")
        if start > now:
            continue
        opening = number(row.get("openPrice", row.get("open")))
        if opening <= 0:
            raise ValueError("Invalid opening price")
        bar = dict(start=start, end=start+MINUTE, open=opening, complete=start+MINUTE <= now)
        if bar["complete"]:
            h, l, c, v = (number(row.get(a, row.get(b))) for a, b in (("highPrice", "high"), ("lowPrice", "low"), ("closePrice", "close"), ("volume", "volume")))
            if h < max(opening, c) or l > min(opening, c) or h < l or min(h, l, c) <= 0 or v < 0:
                raise ValueError("Invalid completed OHLCV")
            bar.update(high=h, low=l, close=c, volume=v)
        if start in out and out[start] != bar:
            raise ValueError("Conflicting candle timestamps")
        out[start] = bar
    return [out[t] for t in sorted(out)]


def candle_record(bar):
    return {k: iso(v) if isinstance(v, datetime) else v for k, v in bar.items()} if bar else None


def analyze_setup(bars, candidate, now, config, session):
    decision = dict(timestamp=iso(now), **candidate, recentImpulsePct=None, recentHigh=None,
                    pullbackPct=None, pullbackLow=None, current1m=None, previous1m=None,
                    bullishBar=False, closeAbovePrevHigh=False, noNewLow=False,
                    reversalConfirmed=False, eligibleForEntry=False, rejectionReason=None)
    completed = [b for b in bars if b["complete"] and b["end"] <= now and b["start"].date() == now.date()
                 and session.get("open") and timestamp(session["open"]) <= b["start"] < timestamp(session["close"])]
    decision.update(current1m=candle_record(completed[-1]) if completed else None,
                    previous1m=candle_record(completed[-2]) if len(completed) > 1 else None)
    if len(completed) < config.window_minutes:
        decision["rejectionReason"] = "MARKET_DATA_ERROR" if candidate.get("marketDataError") else "INSUFFICIENT_COMPLETED_BARS"
        return decision
    window = completed[-config.window_minutes:]
    current, previous = window[-1], window[-2]
    decision.update(current1m=candle_record(current), previous1m=candle_record(previous))
    if any(b["start"]-a["start"] != MINUTE for a, b in zip(window, window[1:])):
        decision["rejectionReason"] = "MISSING_WINDOW_MINUTE"
        return decision
    if (now-current["end"]).total_seconds() > config.max_entry_delay_sec:
        decision["rejectionReason"] = "STALE_TRIGGER_BAR"
        return decision
    # Local high and pullback low exclude confirmation: its future excursion
    # cannot redefine the setup it is supposed to confirm.
    before = window[:-1]
    peak = max(b["high"] for b in before)
    peak_index = max(i for i, b in enumerate(before) if b["high"] == peak)
    pullback = before[peak_index+1:]
    impulse = 100*(peak/window[0]["open"]-1)
    depth = 100*(1-current["close"]/peak)
    low = min((b["low"] for b in pullback), default=None)
    bullish, above = current["close"] > current["open"], current["close"] > previous["high"]
    no_new_low = low is not None and current["low"] >= low
    decision.update(recentImpulsePct=impulse, recentHigh=peak, pullbackPct=depth, pullbackLow=low,
                    bullishBar=bullish, closeAbovePrevHigh=above, noNewLow=no_new_low,
                    reversalConfirmed=bullish and above and no_new_low,
                    triggerBarTimestamp=iso(current["start"]), signalTimestamp=iso(current["end"]))
    reason = ("IMPULSE_BELOW_THRESHOLD" if impulse+1e-9 < config.impulse_pct else
              "NO_POST_HIGH_PULLBACK" if not pullback else
              "PULLBACK_OUTSIDE_RANGE" if not config.pullback_min_pct-1e-9 <= depth <= config.pullback_max_pct+1e-9 else
              "REVERSAL_NOT_CONFIRMED" if not decision["reversalConfirmed"] else None)
    decision.update(eligibleForEntry=reason is None, rejectionReason=reason)
    return decision


class SimpleMomentumPaper:
    """One independent USD cash account, persisted with a recovery journal."""
    def __init__(self, data_dir, market_data, config=None):
        self.directory = Path(data_dir)
        self.config = config or Config.from_env()
        self.market_data = market_data
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file_lock = (self.directory/".writer.lock").open("a+")
        try:
            fcntl.flock(self.file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file_lock.close()
            raise RuntimeError("Simple paper directory already has a writer")
        self.state_path = self.directory/"state.json"
        try:
            self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else dict(
                strategy=NAME, mode="paper", revision=0, enabled=False, initialCash=self.config.initial_cash,
                paperCash=self.config.initial_cash, openPosition=None, pendingSignal=None,
                currentCandidate=None, candidates=[], lastScan=None, lastError=None,
                session=dict(status="UNKNOWN"), completedTrades=0, wins=0, losses=0,
                cumulativePnl=0.0, completedByDate={}, seenTriggers={})
            if self.state.get("strategy") != NAME or self.state.get("mode") != "paper":
                raise ValueError("Wrong paper state identity; refusing to reset it")
            self.recent_trades = []
            self._recover()
        except Exception:
            self.file_lock.close()
            raise

    def _append(self, filename, record):
        encoded = (json.dumps(record, ensure_ascii=False, allow_nan=False)+"\n").encode()
        fd = os.open(self.directory/filename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            with os.fdopen(fd, "ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            self.state["enabled"] = False
            raise

    def _save(self):
        tmp = self.directory/"state.tmp"
        with tmp.open("w", encoding="utf-8") as stream:
            os.chmod(tmp, 0o600)
            json.dump(self.state, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, self.state_path)
        fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _recover(self):
        trades = self.directory/"trades.jsonl"
        known = set()
        if trades.exists():
            with trades.open() as stream:
                for line in stream:
                    trade = json.loads(line)
                    if trade.get("strategy") != NAME or trade.get("mode") != "paper":
                        raise ValueError("Foreign trade log; refusing to share paper accounts")
                    known.add(trade["tradeId"])
                    self.recent_trades = (self.recent_trades+[trade])[-30:]
        journal = self.directory/"signals.jsonl"
        if journal.exists():
            with journal.open() as stream:
                for line in stream:
                    event = json.loads(line)  # Torn/corrupt journal fails closed.
                    after = event.get("stateAfter")
                    if event.get("strategy") != NAME or (after and (after.get("strategy") != NAME or after.get("mode") != "paper")):
                        raise ValueError("Foreign paper journal; refusing to overwrite it")
                    if after and after["revision"] > self.state["revision"]:
                        self.state = after
                    trade = event.get("trade")
                    if trade and trade["tradeId"] not in known:
                        self._append("trades.jsonl", trade)
                        known.add(trade["tradeId"])
                        self.recent_trades = (self.recent_trades+[trade])[-30:]
        if journal.exists():
            self._save()

    def _commit(self, kind, now, **details):
        self.state["revision"] += 1
        self._append("signals.jsonl", dict(strategy=NAME, kind=kind, observedAt=iso(now),
                                            stateAfter=copy.deepcopy(self.state), **details))
        self._save()

    def set_enabled(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a JSON boolean")
        with self.lock:
            self.state["enabled"] = enabled
            if not enabled:
                self.state["pendingSignal"] = None
            self._commit("CONTROL", datetime.now(NY), enabled=enabled)
            return self.status()

    def _enter(self, signal, bar, now):
        fill = bar["open"]*(1+self.config.slippage_bps/10000)
        quantity = self.config.order_usd/fill
        if self.state["paperCash"] < self.config.order_usd:
            raise ValueError("Insufficient independent paper cash")
        self.state["paperCash"] -= self.config.order_usd
        self.state["pendingSignal"] = None
        self.state["openPosition"] = dict(
            symbol=signal["symbol"], tradeId=f"{signal['symbol']}:{signal['signalTimestamp']}",
            date=bar["start"].date().isoformat(), entrySignalTimestamp=signal["signalTimestamp"],
            triggerBarTimestamp=signal["triggerBarTimestamp"], entryTimestamp=iso(bar["start"]),
            entryObservedAt=iso(now), entryDeliveryDelaySeconds=(now-bar["start"]).total_seconds(),
            entryMarketPrice=bar["open"], entryFill=fill, quantity=quantity,
            dayChangeAtEntry=100*(bar["open"]/signal["previousClose"]-1) if signal.get("previousClose") else None,
            dayChangeAtSignal=signal["dayChangePct"], previousClose=signal.get("previousClose"),
            impulsePct=signal["recentImpulsePct"],
            pullbackPct=signal["pullbackPct"], recentHigh=signal["recentHigh"], pullbackLow=signal["pullbackLow"],
            stopPrice=fill*(1-self.config.hard_stop_pct/100),
            trailActivationPrice=fill*(1+self.config.trail_activation_pct/100),
            trailingActivated=False, trailingActivationTimestamp=None, trailingStopPrice=None,
            highestPriceAfterEntry=bar["open"], lowestPriceAfterEntry=bar["open"],
            lastMarketPrice=bar["open"], lastMarketTimestamp=iso(bar["start"]), lastCompletedMinute=None,
            sessionCutoff=signal["sessionCutoff"], timeStopMinutes=self.config.time_stop_minutes,
            trailDistancePct=self.config.trail_distance_pct, slippageBps=self.config.slippage_bps,
            dataGap=False, configurationAtEntry=dict(vars(self.config)),
            excursionMethod="complete held candles; stop candle open/exit only")
        self._commit("ENTRY", now, entry=copy.deepcopy(self.state["openPosition"]))

    def _exit(self, price, execution_time, reason, now):
        p = self.state["openPosition"]
        p["highestPriceAfterEntry"] = max(p["highestPriceAfterEntry"], price)
        p["lowestPriceAfterEntry"] = min(p["lowestPriceAfterEntry"], price)
        fill = price*(1-p["slippageBps"]/10000)
        pnl = (fill-p["entryFill"])*p["quantity"]
        self.state["paperCash"] += fill*p["quantity"]
        self.state["cumulativePnl"] += pnl
        self.state["completedTrades"] += 1
        self.state["wins"] += pnl > 0
        self.state["losses"] += pnl < 0
        self.state["completedByDate"].setdefault(p["date"], []).append(p["symbol"])
        trade = dict(strategy=NAME, mode="paper", **p, exitTimestamp=iso(execution_time),
                     exitObservedAt=iso(now), exitMarketPrice=price, exitFill=fill, exitReason=reason,
                     pnlUsd=pnl, returnPct=100*(fill/p["entryFill"]-1),
                     mfePct=max(0, 100*(p["highestPriceAfterEntry"]/p["entryFill"]-1)),
                     maePct=100*(p["lowestPriceAfterEntry"]/p["entryFill"]-1),
                     holdDurationSeconds=(execution_time-timestamp(p["entryTimestamp"])).total_seconds(),
                     exitTimeBasis="bar_end" if reason in ("HARD_STOP", "TRAILING_STOP", "TRAILING_CLOSE") else "minute_open")
        self.state["openPosition"] = None
        self._commit("EXIT", now, trade=trade)
        self._append("trades.jsonl", trade)
        self.recent_trades = (self.recent_trades+[trade])[-30:]

    def _manage(self, bars, now):
        p = self.state["openPosition"]
        if not p:
            return
        entry = timestamp(p["entryTimestamp"])
        last = timestamp(p["lastCompletedMinute"]) if p["lastCompletedMinute"] else entry-MINUTE
        expected = last+MINUTE
        relevant = [b for b in bars if expected <= b["start"] <= now]
        for bar in relevant:
            if bar["start"] != expected:
                # Never invent stops inside missing candles. Flatten at a fresh
                # observed open and label incomplete excursions explicitly.
                p["dataGap"] = True
                fresh = [b for b in relevant if 0 <= (now-b["start"]).total_seconds() <= self.config.max_entry_delay_sec]
                if fresh:
                    b = fresh[-1]
                    self._exit(b["open"], b["start"], "DATA_GAP_EXIT", now)
                else:
                    self.state["lastError"] = "OPEN_POSITION_DATA_GAP_NO_FRESH_FILL"
                return
            opening = bar["open"]
            p["highestPriceAfterEntry"] = max(p["highestPriceAfterEntry"], opening)
            p["lowestPriceAfterEntry"] = min(p["lowestPriceAfterEntry"], opening)
            p["lastMarketPrice"], p["lastMarketTimestamp"] = opening, iso(bar["start"])
            old_trail = p["trailingStopPrice"] if p["trailingActivated"] else None
            reason = ("HARD_STOP_GAP" if opening <= p["stopPrice"] else
                      "TRAILING_STOP_GAP" if old_trail is not None and opening <= old_trail else
                      "SESSION_EXIT" if bar["start"] >= timestamp(p["sessionCutoff"]) else
                      "TIME_STOP" if bar["start"] >= entry+timedelta(minutes=p["timeStopMinutes"]) and not p["trailingActivated"] else None)
            if reason:
                self._exit(opening, bar["start"], reason, now)
                return
            if not bar["complete"] or bar["end"] > now:
                return
            stop = (p["stopPrice"] if bar["low"] <= p["stopPrice"] else
                    old_trail if old_trail is not None and bar["low"] <= old_trail else None)
            if stop is not None:
                p["lowestPriceAfterEntry"] = min(p["lowestPriceAfterEntry"], stop)
                self._exit(stop, bar["end"], "HARD_STOP" if stop == p["stopPrice"] else "TRAILING_STOP", now)
                return
            p["highestPriceAfterEntry"] = max(p["highestPriceAfterEntry"], bar["high"])
            p["lowestPriceAfterEntry"] = min(p["lowestPriceAfterEntry"], bar["low"])
            p["lastMarketPrice"], p["lastMarketTimestamp"] = bar["close"], iso(bar["end"])
            if not p["trailingActivated"] and bar["high"] >= p["trailActivationPrice"]:
                p["trailingActivated"] = True
                p["trailingActivationTimestamp"] = iso(bar["end"])
            if p["trailingActivated"]:
                p["trailingStopPrice"] = p["highestPriceAfterEntry"]*(1-p["trailDistancePct"]/100)
                if bar["close"] <= p["trailingStopPrice"]:
                    self._exit(bar["close"], bar["end"], "TRAILING_CLOSE", now)
                    return
            p["lastCompletedMinute"] = iso(bar["start"])
            expected += MINUTE
        if now >= timestamp(p["sessionCutoff"]) and self.state["openPosition"]:
            self.state["lastError"] = "SESSION_EXIT_WAITING_FOR_REAL_OPEN"

    def process_snapshot(self, candidates, bars_by_symbol, now, session):
        """Deterministic core. Only scan() obtains real production market data."""
        with self.lock:
            self.state.update(lastScan=iso(now), session=session, candidates=[], lastError=None)
            position = self.state["openPosition"]
            if position:
                self._manage(bars_by_symbol.get(position["symbol"], []), now)
            pending = self.state["pendingSignal"]
            if pending:
                expected = timestamp(pending["signalTimestamp"])
                bar = next((b for b in bars_by_symbol.get(pending["symbol"], []) if b["start"] == expected), None)
                valid = (self.state["enabled"] and session.get("status") == "REGULAR"
                         and expected.date() == now.date() and expected < timestamp(pending["sessionCutoff"])
                         and 0 <= (now-expected).total_seconds() <= self.config.max_entry_delay_sec)
                if bar and valid and not self.state["openPosition"]:
                    self._enter(pending, bar, now)
                    self._manage(bars_by_symbol.get(pending["symbol"], []), now)
                elif not valid or now >= expected+timedelta(seconds=self.config.max_entry_delay_sec):
                    self.state["pendingSignal"] = None
                    reason = ("PAPER_DISABLED" if not self.state["enabled"] else
                              "SESSION_NO_LONGER_REGULAR" if session.get("status") != "REGULAR" else
                              "NEXT_OPEN_AFTER_CUTOFF" if expected >= timestamp(pending["sessionCutoff"]) else
                              "MISSING_OR_STALE_NEXT_MINUTE")
                    self._commit("CANCEL", now, reason=reason, symbol=pending["symbol"])
            for candidate in candidates[:self.config.max_candidates]:
                symbol = candidate["symbol"]
                decision = analyze_setup(bars_by_symbol.get(symbol, []), candidate, now, self.config, session)
                reason = ("SESSION_NOT_REGULAR" if session.get("status") != "REGULAR" else
                          "SESSION_CUTOFF" if now >= timestamp(session["cutoff"]) else
                          "COMPLETED_TODAY" if symbol in self.state["completedByDate"].get(now.date().isoformat(), []) else
                          "POSITION_OPEN" if self.state["openPosition"] else
                          "ENTRY_PENDING" if self.state["pendingSignal"] else
                          "PAPER_DISABLED" if not self.state["enabled"] else
                          "INSUFFICIENT_PAPER_CASH" if self.state["paperCash"] < self.config.order_usd else decision["rejectionReason"])
                if reason is None and self.state["seenTriggers"].get(symbol) == decision["signalTimestamp"]:
                    reason = "DUPLICATE_SIGNAL"
                decision.update(strategy=NAME, eligibleForEntry=reason is None, rejectionReason=reason)
                self._append("decisions.jsonl", decision)
                self.state["candidates"].append(decision)
                if reason is None:
                    self.state["seenTriggers"][symbol] = decision["signalTimestamp"]
                    self.state["pendingSignal"] = dict(decision, sessionCutoff=session["cutoff"])
                    self.state["currentCandidate"] = decision
                    self._commit("TRIGGER", now, signal=decision)
            if not self.state["pendingSignal"]:
                self.state["currentCandidate"] = self.state["candidates"][0] if self.state["candidates"] else None
            self._save()
            return self.status()

    def scan(self):
        # Network waits serialize scans, but never block status or OFF controls.
        with self.scan_lock:
            deadline = clock.monotonic()+50
            now = datetime.now(NY)
            errors = []
            try:
                session = self.market_data.session(now, self.config)
            except Exception as exc:
                session = dict(status="UNKNOWN", date=now.date().isoformat())
                errors.append(str(exc))
            # Open/pending instruments first, regardless of ranking availability.
            with self.lock:
                symbols = [p["symbol"] for p in (self.state["openPosition"], self.state["pendingSignal"]) if p]
            candidates, bars = [], {}
            for symbol in dict.fromkeys(symbols):
                try:
                    bars[symbol] = normalize_candles(self.market_data.candles(symbol), datetime.now(NY), self.config)
                except Exception as exc:
                    errors.append(f"{symbol}: {exc}")
            # Protect existing positions/resolve pending opens BEFORE spending
            # the scan budget on rankings and unrelated candidate requests.
            self.process_snapshot([], bars, datetime.now(NY), session)
            if session["status"] == "REGULAR":
                try:
                    candidates = self.market_data.candidates(self.config)[:self.config.max_candidates]
                except Exception as exc:
                    errors.append(str(exc))
                for item in candidates:
                    symbol = item["symbol"]
                    if symbol not in bars:
                        if clock.monotonic() >= deadline:
                            errors.append("CANDIDATE_SCAN_BUDGET_EXHAUSTED")
                            break
                        try:
                            bars[symbol] = normalize_candles(self.market_data.candles(symbol), datetime.now(NY), self.config)
                        except Exception as exc:
                            item["marketDataError"] = str(exc)
                            errors.append(f"{symbol}: {exc}")
            result = self.process_snapshot(candidates, bars, datetime.now(NY), session)
            if errors:
                with self.lock:
                    self.state["lastError"] = "; ".join(errors)
                    self._append("decisions.jsonl", dict(strategy=NAME, timestamp=iso(datetime.now(NY)), rejectionReason="MARKET_DATA_ERROR", errors=errors))
                    self._save()
                    result = self.status()
            return result

    def status(self):
        with self.lock:
            result = copy.deepcopy(self.state)
            p = result["openPosition"]
            if p:
                p.update(mfePct=max(0, 100*(p["highestPriceAfterEntry"]/p["entryFill"]-1)),
                         maePct=100*(p["lowestPriceAfterEntry"]/p["entryFill"]-1),
                         timeInTradeSeconds=max(0, (datetime.now(NY)-timestamp(p["entryTimestamp"])).total_seconds()))
            unrealized = (p["lastMarketPrice"]-p["entryFill"])*p["quantity"] if p else 0.0
            result.update(paperEquity=result["paperCash"]+(p["lastMarketPrice"]*p["quantity"] if p else 0),
                          realizedPnl=result["cumulativePnl"], unrealizedPnl=unrealized,
                          totalPnl=result["cumulativePnl"]+unrealized,
                          winRate=result["wins"]/result["completedTrades"] if result["completedTrades"] else 0,
                          recentTrades=copy.deepcopy(self.recent_trades), configuration=vars(self.config),
                          persistencePath=str(self.directory), liveOrderCapability=False)
            return result

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self.run_loop, daemon=True, name="simple-paper-v1")
            self.thread.start()

    def run_loop(self):
        while not self.stop_event.is_set():
            started = clock.monotonic()
            try:
                if self.state["enabled"] or self.state["openPosition"] or self.state["pendingSignal"]:
                    self.scan()
            except Exception as exc:
                with self.lock:
                    self.state["enabled"] = False
                    self.state["lastError"] = str(exc)
                # Never hide a persistence failure or keep entering after it.
            self.stop_event.wait(max(1, 60-(clock.monotonic()-started)))

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError("Paper worker still running; retaining its writer lock")
        self.file_lock.close()


def persistent_directory():
    return Path(os.getenv("SIMPLE_PAPER_DATA_DIR", str(Path(os.getenv("PERSISTENT_DATA_DIR", "/var/lib/market-career-dashboard"))/"paper_simple_v1")))
