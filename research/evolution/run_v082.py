#!/usr/bin/env python3
"""V082 isolated 50-bot indicator research; never launches without explicit CLI."""
import json
import math
import os
from pathlib import Path
import sys
if __name__=='__main__':os.environ.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from research.evolution.run_v07_available import parser as base_parser,run
from research.evolution.adaptive_capacity import ResourceMonitor
from research.evolution.v081_diagnostics import smoke_window
from research.evolution.available_data import verify_plan
from research.evolution.data_access import check_dates

CONFIG=Path(__file__).with_name('v082_indicator_config.json')


def parser():
    p=base_parser();p.description=__doc__;p.set_defaults(config=CONFIG,population=50,results_dir=Path('/mnt/research/evolution-v082'))
    p.add_argument('--pilot',action='store_true')
    p.add_argument('--capacity-trial',action='store_true')
    p.add_argument('--capacity-report',type=Path)
    p.add_argument('--max-session-seconds',type=float,default=900)
    return p


def main(argv=None):
    p=parser();args=p.parse_args(argv)
    if args.from_date and args.to_date:check_dates(args.from_date,args.to_date)
    if args.config.is_symlink():p.error('Configuration symlinks are not accepted')
    if not math.isfinite(args.max_session_seconds) or args.max_session_seconds<=0:p.error("Positive finite session deadline required")
    cfg=json.loads(args.config.read_text())
    if not cfg.get('indicator_aware') or cfg.get('selection_policy')!='v081_survival':p.error('V082 explicit configuration required')
    if args.population!=50 or cfg.get('max_population')!=50:p.error('V082_FIXED_50: no automatic increase; new reviewed configuration required')
    if args.smoke:p.error('Use --pilot for audited discovery')
    window=None
    if args.pilot:
        if args.source_run or args.from_date or args.to_date:p.error('Pilot chooses audited TRAIN window')
        plan,_=verify_plan(args.selection_plan);window=smoke_window(plan)
        args.from_date,args.to_date=window['fromDate'],window['toDate']
    elif args.capacity_trial:
        from research.backtest_v4_strategies import exchange_sessions
        if args.source_run or not args.from_date or not args.to_date or not '2022-01-01'<=args.from_date<=args.to_date<='2023-12-31' or not 5<=len(exchange_sessions(args.from_date,args.to_date))<=10:
            p.error('Bounded TRAIN capacity trial requires 5-10 sessions')
    elif args.source_run:
        source=json.loads((args.source_run/'run.json').read_text())
        if source.get('version')!='V082_INDICATOR_1' or source.get('population')!=50:p.error('Frozen V082 50-bot source required')
    else:
        if not args.capacity_report:p.error('New V082 repeated measurement certificate required')
        from research.evolution.calibrate_v082 import verify_capacity
        proof=verify_capacity(args.capacity_report,args.selection_plan,args.config,args.seed)
        args.max_session_seconds=min(args.max_session_seconds,proof["maxSessionSeconds"])
    args.run_purpose='PILOT_50' if args.pilot else 'CAPACITY_TRIAL_50' if args.capacity_trial else 'FROZEN_EVALUATION' if args.source_run else 'TRAIN_FROM_SCRATCH_50'
    monitor=ResourceMonitor(max_session_seconds=args.max_session_seconds)
    try:
        monitor.start();args.resource_monitor=monitor
        directory=run(args)
        monitor.check()
        from research.backtest_intraday_v4 import atomic_json
        info=json.loads((directory/'run.json').read_text())
        if window:atomic_json(directory/'pilot_selection.json',window)
        if (args.pilot or args.capacity_trial) and info.get('completedTrainGenerations',0)<1:
            info.update(phase='error',resultsValid=False,error='V082_PILOT_NO_VALID_GENERATIONS')
            atomic_json(directory/'run.json',info);atomic_json(directory/'status.json',info)
            raise ValueError('V082_PILOT_NO_VALID_GENERATIONS')
        return directory
    except (ValueError,OSError) as exc:p.exit(2,f'V082 BLOCKED: {exc}\n')
    finally:monitor.close()

if __name__=='__main__':main()
