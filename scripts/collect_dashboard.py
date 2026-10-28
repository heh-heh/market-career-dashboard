#!/usr/bin/env python3
"""Bounded hourly dashboard worker. One shared flock; never starts trading."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dashboard_data import atomic_json, read_dashboard


def run_once(data_dir, timeout=600):
    data_dir=Path(data_dir)
    data_dir.mkdir(parents=True,exist_ok=True)
    with (data_dir/'collector.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'phase':'SKIPPED_ALREADY_RUNNING'}),flush=True)
            return 75
        started=datetime.now(timezone.utc).isoformat()
        status={'phase':'RUNNING','startedAt':started,'timeoutSeconds':timeout}
        atomic_json(data_dir/'collector_status.json',status)
        env={**os.environ,'DASHBOARD_DATA_DIR':str(data_dir),
             'DASHBOARD_DATA_FILE':str(data_dir/'dashboard.json'),'COLLECT_MAX_SECONDS':str(timeout),
             'DASHBOARD_COLLECT_LOCK_FD':str(lock.fileno())}
        child=None
        try:
            child=subprocess.Popen([sys.executable,str(ROOT/'scripts/update_data.py'),'--worker'],
                                   cwd=ROOT,env=env,start_new_session=True,pass_fds=(lock.fileno(),))
            def stop(signum,frame):
                if child.poll() is None: os.killpg(child.pid,signal.SIGTERM)
                raise InterruptedError('COLLECTION_INTERRUPTED')
            old={s:signal.signal(s,stop) for s in (signal.SIGTERM,signal.SIGINT)}
            try:
                code=child.wait(timeout=timeout)
            finally:
                for s,handler in old.items(): signal.signal(s,handler)
            if code: raise RuntimeError('COLLECTOR_EXIT_'+str(code))
            data=read_dashboard(data_dir/'dashboard.json')
            status.update(phase='COMPLETED',lastSuccessAt=data['updatedAt'],
                          counts={k:len(data[k]) for k in ('stocks','indices','news','employmentNews','jobPostings')})
            code=0
        except (subprocess.TimeoutExpired,InterruptedError,RuntimeError,OSError,ValueError) as e:
            if child is not None and child.poll() is None:
                os.killpg(child.pid,signal.SIGTERM)
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL); child.wait()
            status.update(phase='FAILED',errorCode=type(e).__name__,lastError=str(e)[:160])
            code=1
        status['finishedAt']=datetime.now(timezone.utc).isoformat()
        atomic_json(data_dir/'collector_status.json',status)
        print(json.dumps(status),flush=True)
        return code


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',default=os.getenv('DASHBOARD_DATA_DIR',str(ROOT/'data')))
    parser.add_argument('--once',action='store_true',help='Run immediately once (default)')
    parser.add_argument('--loop',action='store_true',help='Run once per hour, including after failures')
    parser.add_argument('--timeout',type=int,default=int(os.getenv('COLLECT_MAX_SECONDS','600')))
    parser.add_argument('--interval',type=int,default=int(os.getenv('COLLECT_INTERVAL_SECONDS','3600')))
    a=parser.parse_args()
    if not 10<=a.timeout<=1800 or a.interval<3600: parser.error('timeout 10..1800; interval >=3600')
    if a.loop:
        try:
            state=json.loads((Path(a.data_dir)/'collector_status.json').read_text())
            previous=datetime.fromisoformat(state['startedAt'])
            remaining=a.interval-(datetime.now(timezone.utc)-previous).total_seconds()
            if 0<remaining<=a.interval: time.sleep(remaining)
        except (OSError,ValueError,KeyError,TypeError): pass
    while True:
        start=time.monotonic()
        result=run_once(a.data_dir,a.timeout)
        if not a.loop: return result
        time.sleep(max(1,a.interval-(time.monotonic()-start)))

if __name__=='__main__': sys.exit(main())
