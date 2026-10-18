#!/usr/bin/env python3
"""V083 forward-only 2-50 bot top-two evolution; opt-in CLI only."""
import json
import math
import os
from pathlib import Path
import sys
if __name__=='__main__':os.environ.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from research.evolution.run_v07_available import parser as base_parser,run
from research.evolution.v083_pacing import PacedMonitor
from research.evolution.adaptive_capacity import ResourceMonitor
from research.evolution.v081_diagnostics import smoke_window
from research.evolution.available_data import verify_plan
from research.evolution.data_access import check_dates

CONFIG=Path(__file__).with_name('v083_top_two_config.json')


def parser():
    p=base_parser();p.description=__doc__;p.set_defaults(config=CONFIG,population=50,results_dir=Path('/mnt/research/evolution-v083'))
    p.add_argument('--pilot',action='store_true')
    p.add_argument('--capacity-trial',action='store_true')
    p.add_argument('--capacity-report',type=Path)
    p.add_argument('--max-session-seconds',type=float,default=900)
    p.add_argument('--cpu-pacing-ms',type=float,default=25,help='Per completed minute cooperative pacing; CPU 75%% hard guard unchanged')
    return p


def main(argv=None):
    p=parser();args=p.parse_args(argv)
    if args.from_date and args.to_date:check_dates(args.from_date,args.to_date)
    if args.config.is_symlink():p.error('Configuration symlinks are not accepted')
    if not math.isfinite(args.max_session_seconds) or args.max_session_seconds<=0:p.error("Positive finite session deadline required")
    if not math.isfinite(args.cpu_pacing_ms) or not 5<=args.cpu_pacing_ms<=100:p.error('cpu-pacing-ms must be 5..100')
    args.v083_mode=True
    cfg=json.loads(args.config.read_text())
    if not cfg.get('indicator_aware') or cfg.get('selection_policy')!='v081_survival' or cfg.get('elite_count')!=0 or cfg.get('parent_pool_size')!=2:p.error('V083 top-two configuration required')
    if type(args.population) is not int or not 2<=args.population<=50 or cfg.get('max_population')!=50:p.error('V083_POPULATION_MUST_BE_2_TO_50')
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
        if source.get('version')!='V083_TOP_TWO_1' or source.get('population')!=args.population:p.error('Frozen V083 source population must match selected bots')
    else:
        if not args.capacity_report:p.error('New V083 repeated measurement certificate required')
        from research.evolution.calibrate_v083 import verify_capacity
        proof=verify_capacity(args.capacity_report,args.selection_plan,args.config,args.seed,args.population)
        args.max_session_seconds=min(args.max_session_seconds,proof["maxSessionSeconds"])
    args.run_purpose=('PILOT_' if args.pilot else 'CAPACITY_TRIAL_' if args.capacity_trial else 'FROZEN_EVALUATION_' if args.source_run else 'TRAIN_FROM_SCRATCH_')+str(args.population)
    monitor=PacedMonitor(max_session_seconds=args.max_session_seconds,pacing_seconds=args.cpu_pacing_ms/1000)
    try:
        monitor.start();args.resource_monitor=monitor
        directory=run(args)
        monitor.check()
        from research.backtest_intraday_v4 import atomic_json
        info=json.loads((directory/'run.json').read_text())
        if window:atomic_json(directory/'pilot_selection.json',window)
        if (args.pilot or args.capacity_trial) and info.get('completedTrainGenerations',0)<1:
            info.update(phase='error',resultsValid=False,error='V083_PILOT_NO_VALID_GENERATIONS')
            atomic_json(directory/'run.json',info);atomic_json(directory/'status.json',info)
            raise ValueError('V083_PILOT_NO_VALID_GENERATIONS')
        return directory
    except (ValueError,OSError) as exc:p.exit(2,f'V083 BLOCKED: {exc}\n')
    finally:monitor.close()

if __name__=='__main__':main()
