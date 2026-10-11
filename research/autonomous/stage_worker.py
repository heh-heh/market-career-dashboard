"""Single allowlisted frozen-development evaluation; no caller-supplied shell."""
import argparse
import csv
import gzip
import heapq
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.intraday_v4_engine import Bar
from research.backtest_v4_strategies import normalize_minute_timestamp, MINUTE
from research.evolution import evaluate_population_v03 as frozen
from research.autonomous.experiment_registry import read_json, write_json
from research.autonomous.safety_gate import period_gate, data_path_gate
from research.evolution.features import benchmark_for_symbol
from research.build_v4_data_manifest import source_file
from research.backtest_v4_strategies import validate_data_manifest, manifest_input_path, file_sha256


def prepare_bounded_inputs(args, symbols):
    """Original reviewed contract, or an explicit date-fenced mechanical audit.

    Hashes bind the original complete file. A provisional audit parses OHLCV
    only within the development year; no 2026 price/volume validation features
    are computed. This is deliberately labelled range-scoped provisional data.
    """
    needed = set(symbols) | {"SPY", "QQQ", "SOXX"}
    data_path_gate(args.data_dir)
    for s in symbols: needed.update(benchmark_for_symbol(s))
    if args.data_manifest is not None:
        declared = validate_data_manifest(args.data_manifest, needed, "end")
        for s in needed: data_path_gate(manifest_input_path(args.data_dir, s, declared["symbols"][s]))
        manifest = validate_data_manifest(args.data_manifest, needed, "end", data_dir=args.data_dir)
        paths = {s: manifest_input_path(args.data_dir, s, manifest["symbols"][s]) for s in sorted(needed)}
        return paths, manifest, "REVIEWED_DATA"
    if not args.provisional:
        raise ValueError("REVIEWED_MANIFEST_REQUIRED")
    paths = {s: source_file(args.data_dir, s) for s in sorted(needed)}
    records = {}
    for symbol, path in paths.items():
        before = path.stat()
        digest = file_sha256(path)
        meta = {"symbol": symbol, "path": path.name, "sha256": digest,
                "timestamp_kind": "end", "naive_timezone": args.naive_timezone,
                "timestamp_semantics": "exclusive_minute_end", "priceAdjustmentConfirmed": False,
                "auditMode": "BOUNDED_2025_PROVISIONAL", "auditFrom": "2025-01-02", "auditTo": "2025-12-31"}
        count = sum(1 for _ in bounded_bars(path, symbol, meta, "2025-01-02", "2025-12-31"))
        after = path.stat()
        if not count or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or file_sha256(path) != digest:
            raise ValueError("SOURCE_CHANGED_OR_EMPTY_IN_DEVELOPMENT_PERIOD")
        meta["rowsAudited"] = count
        records[symbol] = meta
    return paths, {"symbols": records, "timestampKind": "end", "priceAdjustmentConfirmed": False,
                   "priceAdjustment": "unknown", "calendar": "XNYS"}, "PROVISIONAL_UNREVIEWED_DATA"


