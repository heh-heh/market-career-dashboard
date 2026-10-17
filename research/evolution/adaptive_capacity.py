"""Measured, bounded V081 resource guard. Never kills or configures other services."""
from __future__ import annotations
from collections import Counter
import os
import shutil
import threading
import time
import psutil

def host_fingerprint():
    import hashlib
    import platform
    from pathlib import Path
    parts=[platform.node(),str(psutil.cpu_count()),str(psutil.virtual_memory().total)]
    for path in ('/etc/machine-id','/proc/sys/kernel/random/boot_id'):
        try:parts.append(Path(path).read_text().strip())
        except OSError:parts.append('unavailable')
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()


TARGETS={'cpuPct':75.,'memoryPct':65.,'swapPct':3.}


def next_population(current, maximum):
    if current >= maximum:return None
    # Conservative coarse growth; every recommendation must actually be tested.
    return min(maximum, max(current+1, int(current*1.5)))


def safe_measurement(report, max_session_seconds):
    return (report.get('samples',0)>=2 and report.get('guardPassed') is True and
        all(report['peak'][k]<=v for k,v in TARGETS.items()) and
        report.get('maxSessionSeconds',float('inf'))<=max_session_seconds)


def recommend(trials, maximum, max_session_seconds, repeats=2):
    passed=[];previous=None
    for trial in trials:
        n=trial['population']
        reports=trial.get('measuredRuns',[])
        good=(trial.get('warmupValid') is True and len(reports)==repeats and
              all(r.get('completedGenerations',0)>=1 and safe_measurement(r,max_session_seconds) for r in reports))
        if not good:break
        if n>maximum or (previous is None and n!=100) or (previous is not None and n!=next_population(previous,maximum)):
            raise ValueError('NONSEQUENTIAL_CAPACITY_TRIALS')
        passed.append(n);previous=n
    return dict(recommendedPopulation=max(passed) if passed else None, maximumPopulation=maximum,
        targets=TARGETS, measuredOnly=True, status='MEASURED_SAFE' if passed else 'BLOCKED_100_UNSAFE_OR_INVALID',
        reason='Largest sequential population that passed warmup and repeated measured pilots; not an untested extrapolation',
        timingLimitSeconds=max_session_seconds)


class ResourceMonitor:
    def __init__(self, *, interval=.5, max_session_seconds=900, targets=None):
        self.interval=interval;self.max_session_seconds=max_session_seconds;self.targets=targets or TARGETS
        self._stop=threading.Event();self._lock=threading.Lock();self._thread=None
        self.count=0;self.sums=Counter();self.peaks=Counter();self.failure=None;self.sessions=[]
        self.started=time.monotonic();self.deadline=None;self.proc=psutil.Process(os.getpid())
        self.io_start=self.proc.io_counters();self.disk_start=psutil.disk_io_counters()
        self.process_start=sum(self.proc.cpu_times()[:2]);self.latest={};self.other_processes=[];self.output=None;self._process_cpu={};self._process_time=time.monotonic()

    def sample(self):
        vm=psutil.virtual_memory();sw=psutil.swap_memory()
        values=dict(cpuPct=psutil.cpu_percent(interval=None),memoryPct=vm.percent,
                    swapPct=sw.percent if sw.total else 0.,processRssMb=self.proc.memory_info().rss/1024**2)
        # Commit safety measurements before optional per-process inspection.
        # /proc enumeration can be slow; it must not starve the host guard.
        with self._lock:
            self.count+=1;self.latest=values;self.sums.update(values)
            for k,v in values.items():self.peaks[k]=max(self.peaks[k],v)
            for k,limit in self.targets.items():
                if values[k]>limit:self.failure=self.failure or f'RESOURCE_TARGET_EXCEEDED:{k}:{values[k]}>{limit}'
            inspect_others=self.count%10==1
        if not inspect_others:return
        others=[];now=time.monotonic();elapsed=max(.001,now-self._process_time);current_cpu={}
        deadline=now+.05
        for process in psutil.process_iter(['pid','name','memory_info','cpu_times','create_time']):
            if time.monotonic()>deadline:break
            try:
                if process.pid!=self.proc.pid:
                    key=(process.pid,process.info['create_time']);cpu=sum(process.info['cpu_times'][:2]);current_cpu[key]=cpu
                    prior=self._process_cpu.get(key)
                    other_cpu=max(0,cpu-prior)/elapsed*100/(psutil.cpu_count() or 1) if prior is not None else None
                    others.append(dict(pid=process.pid,name=process.info['name'],rssMb=process.info['memory_info'].rss/1024**2,cpuHostPct=other_cpu))
            except (psutil.Error,AttributeError,TypeError,KeyError):continue
        self._process_cpu=current_cpu;self._process_time=now
        with self._lock:self.other_processes=sorted(others,key=lambda p:-p['rssMb'])[:8]

    def _loop(self):
        # psutil CPU baselines are per-thread; prime in this same sampler thread.
        psutil.cpu_percent(interval=None)
        while not self._stop.wait(self.interval):
            try:self.sample()
            except Exception as exc:
                with self._lock:self.failure='RESOURCE_MEASUREMENT_FAILED:'+type(exc).__name__

    def start(self):
        psutil.cpu_percent(interval=None);time.sleep(.1);self.sample();self.check()
        self._thread=threading.Thread(target=self._loop,daemon=True);self._thread.start();return self

    def begin_session(self):
        self.deadline=time.monotonic()+self.max_session_seconds

    def check(self):
        if self.deadline is not None and time.monotonic()>self.deadline:
            self.failure='SESSION_TIME_BUDGET_EXCEEDED'
        with self._lock:failure=self.failure
        if failure:raise ValueError('V081_RESOURCE_PAUSED:'+failure)

    def session(self, elapsed):
        with self._lock:
            self.sessions.append(elapsed)
            if elapsed>self.max_session_seconds:self.failure='SESSION_TIME_BUDGET_EXCEEDED'
        self.check()

    def report(self):
        io=self.proc.io_counters();disk=psutil.disk_io_counters()
        with self._lock:
            return dict(samples=self.count,mean={k:v/self.count for k,v in self.sums.items()} if self.count else {},
                peak=dict(self.peaks),latest=dict(self.latest),guardPassed=self.failure is None,failure=self.failure,
                maxSessionSeconds=max(self.sessions,default=0),meanSessionSeconds=sum(self.sessions)/len(self.sessions) if self.sessions else None,
                processCpuSeconds=sum(self.proc.cpu_times()[:2])-self.process_start,
                processReadBytes=max(0,io.read_bytes-self.io_start.read_bytes),processWriteBytes=max(0,io.write_bytes-self.io_start.write_bytes),
                systemReadBytes=max(0,disk.read_bytes-self.disk_start.read_bytes) if disk and self.disk_start else None,
                systemWriteBytes=max(0,disk.write_bytes-self.disk_start.write_bytes) if disk and self.disk_start else None,
                elapsedSeconds=time.monotonic()-self.started,otherProcessesByRss=self.other_processes,
                diskFreeBytes=shutil.disk_usage(self.output).free if self.output else None,
                backgroundLoad='Host CPU/RAM/swap include all jobs; per-process rows are a bounded visible sample, not a complete process ranking; system I/O is not attributed to this trainer')

    def close(self):
        self._stop.set()
        if self._thread:self._thread.join(timeout=2)
