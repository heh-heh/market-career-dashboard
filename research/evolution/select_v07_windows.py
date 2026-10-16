#!/usr/bin/env python3
"""Select consecutive five-session TRAIN windows from verified availability evidence."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"research"))
from research.evolution.available_data import select_windows
from research.evolution.audit_v07_data import resource_preflight


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--audit-dir",type=Path,required=True);p.add_argument("--out-dir",type=Path,required=True)
    p.add_argument("--require-ebs",action="store_true")
    a=p.parse_args(argv)
    try:
        resource_preflight(a.out_dir,require_research_mount=a.require_ebs)
        path=select_windows(a.audit_dir,a.out_dir)
    except (ValueError,OSError) as exc:p.exit(2,f"SELECTION BLOCKED: {exc}\n")
    plan=json.loads(path.read_text())
    print(json.dumps(dict(plan=str(path),usableWindows=plan["usableNonoverlappingWindows"],
        rollingWindows=plan["potentialRollingWindows"],excludedTrainingSessions=plan["excludedTrainingSessions"],
        smokeRange=plan["smokeRange"],finalOosGate="CLOSED")))
    return path


if __name__=="__main__":main()
