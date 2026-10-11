"""Explicit operator tools for range-scoped Toss repair, never a trading runner.

CSV is the evaluator input; SQLite is a collector checkpoint. These tools never
infer that either store is authoritative. Market values are inspected only for
requested pre-2026 dates. Whole-file hashes/backups copy opaque bytes, including
any existing Final OOS bytes, solely for integrity/preservation, not evaluation.
"""
from __future__ import annotations

import argparse
import csv
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import gzip
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"research"))
from research.backtest_v4_strategies import MINUTE, NY, exchange_sessions, normalize_minute_timestamp, file_sha256
from research.build_v4_data_manifest import source_file
from research.evolution.features import benchmark_for_symbol

VERSION = "toss-targeted-repair-1"
FIELDS = ("timestamp", "open", "high", "low", "close", "volume", "currency")
DEFAULT_DATES = ("2024-07-26", "2024-07-29", "2024-07-30", "2024-07-31", "2024-08-01", "2024-08-02")
UTC = timezone.utc


def now():
    return datetime.now(UTC).isoformat()


def save(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def durable_copy(source, destination):
    with Path(source).open("rb") as src, Path(destination).open("xb") as dst:
        shutil.copyfileobj(src, dst)
        dst.flush(); os.fsync(dst.fileno())


def artifact_path(folder, name):
    if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
        raise ValueError("INVALID_REPAIR_ARTIFACT_PATH")
    path = folder/name
    if path.is_symlink() or not path.is_file(): raise ValueError("INVALID_REPAIR_ARTIFACT_PATH")
    return path


def symbols_checked(values):
    symbols = sorted(set(x.strip().upper() for x in values.split(",") if x.strip()))
    if not symbols or any(not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", x) for x in symbols):
        raise ValueError("INVALID_SYMBOL")
    return symbols


def sessions_checked(dates):
    dates = sorted(set(dates))
    if not dates or len(dates) > 366:
        raise ValueError("EXPLICIT_BOUNDED_DATES_REQUIRED")
    for day in dates:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("INVALID_DATE")
        if day >= "2026-01-01":
            raise ValueError("FINAL_OOS_ACCESS_BLOCKED")
    sessions = exchange_sessions(dates[0], dates[-1])
    invalid = [d for d in dates if d not in sessions]
    if invalid:
        raise ValueError("NOT_XNYS_TRADING_SESSION: " + ",".join(invalid))
    return {d: sessions[d] for d in dates}


def disk_guard(path, estimated=0):
    total, _, free = shutil.disk_usage(path)
    reserve = max(10 * 1024**3, int(total * .10))
    if free - estimated < reserve:
        raise ValueError("DISK_RESERVE: destination filesystem lacks required reserve")


def fingerprint(row, symbol):
    if any(row.get(k, symbol).strip().upper() != symbol for k in ("symbol", "ticker")):
        raise ValueError("SYMBOL_IDENTITY_MISMATCH")
    if row.get("currency") != "USD":
        raise ValueError("CURRENCY_UNKNOWN_OR_NON_USD")
    try:
        values = tuple(Decimal(str(row[k])) for k in FIELDS[1:6])
        o, h, l, c, v = values
        if not all(x.is_finite() and math.isfinite(float(x)) for x in values) or min(o,h,l,c) <= 0 or v < 0 or h < max(o,c) or l > min(o,c) or h < l:
            raise ValueError("INVALID_OHLCV")
    except (KeyError, TypeError, InvalidOperation, OverflowError) as exc:
        raise ValueError("INVALID_OHLCV") from exc
    # Extra quote/metadata columns must not be discarded during a repair.
    extra = tuple(sorted((k, v) for k, v in row.items() if k not in FIELDS and k not in ("symbol", "ticker")))
    return values, "USD", extra


def read_csv_rows(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        if not set(FIELDS).issubset(columns) or len(columns) != len(set(columns)):
            raise ValueError("INVALID_OHLCV_SCHEMA")
        yield from reader


def readonly_db(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("INVALID_SQLITE_SOURCE")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def db_rows(path, sessions):
    # SQLite's date conversion honors explicit ISO offsets; no lexicographic
    # UTC-vs-ET filter. Naive labels are separately rejected before validation.
    lo = (sessions[min(sessions)][0] - timedelta(hours=12)).astimezone(UTC).isoformat()
    hi = (sessions[max(sessions)][1] + timedelta(hours=12)).astimezone(UTC).isoformat()
    with closing(readonly_db(path)) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(candles)")]
        if not set(FIELDS).issubset(columns):
            raise ValueError("INVALID_SQLITE_SCHEMA")
        sql = "SELECT " + ",".join(FIELDS) + " FROM candles WHERE julianday(timestamp)>=julianday(?) AND julianday(timestamp)<=julianday(?) ORDER BY julianday(timestamp),timestamp"
        for row in conn.execute(sql, (lo, hi)):
            yield dict(zip(FIELDS, row))


def stable_signature(path):
    s = path.stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]


def db_signature(path):
    return {p.name: stable_signature(p) for p in (path, Path(str(path)+"-wal"), Path(str(path)+"-shm")) if p.exists()}

def audit_db_content_signature(signature, path):
    """Only for read-only audits: protect DB and WAL, excluding ephemeral SHM mtime.

    Keep full db_signature for prepare/apply safety checks.
    """
    return {name: value for name, value in signature.items()
            if name in {path.name, path.name + "-wal"}}


def collector_export_order(path):
    """Existing export sorts TEXT labels; validate that order before publication.

    Timestamp metadata only, no OHLCV/2026 feature access. Mixed explicit UTC/ET
    labels can each be valid yet sort incorrectly in the original collector.
    Never canonicalize/overwrite existing labels automatically to hide this.
    """
    previous = None; disorders = 0; errors = 0; samples = []
    with closing(readonly_db(path)) as conn:
        for (label,) in conn.execute("SELECT timestamp FROM candles ORDER BY timestamp"):
            try: stamp = normalize_minute_timestamp(label, "end", None)
            except (ValueError, TypeError, AttributeError):
                errors += 1
                continue
            if stamp.date().isoformat() >= "2026-01-01": break
            if previous is not None and stamp < previous:
                disorders += 1
                if len(samples) < 5: samples.append(dict(previousStart=previous.isoformat(), currentSourceTimestamp=label))
            previous = stamp
    return dict(passed=not disorders and not errors, outOfOrderTransitions=disorders, timestampErrors=errors, samples=samples,
                scope="Pre-2026 timestamp metadata in existing collector TEXT export order")


def inspect_rows(rows, symbol, sessions, *, strict_scope=False):
    observed = {d: {} for d in sessions}
    facts = {d: dict(duplicates=0, conflictingDuplicates=0, outOfOrder=0, invalidOhlcv=0,
                    extendedRows=0, extendedTimestampSamples=[], timezoneOffsets=[],
                    sourceFirst=None, sourceLast=None, firstSourceTimestamp=None, lastSourceTimestamp=None) for d in sessions}
    errors = []; previous = None; out_of_scope = 0; invalid_timestamp = 0; out_of_order = 0
    for line, row in enumerate(rows, 2):
        try:
            start = normalize_minute_timestamp(row["timestamp"], "end", None)
        except (ValueError, TypeError, AttributeError, KeyError):
            invalid_timestamp += 1
            if len(errors) < 10: errors.append(f"line {line}: TIMEZONE_OR_TIMESTAMP_INVALID")
            continue
        day = start.date().isoformat()
        if previous is not None and start < previous:
            out_of_order += 1
            if day in facts: facts[day]["outOfOrder"] += 1
            if len(errors) < 10: errors.append(f"line {line}: OUT_OF_ORDER")
        previous = start
        if day not in sessions:
            if strict_scope: out_of_scope += 1
            continue
        opening, closing = sessions[day]
        f = facts[day]
        if f["firstSourceTimestamp"] is None: f["firstSourceTimestamp"] = row["timestamp"]
        f["lastSourceTimestamp"] = row["timestamp"]
        f["timezoneOffsets"] = sorted(set(f["timezoneOffsets"] + [str(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).utcoffset())]))
        try:
            fp = fingerprint(row, symbol)
        except ValueError:
            f["invalidOhlcv"] += 1
            continue
        if not opening <= start < closing:
            f["extendedRows"] += 1
            if len(f["extendedTimestampSamples"]) < 10: f["extendedTimestampSamples"].append(row["timestamp"])
            if strict_scope: out_of_scope += 1
            continue
        key = start.astimezone(UTC).isoformat()
        if key in observed[day]:
            f["duplicates"] += 1
            if observed[day][key][0] != fp: f["conflictingDuplicates"] += 1
        else:
            observed[day][key] = (fp, dict(row))
        if f["sourceFirst"] is None: f["sourceFirst"] = row["timestamp"]
        f["sourceLast"] = row["timestamp"]
    report = []
    for day, (opening, closing) in sessions.items():
        expected = int((closing-opening)/MINUTE)
        keys = sorted(observed[day])
        f = facts[day]
        passed = len(keys) == expected and not any(f[k] for k in ("conflictingDuplicates", "outOfOrder", "invalidOhlcv"))
        report.append(dict(symbol=symbol, date=day, sessionOpen=opening.isoformat(), sessionClose=closing.isoformat(),
                           expectedRegularMinutes=expected, actualRegularMinutes=len(keys),
                           missingRegularMinutes=expected-len(keys), firstRthBarStart=keys[0] if keys else None,
                           lastRthBarStart=keys[-1] if keys else None,
                           firstRthBarEnd=observed[day][keys[0]][1]["timestamp"] if keys else None,
                           lastRthBarEnd=observed[day][keys[-1]][1]["timestamp"] if keys else None,
                           expectedFirstRthBarEnd=(opening+MINUTE).isoformat(), expectedLastRthBarEnd=closing.isoformat(),
                           timestampKind="end", timestampSemantics="exclusive_minute_end", timezone="aware -> America/New_York",
                           passed=passed, **f))
    return dict(sessions=report, errors=errors, invalidTimestampCount=invalid_timestamp,
                outOfOrderTransitions=out_of_order,
                outOfScopeRows=out_of_scope,
                passed=all(r["passed"] for r in report) and not invalid_timestamp and not out_of_order and not out_of_scope), observed


def audit(data_dir, symbols, dates):
    root = Path(data_dir).resolve(); sessions = sessions_checked(dates); outputs = {}
    for symbol in symbols:
        path = source_file(root, symbol)
        before = stable_signature(path); digest = file_sha256(path)
        csv_report, csv_seen = inspect_rows(read_csv_rows(path), symbol, sessions)
        if stable_signature(path) != before or file_sha256(path) != digest:
            raise ValueError("SOURCE_CHANGED_DURING_AUDIT")
        out = dict(csv=dict(path=str(path), sha256=digest, signature=before, sizeBytes=path.stat().st_size,
                            modifiedAt=datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(), **csv_report), sqlite=None, comparison=None)
        db = root/(symbol+".sqlite3")
        if db.exists():
            before_db = db_signature(db)
            with closing(readonly_db(db)) as conn:
                count, first, last = conn.execute("SELECT COUNT(*),MIN(timestamp),MAX(timestamp) FROM candles").fetchone()
            db_report, db_seen = inspect_rows(db_rows(db, sessions), symbol, sessions)
            db_order = collector_export_order(db)
            after_db = db_signature(db)
            if audit_db_content_signature(before_db, db) != audit_db_content_signature(after_db, db):
                raise ValueError("SQLITE_CHANGED_DURING_AUDIT")
            shm_name = db.name + "-shm"
            shm_changed = before_db.get(shm_name) != after_db.get(shm_name)
            comparisons = []
            for day in sessions:
                a, b = csv_seen[day], db_seen[day]; common = a.keys() & b.keys()
                conflicts = [k for k in sorted(common) if a[k][0] != b[k][0]]
                comparisons.append(dict(date=day, csvOnly=len(a.keys()-b.keys()), sqliteOnly=len(b.keys()-a.keys()),
                                        same=len(common)-len(conflicts), conflicting=len(conflicts), conflictSamples=conflicts[:10]))
            out["sqlite"] = dict(path=str(db), signature=before_db, signatureAfter=after_db,
                                 shmMetadataChangedDuringAudit=shm_changed,
                                 rows=count, firstTimestamp=first, lastTimestamp=last,
                                 collectorExportOrder=db_order,
                                 ordering="SQL timestamp order; physical insertion order is not chronological evidence", **db_report)
            out["comparison"] = comparisons
        outputs[symbol] = out
    return dict(version=VERSION, generatedAt=now(), researchOnly=True, finalOosGate="CLOSED", calendar="XNYS",
                timestampKind="end", dates=list(sessions), symbols=outputs,
                evidenceScope="Requested RTH; extended row counts separate. Absence of SQLite is not absence of CSV.")


def outside_source(destination, production):
    dest = Path(destination).absolute()
    prod = Path(production).resolve()
    # All production inputs remain untouched by collect/validate/prepare.
    resolved = dest.resolve()
    if resolved == prod or prod in resolved.parents or resolved in prod.parents:
        raise ValueError("STAGING_MUST_BE_SEPARATE_FROM_PRODUCTION")
    if any(p.is_symlink() for p in (dest, *dest.parents)):
        raise ValueError("SYMLINK_DESTINATION_FORBIDDEN")
    return dest


def write_csv(path, rows):
    # Deterministic gzip (no embedded filename/current clock) and lossless strings.
    with path.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader(); writer.writerows(rows)
        raw.flush(); os.fsync(raw.fileno())


def collect(staging, production, symbols, dates, *, fetch=None, get_token=None, pages=100, sleep=0.5):
    sessions = sessions_checked(dates)
    stage = outside_source(staging, production)
    stage.parent.mkdir(parents=True, exist_ok=True); disk_guard(stage.parent, 32*1024**2)
    stage.mkdir(exist_ok=False)
    provenance = dict(version=VERSION, operationId=uuid.uuid4().hex, createdAt=now(), sourceProvider="Toss",
                      sourceEndpoint="GET /api/v1/candles", adjustedQuery=True, priceAdjustmentConfirmed=False,
                      timestampKind="end", timestampSemantics="exclusive_minute_end", calendar="XNYS",
                      symbols=symbols, dates=list(sessions), productionRoot=str(Path(production).resolve()),
                      gitCommit=subprocess.run(["git","rev-parse","HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip(),
                      researchOnly=True, finalOosGate="CLOSED", phase="collecting", pages={})
    save(stage/"staging.json", provenance)
    if fetch is None:
        from scripts.collect_toss_1m import fetch_page, token
        fetch, get_token = fetch_page, token
    def error_recorder(symbol, before, attempt, error, http_code=None, body=None):
        # No auth/body/raw exception text is persisted; only bounded safe facts.
        with (stage/"request_errors.jsonl").open("a") as log:
            log.write(json.dumps(dict(at=now(), symbol=symbol, before=before, attempt=attempt, httpCode=http_code)) + "\n")
    try:
        tok = get_token()
        for symbol in symbols:
            seen = {}
            for day, (opening, closing) in sessions.items():
                cursor = (closing+MINUTE).astimezone(UTC).isoformat()
                first_cursor = cursor
                previous_oldest = None; reached = False; calls = 0
                for _ in range(pages):
                    obj = fetch(tok, symbol, cursor, count=100, refresh_token=lambda: get_token(force=True), error_recorder=error_recorder)
                    calls += 1; candles = obj.get("candles") or []
                    if not candles: break
                    stamps = []
                    for candle in candles:
                        start = normalize_minute_timestamp(str(candle.get("timestamp") or ""), "end", None)
                        stamps.append(start)
                        if not opening <= start < closing: continue
                        row = dict(zip(FIELDS, (candle["timestamp"], candle.get("openPrice"), candle.get("highPrice"),
                                               candle.get("lowPrice"), candle.get("closePrice"), candle.get("volume"), candle.get("currency"))))
                        row = {k: str(v) if v is not None else "" for k,v in row.items()}
                        fp = fingerprint(row, symbol); key = start.astimezone(UTC).isoformat()
                        if key in seen and fingerprint(seen[key], symbol) != fp:
                            raise ValueError("CONFLICTING_API_DUPLICATE")
                        seen.setdefault(key, row)
                    oldest = min(stamps)
                    if oldest <= opening:
                        reached = True; break
                    next_cursor = obj.get("nextBefore")
                    if not next_cursor or (previous_oldest is not None and oldest >= previous_oldest):
                        raise ValueError("PAGINATION_NOT_PROGRESSING")
                    next_time = normalize_minute_timestamp(str(next_cursor), "end", None)
                    current_time = normalize_minute_timestamp(cursor, "end", None)
                    if next_time >= current_time: raise ValueError("PAGINATION_NOT_PROGRESSING")
                    cursor = next_cursor; previous_oldest = oldest
                    time.sleep(sleep)
                provenance["pages"][symbol+":"+day] = dict(calls=calls, reachedSessionOpen=reached,
                                                           firstBefore=first_cursor, lastBefore=cursor,
                                                           sessionOpen=opening.isoformat(), sessionClose=closing.isoformat())
            write_csv(stage/(symbol+".csv.gz"), (seen[k] for k in sorted(seen)))
        provenance.update(phase="collected", finishedAt=now())
        save(stage/"staging.json", provenance)
        return validate_stage(stage)
    except BaseException as exc:
        provenance.update(phase="failed", errorCode=type(exc).__name__, finishedAt=now())
        save(stage/"staging.json", provenance)
        raise RuntimeError("STAGING_BACKFILL_FAILED; inspect local staging request_errors and validation. Production unchanged.") from None


def validate_stage(stage):
    stage = Path(stage).resolve()
    meta = json.loads((stage/"staging.json").read_text())
    outside_source(stage, meta["productionRoot"])
    if meta.get("timestampKind") != "end" or meta.get("calendar") != "XNYS" or meta.get("phase") not in ("collected", "validated"):
        raise ValueError("INVALID_STAGING_PROVENANCE")
    sessions = sessions_checked(meta["dates"]); report = {}
    for symbol in meta["symbols"]:
        symbols_checked(symbol)
        path = source_file(stage, symbol); signature = stable_signature(path)
        digest = file_sha256(path)
        facts, _ = inspect_rows(read_csv_rows(path), symbol, sessions, strict_scope=True)
        if signature != stable_signature(path) or digest != file_sha256(path):
            raise ValueError("STAGING_CHANGED_DURING_VALIDATION")
        report[symbol] = dict(path=path.name, sha256=digest, **facts)
    result = dict(version=VERSION, validatedAt=now(), operationId=meta["operationId"], stagingMetadataSha256=file_sha256(stage/"staging.json"),
                  validationPassed=all(x["passed"] for x in report.values()), symbols=report, dates=list(sessions),
                  timestampKind="end", calendar="XNYS", researchOnly=True, finalOosGate="CLOSED",
                  priceAdjustmentConfirmed=False, reviewStatus="PROVISIONAL_UNREVIEWED_DATA")
    save(stage/"validation.json", result)
    return result


def adopt_staging(source_dir, staging, production, symbols, dates):
    """Bridge preview-first collector's SYMBOL/SYMBOL.csv.gz without re-fetching.

    Original staged artifacts stay read-only/unverified. Only selected RTH rows
    are copied to a new validation bundle. Never fix conflicting source rows by
    sorting/deduplicating first; inspect their original order and values first.
    """
    sessions = sessions_checked(dates)
    source_root = outside_source(source_dir, production)
    dest = outside_source(staging, production)
    if dest == source_root or source_root in dest.parents or dest in source_root.parents:
        raise ValueError("ADOPTION_SOURCE_DESTINATION_OVERLAP")
    dest.parent.mkdir(parents=True, exist_ok=True); disk_guard(dest.parent, 32*1024**2)
    dest.mkdir(exist_ok=False); sources = {}; selected = {}
    for symbol in symbols:
        candidates = [p for p in (source_root/(symbol+".csv.gz"), source_root/(symbol+".csv"),
                                  source_root/symbol/(symbol+".csv.gz"), source_root/symbol/(symbol+".csv")) if p.is_file()]
        if len(candidates) != 1: raise ValueError("NEED_EXACTLY_ONE_STAGED_CSV: " + symbol)
        path = candidates[0]
        if path.is_symlink() or path.parent.is_symlink(): raise ValueError("SYMLINK_STAGING_SOURCE_FORBIDDEN")
        before = stable_signature(path); digest = file_sha256(path)
        facts, observed = inspect_rows(read_csv_rows(path), symbol, sessions)
        if facts["errors"] or any(f[k] for f in facts["sessions"] for k in ("conflictingDuplicates", "invalidOhlcv", "outOfOrder")):
            raise ValueError("EXISTING_STAGING_INVALID_OR_CONFLICTING")
        if before != stable_signature(path) or digest != file_sha256(path): raise ValueError("EXISTING_STAGING_CHANGED")
        selected[symbol] = {k:r for day in observed.values() for k,(_,r) in day.items()}
        if any(tuple(r) != FIELDS for r in selected[symbol].values()): raise ValueError("ADOPTION_REQUIRES_COLLECTOR_EXACT_SCHEMA")
        sources[symbol] = dict(path=str(path),sha256=digest,audit=facts)
    for symbol, rows in selected.items(): write_csv(dest/(symbol+".csv.gz"),(rows[k] for k in sorted(rows)))
    save(dest/"staging.json",dict(version=VERSION,operationId=uuid.uuid4().hex,createdAt=now(),phase="collected",
         sourceProvider="Toss (operator-declared existing staging)",sourceMode="ADOPTED_UNVERIFIED_STAGING",
         originalStagingSources=sources,priceAdjustmentConfirmed=False,timestampKind="end",calendar="XNYS",
         symbols=symbols,dates=list(sessions),productionRoot=str(Path(production).resolve()),researchOnly=True,finalOosGate="CLOSED"))
    return validate_stage(dest)


def checked_stage(stage):
    stage = Path(stage).resolve()
    validation = json.loads((stage/"validation.json").read_text())
    meta = json.loads((stage/"staging.json").read_text())
    if validation.get("validationPassed") is not True or validation.get("stagingMetadataSha256") != file_sha256(stage/"staging.json"):
        raise ValueError("STAGING_VALIDATION_REQUIRED")
    sessions = sessions_checked(meta["dates"])
    rows = {}
    for symbol in meta["symbols"]:
        path = source_file(stage, symbol)
        if validation["symbols"][symbol]["sha256"] != file_sha256(path):
            raise ValueError("STAGING_HASH_MISMATCH")
        facts, observed = inspect_rows(read_csv_rows(path), symbol, sessions, strict_scope=True)
        if not facts["passed"]: raise ValueError("STAGING_INCOMPLETE_OR_INVALID")
        rows[symbol] = {k: row for day in observed.values() for k, (_, row) in day.items()}
    return meta, sessions, rows


def merge_csv(original, destination, additions, symbol):
    """O(target minutes) memory; copy all non-target lines without price parsing.

    After the last requested minute, remaining lines are copied as opaque text.
    Only collector's exact flat CSV schema is supported; no lossy extra columns.
    No earlier/later prices influence repair or selection. Target duplicates may
    be coalesced iff OHLCV/currency identical; conflicting values abort.
    """
    pending = iter(sorted(additions.items())); item = next(pending, None)
    target_seen = {}; inserted = unchanged = duplicates = 0; previous = None; past_target = False
    final_key = max(additions)
    opener = gzip.open if original.suffix == ".gz" else open
    with opener(original, "rt", encoding="utf-8", newline="") as source, destination.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as gz:
            with io.TextIOWrapper(gz, encoding="utf-8", newline="") as out:
                header = source.readline()
                if tuple(next(csv.reader([header]))) != FIELDS: raise ValueError("REPAIR_REQUIRES_COLLECTOR_EXACT_SCHEMA")
                out.write(header); writer = csv.DictWriter(out, fieldnames=FIELDS)
                for line in source:
                    if past_target:
                        out.write(line); unchanged += 1; continue
                    cells = next(csv.reader([line]))
                    if len(cells) != len(FIELDS): raise ValueError("INVALID_FLAT_CSV_ROW")
                    row = dict(zip(FIELDS, cells))
                    start = normalize_minute_timestamp(row["timestamp"], "end", None)
                    key = start.astimezone(UTC).isoformat()
                    if previous is not None and key < previous: raise ValueError("SOURCE_OUT_OF_ORDER")
                    previous = key
                    while item is not None and item[0] < key:
                        writer.writerow(item[1]); inserted += 1
                        item = next(pending, None)
                    if key > final_key:
                        past_target = True
                    if key in additions:
                        fp = fingerprint(row, symbol)
                        if fp != fingerprint(additions[key], symbol): raise ValueError("CONFLICTING_PRODUCTION_ROW")
                        if key in target_seen:
                            if target_seen[key] != fp: raise ValueError("CONFLICTING_PRODUCTION_DUPLICATE")
                            duplicates += 1; continue
                        target_seen[key] = fp
                        if item is not None and item[0] == key: item = next(pending, None)
                    out.write(line); unchanged += 1
                while item is not None:
                    writer.writerow(item[1]); inserted += 1; item = next(pending, None)
        raw.flush(); os.fsync(raw.fileno())
    return dict(insertedRows=inserted, unchangedRows=unchanged, identicalDuplicatesRemoved=duplicates, conflicts=0)


def prepare_merge(production, staging, out_dir, symbol):
    existed = Path(out_dir).exists()
    try:
        return _prepare_merge(production, staging, out_dir, symbol)
    except (ValueError, OSError) as exc:
        folder = outside_source(out_dir, production)
        if not existed and folder.is_dir():
            save(folder/"failure.json", dict(phase="FAILED", failedAt=now(), symbol=symbol, error=str(exc),
                                            conflicts=1 if "CONFLICTING" in str(exc) else 0, productionChanged=False))
        raise


def _prepare_merge(production, staging, out_dir, symbol):
    root = Path(production).resolve(); meta, sessions, rows = checked_stage(staging)
    if symbol not in rows: raise ValueError("SYMBOL_NOT_VALIDATED")
    folder = outside_source(out_dir, root)
    folder.parent.mkdir(parents=True, exist_ok=True)
    original = source_file(root, symbol)
    db = root/(symbol+".sqlite3")
    if db.is_symlink() or original.is_symlink(): raise ValueError("SYMLINK_PRODUCTION_SOURCE_FORBIDDEN")
    disk_guard(folder.parent, original.stat().st_size*12 + (db.stat().st_size*3 if db.exists() else 0))
    folder.mkdir(exist_ok=False)
    before = stable_signature(original); old_hash = file_sha256(original)
    files = []
    backup = folder/(symbol+".original"+original.suffix)
    durable_copy(original, backup)
    backup.chmod(0o400)
    merged = folder/(symbol+".merged.csv.gz")
    counts = merge_csv(original, merged, rows[symbol], symbol)
    merged_audit, _ = inspect_rows(read_csv_rows(merged), symbol, sessions)
    if not merged_audit["passed"]: raise ValueError("MERGED_CSV_INVALID")
    if before != stable_signature(original) or old_hash != file_sha256(original) or old_hash != file_sha256(backup):
        raise ValueError("SOURCE_CHANGED_DURING_PREPARATION")
    files.append(dict(destination=str(original), backup=backup.name, prepared=merged.name,
                      previousSha256=old_hash, newSha256=file_sha256(merged), sourceSignature=before, **counts))
    if db.exists():
        if original.suffix != ".gz": raise ValueError("SQLITE_PAIR_REQUIRES_CSV_GZ")
        wal = Path(str(db)+"-wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError("SQLITE_OFFLINE_PREPARATION_REQUIRED: stop writers and explicitly checkpoint/close before preparing")
        signature = db_signature(db); db_backup = folder/(symbol+".original.sqlite3"); db_merged = folder/(symbol+".merged.sqlite3")
        # Exact offline bytes preserve the original hash as well as all rows.
        durable_copy(db, db_backup)
        db_backup.chmod(0o400)
        durable_copy(db_backup, db_merged)
        inserted = unchanged = 0
        with closing(sqlite3.connect(db_merged)) as conn:
            conn.execute("PRAGMA journal_mode=DELETE")  # prepared copy only
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise ValueError("INVALID_SQLITE_COPY")
            _, existing = inspect_rows(db_rows(db_backup, sessions), symbol, sessions)
            lookup = {k: row for day in existing.values() for k, (_,row) in day.items()}
            for key, row in rows[symbol].items():
                if key in lookup:
                    if fingerprint(lookup[key], symbol) != fingerprint(row, symbol):
                        raise ValueError("CONFLICTING_SQLITE_ROW")
                    unchanged += 1
                else:
                    conn.execute("INSERT INTO candles ("+",".join(FIELDS)+") VALUES (?,?,?,?,?,?,?)", tuple(row[k] for k in FIELDS))
                    inserted += 1
            conn.commit()
        with db_merged.open("rb") as durable: os.fsync(durable.fileno())
        db_facts, _ = inspect_rows(db_rows(db_merged, sessions), symbol, sessions)
        if not collector_export_order(db_merged)["passed"]: raise ValueError("SQLITE_TEXT_EXPORT_ORDER_INVALID")
        if not db_facts["passed"] or signature != db_signature(db) or file_sha256(db) != file_sha256(db_backup): raise ValueError("SQLITE_INVALID_OR_CHANGED")
        # Production WAL must be quiescent before publication, even if preparation
        # can read it. Never delete/checkpoint a production WAL automatically.
        files.append(dict(destination=str(db), backup=db_backup.name, prepared=db_merged.name,
                          previousSha256=file_sha256(db), backupSha256=file_sha256(db_backup), newSha256=file_sha256(db_merged),
                          sourceSignature=stable_signature(db), dbSignature=signature,
                          insertedRows=inserted, unchangedRows=unchanged, conflicts=0))
    plan = dict(version=VERSION, operationId=uuid.uuid4().hex, createdAt=now(), phase="PREPARED", symbol=symbol,
                productionRoot=str(root), stagingRoot=str(Path(staging).resolve()), stagingSha256=file_sha256(Path(staging)/"validation.json"),
                repairedDates=list(sessions), sourceProvider="Toss", timestampKind="end", calendar="XNYS",
                finalOosGate="CLOSED", reviewStatus="PROVISIONAL_UNREVIEWED_DATA", files=files,
                atomicity="Per-file atomic. CSV+SQLite require offline operator window and durable recovery journal.")
    sync_directory(folder)
    save(folder/"merge.json", plan)
    return plan


def pending_journals(root):
    return list(root.glob(".toss-repair-*.json"))


def no_wal(plan):
    for entry in plan["files"]:
        path = Path(entry["destination"])
        if path.suffix == ".sqlite3":
            wal = Path(str(path)+"-wal")
            if wal.exists() and wal.stat().st_size:
                raise ValueError("PRODUCTION_WAL_ACTIVE: stop writers and close/checkpoint SQLite explicitly before preparing again")


def check_plan(plan):
    symbol = plan.get("symbol", "")
    if symbols_checked(symbol) != [symbol] or plan.get("version") != VERSION:
        raise ValueError("INVALID_MERGE_PLAN")
    sessions_checked(plan["repairedDates"])
    root = Path(plan["productionRoot"])
    if not root.is_absolute() or not root.is_dir(): raise ValueError("INVALID_PRODUCTION_ROOT")
    destinations = [Path(x["destination"]).name for x in plan["files"]]
    allowed = {symbol+".csv.gz", symbol+".csv", symbol+".sqlite3"}
    if not 1 <= len(destinations) <= 2 or len(set(destinations)) != len(destinations) or not set(destinations).issubset(allowed):
        raise ValueError("INVALID_MERGE_DESTINATIONS")
    if sum(x.endswith((".csv", ".csv.gz")) for x in destinations) != 1: raise ValueError("CSV_DESTINATION_REQUIRED")
    return root


def apply_merge(folder, *, confirmed=False):
    folder = Path(folder).resolve(); plan = json.loads((folder/"merge.json").read_text())
    root = check_plan(plan)
    if not confirmed: raise ValueError("EXPLICIT_OFFLINE_OPERATOR_CONFIRMATION_REQUIRED")
    if plan["phase"] != "PREPARED": raise ValueError("MERGE_ALREADY_USED_OR_INTERRUPTED")
    with (root/".toss-repair.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if pending_journals(root): raise ValueError("INTERRUPTED_MERGE_RECOVERY_REQUIRED")
        no_wal(plan)
        checked_stage(plan["stagingRoot"])
        if file_sha256(Path(plan["stagingRoot"])/"validation.json") != plan["stagingSha256"]:
            raise ValueError("STAGING_VALIDATION_CHANGED")
        for entry in plan["files"]:
            path = Path(entry["destination"])
            if path.parent.resolve() != root.resolve() or path.is_symlink(): raise ValueError("INVALID_PRODUCTION_PATH")
            if stable_signature(path) != entry["sourceSignature"] or file_sha256(path) != entry["previousSha256"]:
                raise ValueError("PRODUCTION_CHANGED_SINCE_PREPARE")
            if entry.get("dbSignature") and db_signature(path) != entry["dbSignature"]: raise ValueError("SQLITE_CHANGED_SINCE_PREPARE")
            for name, digest in ((entry["backup"], entry.get("backupSha256", entry["previousSha256"])), (entry["prepared"], entry["newSha256"])):
                file = artifact_path(folder,name)
                if file_sha256(file) != digest: raise ValueError("MERGE_ARTIFACT_CHANGED")
        disk_guard(root, sum((folder/x["prepared"]).stat().st_size for x in plan["files"]))
        temporaries = []
        journal = root/(".toss-repair-"+plan["operationId"]+".json")
        try:
            for entry in plan["files"]:
                path = Path(entry["destination"]); tmp = path.with_name("."+path.name+"."+plan["operationId"]+".tmp")
                with (folder/entry["prepared"]).open("rb") as source, tmp.open("xb") as output:
                    shutil.copyfileobj(source, output); output.flush(); os.fsync(output.fileno())
                shutil.copymode(path, tmp); temporaries.append(tmp)
                if file_sha256(tmp) != entry["newSha256"]: raise ValueError("TEMPORARY_COPY_HASH_MISMATCH")
            # Last validation immediately before any production replacement.
            for entry in plan["files"]:
                if file_sha256(Path(entry["destination"])) != entry["previousSha256"]: raise ValueError("PRODUCTION_CHANGED_BEFORE_REPLACE")
            plan.update(phase="APPLYING", startedAt=now())
            save(folder/"merge.json", plan); save(journal, dict(operationDir=str(folder), operationId=plan["operationId"]))
            for entry, tmp in zip(plan["files"], temporaries):
                os.replace(tmp, entry["destination"]); sync_directory(root)
            plan.update(phase="APPLIED", finishedAt=now())
            save(folder/"merge.json", plan)
            journal.unlink(); sync_directory(root)
        except BaseException:
            # Durable backups/journal remain. Ordinary exceptions roll back;
            # SIGKILL/power loss requires explicit recover, never auto-overwrite.
            if journal.exists(): recover_merge(folder, confirmed=True, locked=True)
            raise
        finally:
            for tmp in temporaries: tmp.unlink(missing_ok=True)
    return plan


def recover_merge(folder, *, confirmed=False, locked=False):
    folder = Path(folder).resolve(); plan = json.loads((folder/"merge.json").read_text()); root = check_plan(plan)
    if not confirmed: raise ValueError("EXPLICIT_OFFLINE_OPERATOR_CONFIRMATION_REQUIRED")
    if not locked:
        with (root/".toss-repair.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return recover_merge(folder, confirmed=True, locked=True)
    no_wal(plan)
    journal = root/(".toss-repair-"+plan["operationId"]+".json")
    if not journal.exists() or plan["phase"] not in ("APPLYING", "APPLIED"):
        raise ValueError("NO_INTERRUPTED_MERGE")
    for entry in plan["files"]:
        path = Path(entry["destination"])
        if path.parent.resolve() != root.resolve() or path.is_symlink(): raise ValueError("INVALID_PRODUCTION_PATH")
        if file_sha256(path) not in (entry["previousSha256"], entry["newSha256"], entry.get("backupSha256")):
            raise ValueError("RECOVERY_CONFLICT_REQUIRES_MANUAL_INSPECTION")
        if file_sha256(artifact_path(folder,entry["backup"])) != entry.get("backupSha256", entry["previousSha256"]):
            raise ValueError("BACKUP_HASH_MISMATCH")
    for entry in plan["files"]:
        path = Path(entry["destination"]); tmp = path.with_name("."+path.name+".restore."+plan["operationId"])
        with (folder/entry["backup"]).open("rb") as source, tmp.open("xb") as output:
            shutil.copyfileobj(source, output); output.flush(); os.fsync(output.fileno())
        shutil.copymode(path, tmp); os.replace(tmp, path); sync_directory(root)
    plan.update(phase="ROLLED_BACK", recoveredAt=now()); save(folder/"merge.json", plan)
    journal.unlink(); sync_directory(root)
    return plan


def snapshot(production, destination, symbols, operations):
    """Fresh bounded 2022-2025 campaign input; does not touch old campaigns.

    The campaign requires ALL reference symbols as well. Enforce 2024 complete
    coverage for those too; repairing TQQQ/AAPL alone cannot prove baseline valid.
    Original files are never relabelled as reviewed; provenance stays provisional.
    """
    root = Path(production).resolve()
    if pending_journals(root): raise ValueError("INTERRUPTED_MERGE_RECOVERY_REQUIRED")
    required = {"SPY", "QQQ", "SOXX"}
    for s in symbols: required.update(benchmark_for_symbol(s))
    if not required.issubset(symbols): raise ValueError("SNAPSHOT_REQUIRED_REFERENCES_MISSING: " + ",".join(sorted(required-set(symbols))))
    sessions = exchange_sessions("2024-01-02", "2024-12-31")
    coverage = audit(root, symbols, list(sessions))
    if not all(x["csv"]["passed"] for x in coverage["symbols"].values()): raise ValueError("FULL_2024_COVERAGE_REQUIRED")
    plans = [json.loads((Path(p)/"merge.json").read_text()) for p in operations]
    if not plans or any(p["phase"] != "APPLIED" or p["productionRoot"] != str(root) for p in plans):
        raise ValueError("APPLIED_REPAIR_PROVENANCE_REQUIRED")
    for p in plans:
        for f in p["files"]:
            if file_sha256(Path(f["destination"])) != f["newSha256"]: raise ValueError("POST_REPAIR_SOURCE_CHANGED")
    dest = outside_source(destination, root); dest.parent.mkdir(parents=True, exist_ok=True)
    disk_guard(dest.parent, sum(source_file(root,s).stat().st_size for s in symbols)*10)
    dest.mkdir(exist_ok=False); files = {}
    for symbol in symbols:
        path = source_file(root, symbol); before = stable_signature(path); digest = file_sha256(path)
        def bounded():
            for row in read_csv_rows(path):
                start = normalize_minute_timestamp(row["timestamp"], "end", None)
                day = start.date().isoformat()
                if day > "2025-12-31": break
                if day < "2022-01-03": continue
                fingerprint(row, symbol)
                if tuple(row) != FIELDS: raise ValueError("SNAPSHOT_REQUIRES_COLLECTOR_EXACT_SCHEMA")
                yield row
        target = dest/(symbol+".csv.gz"); write_csv(target, bounded())
        if before != stable_signature(path) or digest != file_sha256(path): raise ValueError("SOURCE_CHANGED_DURING_SNAPSHOT")
        files[symbol] = dict(path=target.name, previousSha256=digest, newSha256=file_sha256(target))
    result = dict(version=VERSION, snapshotId=uuid.uuid4().hex, createdAt=now(), sourceProvider="Toss", symbols=files,
                  repairedDates=sorted({d for p in plans for d in p["repairedDates"]}), repairOperations=plans,
                  timestampKind="end", calendar="XNYS", fromDate="2022-01-03", toDate="2025-12-31",
                  finalOosGate="CLOSED", researchOnly=True, reviewStatus="PROVISIONAL_UNREVIEWED_DATA", coverage=coverage)
    save(dest/"repair_snapshot.json", result)
    return result


def verify_snapshot(path, data_dir, symbols):
    path = Path(path).resolve(); root = Path(data_dir).resolve()
    if path.parent != root or path.name != "repair_snapshot.json": raise ValueError("SNAPSHOT_PATH_MISMATCH")
    info = json.loads(path.read_text())
    if info.get("version") != VERSION or info.get("timestampKind") != "end" or info.get("finalOosGate") != "CLOSED":
        raise ValueError("INVALID_REPAIR_SNAPSHOT")
    if info.get("fromDate") != "2022-01-03" or info.get("toDate") != "2025-12-31": raise ValueError("SNAPSHOT_PERIOD_MISMATCH")
    needed = set(symbols) | {"SPY", "QQQ", "SOXX"}
    for s in symbols: needed.update(benchmark_for_symbol(s))
    for symbol in needed:
        file = source_file(root, symbol)
        entry = info["symbols"].get(symbol)
        if not entry or entry["path"] != file.name or entry["newSha256"] != file_sha256(file):
            raise ValueError("SNAPSHOT_HASH_MISMATCH: " + symbol)
        if info["coverage"]["symbols"][symbol]["csv"]["passed"] is not True:
            raise ValueError("SNAPSHOT_COVERAGE_INVALID")
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("audit", "collect", "adopt", "validate", "prepare", "apply", "recover", "snapshot"):
        p = sub.add_parser(name)
        if name in ("audit", "collect", "adopt", "prepare", "snapshot"):
            p.add_argument("--data-dir", type=Path, default=ROOT/"data/toss_1m")
        if name in ("audit", "collect", "adopt"):
            p.add_argument("--symbols", default="TQQQ,AAPL")
            p.add_argument("--dates", default=",".join(DEFAULT_DATES))
            p.add_argument("--timestamp-kind", choices=["end"], required=True)
        if name == "audit": p.add_argument("--out", type=Path)
        if name in ("collect", "adopt", "validate", "prepare"): p.add_argument("--staging-dir", type=Path, required=True)
        if name == "adopt": p.add_argument("--input-staging-dir",type=Path,required=True)
        if name == "collect":
            p.add_argument("--max-pages-per-session", type=int, default=100)
            p.add_argument("--sleep", type=float, default=.5)
        if name == "prepare":
            p.add_argument("--symbol", required=True)
            p.add_argument("--operation-dir", type=Path, required=True)
        if name in ("apply", "recover"):
            p.add_argument("--operation-dir", type=Path, required=True)
            p.add_argument("--confirm-no-writers", action="store_true", help="Explicit offline maintenance acknowledgement; does not stop services")
        if name == "snapshot":
            p.add_argument("--out-dir", type=Path, required=True)
            p.add_argument("--symbols", default="TQQQ,AAPL,SPY,QQQ,SOXX")
            p.add_argument("--operation-dir", type=Path, action="append", required=True)
    args = parser.parse_args(argv)
    if args.command == "audit":
        result = audit(args.data_dir, symbols_checked(args.symbols), args.dates.split(","))
        if args.out:
            output = outside_source(args.out, args.data_dir)
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists(): raise ValueError("AUDIT_OUTPUT_ALREADY_EXISTS")
            save(output, result)
    elif args.command == "collect":
        if not 1 <= args.max_pages_per_session <= 1000 or args.sleep < 0: raise ValueError("INVALID_REQUEST_BOUNDS")
        result = collect(args.staging_dir, args.data_dir, symbols_checked(args.symbols), args.dates.split(","), pages=args.max_pages_per_session, sleep=args.sleep)
    elif args.command == "adopt": result = adopt_staging(args.input_staging_dir,args.staging_dir,args.data_dir,symbols_checked(args.symbols),args.dates.split(","))
    elif args.command == "validate": result = validate_stage(args.staging_dir)
    elif args.command == "prepare": result = prepare_merge(args.data_dir, args.staging_dir, args.operation_dir, symbols_checked(args.symbol)[0])
    elif args.command == "apply": result = apply_merge(args.operation_dir, confirmed=args.confirm_no_writers)
    elif args.command == "recover": result = recover_merge(args.operation_dir, confirmed=args.confirm_no_writers)
    else: result = snapshot(args.data_dir, args.out_dir, symbols_checked(args.symbols), args.operation_dir)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    if result.get("validationPassed") is False: return 2
    return 0
