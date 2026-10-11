#!/usr/bin/env python3
"""Create a frozen V0.7 shortlist from an internal holdout only."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from research.backtest_intraday_v4 import atomic_json


def build_shortlist(training_run, holdout_run, out_dir, max_count=40):
    training_run=Path(training_run).resolve()
    holdout_run=Path(holdout_run).resolve()
    out_dir=Path(out_dir).resolve()
    train_info=json.loads((training_run/"run.json").read_text(encoding="utf-8"))
    population=json.loads((training_run/"final_population.json").read_text(encoding="utf-8"))
    holdout_info=json.loads((holdout_run/"run.json").read_text(encoding="utf-8"))
    holdout=json.loads((holdout_run/"validation_summary.json").read_text(encoding="utf-8"))

    by_id={r["bot_id"]:r for r in population}
    candidates=[]
    for row in holdout["bots"]:
        if row.get("invalidDays"):
            continue
        ret=float(row.get("validationReturn") or 0.0)
        if ret <= 0:
            continue
        src=by_id.get(row["botId"])
        if src is None or src.get("genome_hash") != row.get("genomeHash"):
            raise ValueError(f"Holdout/source genome mismatch: {row['botId']}")
        candidates.append((ret, row["botId"], src, row))
    candidates.sort(key=lambda x:(-x[0],x[1]))
    selected=candidates[:int(max_count)]
    if not selected:
        raise ValueError("No positive valid genomes in internal holdout")

    out_dir.mkdir(parents=True,exist_ok=False)
    records=[x[2] for x in selected]
    atomic_json(out_dir/"final_population.json",records)
    selection=[{
        "botId":x[1],
        "holdoutReturn":x[0],
        "trades":x[3].get("trades"),
        "transactionCost":x[3].get("transactionCost"),
        "genomeHash":x[3].get("genomeHash"),
    } for x in selected]
    atomic_json(out_dir/"shortlist.json",{
        "selectionBasis":"POSITIVE_INTERNAL_HOLDOUT_THEN_RETURN_RANK",
        "maxCount":int(max_count),
        "eligiblePositiveBots":len(candidates),
        "selectedBots":selection,
    })
    derived=dict(train_info)
    derived.update(
        phase="completed",
        runId=out_dir.name,
        evaluationMode="SHORTLIST_ARTIFACT",
        selectionMode="V07_INTERNAL_HOLDOUT_SHORTLIST",
        shortlistCount=len(records),
        holdoutRunId=holdout_info.get("runId") or holdout_run.name,
        holdoutFrom=holdout_info.get("fromDate"),
        holdoutTo=holdout_info.get("toDate"),
        sourceTrainingRunId=train_info.get("runId") or training_run.name,
        sourceTrainingRunDir=str(training_run),
        sourceTrainFrom=train_info.get("fromDate"),
        sourceTrainTo=train_info.get("toDate"),
        completedSessions=holdout_info.get("completedSessions"),
        requestedSessions=holdout_info.get("requestedSessions"),
    )
    atomic_json(out_dir/"run.json",derived)
    return out_dir


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--training-run",required=True)
    p.add_argument("--holdout-run",required=True)
    p.add_argument("--out-dir",required=True)
    p.add_argument("--max-count",type=int,default=40)
    args=p.parse_args(argv)
    out=build_shortlist(args.training_run,args.holdout_run,args.out_dir,args.max_count)
    print(json.dumps({"shortlistDir":str(out)},sort_keys=True))
    return out


if __name__=="__main__":
    main()
