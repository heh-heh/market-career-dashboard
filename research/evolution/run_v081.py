#!/usr/bin/env python3
"""Isolated V081 fixed-size run. Pilot/calibration never launches long training."""
import json
from pathlib import Path
import sys
if __name__=='__main__':
    import os
    os.environ.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from research.evolution.run_v07_available import parser as base_parser, run
from research.evolution.available_data import verify_plan
from research.evolution.adaptive_capacity import ResourceMonitor
from research.evolution.v081_diagnostics import smoke_window


def parser():
    p=base_parser();p.description=__doc__
    p.set_defaults(config=None,population=100,
                   results_dir=Path('/mnt/research/evolution-v081'))
    p.add_argument('--pilot',action='store_true',help='100-bot, audited 5-10 session pilot; no long training')
    p.add_argument('--capacity-report',type=Path)
    p.add_argument('--capacity-trial',action='store_true',help='Explicit bounded calibration worker, at most ten TRAIN sessions')
    p.add_argument('--max-session-seconds',type=float,default=900)
    return p


def main(argv=None):
    p=parser();args=p.parse_args(argv)
    from research.evolution.data_access import check_dates
    if args.from_date and args.to_date:check_dates(args.from_date,args.to_date)
    if args.config is None:
        args.config=(args.capacity_report.parent/'trial_config.json') if args.capacity_report else Path(__file__).with_name('v081_adaptive_config.json')
    if args.config.is_symlink():p.error('Configuration symlinks are not accepted')
    cfg=json.loads(args.config.read_text())
    if cfg.get('selection_policy')!='v081_survival':p.error('V081 explicit config required')
    if not 1<=args.population<=cfg['max_population']:p.error('Population outside configured maximum')
    if args.smoke:p.error('Use --pilot: audited window discovery with at least one valid generation')
    if args.pilot:
        if args.source_run or args.from_date or args.to_date or args.population!=100:
            p.error('Pilot requires exactly 100 and selects its own audited TRAIN window')
        plan,_=verify_plan(args.selection_plan);window=smoke_window(plan)
        args.from_date,args.to_date=window['fromDate'],window['toDate']
    elif args.capacity_trial:
        from research.backtest_v4_strategies import exchange_sessions
        if (args.source_run or not args.from_date or not args.to_date or
            not '2022-01-01'<=args.from_date<=args.to_date<='2023-12-31' or
            not 5<=len(exchange_sessions(args.from_date,args.to_date))<=10):
            p.error('Capacity trial restricted to 5-10 chronological TRAIN sessions')
        window=None
    elif not args.source_run:
        if not args.capacity_report:p.error('Measured capacity report required for non-pilot training')
        from research.evolution.calibrate_v081 import verify_capacity
        capacity=verify_capacity(args.capacity_report,args.selection_plan,args.config,args.population)
        if args.seed!=capacity.get("seed"):p.error("CAPACITY_SEED_MISMATCH")
        args.max_session_seconds=min(args.max_session_seconds,capacity["maxSessionSeconds"])
        window=None
    else:
        source=json.loads((args.source_run/'run.json').read_text())
        if source.get('version')!='V081_SURVIVAL_1':p.error('Frozen V081 source required')
        args.population=source['population'];window=None
    args.run_purpose='PILOT_100' if args.pilot else 'CAPACITY_TRIAL' if args.capacity_trial else 'FROZEN_EVALUATION' if args.source_run else 'TRAIN_FROM_SCRATCH'
    monitor=ResourceMonitor(max_session_seconds=args.max_session_seconds)
    try:
        monitor.start();args.resource_monitor=monitor
        directory=run(args)
        from research.backtest_intraday_v4 import atomic_json
        info=json.loads((directory/'run.json').read_text())
        if window:atomic_json(directory/'pilot_selection.json',window)
        if (args.pilot or args.capacity_trial) and info.get('completedTrainGenerations',0)<1:
            info.update(phase='error',resultsValid=False,error='V081_PILOT_NO_VALID_GENERATIONS')
            atomic_json(directory/'run.json',info);atomic_json(directory/'status.json',info)
            raise ValueError('V081_PILOT_NO_VALID_GENERATIONS: inspect windows.jsonl and excluded_sessions.jsonl')
        return directory
    except (ValueError,OSError) as exc:
        p.exit(2,f'V081 BLOCKED: {exc}\n')
    finally:monitor.close()


if __name__=='__main__':main()
