"""Read-only, bounded research windows. No numeric OHLCV decoding beyond 2025.

Mixed-year CSV bytes may be hashed and timestamp labels scanned for integrity;
2026 prices are never converted to bars, features, policy inputs or outcomes.
"""
import csv
import gzip
import heapq
import math
from datetime import date

from research.backtest_v4_strategies import Bar, MINUTE, normalize_minute_timestamp, file_sha256


def check_dates(first, last):
    lo, hi = date.fromisoformat(first), date.fromisoformat(last)
    if lo > hi:
        raise ValueError("Reversed date bounds")
    if hi >= date(2026, 1, 1):
        raise ValueError("FINAL_OOS_ACCESS_BLOCKED: 2026 remains CLOSED")


def bounded_bars(path, symbol, meta, first, last, audit=None):
    check_dates(first, last)
    previous = None
    previous_bar = None
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if not {"timestamp", "open", "high", "low", "close", "volume"} <= set(fields) or len(fields) != len(set(fields)):
            raise ValueError(f"{symbol}: invalid OHLCV schema")
        for row in reader:
            # Only labels outside the research window are inspected, never OHLCV.
            start = normalize_minute_timestamp(row["timestamp"], "end", meta.get("naive_timezone"))
            if previous is not None and start < previous:
                raise ValueError(f"{symbol}: out-of-order timestamp: {start}")
            duplicate = start == previous
            previous = start
            day = start.date().isoformat()
            if not first <= day <= last:
                continue
            for key in ("symbol", "ticker"):
                if key in row and row[key].strip().upper() != symbol:
                    raise ValueError(f"{symbol}: symbol identity mismatch")
            values = tuple(float(row[k]) for k in ("open", "high", "low", "close", "volume"))
            o, h, l, c, v = values
            if not all(math.isfinite(x) for x in values) or min(o,h,l,c) <= 0 or v < 0 or h < max(o,c) or l > min(o,c) or h < l:
                raise ValueError(f"{symbol}: invalid OHLCV at {start}")
            bid, ask = row.get("bid"), row.get("ask")
            if bool(bid) != bool(ask) or (bid and not (math.isfinite(float(ask)) and 0 < float(bid) <= float(ask))):
                raise ValueError(f"{symbol}: invalid quote at {start}")
            bid = float(bid) if bid else None
            ask = float(ask) if ask else None
            bar = Bar(start, start+MINUTE, *values)
            if audit is not None:
                audit["rows"] += 1
                audit["firstTimestamp"] = audit["firstTimestamp"] or bar.end.isoformat()
                audit["lastTimestamp"] = bar.end.isoformat()
            if duplicate:
                if previous_bar != (bar, bid, ask):
                    raise ValueError(f"{symbol}: conflicting duplicate at {start}")
                if audit is not None:
                    audit["exactDuplicates"] += 1
                continue
            previous_bar = (bar, bid, ask)
            yield bar


def inspect_window(path, symbol, first, last, naive_timezone=None, *, allow_empty=False):
    before = path.stat()
    sha = file_sha256(path)
    meta = dict(symbol=symbol, path=path.name, sha256=sha, rows=0, firstTimestamp=None,
                lastTimestamp=None, exactDuplicates=0, timestamp_kind="end",
                timestamp_semantics="exclusive_minute_end", naive_timezone=naive_timezone,
                validationErrors=[], researchFrom=first, researchTo=last,
                sourceTimezoneInterpretation=naive_timezone or "OFFSET_QUALIFIED_INPUT_TO_AMERICA_NEW_YORK",
                finalOosGate="CLOSED", auditMode="BOUNDED_PRICE_WINDOW_FULL_FILE_INTEGRITY_HASH")
    for _ in bounded_bars(path, symbol, meta, first, last, meta):
        pass
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or file_sha256(path) != sha:
        raise ValueError(f"{symbol}: source changed during read-only audit")
    if meta["rows"] == 0 and not allow_empty:
        raise ValueError(f"{symbol}: no rows in requested window")
    return meta


def scheduled_days(paths, manifest, calendar):
    """K-way merge one session at a time; emit missing sessions as empty, not skip.

    The caller can invalidate incomplete days; five days always means five
    consecutive XNYS sessions, not five conveniently available source days.
    """
    first, last = min(calendar), max(calendar)
    streams = {s: iter(bounded_bars(p, s, manifest["symbols"][s], first, last)) for s,p in paths.items()}
    heap = []
    try:
        for s, stream in streams.items():
            bar = next(stream, None)
            if bar:
                heapq.heappush(heap, (bar.start, s, bar))
        for day in calendar:
            rows = {}
            while heap and heap[0][0].date().isoformat() <= day:
                _, symbol, bar = heapq.heappop(heap)
                if bar.start.date().isoformat() == day:
                    rows.setdefault(symbol, {})[bar.start] = bar
                nxt = next(streams[symbol], None)
                if nxt:
                    heapq.heappush(heap, (nxt.start, symbol, nxt))
            yield day, rows
    finally:
        for stream in streams.values():
            stream.close()
