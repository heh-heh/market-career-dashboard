"""Authenticated V0.8.2 research job controller. No arbitrary shell or trading actions."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone

import v07_web_jobs as legacy

MOUNT = Path(os.getenv("V082_RESEARCH_MOUNT", "/mnt/research"))
if str(MOUNT) not in ("/mnt/research", "/mnt/research-extra"):
    raise ValueError("V082_UNSUPPORTED_RESEARCH_MOUNT")
ROOT = MOUNT / "v082-web-jobs"
RESULTS = MOUNT / "evolution-v082"
APP = MOUNT / "v082-source"
PYTHON = Path("/home/ubuntu/market-career-dashboard/.venv/bin/python")
RUNNER = APP / "research/evolution/run_v082.py"
PROTOCOL = "V082_WEB_1"
MAX_POPULATION = 50
MAX_SESSION_SECONDS = float(os.getenv("V082_MAX_SESSION_SECONDS", "900"))
if MAX_POPULATION != 50 or MAX_SESSION_SECONDS <= 0: raise ValueError("V082_INVALID_CAPACITY_CONFIG")
STAGES = ("smoke", "calibrate", "train", "holdout", "validation", "export")
PRIOR = {"smoke": None, "calibrate": "smoke", "train": "calibrate", "holdout": "train",
         "validation": "holdout", "export": "validation"}
DATES = {"train": ("2022-01-01", "2023-12-31"),
         "holdout": ("2024-01-01", "2024-12-31"),
         "validation": ("2025-01-01", "2025-12-31")}
JOB_PATTERN = re.compile(r"job-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path):
    return legacy._json(path)


def _write(path, payload):
    legacy._write(path, payload)


def _storage():
    research = MOUNT
    if not research.is_dir() or research.is_symlink() or research.stat().st_dev == Path("/").stat().st_dev:
        raise ValueError("RESEARCH_EBS_MOUNT_REQUIRED")
    if ROOT.is_symlink() or RESULTS.is_symlink():
        raise ValueError("UNSAFE_V082_STORAGE")
    ROOT.mkdir(mode=0o700, exist_ok=True)
    RESULTS.mkdir(mode=0o700, exist_ok=True)
    return ROOT


def _job():
    name = _read(ROOT / "current.json").get("name")
    if not isinstance(name, str) or JOB_PATTERN.fullmatch(name) is None:
        return None
    path = ROOT / name
    return path if path.is_dir() and not path.is_symlink() else None


def _state(job, stage):
    if job is None:
        return {"phase": "not_started", "stage": stage}
    s = _read(job / (stage + ".json"))
    if not s:
        return {"phase": "not_started", "stage": stage}
    if s.get("phase") in ("running", "starting"):
        pid = s.get("pid")
        if isinstance(pid, int):
            try:
                tokens = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace").split("\0")
            except OSError:
                tokens = []
            if not all(x in tokens for x in ("--worker", stage, str(job))) or not any("v082_web_jobs.py" in x for x in tokens):
                return {**s, "phase": "interrupted", "error": "WORKER_NOT_RUNNING"}
            if s.get("identity") and legacy._process_identity(pid) != s["identity"]:
                return {**s, "phase": "interrupted", "error": "WORKER_IDENTITY_CHANGED"}
        elif time.time() - float(s.get("startedEpoch", 0)) > 60:
            return {**s, "phase": "interrupted", "error": "WORKER_START_TIMEOUT"}
    return s


def _plan():
    previous = legacy._current()
    if not previous or legacy._state(previous, "select")["phase"] != "completed":
        raise ValueError("V082_SELECTION_PLAN_REQUIRED: existing V07 audit/selection must be completed")
    p = previous / "selection" / "selection_plan.json"
    if p.is_symlink() or not p.is_file():
        raise ValueError("V082_SELECTION_PLAN_MISSING")
    return p


def _verified_run(job, stage):
    state = _state(job, stage)
    if state["phase"] != "completed":
        raise ValueError("V082_STAGE_ORDER: " + stage + " not completed")
    folder = Path(state.get("runDir") or "")
    if (not folder.is_dir() or folder.is_symlink() or
            folder.resolve().parent != RESULTS.resolve()):
        raise ValueError("V082_RUN_PATH_INVALID")
    result = _read(folder / "run.json")
    if result.get("phase") != "completed" or result.get("finalOosGate") != "CLOSED" or result.get("version") != "V082_INDICATOR_1":
        raise ValueError("V082_RUN_NOT_VERIFIED")
    if stage == "smoke" and not result.get("completedTrainGenerations"):
        raise ValueError("V082_SMOKE_NO_VALID_GENERATIONS")
    if stage == "train":
        if not result.get("completedTrainGenerations") or not result.get("resultsValid") or not result.get("inputHashesVerified"):
            raise ValueError("V082_TRAIN_NOT_VALID")
        if not (folder / "final_population.json").is_file():
            raise ValueError("V082_FROZEN_POPULATION_MISSING")
    return folder


def _capacity(job):
    state = _state(job, "calibrate")
    path = Path(state.get("capacityReport") or "")
    if state["phase"] != "completed" or not path.is_file() or path.is_symlink() or path.resolve().parent.parent != job.resolve():
        raise ValueError("V082_MEASURED_CAPACITY_REQUIRED")
    if legacy._hash(path) != state.get("capacitySha256"): raise ValueError("V082_CAPACITY_CHANGED")
    data = _read(path)
    population = data.get("population")
    if not 0<=time.time()-float(data.get("createdEpoch",0))<=24*3600:raise ValueError("V082_CAPACITY_EXPIRED_REMEASURE")
    if data.get("version")!="V082_CAPACITY_1" or data.get("status")!="MEASURED_SAFE" or not isinstance(population,int) or population != 50:
        raise ValueError("V082_CAPACITY_NOT_SAFE")
    return dict(path=path,population=population)


def _last_log(job):
    states = [_read(job / (s + ".json")) for s in STAGES]
    entry = max(states, key=lambda x: x.get("startedEpoch", 0), default={})
    name = entry.get("logFile")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z]+(-[a-f0-9]+)?\.log", name):
        return None
    return job / name


def _other_active():
    other = legacy._current()
    if other:
        for stage in legacy.STAGES:
            if legacy._state(other, stage)["phase"] in ("running", "starting"):
                raise ValueError("V082_BUSY_V07: another research worker is active")


def _source_app():
    proof=_read(ROOT/"source_revision.json")
    path=Path(proof.get("sourcePath") or str(APP))
    if path.parent!=MOUNT or not path.name.startswith("v082-source") or path.is_symlink():
        raise ValueError("V082_SOURCE_PATH_INVALID")
    return path


def _verify_source():
    app=_source_app()
    if app.is_symlink() or not app.is_dir():
        raise ValueError("V082_PINNED_SOURCE_MISSING")
    try:
        git = ["git", "-c", f"safe.directory={app}", "-C", str(app)]
        sha = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True, timeout=8).strip()
        dirty = subprocess.check_output(git + ["status", "--porcelain", "--untracked-files=no"], text=True, timeout=8).strip()
    except (OSError, subprocess.SubprocessError):
        raise ValueError("V082_SOURCE_NOT_VERIFIABLE")
    proof = _read(ROOT / "source_revision.json")
    if not re.fullmatch(r"[a-f0-9]{40}", str(proof.get("commit", ""))):
        raise ValueError("V082_SOURCE_REVISION_REQUIRED")
    if sha != proof["commit"] or dirty:
        raise ValueError("V082_SOURCE_VERSION_NOT_PINNED")
    if not (app/"research/evolution/run_v082.py").is_file():
        raise ValueError("V082_RUNNER_MISSING")


def start(stage, retry=False, new_job=False):
    if stage not in STAGES:
        raise ValueError("V082_COMMAND_NOT_ALLOWED")
    _storage()
    legacy._check_service_lifetime()
    _other_active()
    if not PYTHON.is_file():
        raise ValueError("V082_PYTHON_MISSING")
    if stage != "export":
        _verify_source()
    plan = _plan()
    plan_hash = legacy._hash(plan)
    with (ROOT / "start.lock").open("a+") as start_lock, (ROOT / "research.lock").open("a+") as run_lock, (legacy.ROOT / "research.lock").open("a+") as shared_lock:
        fcntl.flock(start_lock, fcntl.LOCK_EX)
        try:
            fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(shared_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("V082_BUSY: V0.7/V0.8.2 research worker or child holds a lock")
        job = _job()
        if job and any(_state(job, st)["phase"] in ("starting", "running") for st in STAGES):
            raise ValueError("V082_BUSY: a stage is already running")
        if new_job:
            if stage!="smoke" or retry:raise ValueError("V082_NEW_JOB_REQUIRES_PILOT")
            job=None
        if stage == "smoke" and job and _state(job, "smoke")["phase"] == "completed" and not retry:
            raise ValueError("V082_STAGE_ALREADY_STARTED: do not overwrite existing job")
        if job is None:
            if stage != "smoke" or retry:
                raise ValueError("V082_SMOKE_REQUIRED")
            name = datetime.now(timezone.utc).strftime("job-%Y%m%dT%H%M%SZ-") + os.urandom(6).hex()
            job = ROOT / name
            job.mkdir(mode=0o700)
            _write(ROOT / "current.json", {"name": name})
        evidence = _read(job / "source_plan.json")
        if evidence:
            if stage!="export" and evidence.get("sourceCommit")!=_read(ROOT/"source_revision.json").get("commit"):
                raise ValueError("V082_CAMPAIGN_SOURCE_CHANGED_NEW_JOB_REQUIRED")
            if evidence.get("path") != str(plan.resolve()) or evidence.get("sha256") != plan_hash:
                raise ValueError("V082_PLAN_CHANGED: preserve frozen research selection")
        previous = PRIOR[stage]
        if previous:
            if previous in ("train", "smoke"):
                _verified_run(job, previous)
            elif _state(job, previous)["phase"] != "completed":
                raise ValueError("V082_STAGE_ORDER: complete " + previous)
        if stage == "train": _capacity(job)
        old = _state(job, stage)
        if (job / (stage + ".json")).exists():
            if not retry or old["phase"] not in ("failed", "interrupted"):
                raise ValueError("V082_STAGE_ALREADY_STARTED: existing results preserved")
            archive = job / "retries" / (stage + "-" + uuid.uuid4().hex)
            archive.mkdir(parents=True)
            for filename in (stage + ".json", old.get("logFile", "")):
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
                    continue
                source = job / filename
                if source.is_file() and not source.is_symlink():
                    shutil.copy2(source, archive / source.name)
        elif retry:
            raise ValueError("V082_NO_FAILED_STAGE")
        if not evidence:
            _write(job / "source_plan.json", {"path": str(plan.resolve()), "sha256": plan_hash,"sourceCommit":_read(ROOT/"source_revision.json").get("commit")})
        logfile = stage + ("-" + uuid.uuid4().hex if retry else "") + ".log"
        state = dict(stage=stage, phase="starting", startedEpoch=time.time(), logFile=logfile, finalOosGate="CLOSED")
        _write(job / (stage + ".json"), state)
        try:
            with (job / logfile).open("xb") as log:
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", stage, str(job)],
                    cwd=str(job), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    close_fds=True, start_new_session=True, pass_fds=(run_lock.fileno(), shared_lock.fileno()),
                    env={**os.environ, "PYTHONUNBUFFERED": "1", "V082_LOCK_FD": str(run_lock.fileno()),
                         "V082_SHARED_LOCK_FD": str(shared_lock.fileno())})
        except OSError as exc:
            _write(job / (stage + ".json"), {**state, "phase": "failed", "error": str(exc)[:200]})
            raise
    return "STARTED v082 " + stage + " — browser disconnect will not stop the research worker."


def _export(job):
    stages = ("smoke", "train", "holdout", "validation")
    dest = job / "v082-results.zip"
    if dest.exists() or dest.is_symlink():
        raise ValueError("V082_EXPORT_ALREADY_EXISTS")
    space = shutil.disk_usage(job)
    if space.free < max(10 * 1024**3, space.total * .1):
        raise ValueError("RESOURCE_GUARD_PAUSED")
    allowed = {"run.json", "status.json", "final_population.json", "last_evaluated_population.json",
               "validation_summary.json", "used_sessions.json", "evaluation_sessions.json",
               "generations.jsonl", "windows.jsonl", "lineage.jsonl", "resources.json", "checkpoint.json", "pilot_selection.json"}
    rows = []
    for stage in stages:
        if _state(job, stage)["phase"] != "completed":
            continue
        folder = _verified_run(job, stage)
        rows += [(stage, p) for p in folder.iterdir()
                 if p.is_file() and not p.is_symlink() and
                 (p.name in allowed or re.fullmatch(r"window_[0-9]{4,}\.json", p.name))]
        rows += [(stage,p) for p in folder.glob("attempt_*/*") if p.is_file() and not p.is_symlink() and not p.parent.is_symlink() and p.name in {"metrics.json","trades.csv"}]
    total = sum(p.stat().st_size for _, p in rows)
    if space.free - total < max(10 * 1024**3, space.total * .1):
        raise ValueError("RESOURCE_GUARD_PAUSED: export reserve")
    tmp = job / ("export-" + uuid.uuid4().hex + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "x", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            for stage, path in rows:
                z.write(path, stage + "/" + str(path.relative_to(_verified_run(job,stage))))
            state=_read(job/"calibrate.json")
            cp=Path(state.get("capacityReport") or "")
            if cp.is_file() and cp.resolve().parent.parent==job.resolve():
                z.write(cp,"calibration/capacity.json")
            z.writestr("manifest.json", json.dumps({"version": "V082", "researchOnly": True,
                "finalOosGate": "CLOSED", "files": [stage + "/" + str(p.relative_to(_verified_run(job,stage))) for stage, p in rows]}))
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def worker(stage, job):
    statefile = job / (stage + ".json")
    state = {**_read(statefile), "phase": "running", "pid": os.getpid(),
             "identity": legacy._process_identity(os.getpid()), "startedAt": _now()}
    _write(statefile, state)
    try:
        if stage == "export":
            _export(job)
        else:
            _verify_source()
            evidence = _read(job / "source_plan.json")
            if evidence.get("sourceCommit")!=_read(ROOT/"source_revision.json").get("commit"):
                raise ValueError("V082_CAMPAIGN_SOURCE_CHANGED")
            plan = Path(evidence.get("path") or "")
            if (not plan.is_file() or plan.is_symlink() or
                    legacy._hash(plan) != evidence.get("sha256")):
                raise ValueError("V082_PLAN_CHANGED_DURING_LAUNCH")
            app=_source_app()
            argv = [str(PYTHON), str(app/"research/evolution/run_v082.py"), "--selection-plan", str(plan),
                    "--seed", "42", "--require-ebs", "--research-mount", str(MOUNT), "--results-dir", str(RESULTS), "--max-session-seconds", str(MAX_SESSION_SECONDS)]
            if stage == "smoke":
                argv += ["--pilot"]
            elif stage == "calibrate":
                output = job / ("capacity-" + uuid.uuid4().hex)
                state["capacityReport"]=str(output/"capacity.json")
                _write(statefile,state)
                argv = [str(PYTHON), str(app / "research/evolution/calibrate_v082.py"),
                    "--selection-plan", str(plan), "--out", str(output), "--require-ebs",
                    "--research-mount", str(MOUNT),
                    "--max-session-seconds", str(MAX_SESSION_SECONDS)]
            else:
                a, b = DATES[stage]
                argv += ["--from-date", a, "--to-date", b]
                if stage == "train":
                    capacity = _capacity(job)
                    argv += ["--capacity-report", str(capacity["path"]), "--population", str(capacity["population"])]
                if stage != "train":
                    argv += ["--source-run", str(_verified_run(job, "train"))]
            if Path("/usr/bin/nice").is_file():
                argv = ["/usr/bin/nice", "-n", "10"] + argv
            env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                   "MKL_NUM_THREADS": "1", "GIT_TERMINAL_PROMPT": "0"}
            inherited = int(os.environ.get("V082_LOCK_FD", "-1"))
            shared = int(os.environ.get("V082_SHARED_LOCK_FD", "-1"))
            env["V082_LOCK_FDS"] = ",".join(str(fd) for fd in (inherited,shared) if fd>=0)
            last_dir = None
            log = (job / state["logFile"]).open("ab", buffering=0)
            try:
                with subprocess.Popen(argv, cwd=str(app), env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      pass_fds=tuple(fd for fd in (inherited, shared) if fd >= 0)) as child:
                    state["childPid"] = child.pid
                    _write(statefile, state)
                    for line in iter(child.stdout.readline, b""):
                        if log.tell() + len(line) > 8 * 1024**2:
                            log.close()
                            os.replace(job / state["logFile"], job / (stage + ".previous.log"))
                            log = (job / state["logFile"]).open("ab", buffering=0)
                        log.write(line)
                        try:
                            record = json.loads(line)
                            if isinstance(record, dict) and stage == "calibrate" and "capacityReport" in record:
                                last_dir = record["capacityReport"]
                            if isinstance(record, dict) and "resultsDir" in record:
                                last_dir = record["resultsDir"]
                                observed=Path(last_dir)
                                if observed.is_dir() and not observed.is_symlink() and observed.resolve().parent==RESULTS.resolve():
                                    state["runDir"]=last_dir;_write(statefile,state)
                        except (ValueError, UnicodeDecodeError):
                            pass
                    code = child.wait()
            finally:
                log.close()
            if code:
                raise ValueError("V082_RUN_EXIT_" + str(code))
            if stage == "calibrate":
                capacity_path = Path(last_dir or "")
                if not capacity_path.is_file() or capacity_path.is_symlink() or capacity_path.resolve().parent.parent != job.resolve():
                    raise ValueError("V082_CAPACITY_RESULT_PATH_INVALID")
                capacity = _read(capacity_path)
                if capacity.get("status") != "MEASURED_SAFE" or capacity.get("population") != 50:
                    raise ValueError("V082_CAPACITY_50_UNSAFE_OR_INVALID")
                state.update(capacityReport=str(capacity_path), capacitySha256=legacy._hash(capacity_path))
            else:
                folder = Path(last_dir or "")
                if not folder.is_dir() or folder.is_symlink() or folder.resolve().parent != RESULTS.resolve():
                    raise ValueError("V082_RESULT_PATH_MISSING")
                info = _read(folder / "run.json")
                if info.get("phase") != "completed": raise ValueError("V082_RESULT_NOT_COMPLETED")
                state["runDir"] = str(folder)
                if stage == "smoke" and not info.get("completedTrainGenerations"):
                    raise ValueError("V082_SMOKE_NO_VALID_GENERATIONS: inspect windows/excluded diagnostics")
        state.update(phase="completed", endedAt=_now(), exitCode=0)
    except BaseException as exc:
        state.update(phase="failed", endedAt=_now(), exitCode=1,
                     error=f"{type(exc).__name__}: {exc}"[:250])
        print(state["error"], flush=True)
    _write(statefile, state)


def _prior_zero_diagnosis():
    # Read-only old V08 metadata; never alter its failed/completed state or data.
    try:
        import v08_web_jobs as prior
        from collections import Counter
        job=prior._job()
        if not job:return {"available":False,"reason":"NO_V08_JOB"}
        state=prior._state(job,"smoke");folder=Path(state.get("runDir") or "")
        if folder.is_symlink() or folder.resolve().parent!=prior.RESULTS.resolve():
            return {"available":False,"reason":"NO_VERIFIED_V08_SMOKE_PATH"}
        info=_read(folder/"run.json")
        if info.get("completedTrainGenerations")!=0:
            return {"available":False,"reason":"PRIOR_SMOKE_NOT_ZERO_GENERATION"}
        counts=Counter();samples={}
        for name in ("windows.jsonl","excluded_sessions.jsonl"):
            path=folder/name
            if not path.is_file() or path.is_symlink():continue
            if path.stat().st_size>32*1024**2:return {"available":False,"reason":"DIAGNOSTIC_FILE_TOO_LARGE_USE_CLI"}
            with path.open() as f:
                for line in f:
                    row=json.loads(line);reason=row.get("reason")
                    if not reason:continue
                    key=name+":"+reason;counts[key]+=1
                    entries=samples.setdefault(key,[])
                    if len(entries)<5:entries.append({k:row.get(k) for k in ("date","dates","causedBy","discardedPriorSessions")})
        return dict(available=True,sourceRunId=info.get("runId"),completedGenerations=0,
            selectedSessions=info.get("selectedSessionCount"),counts=dict(counts),samples=samples,
            warning="Window counts and excluded-day counts have different denominators; absent logs are not inferred")
    except (OSError,ValueError,ImportError) as exc:
        return dict(available=False,reason="PRIOR_DIAGNOSIS_UNAVAILABLE:"+type(exc).__name__)


def _live_resources():
    from research.evolution.resource import sample_resources
    import psutil
    values=sample_resources()
    values["cpuPct"]=psutil.cpu_percent(interval=.1)
    return values


def report():
    _storage()
    job = _job()
    if not job:
        return {"protocol":PROTOCOL,"priorZeroGeneration":_prior_zero_diagnosis(),"resources":_live_resources(),"maxPopulation":MAX_POPULATION,"initialPopulation":50,"researchOnly": True, "finalOosGate": "CLOSED", "stages": {}, "runs": {},
                "jobId": None, "message": "V0.8.2 시험 학습 전"}
    states = {k: _state(job, k) for k in STAGES}
    runs, history = {}, []
    history_stage="train" if states["train"].get("runDir") or states["train"]["phase"] in ("starting","running") else "smoke"
    for k in ("smoke", "train", "holdout", "validation"):
        folder = Path(states[k].get("runDir") or "")
        if (not states[k].get("runDir") and states[k]["phase"] in ("running", "starting")
                and isinstance(states[k].get("childPid"), int)):
            # Match by runner PID: never display an unrelated command-line result.
            candidates = [p for p in RESULTS.iterdir() if p.is_dir() and not p.is_symlink()
                          and _read(p/"status.json").get("pid") == states[k]["childPid"]]
            if len(candidates) == 1:
                folder = candidates[0]
        if folder.is_dir() and not folder.is_symlink() and folder.resolve().parent == RESULTS.resolve():
            runs[k] = _read(folder / "status.json")
            # Bounded final session snapshot: historical evidence, never live quotes.
            attempts=sorted(folder.glob("attempt_*/metrics.json"))
            if attempts:
                diagnostic=_read(attempts[-1]).get("diagnostics",{})
                runs[k]["indicatorAnalysis"]=diagnostic.get("latestAnalysis",{})
                runs[k]["indicatorDecisionSamples"]=diagnostic.get("decisionSamples",[])
            if k == history_stage:
                for f in sorted(folder.glob("window_*.json"))[-100:]:
                    w = _read(f)
                    if len(w.get("dates", [])) != 5:
                        continue
                    values = w.get("bots", [])
                    sel = w.get("selection", {})
                    history.append({"generation": len(history)+1, "dates": w["dates"],
                        "meanReturn": sum(e["windowReturn"] for e in values)/len(values) if values else None,
                        "meanMdd": sum(e["windowMaxDrawdown"] for e in values)/len(values) if values else None,
                        "meanFitness": sum(e["fitness"] for e in values)/len(values) if values else None,
                        "parents": sel.get("parentPoolSize"), "recovery": sel.get("randomRecovery"),
                        "preservationParents":sel.get("preservationParents"),"parentSelection":sel.get("parentSelection"),
                        "population":len(values),"profitableBotCount":sel.get("profitableBotCount"),"crossoverCount":sel.get("crossoverCount"),
                        "uniqueGenomes": sel.get("uniqueGenomes"), "offspringCount": sel.get("offspringCount"),
                        "eliteCount": sel.get("eliteCount"), "mutationCount": sel.get("mutationCount"),
                        "profitableBotRatio": sel.get("profitableBotRatio"),
                        "maxParentContribution": sel.get("maxParentContribution")})
    space = shutil.disk_usage(job)
    capacity_state=states["calibrate"]
    capacity_path=Path(capacity_state.get("capacityReport") or "")
    capacity = _read(capacity_path) if capacity_path.is_file() and capacity_path.resolve().parent.parent==job.resolve() else {}
    return {"protocol":PROTOCOL,"priorZeroGeneration":_prior_zero_diagnosis(),"maxPopulation":MAX_POPULATION,"initialPopulation":50,
            "resources":_live_resources(),"capacity":capacity,
            "researchOnly": True, "finalOosGate": "CLOSED", "jobId": job.name,
            "stages": states, "runs": runs, "generations": history,"historyStage":history_stage,
            "diskFreeBytes": space.free, "downloadAvailable":
                states["export"]["phase"] == "completed" and (job / "v082-results.zip").is_file()}


def download():
    _storage()
    job = _job()
    if not job or _state(job, "export")["phase"] != "completed":
        raise ValueError("V082_EXPORT_NOT_READY")
    p = job / "v082-results.zip"
    if p.is_symlink() or not p.is_file():
        raise ValueError("V082_UNSAFE_EXPORT")
    return p


def console(parts):
    if parts in (["v082"], ["v082", "help"]):
        return 0, "v082 smoke | calibrate | train | holdout | validation | export | status | log | report | retry <stage>. 2026 OOS CLOSED."
    if parts == ["v082", "new"]:
        return 0,start("smoke",new_job=True)
    if parts == ["v082", "report"]:
        return 0, json.dumps(report(), ensure_ascii=False)
    if parts == ["v082", "status"]:
        return 0, json.dumps(report(), ensure_ascii=False, indent=2)
    if parts == ["v082", "log"]:
        job = _job()
        return 0, legacy._tail(_last_log(job)) if job else "(V0.8.2 작업 없음)"
    if len(parts) == 2 and parts[1] in STAGES:
        return 0, start(parts[1])
    if len(parts) == 3 and parts[1] == "retry" and parts[2] in STAGES:
        return 0, start(parts[2], retry=True)
    raise ValueError("V082_COMMAND_NOT_ALLOWED")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker" or sys.argv[2] not in STAGES:
        sys.exit("WORKER_COMMAND_NOT_ALLOWED")
    _storage()
    job = _job()
    if not job or str(job) != sys.argv[3]:
        sys.exit("WORKER_JOB_INVALID")
    worker(sys.argv[2], job)
