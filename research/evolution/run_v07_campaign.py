#!/usr/bin/env python3
"""Evolution V0.7: train 2022-2023 -> internal 2024 holdout -> shortlist -> 2025 dev validation."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from research.backtest_intraday_v4 import atomic_json
from research.evolution.run_evolution_v0 import main as train_main
from research.evolution.evaluate_population_v03 import main as eval_main
from research.evolution.build_shortlist_v07 import build_shortlist
from research.evolution.analyze_validation_v04 import analyze


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=ROOT/"data/toss_1m")
    p.add_argument("--results-dir",type=Path,default=Path("/home/ubuntu/evolution-results"))
    p.add_argument("--symbols",default="TQQQ,AAPL")
    p.add_argument("--population",type=int,default=20)
    p.add_argument("--max-population",type=int,default=300)
    p.add_argument("--shortlist-max",type=int,default=40)
    p.add_argument("--seed",type=int,default=42)
    p.add_argument("--dataset-snapshot",type=Path,help="Optional verified repaired CSV snapshot; copied into NEW campaign provenance")
    args=p.parse_args(argv)

    snapshot_info=None
    if args.dataset_snapshot:
        from research.toss_1m_repair import verify_snapshot, symbols_checked
        snapshot_info=verify_snapshot(args.dataset_snapshot,args.data_dir,symbols_checked(args.symbols))

    campaign_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-v07"
    cdir=args.results_dir.resolve()/("campaign-"+campaign_id)
    cdir.mkdir(parents=True,exist_ok=False)
    state=dict(
        version="evolution-v07-campaign",campaignId=campaign_id,phase="training",
        trainFrom="2022-01-03",trainTo="2023-12-29",
        internalHoldoutFrom="2024-01-02",internalHoldoutTo="2024-12-31",
        developmentValidationFrom="2025-01-02",developmentValidationTo="2025-12-31",
        finalOos="2026_UNTOUCHED",
        selectionMode="V07_INTERNAL_HOLDOUT_SHORTLIST",
        baseFitness="v05_structural",
        population=args.population,maxPopulation=args.max_population,shortlistMax=args.shortlist_max,
        seed=args.seed,createdAt=datetime.now(timezone.utc).isoformat(),
    )
    if snapshot_info is not None:
        from research.backtest_v4_strategies import file_sha256
        atomic_json(cdir/"dataset_snapshot.json",snapshot_info)
        state["datasetSnapshot"]=dict(snapshotId=snapshot_info["snapshotId"],path="dataset_snapshot.json",
                                      sourceManifestSha256=file_sha256(args.dataset_snapshot),
                                      repairedDates=snapshot_info["repairedDates"],timestampKind="end",finalOosGate="CLOSED")
    atomic_json(cdir/"campaign.json",state)
    cfg=ROOT/"research/evolution/v07_holdout_config.json"
    try:
        train=train_main([
            "--data-dir",str(args.data_dir),"--timestamp-kind","end","--provisional",
            "--symbols",args.symbols,"--population",str(args.population),
            "--adaptive-resources","--min-population",str(args.population),
            "--max-population",str(args.max_population),
            "--evaluation-window-sessions","7","--initial-capital","100",
            "--seed",str(args.seed),"--from-date","2022-01-03","--to-date","2023-12-29",
            "--results-dir",str(args.results_dir),"--config",str(cfg),
        ])
        state.update(phase="internal_holdout",trainRun=str(train),updatedAt=datetime.now(timezone.utc).isoformat())
        atomic_json(cdir/"campaign.json",state)

        holdout=eval_main([
            "--source-run",str(train),"--data-dir",str(args.data_dir),"--timestamp-kind","end","--provisional",
            "--from-date","2024-01-02","--to-date","2024-12-31",
            "--expected-split","validation","--evaluation-label","internal_holdout",
            "--results-dir",str(args.results_dir),
        ])
        state.update(phase="shortlist",holdoutRun=str(holdout),updatedAt=datetime.now(timezone.utc).isoformat())
        atomic_json(cdir/"campaign.json",state)

        shortlist_dir=cdir/"shortlist"
        build_shortlist(train,holdout,shortlist_dir,args.shortlist_max)
        shortlist_info=json.loads((shortlist_dir/"shortlist.json").read_text(encoding="utf-8"))
        state.update(
            phase="development_validation",shortlistDir=str(shortlist_dir),
            shortlistCount=len(shortlist_info["selectedBots"]),
            internalHoldoutPositiveBots=shortlist_info["eligiblePositiveBots"],
            updatedAt=datetime.now(timezone.utc).isoformat(),
        )
        atomic_json(cdir/"campaign.json",state)

        validation=eval_main([
            "--source-run",str(shortlist_dir),"--data-dir",str(args.data_dir),"--timestamp-kind","end","--provisional",
            "--from-date","2025-01-02","--to-date","2025-12-31",
            "--expected-split","none","--evaluation-label","development_validation",
            "--results-dir",str(args.results_dir),
        ])
        state.update(phase="diagnostics",developmentValidationRun=str(validation),updatedAt=datetime.now(timezone.utc).isoformat())
        atomic_json(cdir/"campaign.json",state)

        d=analyze(validation)
        state.update(
            phase="completed",verdict=d.get("verdict"),finalOosAllowed=False,
            developmentPositiveBots=d.get("positiveBots"),
            meanValidationReturn=d.get("meanValidationReturn"),
            medianValidationReturn=d.get("medianValidationReturn"),
            trainValidationSpearman=d.get("trainValidationSpearman"),
            tradesValidationPearson=d.get("tradesValidationPearson"),
            completedAt=datetime.now(timezone.utc).isoformat(),
        )
        atomic_json(cdir/"campaign.json",state)
        print(json.dumps(state,ensure_ascii=False,allow_nan=False,sort_keys=True))
        return cdir
    except BaseException as exc:
        state.update(phase="error",error=str(exc),failedAt=datetime.now(timezone.utc).isoformat())
        atomic_json(cdir/"campaign.json",state)
        raise


if __name__=="__main__":
    main()
