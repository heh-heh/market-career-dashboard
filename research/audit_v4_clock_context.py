#!/usr/bin/env python3
"""Audit V4 15:30 clock/data coverage without running or tuning strategies."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter
from datetime import datetime, time
from pathlib import Path

try:
    from .backtest_v4_strategies import NY, exchange_sessions, normalize_minute_timestamp
    from .build_v4_data_manifest import source_file
except ImportError:
    from backtest_v4_strategies import NY, exchange_sessions, normalize_minute_timestamp
    from build_v4_data_manifest import source_file

ROOT=Path(__file__).resolve().parents[1]
CLOCKS=("15:28","15:29","15:30","15:31","15:50","15:59")


def parse_clock(value):
    h,m=map(int,value.split(":"))
    return time(h,m)


def inspect_symbol(path,symbol,schedules,timestamp_kind,naive_timezone):
    wanted={parse_clock(x):x for x in CLOCKS}
    days={d:dict(expected=int((close-open_).total_seconds()/60),actual=0,labels=set(),
                 duplicates=0,outOfOrder=0,first=None,last=None,earlyClose=int((close-open_).total_seconds()/60)!=390)
          for d,(open_,close) in schedules.items()}
    previous=None
    seen_day=None
    seen=set()
    opener=gzip.open if path.suffix==".gz" else open
    with opener(path,"rt",encoding="utf-8",newline="") as stream:
        reader=csv.DictReader(stream)
        for row in reader:
            start=normalize_minute_timestamp(row["timestamp"],timestamp_kind,naive_timezone)
            day=start.date().isoformat()
            if day not in schedules:
                continue
            opening,closing=schedules[day]
            if not opening<=start<closing:
                continue
            rec=days[day]
            if previous is not None and start<previous:
                rec["outOfOrder"]+=1
            previous=start
            if seen_day!=day:
                seen_day=day; seen=set()
            if start in seen:
                rec["duplicates"]+=1
            else:
                seen.add(start); rec["actual"]+=1
            if rec["first"] is None or start<rec["first"]: rec["first"]=start
            if rec["last"] is None or start>rec["last"]: rec["last"]=start
            if start.time() in wanted:
                rec["labels"].add(wanted[start.time()])
    agg=Counter()
    samples=[]
    decision_days=set()
    for day,rec in days.items():
        agg["sessionsTotal"]+=1
        if rec["earlyClose"]: agg["earlyCloseSessions"]+=1
        if rec["actual"]==rec["expected"]: agg["completeRegularSessions"]+=1
        else: agg["incompleteRegularSessions"]+=1
        if "15:30" in rec["labels"]: agg["sessionsWith1530StartLabel"]+=1
        # At decision time 15:30, the last completed START-labeled bar starts 15:29.
        if "15:29" in rec["labels"]:
            agg["sessionsWithDecisionInputBar"]+=1
            decision_days.add(day)
        else:
            agg["sessionsMissingDecisionInputBar"]+=1
        agg["missingRegularMinutes"]+=max(0,rec["expected"]-rec["actual"])
        agg["duplicates"]+=rec["duplicates"]
        agg["outOfOrder"]+=rec["outOfOrder"]
        if (rec["actual"]!=rec["expected"] or "15:29" not in rec["labels"]) and len(samples)<10:
            samples.append(dict(date=day,expected=rec["expected"],actual=rec["actual"],
                labels=sorted(rec["labels"]),first=rec["first"].isoformat() if rec["first"] else None,
                last=rec["last"].isoformat() if rec["last"] else None,earlyClose=rec["earlyClose"]))
    return dict(symbol=symbol,path=str(path),summary=dict(agg),problemSamples=samples),decision_days


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols",required=True,help="Comma-separated symbols")
    p.add_argument("--data-dir",type=Path,default=ROOT/"data/toss_1m")
    p.add_argument("--timestamp-kind",choices=("start","end"),required=True)
    p.add_argument("--naive-timezone",choices=("America/New_York","UTC"))
    p.add_argument("--from-date",required=True)
    p.add_argument("--to-date",required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args(argv)
    if a.from_date>a.to_date:
        p.error("--from-date must be <= --to-date")
    schedules=exchange_sessions(a.from_date,a.to_date)
    symbols=list(dict.fromkeys(x.strip().upper() for x in a.symbols.split(",") if x.strip()))
    if not symbols:
        p.error("--symbols is empty")
    result=dict(
        version=1,
        purpose="V4_CLOCK_CONTEXT_DIAGNOSTIC_ONLY",
        timestampKind=a.timestamp_kind,
        normalizedTimezone="America/New_York",
        decisionTime="15:30 America/New_York",
        causalityNote="For START labels, a 15:30 decision may use the completed 15:29 START bar; the 15:30 START bar is not complete until 15:31.",
        fromDate=a.from_date,toDate=a.to_date,
        calendarSessions=len(schedules),
        regularFullSessions=sum(int((c-o).total_seconds()/60)==390 for o,c in schedules.values()),
        earlyCloseSessions=sum(int((c-o).total_seconds()/60)!=390 for o,c in schedules.values()),
        symbols={},benchmarkCoverage={}
    )
    decision_days={}
    for symbol in symbols:
        info,days=inspect_symbol(source_file(a.data_dir,symbol),symbol,schedules,a.timestamp_kind,a.naive_timezone)
        result["symbols"][symbol]=info
        decision_days[symbol]=days
    if "SPY" in decision_days and "QQQ" in decision_days:
        both=decision_days["SPY"] & decision_days["QQQ"]
        result["benchmarkCoverage"]=dict(
            sessionsWithBothDecisionInputBars=len(both),
            sessionsMissingEitherDecisionInputBar=len(schedules)-len(both),
            missingExamples=sorted(set(schedules)-both)[:10],
        )
    a.out.parent.mkdir(parents=True,exist_ok=True)
    tmp=a.out.with_name(a.out.name+".tmp")
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(a.out)
    print(json.dumps(dict(out=str(a.out),benchmarkCoverage=result["benchmarkCoverage"],
                          symbols={k:v["summary"] for k,v in result["symbols"].items()}),ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
