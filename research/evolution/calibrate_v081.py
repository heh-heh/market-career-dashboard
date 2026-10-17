#!/usr/bin/env python3
"""Finite sequential resource calibration; fresh, separated fixed-size pilots only."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from research.backtest_intraday_v4 import atomic_json
from research.backtest_v4_strategies import file_sha256
from research.evolution.available_data import verify_plan,new_output
from research.evolution.adaptive_capacity import next_population,recommend,host_fingerprint
from research.evolution.v081_diagnostics import smoke_window

VERSION='V081_CAPACITY_1'


def code_hashes():return {p.name:file_sha256(p) for p in sorted(Path(__file__).parent.glob('*.py'))}


def run_trial(population,index,root,args,window):
    folder=root/f'population-{population:04d}-{index}';folder.mkdir()
    argv=[sys.executable,str(Path(__file__).with_name('run_v081.py')),'--selection-plan',str(args.selection_plan),
        '--config',str(args.config),'--population',str(population),'--capacity-trial',
        '--from-date',window['fromDate'],'--to-date',window['toDate'],'--results-dir',str(folder/'runs'),
        '--seed',str(args.seed),'--max-session-seconds',str(args.max_session_seconds),'--research-mount',args.research_mount]
    if args.require_ebs:argv.append('--require-ebs')
    # Child has a frequent resource guard; parent also bounds total wall time.
    with (folder/'trial.log').open('xb') as log:
        proc=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1'},pass_fds=tuple(int(os.environ[k]) for k in ("V081_LOCK_FD","V081_SHARED_LOCK_FD") if os.environ.get(k,"-1").isdigit()))
        try:code=proc.wait(timeout=args.max_session_seconds*window['sessions']+600)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:proc.wait(timeout=15)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
            code=124
    runs=list((folder/'runs').glob('*/run.json')) if (folder/'runs').exists() else []
    if len(runs)!=1:return dict(completedGenerations=0,guardPassed=False,error='TRIAL_FAILED_BEFORE_RUN',exitCode=code)
    source=runs[0];info=json.loads(source.read_text())
    report=dict(info.get('resourceReport') or {})
    report.update(completedGenerations=info.get('completedTrainGenerations',0) if code==0 and info.get('phase')=='completed' else 0,
        runFile=str(source.resolve()),runSha256=file_sha256(source),exitCode=code,selectedSessions=info.get('selectedSessionCount'),
        error=info.get('error'),outputBytes=sum(p.stat().st_size for p in source.parent.rglob('*') if p.is_file()))
    return report


def verify_capacity(path,plan,config,population):
    raw=json.loads(Path(path).read_text())
    if (raw.get('hostFingerprint')!=host_fingerprint() or raw.get('version')!=VERSION or raw.get('sourcePlanSha256')!=file_sha256(plan) or
        raw.get('configSha256')!=file_sha256(config) or raw.get('codeHashes')!=code_hashes()):
        raise ValueError('CAPACITY_SOURCE_OR_CODE_MISMATCH')
    age=time.time()-raw['createdEpoch']
    if age<0 or age>24*3600:raise ValueError('CAPACITY_EXPIRED_REMEASURE_HOST')
    result=recommend(raw['trials'],raw['maximumPopulation'],raw['maxSessionSeconds'])
    if result.get('recommendedPopulation')!=population or raw.get('recommendation')!=result:
        raise ValueError('CAPACITY_POPULATION_NOT_MEASURED')
    from dataclasses import replace
    from research.evolution.config import Config
    cfg=Config(**json.loads(Path(config).read_text()))
    seen_runs=set()
    for trial in raw['trials']:
        expected_config=json.loads(json.dumps(replace(cfg,population_size=trial['population'],adaptive_resources=False).snapshot()))
        reports=list(trial.get('measuredRuns',[]))+([trial['warmup']] if 'warmup' in trial else [])
        for report in reports:
            if report.get('completedGenerations',0)>0:
                source=Path(report['runFile'])
                if source.is_symlink() or not source.resolve().is_relative_to(Path(path).resolve().parent):
                    raise ValueError('CAPACITY_RUN_PATH_INVALID')
                if str(source.resolve()) in seen_runs:raise ValueError('CAPACITY_REPEATED_RUN_REUSED')
                seen_runs.add(str(source.resolve()))
                if file_sha256(source)!=report['runSha256']:raise ValueError('CAPACITY_RUN_CHANGED')
                info=json.loads(source.read_text())
                if (info.get('config')!=expected_config or info.get('phase')!='completed' or info.get('population')!=trial['population'] or
                    info.get('sourcePlanSha256')!=raw['sourcePlanSha256'] or
                    info.get('completedTrainGenerations')!=report['completedGenerations'] or
                    info.get('codeHashes')!=raw['codeHashes'] or
                    any(report.get(k)!=v for k,v in info.get('resourceReport',{}).items())):
                    raise ValueError('CAPACITY_MEASUREMENT_MISMATCH')
    return raw


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--selection-plan',type=Path,required=True);p.add_argument('--config',type=Path,default=Path(__file__).with_name('v081_adaptive_config.json'))
    p.add_argument('--out',type=Path,required=True);p.add_argument('--max-population',type=int,default=1000)
    p.add_argument('--seed',type=int,default=42);p.add_argument('--max-session-seconds',type=float,default=900)
    p.add_argument('--require-ebs',action='store_true');p.add_argument('--research-mount',choices=['/mnt/research','/mnt/research-extra'],default='/mnt/research')
    args=p.parse_args(argv)
    cfg=json.loads(args.config.read_text())
    if args.max_population<100 or args.max_session_seconds<=0:p.error('Invalid capacity bound or time budget')
    if args.out.exists():p.error('Never overwrite calibration artifacts')
    plan,_=verify_plan(args.selection_plan);window=smoke_window(plan,desired=5)
    from research.evolution.audit_v07_data import resource_preflight
    resource_preflight(args.out,require_research_mount=args.require_ebs,research_mount=args.research_mount)
    new_output(args.out,plan['dataRoot'])
    original_config_hash=file_sha256(args.config)
    cfg['max_population']=args.max_population
    args.config=args.out/'trial_config.json'
    atomic_json(args.config,cfg)
    raw=dict(version=VERSION,hostFingerprint=host_fingerprint(),researchOnly=True,finalOosGate='CLOSED',createdEpoch=time.time(),
        generatedAt=datetime.now(timezone.utc).isoformat(),sourcePlanSha256=file_sha256(args.selection_plan),
        configSha256=file_sha256(args.config),baseConfigSha256=original_config_hash,configPath=str(args.config.resolve()),codeHashes=code_hashes(),window=window,
        maximumPopulation=args.max_population,seed=args.seed,maxSessionSeconds=args.max_session_seconds,trials=[])
    n=100;previous=None
    atomic_json(args.out/'capacity.json',raw)
    while n is not None:
        raw['currentTrial']=dict(population=n,phase='warmup')
        atomic_json(args.out/'capacity.json',raw)
        warmup=run_trial(n,'warmup',args.out,args,window)
        from research.evolution.adaptive_capacity import safe_measurement
        trial=dict(population=n,warmupValid=warmup.get('completedGenerations',0)>0 and safe_measurement(warmup,args.max_session_seconds),warmup=warmup,measuredRuns=[])
        if trial['warmupValid']:
            for i in range(2):
                raw['currentTrial']=dict(population=n,phase='measured',repetition=i+1)
                atomic_json(args.out/'capacity.json',raw)
                report=run_trial(n,f'measured-{i}',args.out,args,window);trial['measuredRuns'].append(report)
                if not report.get('completedGenerations') or not safe_measurement(report,args.max_session_seconds):break
        raw['trials'].append(trial)
        raw['recommendation']=recommend(raw['trials'],args.max_population,args.max_session_seconds)
        if previous and len(trial['measuredRuns'])==2 and trial['warmupValid']:
            current=trial['measuredRuns'];delta=n-previous['population']
            mem=sum(r['peak']['processRssMb'] for r in current)/2
            sec=sum(r['meanSessionSeconds'] for r in current)/2
            trial['incrementalRssMbPerBot']=(mem-previous['rss'])/delta
            trial['incrementalSessionSecondsPerBot']=(sec-previous['seconds'])/delta
            trial['slopeWarning']='Measured difference includes fixed overhead/cache/host noise; never used without next trial'
        atomic_json(args.out/'capacity.json',raw)
        print(json.dumps(dict(capacityReport=str(args.out/'capacity.json'),population=n,recommendation=raw['recommendation'])),flush=True)
        if raw['recommendation']['recommendedPopulation']!=n:break
        previous=dict(population=n,rss=sum(r['peak']['processRssMb'] for r in trial['measuredRuns'])/2,
                      seconds=sum(r['meanSessionSeconds'] for r in trial['measuredRuns'])/2)
        n=next_population(n,args.max_population)
    raw['currentTrial']=dict(phase='completed')
    atomic_json(args.out/'capacity.json',raw)
    if file_sha256(args.selection_plan)!=raw['sourcePlanSha256'] or code_hashes()!=raw['codeHashes']:raise ValueError('CALIBRATION_INPUT_CHANGED')
    print(json.dumps(dict(capacityReport=str(args.out/'capacity.json'),completed=True,recommendation=raw['recommendation'])))
    if raw['recommendation']['recommendedPopulation'] is None:p.exit(2,'V081_CAPACITY_100_UNSAFE_OR_INVALID: inspect capacity.json\n')
    return args.out/'capacity.json'


if __name__=='__main__':main()
