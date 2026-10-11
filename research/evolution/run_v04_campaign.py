#!/usr/bin/env python3
"""Evolution V0.4 campaign: 2024 train -> 2025 frozen validation -> diagnostics."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from research.backtest_intraday_v4 import atomic_json
from research.evolution.run_evolution_v0 import main as train_main
from research.evolution.evaluate_population_v03 import main as validation_main
from research.evolution.analyze_validation_v04 import analyze


def parser():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=ROOT/"data/toss_1m")
    p.add_argument("--results-dir",type=Path,default=Path("/home/ubuntu/evolution-results"))
    p.add_argument("--symbols",default="TQQQ,AAPL")
    p.add_argument("--population",type=int,default=20)
    p.add_argument("--max-population",type=int,default=300)
    p.add_argument("--seed",type=int,default=42)
    p.add_argument("--train-from",default="2024-06-03")
    p.add_argument("--train-to",default="2024-12-31")
    p.add_argument("--validation-from",default="2025-01-02")
    p.add_argument("--validation-to",default="2025-12-31")
    p.add_argument("--config",type=Path,default=ROOT/"research/evolution/v04_generalization_config.json")
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    campaign_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-v04"
    campaign_dir=args.results_dir.resolve()/("campaign-"+campaign_id)
    campaign_dir.mkdir(parents=True,exist_ok=False)
    state=dict(
        version="evolution-v0.4-campaign", campaignId=campaign_id, phase="training",
        trainFrom=args.train_from, trainTo=args.train_to,
        validationFrom=args.validation_from, validationTo=args.validation_to,
        population=args.population,maxPopulation=args.max_population,seed=args.seed,
        fitnessMode="v04_generalization",createdAt=datetime.now(timezone.utc).isoformat(),
    )
    atomic_json(campaign_dir/"campaign.json",state)
    try:
        train_args=[
            "--data-dir",str(args.data_dir),"--timestamp-kind","end","--provisional",
            "--symbols",args.symbols,"--population",str(args.population),
            "--adaptive-resources","--min-population",str(args.population),
            "--max-population",str(args.max_population),
            "--evaluation-window-sessions","7","--initial-capital","100",
            "--seed",str(args.seed),"--from-date",args.train_from,"--to-date",args.train_to,
            "--results-dir",str(args.results_dir),"--config",str(args.config),
        ]
        train_dir=train_main(train_args)
        state.update(phase="validation",trainRun=str(train_dir),updatedAt=datetime.now(timezone.utc).isoformat())
        atomic_json(campaign_dir/"campaign.json",state)

        val_args=[
            "--source-run",str(train_dir),"--data-dir",str(args.data_dir),
            "--timestamp-kind","end","--provisional",
            "--from-date",args.validation_from,"--to-date",args.validation_to,
            "--results-dir",str(args.results_dir),
        ]
        validation_dir=validation_main(val_args)
        state.update(phase="diagnostics",validationRun=str(validation_dir),updatedAt=datetime.now(timezone.utc).isoformat())
        atomic_json(campaign_dir/"campaign.json",state)

        diagnostics=analyze(validation_dir)
        state.update(
            phase="completed", verdict=diagnostics.get("verdict"),
            finalOosAllowed=diagnostics.get("finalOosAllowed"),
            positiveBots=diagnostics.get("positiveBots"),
            meanValidationReturn=diagnostics.get("meanValidationReturn"),
            medianValidationReturn=diagnostics.get("medianValidationReturn"),
            trainValidationSpearman=diagnostics.get("trainValidationSpearman"),
            tradesValidationPearson=diagnostics.get("tradesValidationPearson"),
            completedAt=datetime.now(timezone.utc).isoformat(),
        )
        atomic_json(campaign_dir/"campaign.json",state)
        print(json.dumps(state,ensure_ascii=False,allow_nan=False,sort_keys=True))
        return campaign_dir
    except BaseException as exc:
        state.update(phase="error",error=str(exc),failedAt=datetime.now(timezone.utc).isoformat())
        atomic_json(campaign_dir/"campaign.json",state)
        raise


if __name__=="__main__":
    main()
