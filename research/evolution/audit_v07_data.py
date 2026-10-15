#!/usr/bin/env python3
"""Read-only source/coverage audit for V07-R1. Never repairs or downloads data."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"research"))
from research.backtest_intraday_v4 import atomic_json
from research.backtest_v4_strategies import exchange_sessions, file_sha256
from research.build_v4_data_manifest import source_file
from research.evolution.completeness import SessionCoverage
from research.evolution.data_access import check_dates, inspect_window, scheduled_days
from research.evolution.features import benchmark_for_symbol

KNOWN_DATES = ("2024-07-26", "2024-07-29", "2024-07-30", "2024-07-31", "2024-08-01", "2024-08-02")


def storage_info(path):
    actual = Path(path).resolve()
    while not actual.exists():
        actual = actual.parent
    result = subprocess.run(["findmnt", "--json", "--target", str(actual), "--output", "TARGET,SOURCE,FSTYPE"],
                            capture_output=True, text=True, check=True)
    return dict(path=str(Path(path).resolve()), mount=json.loads(result.stdout)["filesystems"][0],
                diskFreeBytes=shutil.disk_usage(actual).free)


def resource_preflight(output, *, require_research_mount=False):
    info = storage_info(output)
    root_mount = storage_info("/")["mount"]
    research_mount = storage_info("/mnt/research")["mount"]
    separate = research_mount["target"] == "/mnt/research" and research_mount["source"] != root_mount["source"]
    info["researchMountSeparate"] = separate
    if require_research_mount and (not separate or info["mount"]["target"] != "/mnt/research"):
        raise ValueError("RESEARCH_MOUNT_REQUIRED: output must be on separately mounted /mnt/research")
    existing = Path(output).resolve()
    while not existing.exists():
        existing = existing.parent
    disk = shutil.disk_usage(existing)
    if disk.free < max(10*1024**3, disk.total*.1):
        raise ValueError("RESOURCE_GUARD_PAUSED: less than max(10GiB,10%) disk free")
    return info


def audit(data_dir, symbols, first, last, *, naive_timezone=None):
    check_dates(first, last)
    allowed = set(json.loads((ROOT/"research/universe_v3.json").read_text())["symbols"])
    if not symbols or not set(symbols) <= allowed:
        raise ValueError("Choose symbols from universe_v3")
    needed = set(symbols) | {"SPY", "QQQ", "SOXX"}
    for s in symbols:
        needed.update(benchmark_for_symbol(s))
    paths = {s: source_file(Path(data_dir), s) for s in sorted(needed)}
    meta = {s: inspect_window(p,s,first,last,naive_timezone) for s,p in paths.items()}
    calendar = exchange_sessions(first, last)
    coverage = SessionCoverage(paths)
    repaired = {s: {} for s in ("AAPL", "TQQQ") if s in paths}
    days = 0
    for day, rows in scheduled_days(paths, {"symbols": meta}, calendar):
        opening, closing = calendar[day]
        coverage.observe(day,rows,opening,closing)
        days += 1
        if day in KNOWN_DATES:
            from research.backtest_v4_strategies import MINUTE
            for symbol in repaired:
                expected = int((closing-opening)/MINUTE)
                observed = sum(opening+i*MINUTE in rows.get(symbol,{}) for i in range(expected))
                repaired[symbol][day] = dict(observedMinutes=observed, expectedMinutes=expected, complete=observed==expected)
    for s,p in paths.items():
        if file_sha256(p) != meta[s]["sha256"]:
            raise ValueError(f"{s}: source mutated during coverage audit")
    return dict(version="V07_R1_READ_ONLY_AUDIT_1", generatedAt=datetime.now(timezone.utc).isoformat(),
                dataRoot=str(Path(data_dir).resolve()), timestampKind="end", calendar="XNYS",
                finalOosGate="CLOSED", fromDate=first, toDate=last, symbols=meta,
                requestedSessions=len(calendar), sessionsAudited=days, coverage=coverage.snapshot(),
                known2024RepairCheck=repaired, mechanicalPass=coverage.incomplete==0,
                priceAdjustmentConfirmed=False, dataMode="PROVISIONAL_UNREVIEWED_DATA",
                warning="Mechanical coverage is not reviewed adjustment/identity provenance; no manifest review flags changed",
                storage=storage_info(data_dir))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir",type=Path,default=ROOT/"data/toss_1m")
    p.add_argument("--symbols",default="AAPL,TQQQ")
    p.add_argument("--from-date",default="2022-01-01")
    p.add_argument("--to-date",default="2025-12-31")
    p.add_argument("--timestamp-kind",choices=["end"],default="end")
    p.add_argument("--naive-timezone",choices=["UTC","America/New_York"])
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--require-ebs",action="store_true")
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("Refusing to overwrite an existing research artifact")
    data=args.data_dir.resolve(); out=args.out.resolve()
    if data==out or data in out.parents or (ROOT/"data").resolve() in out.parents:
        p.error("Audit output must be outside raw data")
    try:
        storage=resource_preflight(out,require_research_mount=args.require_ebs)
        result=audit(data, sorted(set(args.symbols.split(","))), args.from_date,args.to_date, naive_timezone=args.naive_timezone)
        result["outputStorage"]=storage
        atomic_json(out,result)
    except (ValueError,OSError) as exc:
        p.exit(2,f"AUDIT BLOCKED: {exc}\n")
    print(json.dumps(dict(out=str(out),mechanicalPass=result["mechanicalPass"],coverage=result["coverage"],finalOosGate="CLOSED")))
    return 0 if result["mechanicalPass"] else 2


if __name__=="__main__":
    raise SystemExit(main())
