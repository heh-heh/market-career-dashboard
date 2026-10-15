#!/usr/bin/env python3
"""Compare independently recorded training arms; no fitting, promotion or OOS access."""
import argparse
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from research.backtest_intraday_v4 import atomic_json
from research.evolution.data_access import check_dates


def compare(runs):
    result=[]; infos=[]
    for directory in map(Path,runs):
        info=json.loads((directory/"run.json").read_text())
        check_dates(info["fromDate"],info["toDate"])
        if info["phase"] != "completed" or info.get("evaluationMode"):
            raise ValueError("Compare completed training runs; frozen validation is reported separately")
        infos.append(info)
        means=[]; medians=[]; best=[]; trades=0; complete_windows=0
        with (directory/"generations.jsonl").open() as stream:
            for line in stream:
                row=json.loads(line)
                if row["split"] != "train": continue
                if row.get("meanDailyReturn") is not None: means.append(row["meanDailyReturn"])
                if row.get("medianDailyReturn") is not None: medians.append(row["medianDailyReturn"])
                if row.get("bestDailyReturn") is not None: best.append(row["bestDailyReturn"])
                trades+=row["tradesToday"]
                complete_windows+=bool(row.get("windowComplete"))
        costs=[]; mdd=[]; timing=[]; fitness=[]; regimes={r:dict(trades=0,pnlUsd=0.) for r in ("BULL","BEAR","SIDEWAYS")}
        for metrics in sorted(directory.glob("generation_*/metrics.json")):
            for bot in json.loads(metrics.read_text())["bots"]:
                costs.append(bot["transactionCost"])
                mdd.append(bot["maxDrawdown"])
                if bot.get("evaluationValid"):
                    fitness.append(bot["fitness"])
                if "timing" in bot: timing.append(bot["timing"])
                for r in regimes:
                    for k in regimes[r]: regimes[r][k]+=bot.get("regimePerformance",{}).get(r,{}).get(k,0)
        result.append(dict(runId=info["runId"],directory=str(directory),seed=info["seed"],
            fitnessMode=info["fitnessMode"],evaluationWindowSessions=info["evaluationWindowSessions"],
            completedGenerations=complete_windows,meanDailyPopulationReturn=statistics.mean(means) if means else None,
            medianDailyPopulationReturn=statistics.median(medians) if medians else None,
            meanBestDailyReturn=statistics.mean(best) if best else None,trades=trades,
            totalSimulatedCostUsd=sum(costs),meanBotDayMaxDrawdown=statistics.mean(mdd) if mdd else None,
            meanDailyFitness=statistics.mean(fitness) if fitness else None,
            regimePerformance=regimes if timing else "NOT_RECORDED_IN_LEGACY_BASELINE",
            timingSums={k:sum(t[k] for t in timing) for k in timing[0]} if timing else None,
            dataValid=info.get("resultsValid",not info.get("missingSessions")),
            finalOosGate="CLOSED"))
    differences=[]
    for key in ("seed","symbols","fromDate","toDate","executionModel","dataOrigin","dataMode"):
        if any(i.get(key)!=infos[0].get(key) for i in infos[1:]): differences.append(key)
    for key in ("population_size","initial_capital","slippage_bps","half_spread_bps","max_trades_per_session","entry_cooldown_minutes"):
        if any(i["config"].get(key)!=infos[0]["config"].get(key) for i in infos[1:]): differences.append("config."+key)
    hashes=[{s:m["sha256"] for s,m in i["inputManifest"]["symbols"].items()} for i in infos]
    if any(h!=hashes[0] for h in hashes[1:]): differences.append("inputHashes")
    return dict(version="V07_R1_COMPARISON_1",arms=result,comparable=not differences,differences=differences,
                decision="DESCRIPTIVE_ONLY_NO_PROMOTION", finalOosGate="CLOSED",
                caveats=["Training population statistics are selected in-sample; not a frozen portfolio equity curve",
                         "Changing both policy schema and fitness confounds attribution: use 5d legacy-fitness control",
                         "Historical 2024 shortlist selection is model selection; 2025 remains development validation, never threshold fitting"])


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs",nargs="+",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists(): p.error("Refusing to overwrite an existing comparison")
    out=args.out.resolve()
    protected=[(ROOT/"data").resolve()]+[r.resolve() for r in args.runs]
    for r in args.runs:
        info=json.loads((r/"run.json").read_text()); protected.append(Path(info["dataRoot"]).resolve())
    if any(out==r or r in out.parents for r in protected): p.error("Comparison must be outside source artifacts and raw data")
    result=compare(args.runs);atomic_json(out,result)
    print(json.dumps(result,allow_nan=False))


if __name__=="__main__": main()
