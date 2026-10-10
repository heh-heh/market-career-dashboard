"""Allowlisted historical jobs only. No trading imports, shell, or user commands."""
import fcntl
import io
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

BACKTEST_ENGINES = {
    "simple-v1": dict(label="Simple Momentum V1",script="backtest_simple_v1.py",prefix="backtest_simple_v1"),
    "v3": dict(label="Strategy Engine V3",script="backtest_multistrategy_v1.py",prefix="backtest_multistrategy_v1"),
    "v4-ir1": dict(label="V4 · IR1 Relative Strength Pullback",script="backtest_intraday_v4.py",prefix="backtest_v4_ir1",strategy="ir1"),
    "v4-ir2": dict(label="V4 · IR2 Failed Opening Breakdown",script="backtest_intraday_v4.py",prefix="backtest_v4_ir2",strategy="ir2"),
    "v4-ir3": dict(label="V4 · IR3 Late-Day Index Momentum",script="backtest_intraday_v4.py",prefix="backtest_v4_ir3",strategy="ir3"),
    "v4-all": dict(label="V4 · All IR Strategies",script="backtest_intraday_v4.py",prefix="backtest_v4_all",strategy="all"),
}


def read_json(path):
    try: return json.loads(path.read_text())
    except (OSError,ValueError): return {}


def process_args(pid):
    try:
        values=Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")
        return [v.decode(errors="replace") for v in values if v]
    except (OSError,TypeError,ValueError): return []


