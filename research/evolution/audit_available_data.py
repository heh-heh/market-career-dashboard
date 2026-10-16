#!/usr/bin/env python3
"""Audit every universe source read-only, with exact compressed missing-minute ranges."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"research"))
from research.evolution.available_data import audit_all
from research.evolution.audit_v07_data import resource_preflight


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir",type=Path,default=ROOT/"data/toss_1m")
    p.add_argument("--out-dir",type=Path,required=True)
    p.add_argument("--symbols",help="Default: ALL universe_v3 symbols, never just AAPL/TQQQ")
    p.add_argument("--from-date",default="2022-01-01");p.add_argument("--to-date",default="2025-12-31")
    p.add_argument("--timestamp-kind",choices=["end"],default="end")
    p.add_argument("--naive-timezone",choices=["UTC","America/New_York"])
    p.add_argument("--require-ebs",action="store_true")
    a=p.parse_args(argv)
    try:
        resource_preflight(a.out_dir,require_research_mount=a.require_ebs)
        directory=audit_all(a.data_dir,a.out_dir,a.from_date,a.to_date,a.symbols.split(",") if a.symbols else None,a.naive_timezone)
    except (ValueError,OSError) as exc:p.exit(2,f"AUDIT BLOCKED: {exc}\n")
    result=json.loads((directory/"audit.json").read_text())
    print(json.dumps(dict(auditDir=str(directory),symbols=len(result["symbols"]),sessions=result["sessions"],
        eligibleSessions=result["eligibleSessions"],rawUniverseComplete=result["rawUniverseComplete"],
        finalOosGate="CLOSED",dataMode=result["dataMode"])))
    # Completed audit is not equivalent to complete data; missingness is evidence,
    # not a fatal command error. Invalid schema/conflicts/unstable source still fail.
    return directory


if __name__=="__main__":main()
