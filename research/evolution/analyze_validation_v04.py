#!/usr/bin/env python3
"""Evolution V0.4 generalization diagnostics for an existing frozen validation run."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics


def _rank(values):
    order=sorted(range(len(values)), key=lambda i:(values[i],i))
    ranks=[0.0]*len(values)
    i=0
    while i<len(order):
        j=i
        while j+1<len(order) and values[order[j+1]]==values[order[i]]:
            j+=1
        r=(i+j)/2+1
        for k in range(i,j+1):
            ranks[order[k]]=r
        i=j+1
    return ranks


def _pearson(a,b):
    if len(a)<2 or len(a)!=len(b):
        return None
    ma,mb=statistics.mean(a),statistics.mean(b)
    da=[x-ma for x in a]; db=[y-mb for y in b]
    va=sum(x*x for x in da); vb=sum(y*y for y in db)
    if va<=0 or vb<=0:
        return None
    return sum(x*y for x,y in zip(da,db))/math.sqrt(va*vb)


def _spearman(a,b):
    return _pearson(_rank(a),_rank(b)) if len(a)>=2 else None


def _latest_validation(root):
    root=Path(root)
    runs=[]
    if root.exists():
        for d in root.iterdir():
            if not d.is_dir() or not (d/"run.json").is_file() or not (d/"validation_summary.json").is_file():
                continue
            try:
                info=json.loads((d/"run.json").read_text(encoding="utf-8"))
            except (OSError,json.JSONDecodeError):
                continue
            if info.get("phase")=="completed" and info.get("evaluationMode")=="FROZEN_VALIDATION":
                runs.append(d)
    if not runs:
        raise ValueError("No completed frozen validation run found")
    return sorted(runs,key=lambda p:p.name)[-1]


def analyze(run_dir):
    run_dir=Path(run_dir).resolve()
    info=json.loads((run_dir/"run.json").read_text(encoding="utf-8"))
    summary=json.loads((run_dir/"validation_summary.json").read_text(encoding="utf-8"))
    source=Path(info["sourceRunDir"])
    source_pop=json.loads((source/"final_population.json").read_text(encoding="utf-8"))

    train={r["bot_id"]:r for r in source_pop}
    rows=[]
    for v in summary["bots"]:
        t=train.get(v["botId"])
        if t is None or t.get("genome_hash")!=v.get("genomeHash"):
            raise ValueError(f"Source/validation genome mismatch: {v['botId']}")
        rows.append({
            "botId":v["botId"],
            "genomeHash":v["genomeHash"],
            "trainLifetimeReturn":float(t.get("lifetime_return") or 0.0),
            "trainLifetimeDays":int(t.get("lifetime_days") or 0),
            "validationReturn":float(v["validationReturn"]),
            "validationPnlUsd":float(v["pnlUsd"]),
            "trades":int(v["trades"]),
            "transactionCost":float(v["transactionCost"]),
            "validDays":int(v["validDays"]),
            "invalidDays":int(v["invalidDays"]),
        })

    valid=[r for r in rows if r["invalidDays"]==0]
    vals=[r["validationReturn"] for r in valid]
    train_ret=[r["trainLifetimeReturn"] for r in valid]
    trades=[r["trades"] for r in valid]
    costs=[r["transactionCost"] for r in valid]
    positive=[r for r in valid if r["validationReturn"]>0]
    negative=[r for r in valid if r["validationReturn"]<0]
    neutral=[r for r in valid if r["validationReturn"]==0]
    positive_both=[r for r in positive if r["trainLifetimeReturn"]>0]
    top=sorted(valid,key=lambda r:(-r["validationReturn"],r["botId"]))[:20]

    mean_val=statistics.mean(vals) if vals else None
    positive_share=len(positive)/len(valid) if valid else 0.0
    if not valid:
        verdict="INVALID_NO_VALID_BOTS"
    elif mean_val is not None and mean_val<0 and positive_share<0.20:
        verdict="FAIL_GENERALIZATION"
    elif mean_val is not None and mean_val<0:
        verdict="WEAK_GENERALIZATION"
    else:
        verdict="MIXED_REVIEW_REQUIRED"

    diagnostic={
        "version":"evolution-v0.4-diagnostics",
        "runId":info.get("runId") or run_dir.name,
        "sourceRunId":info.get("sourceRunId"),
        "sourceTrainFrom":info.get("sourceTrainFrom"),
        "sourceTrainTo":info.get("sourceTrainTo"),
        "validationFrom":info.get("fromDate"),
        "validationTo":info.get("toDate"),
        "population":len(rows),
        "validBots":len(valid),
        "invalidBots":len(rows)-len(valid),
        "positiveBots":len(positive),
        "negativeBots":len(negative),
        "neutralBots":len(neutral),
        "positiveShare":positive_share,
        "positiveTrainAndValidationBots":len(positive_both),
        "bestValidationReturn":max(vals) if vals else None,
        "meanValidationReturn":mean_val,
        "medianValidationReturn":statistics.median(vals) if vals else None,
        "worstValidationReturn":min(vals) if vals else None,
        "meanTrainLifetimeReturn":statistics.mean(train_ret) if train_ret else None,
        "medianTrainLifetimeReturn":statistics.median(train_ret) if train_ret else None,
        "trainValidationSpearman":_spearman(train_ret,vals),
        "tradesValidationPearson":_pearson(trades,vals),
        "costValidationPearson":_pearson(costs,vals),
        "totalTrades":sum(trades),
        "meanTradesPerBot":statistics.mean(trades) if trades else None,
        "medianTradesPerBot":statistics.median(trades) if trades else None,
        "totalTransactionCost":sum(costs),
        "meanTransactionCostPerBot":statistics.mean(costs) if costs else None,
        "verdict":verdict,
        "finalOosAllowed":False,
        "nextStep":"TUNE_ON_2025_VALIDATION_RETRAIN_2024_THEN_REVALIDATE; KEEP_2026_UNTOUCHED",
        "topValidationBots":top,
    }
    (run_dir/"generalization_diagnostics.json").write_text(json.dumps(diagnostic,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return diagnostic


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--results-dir",type=Path,default=Path("/home/ubuntu/evolution-results"))
    p.add_argument("--run",default="latest")
    args=p.parse_args(argv)
    run=_latest_validation(args.results_dir) if args.run=="latest" else Path(args.run)
    d=analyze(run)
    print(json.dumps(d,ensure_ascii=False,allow_nan=False,sort_keys=True))
    return d


if __name__=="__main__":
    main()
