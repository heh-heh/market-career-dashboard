#!/usr/bin/env python3
"""Evolution V0.6 stability campaign: 2022-2024 train -> 2025 frozen validation."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from research.evolution.run_v04_campaign import main as campaign_main


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=ROOT/"data/toss_1m")
    p.add_argument("--results-dir",type=Path,default=Path("/home/ubuntu/evolution-results"))
    p.add_argument("--symbols",default="TQQQ,AAPL")
    p.add_argument("--population",type=int,default=20)
    p.add_argument("--max-population",type=int,default=300)
    p.add_argument("--seed",type=int,default=42)
    p.add_argument("--train-from",default="2022-01-03")
    p.add_argument("--train-to",default="2024-12-31")
    p.add_argument("--validation-from",default="2025-01-02")
    p.add_argument("--validation-to",default="2025-12-31")
    args=p.parse_args(argv)
    forwarded=[
        "--data-dir",str(args.data_dir),
        "--results-dir",str(args.results_dir),
        "--symbols",args.symbols,
        "--population",str(args.population),
        "--max-population",str(args.max_population),
        "--seed",str(args.seed),
        "--train-from",args.train_from,
        "--train-to",args.train_to,
        "--validation-from",args.validation_from,
        "--validation-to",args.validation_to,
        "--config",str(ROOT/"research/evolution/v06_stability_config.json"),
    ]
    return campaign_main(forwarded)


if __name__=="__main__":
    main()
