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
import uuid
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
    try:
        value=json.loads(path.read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError): return {}


def process_args(pid):
    try:
        values=Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")
        return [v.decode(errors="replace") for v in values if v]
    except (OSError,TypeError,ValueError): return []


class PreflightBlocked(RuntimeError):
    """A missing prerequisite: HTTP 412, no subprocess has been launched."""


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

    def preflight(self,engine):
        self.spec(engine)
        manifest=Path(os.getenv("V4_DATA_MANIFEST",str(self.root/"research/v4_data_manifest.json")))
        data=self.root/"data/toss_1m"
        reasons=[]
        if not data.is_dir() or not (any(data.glob("*.csv.gz")) or any(data.glob("*.csv"))):
            reasons.append("HISTORICAL_DATA_REQUIRED: historical Toss 1m data is unavailable")
        if not (self.root/"research"/self.spec(engine)["script"]).is_file():
            reasons.append("BACKTEST_SCRIPT_REQUIRED: Backtest script is missing")
        return dict(runnable=not reasons,blockedReason="; ".join(reasons) or None,
                    preflightReasons=reasons,manifestAvailable=manifest.is_file() if engine.startswith("v4-") else None,
                    dataMode=("REVIEWED_MANIFEST" if manifest.is_file() else "PROVISIONAL_UNREVIEWED_DATA") if engine.startswith("v4-") else None,
                    provisional=engine.startswith("v4-") and not manifest.is_file())

    def status(self,engine):
        paths=self.paths(engine); state=read_json(paths["state"]); result=read_json(paths["result"])
        launch=read_json(paths["launch"])
        if state.get("runId") and state["runId"]!=result.get("runId"): result={}
        if state.get("runId") and launch.get("runId") != state["runId"]: launch={}
        pid=state.get("pid") or launch.get("pid")
        running=self.running(pid,engine)
        phase=state.get("phase") or ("completed" if result else "waiting")
        starting=(phase=="starting" and not pid and time.time()-state.get("launchRequestedAt",0)<10)
        if running and phase=="backtest": phase="running"
        if state.get("running") and not running and not starting and phase not in {"completed","error"}: phase="interrupted"
        readiness=self.preflight(engine)
        if not running and not starting:
            active=self.active_job()
            if active:
                readiness["runnable"]=False
                readiness["blockedReason"]="Another historical backtest is running: "+active
                readiness["preflightReasons"].append(readiness["blockedReason"])
        if phase in {"waiting","blocked"} and not running: phase="waiting" if readiness["runnable"] else "blocked"
        if running or starting: readiness["runnable"]=False
        try:
            with paths["log"].open("rb") as f:
                f.seek(max(0,paths["log"].stat().st_size-16000))
                log=f.read().decode(errors="replace")
        except OSError: log=""
        merged={**state,**{k:result.get(k,{}) for k in ("funnel","overall","byStrategy","bySymbol","byYear","byMonth","bySession","byExitReason","byRegime","configuration","execution","data") if result}}
        merged.update(engine=engine,label=self.spec(engine)["label"],pid=pid,running=running,phase=phase,
            summary=result.get("overall") or state.get("summary") or {},log=log,
            progress=state.get("progress",100 if result else 0),downloadAvailable=bool(result),resultAvailable=bool(result),
            **readiness)
        return merged

    def active_job(self):
        for engine in BACKTEST_ENGINES:
            paths=self.paths(engine)
            state=read_json(paths["state"])
            pid=state.get("pid") or read_json(paths["launch"]).get("pid")
            if self.running(pid,engine): return engine
            if state.get("phase")=="starting" and not pid and time.time()-state.get("launchRequestedAt",0)<10: return engine
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
            manifest=os.getenv("V4_DATA_MANIFEST",str(self.root/"research/v4_data_manifest.json"))
            cmd += ["--strategy",spec["strategy"],"--data-dir",str(self.root/"data/toss_1m"),
                    "--trades-csv",str(paths["trades"]),"--data-audit",str(paths["audit"]),"--decisions",str(paths["decisions"])]
            if Path(manifest).is_file():
                cmd += ["--manifest",manifest]
                reviewed=read_json(Path(manifest))
                if reviewed.get("timestampKind") in {"start","end"}: cmd += ["--timestamp-kind",reviewed["timestampKind"]]
            else:
                cmd += ["--provisional"]
        else:
            cmd += ["--data",str(self.root/"data/toss_1m")]
            if engine=="simple-v1": cmd += ["--trades-csv",str(paths["trades"])]
        return cmd

    def start(self,engine,legacy_start=None):
        self.spec(engine)
        self.directory.mkdir(parents=True,exist_ok=True)
        with self.lock, (self.directory/"historical_backtest.lock").open("a") as lockfile:
            try:
                fcntl.flock(lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another historical backtest holds the execution lock") from None
            active=self.active_job()
            if active: raise ValueError("Another historical backtest is running: "+active)
            if legacy_start is not None: return legacy_start()
            readiness=self.preflight(engine)
            if not readiness["runnable"]: raise PreflightBlocked(readiness["blockedReason"])
            cmd=self.command(engine)
            paths=self.paths(engine)
            # A new V4 run must not delete the baseline it is meant to compare.
            # Rename on the same filesystem; no raw data or forward paper paths.
            archived=None
            if engine.startswith("v4-"):
                previous=[p for p in paths.values() if p.exists() or p.is_symlink()]
                if previous:
                    archived=self.directory/"backtest_archive"/(self.spec(engine)["prefix"]+"-"+uuid.uuid4().hex)
                    archived.mkdir(parents=True,exist_ok=False)
                    for p in previous: p.rename(archived/p.name)
            else:
                for p in paths.values(): p.unlink(missing_ok=True)
            def write_json(path,obj):
                tmp=path.with_suffix(path.suffix+".tmp")
                tmp.write_text(json.dumps(obj))
                os.replace(tmp,path)
            run_id=uuid.uuid4().hex
            if engine.startswith("v4-"): cmd += ["--run-id",run_id]
            state=dict(runId=run_id,launchRequestedAt=time.time(),engine=engine,strategy=self.spec(engine).get("strategy"),pid=None,phase="starting",
                running=True,progress=0,updatedAt=time.time(),trades=0,signals=0,entries=0,error=None)
            if archived: state["previousArtifactArchive"]=str(archived)
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
            write_json(paths["launch"],dict(pid=proc.pid,engine=engine,runId=run_id))
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
