#!/usr/bin/env python3
"""Incrementally collect the expanded V3 research universe.

Existing symbol history is preserved. Symbols whose persisted history already
covers the requested start/end window are skipped. New/missing symbols are
collected sequentially to stay within Toss API limits.
"""
from __future__ import annotations
import argparse
import gzip
import csv
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"/"toss_1m"
UNIVERSE=ROOT/"research"/"universe_v3.json"
STATE=ROOT/"data"/"expanded_universe_progress.json"
COLLECTOR=ROOT/"scripts"/"collect_toss_1m.py"


def load_symbols():
    obj=json.loads(UNIVERSE.read_text(encoding="utf-8"))
    return [str(x).upper() for x in obj.get("symbols",[]) if x]


def bounds_csv(path):
    if not path.exists():
        return 0,None,None
    count=0; first=None; last=None
    try:
        with gzip.open(path,"rt",encoding="utf-8",newline="") as fp:
            for r in csv.DictReader(fp):
                ts=r.get("timestamp")
                if not ts: continue
                count+=1
                if first is None or ts<first:first=ts
                if last is None or ts>last:last=ts
    except Exception:
        return 0,None,None
    return count,first,last


def bounds_sqlite(path):
    if not path.exists():
        return 0,None,None
    try:
        con=sqlite3.connect(path)
        row=con.execute("SELECT COUNT(*),MIN(timestamp),MAX(timestamp) FROM candles").fetchone()
        con.close()
        return int(row[0] or 0),row[1],row[2]
    except Exception:
        return 0,None,None


def bounds(symbol):
    db=DATA/f"{symbol}.sqlite3"
    csvp=DATA/f"{symbol}.csv.gz"
    a=bounds_sqlite(db)
    b=bounds_csv(csvp)
    return a if a[0]>=b[0] else b


def write_state(obj):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    tmp=STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(tmp,STATE)


def covered(oldest,latest,since,until):
    if not oldest or not latest:return False
    return oldest<=since and latest>=until


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--since",default="2021-01-01T00:00:00+00:00")
    ap.add_argument("--until",default="2026-10-01T23:59:59+00:00")
    ap.add_argument("--sleep-between-symbols",type=float,default=3.0)
    args=ap.parse_args()

    symbols=load_symbols()
    state={
      "phase":"starting","running":True,"symbols":symbols,
      "totalSymbols":len(symbols),"completedSymbols":[],
      "skippedSymbols":[],"failedSymbols":[],
      "currentSymbol":None,"updatedAt":time.time()
    }
    write_state(state)

    for idx,symbol in enumerate(symbols,1):
        rows,oldest,latest=bounds(symbol)
        state.update(currentSymbol=symbol,currentIndex=idx,storedRows=rows,
                     oldestTimestamp=oldest,latestTimestamp=latest,updatedAt=time.time())
        if covered(oldest,latest,args.since,args.until):
            state["skippedSymbols"].append(symbol)
            state["completedSymbols"].append(symbol)
            state["progress"]=round(100*len(state["completedSymbols"])/len(symbols),1)
            write_state(state)
            continue

        state["phase"]="collecting"
        write_state(state)
        cmd=[
          sys.executable,str(COLLECTOR),"--symbols",symbol,
          "--since",args.since,"--until",args.until
        ]
        rc=subprocess.run(cmd,cwd=str(ROOT)).returncode
        if rc==0:
            state["completedSymbols"].append(symbol)
        else:
            state["failedSymbols"].append(symbol)
        rows,oldest,latest=bounds(symbol)
        state.update(storedRows=rows,oldestTimestamp=oldest,latestTimestamp=latest,
                     progress=round(100*len(state["completedSymbols"])/len(symbols),1),
                     updatedAt=time.time())
        write_state(state)
        time.sleep(args.sleep_between_symbols)

    state.update(phase="completed" if not state["failedSymbols"] else "completed_with_errors",
                 running=False,currentSymbol=None,progress=100.0,updatedAt=time.time())
    write_state(state)


if __name__=="__main__":
    main()
