#!/usr/bin/env python3
"""V082 fixed-50 warmup + two measured trials, no automatic scaling or training."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from research.backtest_intraday_v4 import atomic_json
from research.backtest_v4_strategies import file_sha256
from research.evolution.adaptive_capacity import host_fingerprint,safe_measurement
from research.evolution.available_data import verify_plan,new_output
from research.evolution.v081_diagnostics import smoke_window
from research.evolution.audit_v07_data import resource_preflight
from research.evolution.run_v082 import CONFIG
from research.evolution.config import Config


def code_hashes():return {p.name:file_sha256(p) for p in sorted(CONFIG.parent.glob('*.py'))}


def verify_capacity(path,plan,config,seed):
    path=Path(path);d=json.loads(path.read_text())
    if (d.get('version')!='V082_CAPACITY_1' or d.get('population')!=50 or d.get('status')!='MEASURED_SAFE' or
        d.get('host')!=host_fingerprint() or not 0<=time.time()-d.get('createdEpoch',0)<=86400 or
        d.get('planSha256')!=file_sha256(plan) or d.get('configSha256')!=file_sha256(config) or
        d.get('codeHashes')!=code_hashes() or d.get('seed')!=seed):raise ValueError('V082_CAPACITY_STALE_OR_MISMATCH')
    if len(d.get('trials',[]))!=3:raise ValueError('V082_REPEATED_MEASUREMENTS_REQUIRED')
    seen=set()
    for entry in d['trials']:
        raw=Path(entry['path']);run=raw.resolve()
        if raw.is_symlink() or not run.is_relative_to(path.resolve().parent) or run in seen or file_sha256(run)!=entry['sha256']:
            raise ValueError('V082_CAPACITY_RUN_CHANGED')
        seen.add(run);info=json.loads(run.read_text());report=info.get('resourceReport',{})
        if info.get('version')!='V082_INDICATOR_1' or info.get('phase')!='completed' or info.get('population')!=50 or not info.get('completedTrainGenerations') or info.get('codeHashes')!=d['codeHashes'] or info.get('sourcePlanSha256')!=d['planSha256'] or info.get('seed')!=seed or info.get('dataOrigin')!=d.get('dataOrigin') or info.get('config')!=json.loads(json.dumps(Config(**json.loads(Path(config).read_text())).snapshot())) or not safe_measurement(report,d['maxSessionSeconds']):
            raise ValueError('V082_CAPACITY_UNSAFE_OR_INVALID')
    return d


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--selection-plan',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--data-origin',choices=['toss','synthetic'],default='toss')
    p.add_argument('--config',type=Path,default=CONFIG);p.add_argument('--seed',type=int,default=42)
    p.add_argument('--require-ebs',action='store_true');p.add_argument('--research-mount',choices=['/mnt/research','/mnt/research-extra'],default='/mnt/research')
    p.add_argument('--max-session-seconds',type=float,default=900);a=p.parse_args(argv)
    if not math.isfinite(a.max_session_seconds) or a.max_session_seconds<=0:p.error('Positive finite session deadline required')
    plan,_=verify_plan(a.selection_plan);window=smoke_window(plan,desired=5)
    resource_preflight(a.out,require_research_mount=a.require_ebs,research_mount=a.research_mount)
    out=new_output(a.out,plan['dataRoot'])
    cert=dict(version='V082_CAPACITY_1',population=50,dataOrigin=a.data_origin,automaticIncrease=False,status='RUNNING',host=host_fingerprint(),createdEpoch=time.time(),
        planSha256=file_sha256(a.selection_plan),configSha256=file_sha256(a.config),codeHashes=code_hashes(),seed=a.seed,maxSessionSeconds=a.max_session_seconds,trials=[],finalOosGate='CLOSED')
    atomic_json(out/'capacity.json',cert)
    try:
        for label in ('warmup','measured-1','measured-2'):
            cmd=[sys.executable,str(CONFIG.with_name('run_v082.py')),'--selection-plan',str(a.selection_plan),'--config',str(a.config),'--results-dir',str(out/label),
                '--population','50','--seed',str(a.seed),'--data-origin',a.data_origin,'--capacity-trial','--from-date',window['fromDate'],'--to-date',window['toDate'],
                '--max-session-seconds',str(a.max_session_seconds),'--research-mount',a.research_mount]
            if a.require_ebs:cmd+=['--require-ebs']
            # Retain inherited shared research lock descriptors across each child.
            fds=tuple(int(v) for v in os.environ.get('V082_LOCK_FDS','').split(',') if v)
            with (out/(label+'.log')).open('w') as log:
                process=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,pass_fds=fds,
                    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1'))
                try:rc=process.wait(timeout=a.max_session_seconds*len(window['dates'])+600)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                    raise ValueError('V082_OWNED_TRIAL_TIMEOUT')
            files=list((out/label).glob('*/run.json'))
            if rc or len(files)!=1:raise ValueError('V082_TRIAL_FAILED:'+label)
            run=files[0];info=json.loads(run.read_text())
            if info.get('phase')!='completed' or not info.get('completedTrainGenerations') or not safe_measurement(info.get('resourceReport',{}),a.max_session_seconds):raise ValueError('V082_TRIAL_UNSAFE:'+label)
            cert['trials'].append(dict(label=label,path=str(run.resolve()),sha256=file_sha256(run),resourceReport=info['resourceReport']))
            atomic_json(out/'capacity.json',cert)
        cert['status']='MEASURED_SAFE';atomic_json(out/'capacity.json',cert)
    except Exception as exc:
        cert.update(status='BLOCKED',error=str(exc));atomic_json(out/'capacity.json',cert);raise
    print(json.dumps(dict(capacityReport=str(out/'capacity.json'),status=cert['status'],population=50)))
    return out

if __name__=='__main__':main()
