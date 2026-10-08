#!/usr/bin/env python3
"""Research-only implementation of strategy_spec_v4.md (c9e12ce).

No collection, orders, parameter search or portfolio allocation. Runtime needs
existing SYMBOL.csv.gz files and exchange_calendars (XNYS sessions). Explicit
timestamp/price-consistency acknowledgements are required, never inferred from
future rows. --help and compilation require only the Python standard library.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics as st
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
MINUTE = timedelta(minutes=1)
SLIP = 0.0002
SEMI = frozenset("NVDA AMD MU INTC AVGO MRVL AMAT LRCX KLAC TSM QCOM SOXL".split())
TECH = frozenset("AAPL MSFT META AMZN GOOGL TSLA PLTR NFLX COIN MSTR TQQQ".split())
TRADABLE = SEMI | TECH
PARAMS = {
    "S1": dict(impulse_atr=1.0, pullback_max=0.50, pullback_volume_max=0.70,
               trigger_volume_min=1.0, entry_chase_atr=0.30, max_hold_minutes=45),
    "S2": dict(opening_range_minutes=15, break_min=0.10, break_max=0.75,
               reclaim_max_bars=2, entry_chase_atr=0.30, max_hold_minutes=30),
    "S3": dict(rs_min=0.25, compression_max=0.70, box_width_max=1.20,
               expansion_volume_min=1.50, wait_max_bars=3, entry_chase_atr=0.30),
}
WINDOWS = {"S1": (time(10), time(14)), "S2": (time(9, 45), time(11)),
           "S3": (time(10, 30), time(14, 30))}


def ratio(numerator, denominator):
    return numerator / denominator if denominator is not None and denominator > 0 else None


def iso(value):
    return value.isoformat() if value is not None else None


def benchmark_for_symbol(symbol):
    """Frozen mapping; inverse instruments have metadata but no baseline rules."""
    if symbol in SEMI or symbol == "SOXS":
        return "SOXX", (-3 if symbol == "SOXS" else 3 if symbol == "SOXL" else 1)
    if symbol in TECH or symbol == "SQQQ":
        return "QQQ", (-3 if symbol == "SQQQ" else 3 if symbol == "TQQQ" else 1)
    raise ValueError(f"No frozen benchmark mapping for {symbol}")


@dataclass(frozen=True, slots=True)
class Bar:
    start: datetime
    end: datetime
    o: float
    h: float
    l: float
    c: float
    v: float
    bid: float | None = None
    ask: float | None = None


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_input_path(data_dir, symbol, meta):
    """Bind each reviewed instrument to one finalized CSV inside data-dir."""
    name = meta.get("path")
    if not isinstance(name, str) or name not in (f"{symbol}.csv", f"{symbol}.csv.gz"):
        raise ValueError(f"{symbol}: manifest path must be SYMBOL.csv or SYMBOL.csv.gz")
    root = data_dir.resolve()
    path = (root/name).resolve()
    if path.parent != root or not path.is_file():
        raise ValueError(f"{symbol}: manifest input missing or outside data-dir: {name}")
    return path


def normalize_minute_timestamp(value, timestamp_kind, naive_timezone):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        if naive_timezone is None:
            raise ValueError("manifest requires timezone-aware rows")
        zone = ZoneInfo(naive_timezone)
        # Reject ambiguous/nonexistent local times; a declaration cannot resolve
        # a DST fold or a missing wall-clock minute.
        candidates = [dt.replace(tzinfo=zone, fold=f) for f in (0, 1)]
        valid = [x for x in candidates if x.astimezone(ZoneInfo("UTC")).astimezone(zone).replace(tzinfo=None) == dt]
        if not valid or len({x.utcoffset() for x in valid}) != 1:
            raise ValueError("ambiguous/nonexistent naive local timestamp")
        dt = valid[0]
    dt = dt.astimezone(NY)
    start = dt - MINUTE if timestamp_kind == "end" else dt
    if start.second or start.microsecond:
        raise ValueError("timestamp must be an exact minute boundary; inclusive :59 end labels are unsupported")
    return start


def validate_data_manifest(path, symbols, timestamp_kind, *, data_dir=None):
    """Require externally reviewed, byte-bound provenance, not a boolean alone.

    OHLCV cannot prove corporate-action history. The manifest records that
    external verification; hashes ensure the reviewed inputs are actually read.
    """
    if path is None:
        raise ValueError("--data-manifest is required; --confirm-price-adjustment alone is not verification")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("Data manifest must have version 1")
    if manifest.get("validationPassed") is not True:
        raise ValueError("Manifest validation did not PASS")
    if manifest.get("priceAdjustmentConfirmed") is not True:
        raise ValueError("Manifest priceAdjustmentConfirmed must be true")
    if manifest.get("timestampKind") != timestamp_kind:
        raise ValueError("Manifest timestamp_kind disagrees with CLI")
    if manifest.get("calendar") != "XNYS":
        raise ValueError("Manifest calendar must be XNYS")
    for field in ("verified_by", "verification_note"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError(f"Manifest requires external review evidence: {field}")
    files = manifest.get("symbols")
    if not isinstance(files, dict):
        raise ValueError("Manifest requires symbols metadata")
    bases = set()
    for symbol in sorted(symbols):
        meta = files.get(symbol)
        if not isinstance(meta, dict):
            raise ValueError(f"Manifest lacks required symbol {symbol}")
        if meta.get("validationPassed") is not True or meta.get("priceAdjustmentConfirmed") is not True:
            raise ValueError(f"{symbol}: validation/price adjustment confirmation did not PASS")
        if meta.get("symbol") != symbol or meta.get("symbolIdentityConfirmed") is not True:
            raise ValueError(f"{symbol}: symbol identity is unverified")
        for field in ("conflictingDuplicates", "ohlcViolations", "timestampViolations", "quoteViolations", "symbolViolations"):
            if meta.get(field) != 0:
                raise ValueError(f"{symbol}: {field} must be zero")
        digest = meta.get("sha256", "")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"{symbol}: manifest requires SHA256 of the exact input bytes")
        if meta.get("timestamp_kind") != timestamp_kind:
            raise ValueError(f"{symbol}: manifest timestamp_kind disagrees with CLI")
        expected = "minute_start" if timestamp_kind == "start" else "exclusive_minute_end"
        if meta.get("timestamp_semantics") != expected:
            raise ValueError(f"{symbol}: timestamp_semantics must be {expected}; inclusive :59 labels are unsupported")
        if "naive_timezone" not in meta or meta["naive_timezone"] not in (None, "America/New_York", "UTC"):
            raise ValueError(f"{symbol}: declare naive_timezone=null (aware rows only), America/New_York or UTC")
        if meta.get("split_consistency_verified") is not True:
            raise ValueError(f"{symbol}: split/leveraged-ETF adjustment consistency is unverified")
        if meta.get("point_in_time_eligibility_verified") is not True:
            raise ValueError(f"{symbol}: historical $5/ADV20 eligibility versus as-traded prices/volume is unverified")
        basis = meta.get("price_basis")
        if basis not in ("split_adjusted_ohlcv", "unadjusted_split_free_ohlcv"):
            raise ValueError(f"{symbol}: unknown/unverified price and volume adjustment basis")
        adjustment = {"split_adjusted_ohlcv": "split-adjusted", "unadjusted_split_free_ohlcv": "raw"}[basis]
        if meta.get("priceAdjustment") != adjustment or manifest.get("priceAdjustment") != adjustment:
            raise ValueError(f"{symbol}: inconsistent priceAdjustment declaration")
        bases.add(basis)
        if data_dir is not None:
            input_path = manifest_input_path(data_dir, symbol, meta)
            before = input_path.stat()
            actual = file_sha256(input_path)
            after = input_path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise ValueError(f"{symbol}: input changed during manifest preflight")
            if actual != digest:
                raise ValueError(f"{symbol}: SHA256 disagrees with manifest; rebuild and review it")
    if len(bases) != 1:
        raise ValueError("Mixed symbol/benchmark adjustment bases are forbidden")
    return manifest


def load_1m_data(path, timestamp_kind, *, expected_sha256=None, naive_timezone="America/New_York"):
    """Retain invalid/missing observations; never silently drop a bad RTH row."""
    days = defaultdict(dict)
    errors = defaultdict(dict)
    untrusted_open = set()
    before = path.stat()
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{path}: required columns {sorted(required)}")
        for line, row in enumerate(reader, 2):
            try:
                start = normalize_minute_timestamp(row["timestamp"], timestamp_kind, naive_timezone)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{path}:{line}: invalid timestamp semantics: {exc}") from exc
            if not time(9, 30) <= start.time() < time(16):
                continue
            day = start.date().isoformat()
            try:
                o, h, l, c, v = (float(row[k]) for k in ("open", "high", "low", "close", "volume"))
                if not all(math.isfinite(x) for x in (o, h, l, c, v)):
                    raise ValueError("nonfinite OHLCV")
                if min(o, h, l, c) <= 0 or v < 0 or l > min(o, c) or h < max(o, c) or h < l:
                    raise ValueError("invalid OHLCV")
                bid = float(row["bid"]) if row.get("bid") else None
                ask = float(row["ask"]) if row.get("ask") else None
                if (bid is None) != (ask is None):
                    raise ValueError("partial quote")
                if bid is not None and (not math.isfinite(bid) or not math.isfinite(ask)
                                        or not 0 < bid <= ask):
                    raise ValueError("invalid quote")
                bar = Bar(start, start + MINUTE, o, h, l, c, v, bid, ask)
                if start in days[day] and days[day][start] != bar:
                    if days[day][start].o != bar.o:
                        untrusted_open.add(start)
                    raise ValueError("conflicting duplicate")
                if start not in untrusted_open:
                    days[day][start] = bar
            except (ValueError, TypeError) as exc:
                errors[day][start] = str(exc)
                # A malformed later H/L/C cannot veto a valid next-minute open
                # before that minute completes. Keep only an auditable opening
                # observation; NaNs are unavailable fields, never feature input.
                try:
                    opening_price = float(row["open"])
                    if math.isfinite(opening_price) and opening_price > 0 and start not in untrusted_open:
                        old = days[day].get(start)
                        if old is None:
                            days[day][start] = Bar(start, start+MINUTE, opening_price,
                                                  math.nan, math.nan, math.nan, math.nan)
                        elif old.o != opening_price:
                            untrusted_open.add(start)
                            days[day].pop(start, None)
                    elif not math.isfinite(opening_price) or opening_price <= 0:
                        untrusted_open.add(start)
                        days[day].pop(start, None)
                except (ValueError, TypeError):
                    untrusted_open.add(start)
                    days[day].pop(start, None)
                if start in untrusted_open:
                    days[day].pop(start, None)
    if expected_sha256 is not None:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b""):
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError(f"{path}: input changed while reading; collector was not interrupted")
        if digest.hexdigest() != expected_sha256:
            raise ValueError(f"{path}: SHA256 disagrees with externally verified manifest")
    return dict(days), dict(errors)


def exchange_sessions(first, last):
    try:
        import exchange_calendars as xc
    except ImportError as exc:
        raise RuntimeError("Runtime requires exchange_calendars; no weekday-only calendar fallback.") from exc
    # Calendar construction needs start < end and at least one session. Padding
    # also lets single-day/holiday-only validation return an exact empty slice.
    start_bound = (datetime.fromisoformat(first)-timedelta(days=7)).date().isoformat()
    end_bound = (datetime.fromisoformat(last)+timedelta(days=7)).date().isoformat()
    cal = xc.get_calendar("XNYS", start=start_bound, end=end_bound)
    schedule = cal.schedule
    open_col = "market_open" if "market_open" in schedule else "open"
    close_col = "market_close" if "market_close" in schedule else "close"
    sessions = {}
    for label, row in schedule.iterrows():
        day = str(label.date())
        if first <= day <= last:
            values = []
            for key in (open_col, close_col):
                stamp = row[key]
                if stamp.tzinfo is None:
                    stamp = stamp.tz_localize("UTC")
                values.append(stamp.to_pydatetime().astimezone(NY))
            sessions[day] = tuple(values)
    return sessions


def include_symbol_history(days, errors, evaluation_sessions):
    """Keep existing symbol warmup even when reference coverage starts later.

    Evaluation/fold dates remain the reference-derived dates, unchanged.
    """
    available = sorted(set(days) | set(errors))
    first, last = next(iter(evaluation_sessions)), next(reversed(evaluation_sessions))
    if available and available[0] < first:
        return exchange_sessions(available[0], last)
    return evaluation_sessions


def build_5m_bars(rows, opening, closing, errors):
    """Clock buckets, never positional chunks across a gap. End is availability."""
    bars = {}
    t = opening
    j = 0
    while t + 5 * MINUTE <= closing:
        chunk = [rows.get(t + k * MINUTE) for k in range(5)]
        if all(chunk) and not any(t + k * MINUTE in errors for k in range(5)):
            bars[j] = Bar(t, t + 5 * MINUTE, chunk[0].o, max(b.h for b in chunk),
                          min(b.l for b in chunk), chunk[-1].c, sum(b.v for b in chunk))
        t += 5 * MINUTE
        j += 1
    return bars


def align_completed_timeframe(features, decision_time):
    """Availability-asof alignment; equality means the bar has just completed."""
    i = bisect_right(features.ends, decision_time) - 1
    if i < 0 or decision_time - features.ends[i] >= 5 * MINUTE:
        return None
    return features.snapshots.get(i)


class SymbolData:
    def __init__(self, symbol, days, errors, sessions):
        self.symbol, self.days, self.errors, self.sessions = symbol, days, errors, sessions
        self.daily = {}
        self.daily_tr = {}
        previous_close = None
        for day, (opening, closing) in sessions.items():
            rows = days.get(day, {})
            bad = errors.get(day, {})
            n = int((closing - opening) / MINUTE)
            full = [rows.get(opening + i * MINUTE) for i in range(n)]
            if all(full) and not any(opening <= t < closing for t in bad):
                high, low = max(b.h for b in full), min(b.l for b in full)
                close, volume = full[-1].c, sum(b.v for b in full)
                self.daily[day] = dict(o=full[0].o, h=high, l=low, c=close, v=volume)
                if previous_close is not None:
                    self.daily_tr[day] = max(high-low, abs(high-previous_close), abs(low-previous_close))
                previous_close = close
            else:
                previous_close = None

    def eligibility(self, day):
        previous = [d for d in self.sessions if d < day]
        complete = [d for d in previous if d in self.daily]
        if len(complete) < 60 or not previous or previous[-1] not in self.daily:
            return "DAILY_HISTORY_UNAVAILABLE", {}
        recent_tr = [self.daily_tr.get(d) for d in previous[-14:]]
        recent_daily = [self.daily.get(d) for d in previous[-20:]]
        if len(recent_tr) != 14 or any(v is None for v in recent_tr) or any(v is None for v in recent_daily):
            return "DAILY_HISTORY_UNAVAILABLE", {}
        adv = st.median(b["c"] * b["v"] for b in recent_daily)
        prev = self.daily[previous[-1]]["c"]
        daily_atr = st.fmean(recent_tr)
        opening = self.sessions[day][0]
        first = self.days.get(day, {}).get(opening)
        if first is None or opening in self.errors.get(day, {}):
            return "OPEN_DATA_INVALID", {}
        gap = ratio(first.o-prev, daily_atr)
        details = dict(ADV20=adv, previousClose=prev, dailyATR=daily_atr, openingGap=gap)
        if gap is None:
            return "DAILY_ATR_UNAVAILABLE", details
        if prev < 5 or adv < 50_000_000:
            return "DAILY_LIQUIDITY_FAILED", details
        if abs(gap) > 2:
            return "GAP_EXCLUSION", details
        return None, details

    def warmup(self, day):
        """Only immediately preceding scheduled session; holes stay unavailable."""
        previous = [d for d in self.sessions if d < day]
        if not previous:
            return [], None
        prev = previous[-1]
        opening, closing = self.sessions[prev]
        bars = build_5m_bars(self.days.get(prev, {}), opening, closing, self.errors.get(prev, {}))
        previous_previous = self.daily.get(previous[-2]) if len(previous) > 1 else None
        prior_close = previous_previous["c"] if previous_previous else None
        trs = []
        for i in range(int((closing-opening)/(5*MINUTE))):
            bar = bars.get(i)
            trs.append(max(bar.h-bar.l, abs(bar.h-prior_close), abs(bar.l-prior_close))
                       if bar is not None and prior_close is not None else None)
            prior_close = bar.c if bar else None
        return trs[-14:], self.daily.get(prev, {}).get("c")

    @lru_cache(maxsize=24)
    def features(self, day):
        return compute_common_features(self, day)


class SessionFeatures:
    def __init__(self, data, day):
        self.data, self.day = data, day
        self.opening, self.closing = data.sessions[day]
        self.rows = data.days.get(day, {})
        self.errors = data.errors.get(day, {})
        self.bars = build_5m_bars(self.rows, self.opening, self.closing, self.errors)
        count = int((self.closing-self.opening)/(5*MINUTE))
        self.ends = [self.opening+(i+1)*5*MINUTE for i in range(count)]
        self.vwaps, self.cumvol, self.trs, self.atrs, self.snapshots = {}, {}, {}, {}, {}
        self.first_error_end = None

    def rvol(self, T, j):
        """Diagnostic only, computed from past sessions; not a trading gate."""
        cv = self.cumvol.get(T)
        previous = [d for d in self.data.sessions if d < self.day][-20:]
        expected = []
        elapsed = int((T-self.opening)/MINUTE)
        for day in previous:
            opening, closing = self.data.sessions[day]
            if opening+elapsed*MINUTE > closing:
                continue
            rows, errors = self.data.days.get(day, {}), self.data.errors.get(day, {})
            window = [rows.get(opening+i*MINUTE) for i in range(elapsed)]
            if all(window) and not any(opening <= t < opening+elapsed*MINUTE for t in errors):
                expected.append(sum(b.v for b in window))
        if len(expected) >= 10:
            return {"value": ratio(cv, st.median(expected)) if cv is not None else None,
                    "mode": "CUMULATIVE", "historySessions": len(expected)}
        prior = [self.bars.get(i) for i in range(j-10, j)]
        value = ratio(self.bars[j].v, st.median(b.v for b in prior)) if j in self.bars and all(prior) else None
        return {"value": value, "mode": "LOCAL5" if value is not None else "UNAVAILABLE"}


def compute_common_features(data, day):
    f = SessionFeatures(data, day)
    pv = volume = 0.0
    prefix_valid = True
    t = f.opening
    while t < f.closing:
        b = f.rows.get(t)
        if b is None or t in f.errors:
            prefix_valid = False
            if f.first_error_end is None:
                f.first_error_end = t + MINUTE
        if b is not None and prefix_valid:
            pv += (b.h+b.l+b.c)/3*b.v
            volume += b.v
            f.vwaps[b.end] = ratio(pv, volume)
            f.cumvol[b.end] = volume
        t += MINUTE
    warm, prior_close = data.warmup(day)
    trailing = list(warm)
    f.atrs[-1] = st.fmean(trailing) if len(trailing) == 14 and all(x is not None for x in trailing) else None
    for j, end in enumerate(f.ends):
        bar = f.bars.get(j)
        tr = (max(bar.h-bar.l, abs(bar.h-prior_close), abs(bar.l-prior_close))
              if bar is not None and prior_close is not None else None)
        f.trs[j] = tr
        trailing.append(tr)
        trailing = trailing[-14:]
        a = st.fmean(trailing) if len(trailing) == 14 and all(x is not None for x in trailing) else None
        f.atrs[j] = a
        prior_close = bar.c if bar else None
        vwap = f.vwaps.get(end)
        old_vwap = f.vwaps.get(end-15*MINUTE) if j >= 3 else None
        if bar is None or vwap is None or a is None or a <= 0 or old_vwap is None:
            continue
        er_bars = [f.bars.get(k) for k in range(j-12, j+1)]
        er = None
        if all(er_bars):
            path = sum(abs(er_bars[k].c-er_bars[k-1].c) for k in range(1, 13))
            er = abs(er_bars[-1].c-er_bars[0].c)/path if path else 0.0
        distance, slope = (bar.c-vwap)/a, (vwap-old_vwap)/a
        prior = f.bars.get(j-1)
        f.snapshots[j] = dict(index=j, timestamp=end, price=bar.c, ATR=a, VWAP=vwap,
                              distance=distance, slope=slope, ER=er,
                              return5=bar.c/prior.c-1 if prior else None,
                              UP=distance > 0 and slope >= 0,
                              BEAR=distance <= -0.50 and slope <= -0.15)
    return f


def market_context(references, T, sector_required):
    names = ["QQQ", "SPY"] + (["SOXX"] if sector_required else [])
    values = {name: align_completed_timeframe(references[name], T) for name in names}
    if not all(values.values()):
        return None
    q, spy = values["QQQ"], values["SPY"]
    mild = not q["BEAR"] and not spy["BEAR"]
    return dict(references=values, MARKET_UP=q["UP"] and not spy["BEAR"], MILD=mild,
                RECOVERY=mild and (q["distance"] > 0 or spy["distance"] > 0),
                SECTOR_UP=values["SOXX"]["UP"] if sector_required else True,
                SECTOR_NOT_BEAR=not values["SOXX"]["BEAR"] if sector_required else True,
                QQQ_NONNEGATIVE=q["return5"] is not None and q["return5"] >= 0)


def rs_at(f, benchmark, j, leverage, frozen_atr):
    rows = [f.bars.get(j), f.bars.get(j-3), benchmark.bars.get(j), benchmark.bars.get(j-3)]
    if not all(rows) or frozen_atr is None or frozen_atr <= 0:
        return None
    a, old_a, b, old_b = rows
    residual = a.c/old_a.c-1 - leverage*(b.c/old_b.c-1)
    return dict(raw=residual, normalized=residual/(frozen_atr/old_a.c))


class StrategyState:
    def __init__(self, strategy, symbol, day, f, references, benchmark):
        self.strategy, self.symbol, self.day = strategy, symbol, day
        self.f, self.references, self.benchmark = f, references, benchmark
        self.leverage = benchmark_for_symbol(symbol)[1]
        self.sector_required = symbol in SEMI and strategy != "S2"
        self.state, self.event, self.transitions = "IDLE", {}, []
        self.trade = None
        self.pending_exits = set()
        self.stall_checked = False
        self.duplicates = []
        self.terminal_observations_skipped = 0
        self.eligibility_reason, self.eligibility = f.data.eligibility(day)
        self.force = f.closing-10*MINUTE

    def transition(self, state, T, reason, **values):
        self.transitions.append(dict(timestamp=iso(T), previousState=self.state, nextState=state,
                                     reason=reason, values=values))
        self.state = state

    def cancel(self, T, reason, **values):
        self.transition("CANCELLED", T, reason, **values)

    def context(self, T):
        return market_context(self.references, T, self.sector_required)

    def trigger_context(self, ctx):
        if ctx is None:
            return False
        if self.strategy == "S2":
            return ctx["RECOVERY"]
        return ctx["MARKET_UP"] and ctx["SECTOR_UP"] and (
            self.strategy != "S3" or ctx["QQQ_NONNEGATIVE"])

    def hold_rs(self, j):
        vals = [rs_at(self.f, self.benchmark, k, self.leverage, self.event["A"])
                for k in range(j-2, j+1)]
        return vals if all(vals) else None

    def setup(self, T, j, **event):
        self.event = dict(event, setup_index=j, setup_time=T, A=self.f.atrs[j-1])
        self.transition("SETUP", T, "FIRST_SETUP", **self.event)

    def arm(self, T, **event):
        self.event.update(event, armed_time=T)
        self.transition("ARMED", T, "ARMED", **event)

    def trigger(self, T, j, bar, stop, target, breakout, **diagnostics):
        self.event.update(trigger_time=T, trigger_j=j, trigger_close=bar.c,
                          trigger_vwap=self.f.vwaps[T], stop=stop, target=target,
                          breakout=breakout, diagnostics=diagnostics, context=self.context(T))
        self.transition("TRIGGERED", T, "TRIGGER", stop=stop, target=target,
                        breakout=breakout, **diagnostics)

    def on_close(self, bar):
        T = bar.end
        j = int((T-self.f.opening)/(5*MINUTE))-1
        five_close = (T-self.f.opening) % (5*MINUTE) == timedelta(0)
        if self.state in ("EXIT", "CANCELLED"):
            self.terminal_observations_skipped += 1
            # Probe only the frozen IDLE->SETUP predicate, never re-arm or trade.
            # These are skipped setup signals, not claimed completed triggers.
            if five_close and self.event and self.eligibility_reason is None and self.f.vwaps.get(T) is not None:
                probe = StrategyState(self.strategy, self.symbol, self.day, self.f,
                                      self.references, self.benchmark)
                probe.on_close(bar)
                if probe.state == "SETUP":
                    self.duplicates.append(dict(timestamp=iso(T), reason="DAILY_ATTEMPT_LOCKED",
                                                signalStage="SETUP", firstSetup=iso(self.event["setup_time"])))
            return
        if self.state == "MANAGING":
            self.after_position_close(bar, j, five_close)
            return
        if self.state == "TRIGGERED":
            raise RuntimeError("TRIGGERED must be handled at the next open, not another close")
        begin, end = WINDOWS[self.strategy]
        if self.eligibility_reason:
            self.cancel(T, self.eligibility_reason, **self.eligibility)
            return
        if T.time() >= end or T >= self.force or T.time() >= time(15, 15):
            self.cancel(T, "NO_SETUP" if self.state == "IDLE" else "ENTRY_WINDOW_CLOSED")
            return
        if T.time() < begin:
            return
        required_names = ["QQQ", "SPY"] + (["SOXX"] if self.sector_required else [])
        for name in required_names:
            rf = self.references[name]
            elapsed = int((T-rf.opening)/(5*MINUTE))
            snapshot_end = rf.opening + elapsed*5*MINUTE
            if rf.first_error_end is not None and rf.first_error_end <= snapshot_end:
                self.cancel(T, "REFERENCE_DATA_INVALID", reference=name,
                            errorAvailableAt=iso(rf.first_error_end))
                return
        if bar.bid is not None and (bar.ask-bar.bid)/((bar.ask+bar.bid)/2) > 0.005:
            self.cancel(T, "SPREAD_EXCESSIVE")
            return
        ctx = self.context(T)
        if ctx is None:
            if self.state != "IDLE":
                self.cancel(T, "REFERENCE_UNAVAILABLE")
            return
        if self.state == "IDLE" and (not five_close or self.f.atrs.get(j-1) is None or self.f.atrs[j-1] <= 0):
            return
        if self.strategy == "S1":
            evaluate_s1(self, bar, j, five_close, ctx)
        elif self.strategy == "S2":
            evaluate_s2(self, bar, j, five_close, ctx)
        else:
            evaluate_s3(self, bar, j, five_close, ctx)

    def on_open(self, bar):
        if self.state == "TRIGGERED":
            self.enter(bar)
        if self.state == "MANAGING":
            self.manage_open(bar)

    def on_intrabar(self, bar):
        if self.state == "MANAGING":
            self.manage_range(bar)

    def enter(self, bar):
        e = self.event
        T = bar.start
        if T != e["trigger_time"]:
            self.cancel(T, "MISSING_NEXT_OPEN")
            return
        start, end = WINDOWS[self.strategy]
        if not start <= T.time() < end or T >= self.force or T.time() >= time(15, 15):
            self.cancel(T, "ENTRY_WINDOW_CLOSED")
            return
        ctx = self.context(T)
        if not self.trigger_context(ctx):
            self.cancel(T, "ENTRY_REFERENCE_FAILED")
            return
        if self.strategy == "S3":
            values = self.hold_rs(e["trigger_j"])
            if values is None or any(v["normalized"] < 0.25 for v in values):
                self.cancel(T, "ENTRY_RS_FAILED")
                return
        floor = max(e["origin"], e["trigger_vwap"]) if self.strategy == "S1" else (
            e["orl"] if self.strategy == "S2" else e["bh"])
        if bar.o <= floor:
            self.cancel(T, "ENTRY_STRUCTURE_FAILED", open=bar.o, floor=floor)
            return
        entry = bar.o*(1+SLIP)
        risk = entry-e["stop"]
        target = entry+2*risk if self.strategy == "S3" else e["target"]
        q = PARAMS[self.strategy]["entry_chase_atr"]
        if entry > e["breakout"]+q*e["A"] or entry > e["trigger_close"]+0.30*e["A"]:
            self.cancel(T, "ENTRY_CHASE", fill=entry, breakout=e["breakout"])
            return
        if risk <= 0 or target <= entry or (target*(1-SLIP)-entry)/risk < 1:
            self.cancel(T, "INSUFFICIENT_REWARD", fill=entry, target=target, risk=risk)
            return
        benchmark, _ = benchmark_for_symbol(self.symbol)
        relative = rs_at(self.f, self.benchmark, e["trigger_j"], self.leverage, e["A"]) if self.benchmark else None
        self.trade = dict(strategy=self.strategy, symbol=self.symbol, date=self.day,
                          setupTimestamp=iso(e["setup_time"]), armedTimestamp=iso(e["armed_time"]),
                          triggerTimestamp=iso(e["trigger_time"]), entryTimestamp=iso(T),
                          exitTimestamp=None, entryPrice=entry, rawEntryPrice=bar.o,
                          exitPrice=None, stopPrice=e["stop"], targetPrice=target,
                          grossR=None, netR=None, exitReason=None, riskPerShare=risk,
                          marketRegime=ctx, benchmark=benchmark,
                          sectorBenchmark="SOXX" if self.symbol in SEMI else None,
                          sectorConfirmationRequired=self.sector_required,
                          relativeStrengthBenchmarkAvailable=self.benchmark is not None,
                          direction="LONG", sector="SEMICONDUCTOR" if self.symbol in SEMI else "TECH",
                          economicGroup="TECH_LEVERAGED" if self.symbol == "TQQQ" else
                          "SEMI_LEVERAGED" if self.symbol == "SOXL" else self.symbol,
                          ATR=e["A"], VWAP=e["trigger_vwap"],
                          RVOL=self.f.rvol(e["trigger_time"], e["trigger_j"]),
                          relativeStrength=relative, diagnostics=e["diagnostics"],
                          eligibility=self.eligibility, spreadGate="OBSERVED" if self.f.rows[e["trigger_time"]-MINUTE].bid is not None else "DISABLED",
                          eventId=f"{self.day}/{self.symbol}/{self.strategy}/{iso(e['setup_time'])}",
                          anchorStart=iso(e["anchor_start"]), anchorEnd=iso(e["trigger_time"]),
                          breakoutLevel=e["breakout"], mfeR=0.0, maeR=0.0)
        self.transition("ENTRY", T, "FILL", entry=entry, risk=risk)
        self.transition("MANAGING", T, "POSITION_OPEN")

    def finish(self, T, raw_exit, reason, interval_end=None):
        tr = self.trade
        tr["exitTimestamp"] = iso(T)
        tr["exitReason"] = reason
        tr["exitIntervalEnd"] = iso(interval_end)
        if raw_exit is not None:
            tr["rawExitPrice"] = raw_exit
            tr["exitPrice"] = raw_exit*(1-SLIP)
            tr["grossR"] = (raw_exit-tr["rawEntryPrice"])/tr["riskPerShare"]
            tr["netR"] = (tr["exitPrice"]-tr["entryPrice"])/tr["riskPerShare"]
            tr["slippageCostR"] = tr["grossR"]-tr["netR"]
            # The exit price itself is observed/proven by the execution model.
            # Do not attribute the rest of an exit candle's extrema to the trade.
            tr["mfeR"] = max(tr["mfeR"], max(raw_exit-tr["entryPrice"], 0)/tr["riskPerShare"])
            tr["maeR"] = max(tr["maeR"], max(tr["entryPrice"]-raw_exit, 0)/tr["riskPerShare"])
        else:
            tr["rawExitPrice"] = tr["slippageCostR"] = None
        self.transition("EXIT", T, reason, exitPrice=tr["exitPrice"])

    def data_error(self, T, reason):
        if self.state == "MANAGING":
            self.finish(T, None, "UNRESOLVED_DATA")
        elif self.state == "TRIGGERED":
            self.cancel(T, "MISSING_NEXT_OPEN", detail=reason)
        elif self.state not in ("EXIT", "CANCELLED"):
            self.cancel(T, "DATA_INVALID", detail=reason)

    def manage_open(self, bar):
        tr = self.trade
        entry_time = datetime.fromisoformat(tr["entryTimestamp"])
        max_hold = PARAMS[self.strategy].get("max_hold_minutes", 60)
        if bar.start >= self.force:
            self.pending_exits.add("FORCE_EXIT")
        if bar.start >= entry_time+max_hold*MINUTE:
            self.pending_exits.add("MAX_HOLD")
        if bar.o <= tr["stopPrice"]:
            self.finish(bar.start, bar.o, "STOP_GAP")
            return
        if self.pending_exits:
            reason = next(r for r in ("FORCE_EXIT", "MAX_HOLD", "STRUCTURE_EXIT", "STALL_EXIT")
                          if r in self.pending_exits)
            self.finish(bar.start, bar.o, reason)
            return

    def manage_range(self, bar):
        tr = self.trade
        # OHLC does not reveal intrabar times. Timestamp is this minute's start;
        # exitIntervalEnd makes that uncertainty explicit, never a made-up tick.
        if bar.l <= tr["stopPrice"]:
            self.finish(bar.start, tr["stopPrice"], "STOP", bar.end)
        elif bar.h >= tr["targetPrice"]:
            self.finish(bar.start, tr["targetPrice"], "TARGET", bar.end)

    def after_position_close(self, bar, j, five_close):
        tr = self.trade
        tr["mfeR"] = max(tr["mfeR"], max(bar.h-tr["entryPrice"], 0)/tr["riskPerShare"])
        tr["maeR"] = max(tr["maeR"], max(tr["entryPrice"]-bar.l, 0)/tr["riskPerShare"])
        if self.strategy == "S1" and not self.stall_checked:
            if bar.end >= datetime.fromisoformat(tr["entryTimestamp"])+20*MINUTE:
                self.stall_checked = True
                if tr["mfeR"] < 0.30:
                    self.pending_exits.add("STALL_EXIT")
        if self.strategy == "S3" and five_close and bar.c < self.event["bh"]-0.10*self.event["A"]:
            self.pending_exits.add("STRUCTURE_EXIT")


def evaluate_s1(s, bar, j, five_close, ctx):
    f, e, p = s.f, s.event, PARAMS["S1"]
    T = bar.end
    confirmed = ctx["MARKET_UP"] and ctx["SECTOR_UP"]
    if s.state == "IDLE":
        if not confirmed or j < 8 or any(k not in f.bars for k in range(j-8, j+1)):
            return
        impulse = [f.bars[k] for k in range(j-2, j+1)]
        origin, last, A = impulse[0].o, impulse[-1].c, f.atrs[j-1]
        prior_high = max(f.bars[k].h for k in range(j-8, j-2))
        if last-origin >= p["impulse_atr"]*A and last > prior_high+0.10*A and last > f.vwaps[T]:
            s.setup(T, j, origin=origin, end_close=last, advance=last-origin,
                    impulse_high=max(b.h for b in impulse), impulse_volume=sum(b.v for b in impulse),
                    pull_low=None, anchor_start=impulse[0].start)
        return
    e["pull_low"] = bar.l if e["pull_low"] is None else min(e["pull_low"], bar.l)
    retrace = (e["end_close"]-e["pull_low"])/e["advance"]
    if retrace > p["pullback_max"] or bar.c <= max(e["origin"], f.vwaps.get(T, math.inf)):
        s.cancel(T, "PULLBACK_STRUCTURE_FAILED", retracement=retrace)
        return
    if not confirmed:
        s.cancel(T, "CONTEXT_FAILED")
        return
    pull = [f.bars[k] for k in range(e["setup_index"]+1, j+1)] if five_close else []
    contraction = ratio(sum(b.v for b in pull)/(5*len(pull)), e["impulse_volume"]/15) if pull else None
    if five_close and pull and contraction is None:
        s.cancel(T, "FEATURE_UNAVAILABLE")
        return
    if s.state == "SETUP":
        if five_close:
            if 0.20 <= retrace <= p["pullback_max"] and contraction <= p["pullback_volume_max"]:
                s.arm(T, pivot=f.bars[j].h, retracement=retrace, contraction=contraction)
            elif j-e["setup_index"] >= 3:
                s.cancel(T, "PULLBACK_TIMEOUT")
        return
    if five_close and pull and contraction > p["pullback_volume_max"]:
        s.cancel(T, "PULLBACK_VOLUME_FAILED", contraction=contraction)
        return
    previous_minutes = [f.rows.get(bar.start-k*MINUTE) for k in range(1, 21)]
    tv = ratio(bar.v, st.median(b.v for b in previous_minutes)) if all(previous_minutes) else None
    if tv is None:
        s.cancel(T, "FEATURE_UNAVAILABLE", feature="TV1")
        return
    if T <= e["armed_time"]+5*MINUTE and bar.c > e["pivot"]+0.10*e["A"] and tv >= p["trigger_volume_min"]:
        s.trigger(T, j, bar, e["pull_low"]-0.10*e["A"], e["impulse_high"], e["pivot"]+0.10*e["A"],
                  pivot=e["pivot"], pullbackLow=e["pull_low"], retracement=retrace,
                  contraction=contraction, triggerVolumeRatio=tv)
    elif T >= e["armed_time"]+5*MINUTE:
        s.cancel(T, "TRIGGER_TIMEOUT")


def evaluate_s2(s, bar, j, five_close, ctx):
    f, e, p, T = s.f, s.event, PARAMS["S2"], bar.end
    if s.state == "IDLE":
        opening = [f.rows.get(f.opening+k*MINUTE) for k in range(15)]
        if not all(opening) or not ctx["MILD"]:
            return
        orh, orl = max(b.h for b in opening), min(b.l for b in opening)
        width = (orh-orl)/f.atrs[j-1]
        if 0.50 <= width <= 2.50:
            s.setup(T, j, orh=orh, orl=orl, orm=(orh+orl)/2, anchor_start=f.opening)
        return
    if not ctx["MILD"]:
        s.cancel(T, "MARKET_BEARISH")
        return
    if s.state == "SETUP":
        if bar.l < e["orl"]-p["break_max"]*e["A"]:
            s.cancel(T, "EXCESSIVE_PENETRATION")
        elif five_close:
            breakdown = f.bars[j]
            penetration = (e["orl"]-breakdown.l)/e["A"]
            if breakdown.c < e["orl"] and p["break_min"] <= penetration <= p["break_max"]:
                s.arm(T, failure_low=breakdown.l, breakdown_end=T, breakdown_index=j,
                      penetration=penetration, breakdown_volume=breakdown.v)
        return
    if bar.l < e["failure_low"]:
        s.cancel(T, "SECOND_LOWER_LOW", failureLow=e["failure_low"], low=bar.l)
        return
    if not five_close:
        return
    age = j-e["breakdown_index"]
    if bar.c > e["orl"]+0.10*e["A"]:
        if not ctx["RECOVERY"]:
            s.cancel(T, "RECLAIM_WITHOUT_RECOVERY")
        elif 1 <= age <= p["reclaim_max_bars"]:
            s.trigger(T, j, bar, e["failure_low"]-0.10*e["A"], e["orm"], e["orl"]+0.10*e["A"],
                      breakdownTimestamp=iso(e["breakdown_end"]), breakdownLow=e["failure_low"],
                      reclaimTimestamp=iso(T), barsSinceBreakdown=age, marketRecovery=ctx["RECOVERY"],
                      breakdownVolume=e["breakdown_volume"], reclaimVolume=f.bars[j].v,
                      ORHigh=e["orh"], ORLow=e["orl"], ORMid=e["orm"])
    if s.state == "ARMED" and age >= p["reclaim_max_bars"]:
        s.cancel(T, "RECLAIM_TIMEOUT", barsSinceBreakdown=age)


def evaluate_s3(s, bar, j, five_close, ctx):
    f, e, p, T = s.f, s.event, PARAMS["S3"], bar.end
    if s.state == "IDLE":
        if j < 17 or not ctx["MILD"] or not ctx["SECTOR_NOT_BEAR"]:
            return
        if any(k not in f.bars for k in range(j-17, j+1)) or any(f.trs.get(k) is None for k in range(j-17, j+1)):
            return
        A = f.atrs[j-1]
        bh = max(f.bars[k].h for k in range(j-5, j+1))
        bl = min(f.bars[k].l for k in range(j-5, j+1))
        contraction = ratio(st.fmean(f.trs[k] for k in range(j-2, j+1)),
                            st.fmean(f.trs[k] for k in range(j-17, j-5)))
        rs = rs_at(f, s.benchmark, j, s.leverage, A)
        if contraction is not None and rs and (bh-bl)/A <= p["box_width_max"] and contraction <= p["compression_max"] and rs["normalized"] >= p["rs_min"] and bar.c >= f.vwaps[T]:
            s.setup(T, j, bh=bh, bl=bl, compression=contraction, box_width=(bh-bl)/A,
                    anchor_start=f.bars[j-5].start)
        return
    if bar.l < e["bl"]:
        s.cancel(T, "BOX_LOW_BROKEN")
        return
    if not five_close:
        return
    rs = rs_at(f, s.benchmark, j, s.leverage, e["A"])
    if rs is None:
        s.cancel(T, "FEATURE_UNAVAILABLE", feature="RS15")
        return
    if rs["normalized"] < p["rs_min"] or bar.c < f.vwaps[T] or not ctx["MILD"] or not ctx["SECTOR_NOT_BEAR"]:
        s.cancel(T, "HOLD_FAILED", relativeStrength=rs)
        return
    age = j-e["setup_index"]
    values = s.hold_rs(j)
    held = values is not None and all(v["normalized"] >= p["rs_min"] for v in values)
    if s.state == "SETUP":
        if bar.c > e["bh"]:
            s.cancel(T, "EARLY_ESCAPE")
        elif held and f.bars[j].l >= e["bl"]:
            s.arm(T, relativeStrengthHold=values)
    else:
        prior = [f.bars.get(k) for k in range(j-10, j)]
        ev = ratio(f.bars[j].v, st.median(b.v for b in prior)) if all(prior) else None
        if ev is None or values is None:
            s.cancel(T, "FEATURE_UNAVAILABLE", feature="EV5/RS_HOLD")
            return
        breakout = bar.c > e["bh"]+0.10*e["A"]
        if breakout and ev >= p["expansion_volume_min"] and held and s.trigger_context(ctx):
            s.trigger(T, j, bar, e["bl"]-0.10*e["A"], None, e["bh"]+0.10*e["A"],
                      boxHigh=e["bh"], boxLow=e["bl"], compression=e["compression"],
                      boxWidthATR=e["box_width"], expansionVolumeRatio=ev, relativeStrengthHold=values)
        elif bar.c > e["bh"]:
            s.cancel(T, "UNCONFIRMED_ESCAPE", expansionVolumeRatio=ev, held=held)
    if s.state in ("SETUP", "ARMED") and age >= p["wait_max_bars"]:
        s.cancel(T, "BOX_TIMEOUT", barsSinceSetup=age)


def run_session(symbol, day, strategies, f, references, benchmark):
    states = [StrategyState(name, symbol, day, f, references, benchmark) for name in strategies]
    t = f.opening
    while t < f.closing:
        bar = f.rows.get(t)
        if bar is None:
            for s in states:
                s.data_error(t, f.errors.get(t, "MISSING_MINUTE"))
        else:
            for s in states:
                # open and close are separate phases: no bar H/L/C entry guard.
                s.on_open(bar)
                if t in f.errors:
                    s.data_error(bar.end, f.errors[t])
                else:
                    s.on_intrabar(bar)
                    s.on_close(bar)
        t += MINUTE
    for s in states:
        if s.state == "MANAGING":
            s.finish(f.closing, None, "UNRESOLVED_DATA")
        elif s.state == "TRIGGERED":
            s.cancel(f.closing, "MISSING_NEXT_OPEN")
        elif s.state not in ("EXIT", "CANCELLED"):
            s.cancel(f.closing, "NO_SETUP" if s.state == "IDLE" else "SESSION_END")
    return states


def metrics(trades):
    completed = sorted((t for t in trades if t["netR"] is not None),
                       key=lambda t: (t["exitTimestamp"], t["symbol"], t["strategy"]))
    values = [t["netR"] for t in completed]
    wins, losses = [v for v in values if v > 0], [v for v in values if v < 0]
    balance = peak = drawdown = 0.0
    for value in values:
        balance += value
        peak = max(peak, balance)
        drawdown = max(drawdown, peak-balance)
    unresolved = len(trades)-len(values)
    return dict(trades=len(values), unresolvedTrades=unresolved,
                performanceValid=unresolved == 0,
                metricScope="RESOLVED_TRADES_ONLY" if unresolved else "ALL_RECORDED_TRADES",
                wins=len(wins),
                losses=len(losses), breakeven=len(values)-len(wins)-len(losses),
                winRate=len(wins)/len(values) if values else None,
                expectancyR=st.fmean(values) if values else None,
                profitFactorR=sum(wins)/abs(sum(losses)) if losses else None,
                profitFactorStatus="FINITE" if losses else "NO_LOSSES" if wins else "NO_TRADES_OR_BREAKEVEN",
                maxDrawdownR=drawdown, averageWinR=st.fmean(wins) if wins else None,
                averageLossR=-st.fmean(losses) if losses else None, netR=sum(values))


def time_bucket(trade):
    t = datetime.fromisoformat(trade["entryTimestamp"]).time()
    for end, name in ((time(10), "09:30-10:00"), (time(11), "10:00-11:00"),
                      (time(12), "11:00-12:00"), (time(14), "12:00-14:00"),
                      (time(15), "14:00-15:00"), (time(16), "15:00-16:00")):
        if t < end:
            return name
    raise ValueError("Entry outside session")


def grouped_metrics(trades, key):
    groups = defaultdict(list)
    for trade in trades:
        groups[key(trade)].append(trade)
    return {name: metrics(group) for name, group in sorted(groups.items())}


def chronological_folds(trades, session_days):
    """Frozen 45/60/75/100% session splits; no parameter fitting or selection."""
    n = len(session_days)
    cuts = [int(n*0.45), int(n*0.60), int(n*0.75), n]
    if n < 4 or not (0 < cuts[0] < cuts[1] < cuts[2] < n):
        return dict(status="INSUFFICIENT_SESSIONS_FOR_THREE_FOLDS", folds=[])
    folds = []
    for i in range(3):
        train_days = set(session_days[:cuts[i]])
        oos_days = set(session_days[cuts[i]:cuts[i+1]])
        train = [t for t in trades if t["date"] in train_days]
        oos = [t for t in trades if t["date"] in oos_days]
        folds.append(dict(fold=i+1, trainStart=session_days[0], trainEnd=session_days[cuts[i]-1],
                          oosStart=session_days[cuts[i]], oosEnd=session_days[cuts[i+1]-1],
                          train=metrics(train), OOS=metrics(oos),
                          trainByStrategy=grouped_metrics(train, lambda t: t["strategy"]),
                          oosByStrategy=grouped_metrics(oos, lambda t: t["strategy"]),
                          oosBySymbol=grouped_metrics(oos, lambda t: t["symbol"])))
    return dict(status="EVALUATION_ONLY", folds=folds)


def overlap_report(decisions, trades):
    """Signal overlap, including guard-cancelled triggers; no allocation."""
    days = defaultdict(dict)
    for decision in decisions:
        days[(decision["date"], decision["symbol"])][decision["strategy"]] = decision
    output = []
    for (day, symbol), strategies in sorted(days.items()):
        if not {"S1", "S3"}.issubset(strategies):
            continue
        a, b = (strategies[name].get("triggerTimestamp") for name in ("S1", "S3"))
        delta = (datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds()/60 if a and b else None
        output.append(dict(date=day, symbol=symbol, classification="BOTH" if a and b else
                           "S1_ONLY" if a else "S3_ONLY" if b else "NEITHER",
                           s1Trigger=a, s3Trigger=b, signedTriggerDifferenceMinutes=delta,
                           absoluteTriggerDifferenceMinutes=abs(delta) if delta is not None else None))
    pairs = []
    grouped = defaultdict(list)
    for trade in trades:
        grouped[(trade["date"], trade["symbol"])].append(trade)
    for group in grouped.values():
        for i, a in enumerate(group):
            for b in group[i+1:]:
                delta = abs((datetime.fromisoformat(a["triggerTimestamp"])-datetime.fromisoformat(b["triggerTimestamp"])).total_seconds())/60
                anchor_start = max(datetime.fromisoformat(a["anchorStart"]), datetime.fromisoformat(b["anchorStart"]))
                anchor_end = min(datetime.fromisoformat(a["anchorEnd"]), datetime.fromisoformat(b["anchorEnd"]))
                kinds = []
                if delta <= 10 and (anchor_end-anchor_start).total_seconds() >= 60:
                    kinds.append("SAME_EVENT_DUPLICATE")
                if delta <= 10 and a["economicGroup"] == b["economicGroup"]:
                    kinds.append("RELATED_INSTRUMENT")
                if a["exitTimestamp"] and b["exitTimestamp"] and max(a["entryTimestamp"], b["entryTimestamp"]) < min(a["exitTimestamp"], b["exitTimestamp"]):
                    kinds.append("CONCURRENT_EXPOSURE")
                if kinds:
                    pairs.append(dict(a=a["eventId"], b=b["eventId"], kinds=kinds))
    return dict(dailyS1S3=output, duplicatePairs=pairs, allocation="NOT_IMPLEMENTED")


def json_safe(value):
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite result; no Infinity/NaN in output")
    return value


def parse_args():
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, default=root/"data/toss_1m")
    p.add_argument("--out", type=Path, default=root/"research/backtest_v4_strategies_result.json")
    p.add_argument("--strategies", default="S1,S2,S3")
    p.add_argument("--symbols", help="Comma-separated frozen LONG instruments; default: universe_v3.json subset")
    p.add_argument("--timestamp-kind", choices=("start", "end"), help="Required runtime confirmation of raw timestamp meaning")
    p.add_argument("--confirm-price-adjustment", action="store_true", help="Confirm OHLC price units/split adjustments are consistent")
    p.add_argument("--data-manifest", "--manifest", dest="data_manifest", type=Path, help="Required externally reviewed timestamp/adjustment provenance and per-file SHA256")
    return p.parse_args()


def main():
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    strategies = list(dict.fromkeys(args.strategies.upper().split(",")))
    if not strategies or any(s not in PARAMS for s in strategies):
        raise ValueError("--strategies must select S1,S2,S3")
    universe = json.loads((root/"research/universe_v3.json").read_text())["symbols"]
    symbols = list(dict.fromkeys(s.strip().upper() for s in args.symbols.split(","))) if args.symbols else [s for s in universe if s in TRADABLE]
    if not symbols or any(s not in TRADABLE or s not in universe for s in symbols):
        raise ValueError("Select frozen LONG stock/TQQQ/SOXL symbols only; inverse ETF rules are not implemented")
    if args.timestamp_kind is None or not args.confirm_price_adjustment:
        raise ValueError("Specify --timestamp-kind start|end and --confirm-price-adjustment after verifying source conventions")
    required = {"QQQ", "SPY"}
    if any(s in SEMI for s in symbols) and any(s in ("S1", "S3") for s in strategies):
        required.add("SOXX")
    # S2 sector/RS are diagnostics only; do not make SOXX mandatory for S2.
    manifest = validate_data_manifest(args.data_manifest, required | set(symbols), args.timestamp_kind,
                                      data_dir=args.data_dir)

    def load_verified(symbol):
        meta = manifest["symbols"][symbol]
        return load_1m_data(manifest_input_path(args.data_dir, symbol, meta), args.timestamp_kind,
                            expected_sha256=meta["sha256"], naive_timezone=meta["naive_timezone"])

    raw_refs = {s: load_verified(s) for s in required}
    dates = sorted({d for days, errors in raw_refs.values() for d in set(days) | set(errors)})
    if not dates:
        raise ValueError("Required references contain no minute data")
    sessions = exchange_sessions(dates[0], dates[-1])
    references = {s: SymbolData(s, *raw_refs[s], sessions) for s in required}
    raw_refs.clear()
    trades, decisions, transitions, skipped_signals = [], [], [], []
    coverage = {}
    for symbol in symbols:
        raw = load_verified(symbol)
        symbol_sessions = include_symbol_history(*raw, sessions)
        data = SymbolData(symbol, *raw, symbol_sessions)
        available_dates = sorted(set(raw[0]) | set(raw[1]))
        coverage[symbol] = dict(availableDataStart=available_dates[0] if available_dates else None,
                               availableDataEnd=available_dates[-1] if available_dates else None,
                               historyCalendarStart=next(iter(symbol_sessions)),
                               evaluationStart=next(iter(sessions)),
                               evaluationEnd=next(reversed(sessions)))
        benchmark_name, _ = benchmark_for_symbol(symbol)
        for day in sessions:
            f = data.features(day)
            ref_features = {name: ref.features(day) for name, ref in references.items()}
            benchmark = ref_features.get(benchmark_name)
            for state in run_session(symbol, day, strategies, f, ref_features, benchmark):
                if state.trade:
                    trades.append(state.trade)
                decisions.append(dict(strategy=state.strategy, symbol=symbol, date=day,
                                      state=state.state, reason=state.transitions[-1]["reason"],
                                      setupTimestamp=iso(state.event.get("setup_time")),
                                      armedTimestamp=iso(state.event.get("armed_time")),
                                      triggerTimestamp=iso(state.event.get("trigger_time")),
                                      firstAttemptLocked=bool(state.event),
                                      terminalObservationsSkipped=state.terminal_observations_skipped,
                                      skippedDuplicateSetupSignals=len(state.duplicates),
                                      eligibility=state.eligibility))
                skipped_signals.extend(dict(strategy=state.strategy, symbol=symbol, date=day, **item)
                                       for item in state.duplicates)
                transitions.extend(dict(strategy=state.strategy, symbol=symbol, date=day, **tr)
                                   for tr in state.transitions)
        print(f"{symbol}: processed {len(sessions)} sessions", flush=True)
        data.features.cache_clear()
    result = dict(specification="strategy_spec_v4.md@c9e12ce", parameters=PARAMS,
                  dataCoverage=coverage,
                  dataVerification=dict(manifestPath=str(args.data_manifest),
                                        verifiedBy=manifest["verified_by"],
                                        verificationNote=manifest["verification_note"],
                                        symbols={s: manifest["symbols"][s] for s in sorted(required | set(symbols))}),
                  selectedStrategies=strategies, selectedSymbols=symbols,
                  assumptions=dict(timestampKind=args.timestamp_kind, calendar="XNYS",
                                   priceAdjustmentConfirmed=True, slippageBpsPerSide=2,
                                   commission=0, otherFees=0, firstSetupAttemptPerDay=True,
                                   grossR="raw price difference / modeled initial risk",
                                   intrabarExitTimestamp="minute start; see exitIntervalEnd",
                                   excursionConvention="completed surviving bars plus proven exit price; exit-candle extrema are uncertain",
                                   combinedMetrics="sum of independent unit-R experiments, not portfolio performance"),
                  referenceBenchmark="MR009_REFERENCE skipped; compare existing V3 result separately (different execution contract)",
                  metrics=metrics(trades), byStrategy=grouped_metrics(trades, lambda t: t["strategy"]),
                  bySymbol=grouped_metrics(trades, lambda t: t["symbol"]),
                  bySymbolStrategy=grouped_metrics(trades, lambda t: t["symbol"]+"/"+t["strategy"]),
                  byYear=grouped_metrics(trades, lambda t: t["date"][:4]),
                  byMonth=grouped_metrics(trades, lambda t: t["date"][:7]),
                  byStrategyYear=grouped_metrics(trades, lambda t: t["strategy"]+"/"+t["date"][:4]),
                  byStrategyMonth=grouped_metrics(trades, lambda t: t["strategy"]+"/"+t["date"][:7]),
                  byTimeOfDay=grouped_metrics(trades, time_bucket),
                  byStrategyTimeOfDay=grouped_metrics(trades, lambda t: t["strategy"]+"/"+time_bucket(t)),
                  walkForward=chronological_folds(trades, list(sessions)),
                  overlap=overlap_report(decisions, trades),
                  funnel=dict(Counter(t["nextState"] for t in transitions)),
                  cancellationReasons=dict(Counter(d["reason"] for d in decisions if d["state"] == "CANCELLED")),
                  trades=trades, decisions=decisions, transitions=transitions,
                  skippedDuplicateSetupSignals=skipped_signals,
                  significance="No statistical significance or profitability claim")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(json_safe(result), ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    print("strategy trades winRate expectancyR PF maxDD_R")
    if not result["metrics"]["performanceValid"]:
        print("INCOMPLETE PERFORMANCE: unresolved trades excluded; conditional metrics cannot validate a strategy")
    for name in strategies:
        m = metrics([t for t in trades if t["strategy"] == name])
        print(name, m["trades"], m["winRate"], m["expectancyR"], m["profitFactorR"], m["maxDrawdownR"])
    for fold in result["walkForward"]["folds"]:
        print(f"OOS fold {fold['fold']}: {fold['oosStart']}..{fold['oosEnd']} {fold['OOS']}")
    print(f"Result: {args.out}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(f"V4 backtest refused: {exc}") from exc