def bounded_bars(path, symbol, meta, first, last):
    """Date fence before constructing price bars. No future benchmark features.

    Full-file hashes remain mechanical integrity checks, never feature inputs.
    A shared multi-year CSV may contain Final OOS; its price values beyond the
    cutoff are neither converted nor exposed to the simulation.
    """
    data_path_gate(path)
    opener = gzip.open if Path(path).suffix == ".gz" else open
    previous = None
    previous_quotes = None
    with opener(path, "rt", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        if not {"timestamp", "open", "high", "low", "close", "volume"}.issubset(columns) or len(columns) != len(set(columns)):
            raise ValueError("Invalid/duplicate OHLCV schema")
        previous_stamp = None
        for row in reader:
            start = normalize_minute_timestamp(row["timestamp"], "end", meta["naive_timezone"])
            day = start.date().isoformat()
            if previous_stamp is not None and start < previous_stamp:
                raise ValueError("Out-of-order data before date fence")
            previous_stamp = start
            if day > last:
                break
            if day < first:
                continue
            period_gate(day, day, "development_validation")
            if any((row.get(k) or "").strip().upper() != symbol for k in ("symbol", "ticker") if k in columns):
                raise ValueError("Symbol identity mismatch")
            if row.get("currency") and row["currency"].strip().upper() != "USD":
                raise ValueError("Non-USD data")
            bid = float(row["bid"]) if row.get("bid") else None
            ask = float(row["ask"]) if row.get("ask") else None
            if (bid is None) != (ask is None) or (bid is not None and (not math.isfinite(bid + ask) or not 0 < bid <= ask)):
                raise ValueError("Invalid/partial quote")
            values = [float(row[k]) for k in ("open", "high", "low", "close", "volume")]
            bar = Bar(start, start + MINUTE, *values)
            if not all(math.isfinite(x) for x in values) or min(values[:4]) <= 0 or values[4] < 0 or bar.h < max(bar.o, bar.c) or bar.l > min(bar.o, bar.c) or bar.h < bar.l:
                raise ValueError("Invalid OHLCV")
            if previous and bar.start < previous.start:
                raise ValueError("Out-of-order data")
            if previous and bar.start == previous.start:
                if bar != previous or (bid, ask) != previous_quotes:
                    raise ValueError("Conflicting duplicate")
                continue
            previous = bar
            previous_quotes = (bid, ask)
            yield bar


def bounded_day_stream(paths, manifest, first="2025-01-02", last="2025-12-31"):
    period_gate(first, last, "development_validation")
    streams = {}; heap = []; rows = {}; day = None
    try:
        for symbol, path in sorted(paths.items()):
            streams[symbol] = iter(bounded_bars(path, symbol, manifest["symbols"][symbol], first, last))
            bar = next(streams[symbol], None)
            if bar: heapq.heappush(heap, (bar.start, symbol, bar))
        while heap:
            _, symbol, bar = heapq.heappop(heap)
            current = bar.start.date().isoformat()
            if day is not None and current != day:
                yield day, rows
                rows = {}
            day = current
            rows.setdefault(symbol, {})[bar.start] = bar
            bar = next(streams[symbol], None)
            if bar: heapq.heappush(heap, (bar.start, symbol, bar))
        if day: yield day, rows
    finally:
        for stream in streams.values():
            stream.close()


def main(argv=None):
    p = argparse.ArgumentParser(description="Internal autonomous stage: frozen 2025 evaluation only")
    p.add_argument("--iteration", type=Path, required=True)
    args = p.parse_args(argv)
    folder = args.iteration.absolute()
    settings = read_json(folder / "config.json")
    source = folder / "shortlist-v08"
    period_gate("2025-01-02", "2025-12-31", "development_validation")
    output = folder / "validation-attempts"
    output.mkdir(exist_ok=True)
    # Preserve legacy evaluator without allowing it to materialize one future day.
    frozen.day_stream = bounded_day_stream
    frozen.prepare_inputs = prepare_bounded_inputs
    command = ["--source-run", str(source), "--data-dir", settings["dataDir"],
               "--results-dir", str(output), "--timestamp-kind", "end",
               "--from-date", "2025-01-02", "--to-date", "2025-12-31",
               "--expected-split", "none", "--evaluation-label", "development_validation"]
    if settings.get("dataManifest"):
        command += ["--data-manifest", settings["dataManifest"]]
    elif settings.get("provisional"):
        command += ["--provisional"]
    else:
        raise ValueError("REVIEWED_MANIFEST_REQUIRED: no implicit provisional fallback")
    if settings.get("naiveTimezone"):
        command += ["--naive-timezone", settings["naiveTimezone"]]
    run = frozen.main(command)
    write_json(folder / "validation_run.json", {"path": str(run), "run": read_json(run / "run.json")})


if __name__ == "__main__":
    main()