class BacktestManager:
    def __init__(self,root,directory=None):
        self.root=Path(root)
        self.directory=Path(directory or os.getenv("BACKTEST_DATA_DIR","/var/lib/market-career-dashboard"))
        self.lock=threading.RLock()

    def spec(self,engine):
        if engine not in BACKTEST_ENGINES: raise ValueError("Unknown backtest engine")
        return BACKTEST_ENGINES[engine]

    def paths(self,engine):
        prefix=self.spec(engine)["prefix"]
        return {k:self.directory/(prefix+v) for k,v in dict(result="_result.json",state="_state.json",launch="_launch.json",trades="_trades.csv",
                      log=".log",audit="_data_audit.json",decisions="_decisions.jsonl").items()}

    def running(self,pid,engine):
        script=str(self.root/"research"/self.spec(engine)["script"])
        return script in process_args(pid)

    def status(self,engine):
        paths=self.paths(engine); state=read_json(paths["state"]); result=read_json(paths["result"])
        if state.get("runId") and state["runId"]!=result.get("runId"): result={}
        pid=state.get("pid") or read_json(paths["launch"]).get("pid")
        running=self.running(pid,engine)
        phase=state.get("phase") or ("completed" if result else "waiting")
        if running and phase=="backtest": phase="running"
        if state.get("running") and not running and phase not in {"completed","error"}: phase="interrupted"
        try:
            # Read a bounded tail, not a multi-GB historical log.
            with paths["log"].open("rb") as f:
                f.seek(max(0,paths["log"].stat().st_size-16000))
                log=f.read().decode(errors="replace")
        except OSError: log=""
        merged={**state,**{k:result.get(k,{}) for k in ("funnel","overall","byStrategy","bySymbol","byYear","byMonth","bySession","byExitReason","byRegime","configuration","execution","data") if result}}
        data_dir=self.root/"data/toss_1m"
        has_data=data_dir.is_dir() and (any(data_dir.glob("*.csv.gz")) or any(data_dir.glob("*.csv")))
        manifest_path=Path(os.getenv("V4_DATA_MANIFEST",str(self.root/"research/v4_data_manifest.json")))
        blocked_reason=None
        if not has_data:
            blocked_reason="historical Toss 1m data is unavailable"
        elif engine.startswith("v4-") and not manifest_path.is_file():
            blocked_reason="Reviewed V4 data manifest is missing; finish data collection/audit and build the reviewed manifest first"
        merged.update(engine=engine,label=self.spec(engine)["label"],pid=pid,running=running,phase=phase,
            summary=result.get("overall") or state.get("summary") or {},log=log,
            progress=state.get("progress",100 if result else 0),downloadAvailable=bool(result),resultAvailable=bool(result),
            runnable=blocked_reason is None,blockedReason=blocked_reason,
            manifestAvailable=manifest_path.is_file() if engine.startswith("v4-") else None)
        return merged

    def active_job(self):
        for engine in BACKTEST_ENGINES:
            paths=self.paths(engine)
            state=read_json(paths["state"])
            pid=state.get("pid") or read_json(paths["launch"]).get("pid")
            if self.running(pid,engine): return engine
        # Also guard manual CLI/systemd historical runs not launched by us.
        for p in Path("/proc").iterdir():
            if not p.name.isdigit(): continue
            args=process_args(p.name)
            for arg in args:
                path=Path(arg)
                if (path.name.startswith("backtest_") and path.suffix==".py" and path.parent.resolve()==(self.root/"research").resolve()) or path==self.root/"bin/backtest_toss_v5":
                    return path.name
        return None

    def command(self,engine):
        spec=self.spec(engine); paths=self.paths(engine); script=self.root/"research"/spec["script"]
        cmd=[sys.executable,str(script),"--out",str(paths["result"]),"--state",str(paths["state"]),"--log",str(paths["log"])]
        if "strategy" in spec:
            cmd += ["--strategy",spec["strategy"],"--data-dir",str(self.root/"data/toss_1m"),
                    "--manifest",os.getenv("V4_DATA_MANIFEST",str(self.root/"research/v4_data_manifest.json")),
                    "--trades-csv",str(paths["trades"]),"--data-audit",str(paths["audit"]),"--decisions",str(paths["decisions"])]
        else:
            cmd += ["--data",str(self.root/"data/toss_1m")]
            if engine=="simple-v1": cmd += ["--trades-csv",str(paths["trades"])]
        return cmd

    def start(self,engine,legacy_start=None):
        self.spec(engine)
        self.directory.mkdir(parents=True,exist_ok=True)
        with self.lock, (self.directory/"historical_backtest.lock").open("a") as lockfile:
            fcntl.flock(lockfile,fcntl.LOCK_EX)
            active=self.active_job()
            if active: raise ValueError("Another historical backtest is running: "+active)
            if legacy_start is not None: return legacy_start()
            data=self.root/"data/toss_1m"
            if not data.is_dir() or not (any(data.glob("*.csv.gz")) or any(data.glob("*.csv"))):
                raise RuntimeError("historical Toss 1m data is unavailable")
            cmd=self.command(engine)
            if not Path(cmd[1]).is_file(): raise RuntimeError("Backtest script is missing")
            if engine.startswith("v4-") and not Path(cmd[cmd.index("--manifest")+1]).is_file():
                raise RuntimeError("Reviewed V4 data manifest is missing; build/review it first")
            paths=self.paths(engine)
            # Keep failed/previous artifacts on preflight failure. Once accepted,
            # create a new run; existing forward logs are never included here.
            for p in paths.values(): p.unlink(missing_ok=True)
            def write_json(path,obj):
                tmp=path.with_suffix(path.suffix+".tmp")
                tmp.write_text(json.dumps(obj))
                os.replace(tmp,path)
            state=dict(engine=engine,strategy=self.spec(engine).get("strategy"),pid=None,phase="starting",
                running=True,progress=0,updatedAt=time.time(),trades=0,signals=0,entries=0,error=None)
            write_json(paths["state"],state)
            with paths["log"].open("ab") as log:
                try:
                    proc=subprocess.Popen(cmd,cwd=self.root,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                                          start_new_session=True,close_fds=True)
                except OSError as exc:
                    state.update(phase="error",running=False,error=str(exc))
                    write_json(paths["state"],state)
                    raise
            # Separate launch metadata never overwrites the child's progress.
            write_json(paths["launch"],dict(pid=proc.pid,engine=engine))
            return self.status(engine)

    def download(self,engine):
        paths=self.paths(engine)
        if not paths["result"].exists(): raise FileNotFoundError("backtest result is not available yet")
        status=self.status(engine)
        if status["running"]: raise ValueError("Backtest is still running")
        if not status["downloadAvailable"]: raise FileNotFoundError("Current run has no completed result")
        result=read_json(paths["result"])
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w",compression=zipfile.ZIP_DEFLATED) as archive:
            for key in ("result","trades","state","log","audit","decisions"):
                if paths[key].exists(): archive.write(paths[key],paths[key].name)
            if engine=="v3" and not paths["trades"].exists():
                # Compatibility adapter only: never alter V3's simulation.
                import csv
                fp=io.StringIO(); rows=result.get("trades",[])
                fields=sorted(set().union(*(t.keys() for t in rows))) if rows else ["symbol","strategy"]
                writer=csv.DictWriter(fp,fieldnames=fields);writer.writeheader()
                for row in rows: writer.writerow(row)
                archive.writestr(paths["trades"].name,fp.getvalue())
        return buf.getvalue()
