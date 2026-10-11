#!/usr/bin/env python3
"""Export an immutable, bounded pre-2026 research data bundle for Codex.

No network, no OAuth, no production writes; source is the isolated collection.
Never include 2026 Final OOS rows, secrets, or token caches.
"""
import argparse
import csv
import gzip
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def export(root, output, symbols, years):
    root = Path(root).resolve()
    output = Path(output).resolve()
    if root == output or root in output.parents or output in root.parents:
        raise ValueError("OUTPUT_MUST_BE_OUTSIDE_SOURCE")
    if output.exists():
        raise FileExistsError("NEW_EXPORT_DIRECTORY_REQUIRED")
    if not years or not set(years) <= {2022, 2023, 2024, 2025}:
        raise ValueError("FINAL_OOS_CLOSED")
    if not symbols or len(symbols) > 50 or len(set(symbols)) != len(symbols):
        raise ValueError("INVALID_SYMBOLS")
    for symbol in symbols:
        if not re.fullmatch(r"[A-Z0-9]{1,8}", symbol):
            raise ValueError("INVALID_SYMBOL")
        src = root / (symbol + ".csv.gz")
        if not src.is_file() or src.is_symlink() or src.resolve().parent != root:
            raise FileNotFoundError(symbol)
    output.mkdir(parents=True)
    report = {"researchOnly": True, "finalOosGate": "CLOSED",
              "verifiedComplete": False, "years": sorted(years), "files": []}
    for symbol in symbols:
        src = root / (symbol + ".csv.gz")
        temp = {}
        writers = {}
        counts = {year: 0 for year in years}
        try:
            for year in sorted(years):
                handle = gzip.open(output / f"{symbol}-{year}.csv.gz", "wt", newline="", encoding="utf-8")
                temp[year] = handle
                writers[year] = csv.writer(handle)
                writers[year].writerow(["timestamp","open","high","low","close","volume","currency"])
            with gzip.open(src, "rt", newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                for row in reader:
                    try:
                        dt = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            continue
                        year = dt.astimezone(ET).year
                    except (KeyError, TypeError, ValueError):
                        continue
                    if year not in writers:
                        continue
                    writers[year].writerow([row.get(k, "") for k in
                        ("timestamp","open","high","low","close","volume","currency")])
                    counts[year] += 1
        finally:
            for stream in temp.values():
                stream.close()
        for year in sorted(years):
            path = output / f"{symbol}-{year}.csv.gz"
            digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
            report["files"].append({"name": path.name, "sha256": digest, "rows": counts[year]})
    (output / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="/mnt/research/collection-50-v1")
    ap.add_argument("--output", required=True)
    ap.add_argument("--symbols", default="AAPL,TQQQ")
    ap.add_argument("--years", default="2022,2023,2024,2025")
    a = ap.parse_args()
    symbols = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    years = [int(x) for x in a.years.split(",") if x]
    manifest = export(a.source, a.output, symbols, years)
    print(json.dumps({"output": str(Path(a.output).resolve()),
        "fileCount": len(manifest["files"]), "verifiedComplete": False}))


if __name__ == "__main__":
    main()
