#!/usr/bin/env python3
"""Validate finalized collector CSV/CSV.GZ inputs, without collecting or adjusting.

SQLite is the collector's mutable staging store, not a V4 backtest input. This
tool reads only explicitly selected finalized SYMBOL.csv[.gz] files. Temporary
SQLite indexes keep duplicate/coverage checks bounded in memory.

OHLCV alone cannot establish splits, dividend adjustments, symbol identity or
point-in-time $5/ADV20 eligibility. Explicit review flags and named evidence are
required for PASS. --price-adjustment declares the basis, not that evidence.
Raw prices must be split-free over the reviewed history/windows to satisfy the
existing V4 contract. Fully adjusted/unknown series remain unsupported.

START labels describe [T,T+1m); END labels are exclusive boundaries of [T-1m,T).
Inclusive :59 end labels are rejected. Coverage is diagnostic: missing RTH bars
are not a manifest failure (V4 separately cancels incomplete strategy sessions).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import sqlite3
import struct
import tempfile
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from backtest_v4_strategies import (MINUTE, NY, exchange_sessions, file_sha256,
                                    normalize_minute_timestamp)

VALIDATOR_VERSION = "v4-manifest-1.0"
FIELDS = ("timestamp", "open", "high", "low", "close", "volume")
BASES = {"raw": "unadjusted_split_free_ohlcv", "split-adjusted": "split_adjusted_ohlcv"}


def identity(stat):
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def source_file(data_root, symbol):
    # CSV.GZ is the collector export; a plain CSV is also supported. If both
    # exist, never silently choose an obsolete or differently adjusted copy.
    candidates = [data_root/f"{symbol}{suffix}" for suffix in (".csv.gz", ".csv")]
    existing = [p for p in candidates if p.is_file()]
    if len(existing) != 1:
        raise ValueError(f"{symbol}: need exactly one finalized CSV/CSV.GZ; found {len(existing)} (SQLite staging is unsupported)")
    path = existing[0]
    if path.resolve().parent != data_root.resolve():
        raise ValueError(f"{symbol}: input symlink escapes data-dir")
    return path


def inspect_file(path, symbol, timestamp_kind, naive_timezone, *, calendar_provider=exchange_sessions):
    """Automated facts only; no automatic price-basis or filename attestation."""
    before = path.stat()
    digest = file_sha256(path)
    meta = dict(symbol=symbol, path=path.name, sha256=digest, rows=0,
                firstTimestamp=None, lastTimestamp=None, duplicates=0,
                exactDuplicates=0, conflictingDuplicates=0, quoteConflicts=0,
                ohlcViolations=0, quoteViolations=0, timestampViolations=0,
                symbolViolations=0, outOfOrderTransitions=0,
                monotonicTimestampOrdering=True, strictlyIncreasingTimestamps=True,
                timestamp_kind=timestamp_kind,
                timestamp_semantics="minute_start" if timestamp_kind == "start" else "exclusive_minute_end",
                naive_timezone=naive_timezone, naiveRows=0, awareRows=0,
                expectedRegularMinutes=0, observedRegularMinutes=0,
                missingRegularMinutes=0, completenessPct=0.0,
                outsideRegularSessionMinutes=0, sessionGapCount=0,
                sessionsWithMissingMinutes=0, missingSessionExamples=[],
                validationErrors=[], errorExamples=[])
    errors = meta["validationErrors"]
    offsets, currencies = Counter(), Counter()
    first = last = previous = None
    raw_first = raw_last = None
    # A disk-backed key index avoids retaining millions of OHLC tuples in RAM.
    with tempfile.TemporaryDirectory(prefix="v4-validate-") as tmp:
        with sqlite3.connect(str(Path(tmp)/"minutes.sqlite3")) as conn:
            conn.execute("CREATE TABLE minutes (t INTEGER PRIMARY KEY, ohlcv BLOB NOT NULL, quotes TEXT NOT NULL)")
            conn.execute("CREATE TABLE seen (t INTEGER PRIMARY KEY, ohlcv BLOB NOT NULL, quotes TEXT NOT NULL)")
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                columns = reader.fieldnames or []
                meta["ohlcvSchema"] = dict(columns=columns, required=list(FIELDS), numeric="finite IEEE float64", timestamp="ISO-8601 exact minute")
                if not set(FIELDS).issubset(columns) or len(columns) != len(set(columns)):
                    raise ValueError(f"{symbol}: missing/duplicate OHLCV columns; got {columns}")
                identity_columns = [c for c in ("symbol", "ticker") if c in columns]
                meta["symbolIdentitySource"] = "row columns: " + ",".join(identity_columns) if identity_columns else "filename; external identity review required"
                for line, row in enumerate(reader, 2):
                    meta["rows"] += 1
                    if any((row.get(c) or "").strip().upper() != symbol for c in identity_columns):
                        meta["symbolViolations"] += 1
                    if row.get("currency"):
                        currencies[row["currency"].strip().upper()] += 1
                    try:
                        raw = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                        meta["naiveRows" if raw.tzinfo is None else "awareRows"] += 1
                        offsets[str(raw.utcoffset()) if raw.tzinfo else "naive"] += 1
                        start = normalize_minute_timestamp(row["timestamp"], timestamp_kind, naive_timezone)
                    except (ValueError, TypeError, AttributeError) as exc:
                        meta["timestampViolations"] += 1
                        if len(meta["errorExamples"]) < 5:
                            meta["errorExamples"].append(f"line {line}: timestamp: {exc}")
                        continue
                    if previous is not None:
                        if start < previous:
                            meta["outOfOrderTransitions"] += 1
                        if start <= previous:
                            meta["strictlyIncreasingTimestamps"] = False
                    previous = start
                    if first is None or start < first:
                        first, raw_first = start, row["timestamp"]
                    if last is None or start > last:
                        last, raw_last = start, row["timestamp"]
                    try:
                        values = tuple(float(row[k]) for k in FIELDS[1:])
                        o, h, l, c, v = values
                        if not all(math.isfinite(x) for x in values) or min(o, h, l, c) <= 0 or v < 0 or h < max(o, c) or l > min(o, c) or h < l:
                            raise ValueError("invalid/nonfinite OHLCV")
                    except (ValueError, TypeError) as exc:
                        meta["ohlcViolations"] += 1
                        if len(meta["errorExamples"]) < 5:
                            meta["errorExamples"].append(f"line {line}: {exc}")
                        values = None
                    try:
                        bid = float(row["bid"]) if row.get("bid") else None
                        ask = float(row["ask"]) if row.get("ask") else None
                        if (bid is None) != (ask is None) or (bid is not None and (not math.isfinite(bid) or not math.isfinite(ask) or not 0 < bid <= ask)):
                            raise ValueError("invalid/partial quote")
                    except (ValueError, TypeError):
                        meta["quoteViolations"] += 1
                        bid = ask = None
                        quotes = json.dumps([row.get("bid"), row.get("ask"), "invalid"])
                    else:
                        quotes = json.dumps([bid, ask])
                    # Include invalid OHLC rows in duplicate diagnostics too;
                    # they cannot contribute to validated session coverage.
                    fingerprint = (struct.pack("!5d", *(x if x else 0.0 for x in values)) if values is not None
                                   else json.dumps([row.get(k) for k in FIELDS[1:]]).encode())
                    key = int(start.timestamp())
                    inserted = conn.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?)", (key, fingerprint, quotes)).rowcount
                    if not inserted:
                        meta["duplicates"] += 1
                        old, old_quotes = conn.execute("SELECT ohlcv,quotes FROM seen WHERE t=?", (key,)).fetchone()
                        if old != fingerprint or old_quotes != quotes:
                            meta["conflictingDuplicates"] += 1
                            if old_quotes != quotes:
                                meta["quoteConflicts"] += 1
                        else:
                            meta["exactDuplicates"] += 1
                    if values is not None:
                        conn.execute("INSERT OR IGNORE INTO minutes VALUES (?,?,?)", (key, fingerprint, quotes))
            conn.commit()
            meta["monotonicTimestampOrdering"] = meta["outOfOrderTransitions"] == 0
            meta["sourceUTCOffsets"] = dict(offsets)
            meta["sourceTimezoneInterpretation"] = dict(aware="explicit per-row UTC offset converted to America/New_York", naive=naive_timezone)
            meta["currencies"] = dict(currencies)
            if currencies and set(currencies) != {"USD"}:
                errors.append(f"US dollar price eligibility requires USD, found {sorted(currencies)}")
            if first is not None:
                meta["firstTimestamp"] = (first if timestamp_kind == "start" else first+MINUTE).isoformat()
                meta["lastTimestamp"] = (last if timestamp_kind == "start" else last+MINUTE).isoformat()
                meta["rawFirstTimestamp"], meta["rawLastTimestamp"] = raw_first, raw_last
                sessions = calendar_provider(first.date().isoformat(), last.date().isoformat())
                boundary_examples = []
                for day, (opening, closing) in sessions.items():
                    expected = int((closing-opening).total_seconds()/60)
                    times = [t for (t,) in conn.execute("SELECT t FROM minutes WHERE t>=? AND t<? ORDER BY t", (int(opening.timestamp()), int(closing.timestamp())))]
                    observed = len(times)
                    missing = expected-observed
                    meta["expectedRegularMinutes"] += expected
                    meta["observedRegularMinutes"] += observed
                    meta["missingRegularMinutes"] += missing
                    meta["sessionGapCount"] += sum(b-a > 60 for a, b in zip(times, times[1:]))
                    if missing:
                        meta["sessionsWithMissingMinutes"] += 1
                        if len(meta["missingSessionExamples"]) < 5:
                            meta["missingSessionExamples"].append(dict(date=day, expected=expected, observed=observed, missing=missing))
                    if times and len(boundary_examples) < 5:
                        shift = MINUTE if timestamp_kind == "end" else MINUTE*0
                        boundary_examples.append(dict(date=day, firstObservedLabel=(datetime.fromtimestamp(times[0], NY)+shift).isoformat(),
                                                     lastObservedLabel=(datetime.fromtimestamp(times[-1], NY)+shift).isoformat(),
                                                     expectedFirstLabel=(opening+shift).isoformat(), expectedLastLabel=(closing-MINUTE+shift).isoformat()))
                meta["timestampLabelDiagnostics"] = boundary_examples
                total = conn.execute("SELECT COUNT(*) FROM minutes").fetchone()[0]
                meta["outsideRegularSessionMinutes"] = total-meta["observedRegularMinutes"]
                if meta["expectedRegularMinutes"]:
                    meta["completenessPct"] = 100*meta["observedRegularMinutes"]/meta["expectedRegularMinutes"]
            if not meta["observedRegularMinutes"]:
                errors.append("No validated XNYS regular-session minutes")
    for field in ("conflictingDuplicates", "ohlcViolations", "timestampViolations", "symbolViolations", "quoteViolations"):
        if meta[field]:
            errors.append(f"{field}={meta[field]}")
    # Hash both before and after the scan: a collector atomic export/rewrite must
    # invalidate this review, without stopping that collector.
    if file_sha256(path) != digest or identity(path.stat()) != identity(before):
        errors.append("Input changed during validation; wait for a finalized export and retry")
    return meta


def build_manifest(data_root, symbols, timestamp_kind, price_adjustment, *, naive_timezone=None,
                   verified_by="", verification_note="", confirm_split_consistency=False,
                   confirm_point_in_time_eligibility=False, confirm_symbol_identity=False,
                   calendar_provider=exchange_sessions):
    confirmed = price_adjustment in BASES
    review_present = bool(verified_by.strip() and verification_note.strip())
    try:
        calendar_version = version("exchange_calendars")
    except PackageNotFoundError:
        calendar_version = None  # Production inspection will FAIL without it.
    manifest = dict(version=1, validatorVersion=VALIDATOR_VERSION,
                    generatedAt=datetime.now(timezone.utc).isoformat(), dataRoot=str(data_root.resolve()),
                    timestampKind=timestamp_kind,
                    timezone=naive_timezone or "explicit per-row UTC offsets (aware-only)",
                    priceAdjustment=price_adjustment, priceAdjustmentConfirmed=confirmed,
                    calendar="XNYS", calendarVersion=calendar_version,
                    verified_by=verified_by, verification_note=verification_note,
                    validationPassed=False, validationErrors=[], symbols={})
    if timestamp_kind not in ("start", "end"):
        raise ValueError("Explicit --timestamp-kind start|end is required")
    if naive_timezone not in (None, "America/New_York", "UTC"):
        raise ValueError("--naive-timezone must be America/New_York or UTC; omitted means aware-only")
    if not confirmed:
        manifest["validationErrors"].append("Unknown/fully-adjusted price basis cannot PASS V4; no automatic adjustment")
    if not review_present:
        manifest["validationErrors"].append("External review requires --verified-by and --verification-note")
    for symbol in symbols:
        try:
            path = source_file(data_root, symbol)
            meta = inspect_file(path, symbol, timestamp_kind, naive_timezone, calendar_provider=calendar_provider)
        except (ValueError, OSError, RuntimeError, EOFError, csv.Error, sqlite3.Error) as exc:
            meta = dict(symbol=symbol, validationErrors=[str(exc)])
        meta.update(priceAdjustment=price_adjustment, priceAdjustmentConfirmed=confirmed,
                    price_basis=BASES.get(price_adjustment, "unknown"),
                    split_consistency_verified=review_present and confirm_split_consistency,
                    point_in_time_eligibility_verified=review_present and confirm_point_in_time_eligibility,
                    symbolIdentityConfirmed=review_present and confirm_symbol_identity and meta.get("symbolViolations") == 0)
        reasons = meta["validationErrors"]
        if not confirmed:
            reasons.append("priceAdjustmentConfirmed=false")
        if not meta["split_consistency_verified"]:
            reasons.append("External split/price AND volume consistency review required (--confirm-split-consistency); raw must be split-free")
        if not meta["point_in_time_eligibility_verified"]:
            reasons.append("External as-traded $5/ADV20 review required (--confirm-point-in-time-eligibility)")
        if not meta["symbolIdentityConfirmed"]:
            reasons.append("Source-to-symbol identity review required (--confirm-symbol-identity); filename alone is not proof")
        meta["validationPassed"] = not reasons
        manifest["symbols"][symbol] = meta
    # A previously scanned symbol may have been re-exported while later symbols
    # were checked. Recheck every successful file immediately before publishing.
    for symbol, meta in manifest["symbols"].items():
        if not meta["validationPassed"]:
            continue
        try:
            path = data_root/meta["path"]
            before = path.stat()
            digest = file_sha256(path)
            if digest != meta["sha256"] or identity(before) != identity(path.stat()):
                raise ValueError("Input changed before manifest publication; retry after finalized export")
        except (OSError, ValueError) as exc:
            meta["validationErrors"].append(str(exc))
            meta["validationPassed"] = False
    manifest["validationPassed"] = bool(symbols) and not manifest["validationErrors"] and all(m["validationPassed"] for m in manifest["symbols"].values())
    return manifest


def parse_args():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=root/"data/toss_1m")
    parser.add_argument("--out", type=Path, default=root/"research/v4_data_manifest.json")
    parser.add_argument("--symbols", help="Comma-separated universe_v3 symbols; default: entire universe")
    parser.add_argument("--timestamp-kind", required=True, choices=("start", "end"))
    parser.add_argument("--naive-timezone", choices=("America/New_York", "UTC"), help="Explicit interpretation of naive timestamps; omit to REQUIRE offsets")
    parser.add_argument("--price-adjustment", required=True, choices=("raw", "split-adjusted", "fully-adjusted", "unknown"), help="Explicit declared basis; fully-adjusted/unknown produce FAIL")
    parser.add_argument("--verified-by", default="", help="Identity of external provenance reviewer")
    parser.add_argument("--verification-note", default="", help="Concrete source/corporate-action and as-traded eligibility review evidence; not a generic acknowledgement")
    parser.add_argument("--confirm-split-consistency", action="store_true", help="Attest external review of split-adjusted price AND volume consistency, or raw split-free history")
    parser.add_argument("--confirm-point-in-time-eligibility", action="store_true", help="Attest historical $5/ADV20 eligibility has been checked against as-traded prices/volume")
    parser.add_argument("--confirm-symbol-identity", action="store_true", help="Attest source instrument identity for each selected filename, even without a ticker column")
    return parser.parse_args()


def main():
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    universe = json.loads((root/"research/universe_v3.json").read_text())["symbols"]
    symbols = list(dict.fromkeys(s.strip().upper() for s in args.symbols.split(","))) if args.symbols else universe
    if not symbols or any(s not in universe for s in symbols):
        raise ValueError("--symbols must select symbols in research/universe_v3.json")
    # Never overwrite collector files, even if --out was mistyped.
    if args.out.resolve().is_relative_to(args.data_dir.resolve()):
        raise ValueError("Manifest output must be outside the historical data directory")
    manifest = build_manifest(args.data_dir, symbols, args.timestamp_kind, args.price_adjustment,
                              naive_timezone=args.naive_timezone, verified_by=args.verified_by,
                              verification_note=args.verification_note,
                              confirm_split_consistency=args.confirm_split_consistency,
                              confirm_point_in_time_eligibility=args.confirm_point_in_time_eligibility,
                              confirm_symbol_identity=args.confirm_symbol_identity)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Never leave a truncated PASS manifest if this process is interrupted.
    fd, name = tempfile.mkstemp(prefix=args.out.name+".", suffix=".tmp", dir=args.out.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(name, args.out)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    for symbol, meta in manifest["symbols"].items():
        passed = meta["validationPassed"]
        print(f"{symbol:5} {'PASS' if passed else 'FAIL'} rows={meta.get('rows', 0)} coverage={meta.get('completenessPct', 0):.2f}% sha256={meta.get('sha256', 'unavailable')}")
        for reason in meta["validationErrors"]:
            print(f"  {reason}")
    for reason in manifest["validationErrors"]:
        print(f"  {reason}")
    print(f"Overall: {'PASS' if manifest['validationPassed'] else 'FAIL'}; manifest={args.out}")
    return 0 if manifest["validationPassed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(f"V4 data validation refused: {exc}")
