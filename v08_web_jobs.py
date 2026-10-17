"""Authenticated V0.8 research job controller. No arbitrary shell or trading actions."""
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

ROOT = Path("/mnt/research/v08-web-jobs")
RESULTS = Path("/mnt/research/evolution-v08-profit-only")
APP = Path("/mnt/research/v08-source")
PYTHON = Path("/home/ubuntu/market-career-dashboard/.venv/bin/python")
RUNNER = APP / "research/evolution/run_v08_profit_only.py"
PINNED_V08_SHA = "eed5a21d3bf7418daf7fc18f042eda43b586c0dc"
STAGES = ("smoke", "train", "holdout", "validation", "export")
PRIOR = {"smoke": None, "train": "smoke", "holdout": "train",
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
    research = Path("/mnt/research")
    if not research.is_dir() or research.is_symlink() or research.stat().st_dev == Path("/").stat().st_dev:
        raise ValueError("RESEARCH_EBS_MOUNT_REQUIRED")
    if ROOT.is_symlink() or RESULTS.is_symlink():
        raise ValueError("UNSAFE_V08_STORAGE")
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
            if not all(x in tokens for x in ("--worker", stage, str(job))) or not any("v08_web_jobs.py" in x for x in tokens):
                return {**s, "phase": "interrupted", "error": "WORKER_NOT_RUNNING"}
            if s.get("identity") and legacy._process_identity(pid) != s["identity"]:
                return {**s, "phase": "interrupted", "error": "WORKER_IDENTITY_CHANGED"}
        elif time.time() - float(s.get("startedEpoch", 0)) > 60:
            return {**s, "phase": "interrupted", "error": "WORKER_START_TIMEOUT"}
    return s


def _plan():
    previous = legacy._current()
    if not previous or legacy._state(previous, "select")["phase"] != "completed":
        raise ValueError("V08_SELECTION_PLAN_REQUIRED: existing V07 audit/selection must be completed")
    p = previous / "selection" / "selection_plan.json"
    if p.is_symlink() or not p.is_file():
        raise ValueError("V08_SELECTION_PLAN_MISSING")
    return p


def _verified_run(job, stage):
    state = _state(job, stage)
    if state["phase"] != "completed":
        raise ValueError("V08_STAGE_ORDER: " + stage + " not completed")
    folder = Path(state.get("runDir") or "")
    if (not folder.is_dir() or folder.is_symlink() or
            folder.resolve().parent != RESULTS.resolve()):
        raise ValueError("V08_RUN_PATH_INVALID")
    result = _read(folder / "run.json")
    if result.get("phase") != "completed" or result.get("finalOosGate") != "CLOSED" or result.get("version") != "V08_PROFIT_ONLY_1":
        raise ValueError("V08_RUN_NOT_VERIFIED")
    if stage == "train":
        if not result.get("completedTrainGenerations") or not result.get("resultsValid") or not result.get("inputHashesVerified"):
            raise ValueError("V08_TRAIN_NOT_VALID")
        if not (folder / "final_population.json").is_file():
            raise ValueError("V08_FROZEN_POPULATION_MISSING")
    return folder


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
                raise ValueError("V08_BUSY_V07: another research worker is active")


def _verify_source():
    if APP.is_symlink() or not APP.is_dir():
        raise ValueError("V08_PINNED_SOURCE_MISSING")
    try:
        git = ["git", "-c", f"safe.directory={APP}", "-C", str(APP)]
        sha = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True, timeout=8).strip()
        dirty = subprocess.check_output(git + ["status", "--porcelain", "--untracked-files=no"], text=True, timeout=8).strip()
    except (OSError, subprocess.SubprocessError):
        raise ValueError("V08_SOURCE_NOT_VERIFIABLE")
    if sha != PINNED_V08_SHA or dirty:
        raise ValueError("V08_SOURCE_VERSION_NOT_PINNED")
    if not RUNNER.is_file():
        raise ValueError("V08_RUNNER_MISSING")


def start(stage, retry=False):
    if stage not in STAGES:
        raise ValueError("V08_COMMAND_NOT_ALLOWED")
    _storage()
    legacy._check_service_lifetime()
    _other_active()
    if not PYTHON.is_file():
        raise ValueError("V08_PYTHON_MISSING")
    if stage != "export":
        _verify_source()
    _plan()
    with (ROOT / "start.lock").open("a+") as start_lock, (ROOT / "research.lock").open("a+") as run_lock, (legacy.ROOT / "research.lock").open("a+") as shared_lock:
        fcntl.flock(start_lock, fcntl.LOCK_EX)
        try:
            fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(shared_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("V08_BUSY: V0.7/V0.8 research worker or child holds a lock")
        job = _job()
        if job and any(_state(job, st)["phase"] in ("starting", "running") for st in STAGES):
            raise ValueError("V08_BUSY: a stage is already running")
        if stage == "smoke" and job and _state(job, "smoke")["phase"] == "completed" and not retry:
            raise ValueError("V08_STAGE_ALREADY_STARTED: do not overwrite existing job")
        if job is None:
            if stage != "smoke" or retry:
                raise ValueError("V08_SMOKE_REQUIRED")
            name = datetime.now(timezone.utc).strftime("job-%Y%m%dT%H%M%SZ-") + os.urandom(6).hex()
            job = ROOT / name
            job.mkdir(mode=0o700)
            _write(ROOT / "current.json", {"name": name})
        previous = PRIOR[stage]
        if previous:
            if previous == "train":
                _verified_run(job, "train")
            elif _state(job, previous)["phase"] != "completed":
                raise ValueError("V08_STAGE_ORDER: complete " + previous)
        old = _state(job, stage)
        if (job / (stage + ".json")).exists():
            if not retry or old["phase"] not in ("failed", "interrupted"):
                raise ValueError("V08_STAGE_ALREADY_STARTED: existing results preserved")
            archive = job / "retries" / (stage + "-" + uuid.uuid4().hex)
            archive.mkdir(parents=True)
            for filename in (stage + ".json", old.get("logFile", "")):
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
                    continue
                source = job / filename
                if source.is_file() and not source.is_symlink():
                    shutil.copy2(source, archive / source.name)
        elif retry:
            raise ValueError("V08_NO_FAILED_STAGE")
        logfile = stage + ("-" + uuid.uuid4().hex if retry else "") + ".log"
        state = dict(stage=stage, phase="starting", startedEpoch=time.time(), logFile=logfile, finalOosGate="CLOSED")
        _write(job / (stage + ".json"), state)
        try:
            with (job / logfile).open("xb") as log:
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", stage, str(job)],
                    cwd=str(job), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    close_fds=True, start_new_session=True, pass_fds=(run_lock.fileno(), shared_lock.fileno()),
                    env={**os.environ, "PYTHONUNBUFFERED": "1", "V08_LOCK_FD": str(run_lock.fileno()),
                         "V08_SHARED_LOCK_FD": str(shared_lock.fileno())})
        except OSError as exc:
            _write(job / (stage + ".json"), {**state, "phase": "failed", "error": str(exc)[:200]})
            raise
    return "STARTED v08 " + stage + " — browser disconnect will not stop the research worker."


def _export(job):
    stages = ("smoke", "train", "holdout", "validation")
    dest = job / "v08-results.zip"
    if dest.exists() or dest.is_symlink():
        raise ValueError("V08_EXPORT_ALREADY_EXISTS")
    space = shutil.disk_usage(job)
    if space.free < max(10 * 1024**3, space.total * .1):
        raise ValueError("RESOURCE_GUARD_PAUSED")
    allowed = {"run.json", "status.json", "final_population.json", "last_evaluated_population.json",
               "validation_summary.json", "used_sessions.json", "evaluation_sessions.json",
               "generations.jsonl", "windows.jsonl", "lineage.jsonl"}
    rows = []
    for stage in stages:
        if _state(job, stage)["phase"] != "completed":
            continue
        folder = _verified_run(job, stage)
        rows += [(stage, p) for p in folder.iterdir()
                 if p.is_file() and not p.is_symlink() and
                 (p.name in allowed or re.fullmatch(r"window_[0-9]{4,}\.json", p.name))]
    total = sum(p.stat().st_size for _, p in rows)
    if space.free - total < max(10 * 1024**3, space.total * .1):
        raise ValueError("RESOURCE_GUARD_PAUSED: export reserve")
    tmp = job / ("export-" + uuid.uuid4().hex + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "x", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            for stage, path in rows:
                z.write(path, stage + "/" + path.name)
            z.writestr("manifest.json", json.dumps({"version": "V08", "researchOnly": True,
                "finalOosGate": "CLOSED", "files": [stage + "/" + p.name for stage, p in rows]}))
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
            plan = _plan()
            argv = [str(PYTHON), str(RUNNER), "--selection-plan", str(plan),
                    "--seed", "42", "--require-ebs", "--results-dir", str(RESULTS)]
            if stage == "smoke":
                argv += ["--smoke"]
            else:
                a, b = DATES[stage]
                argv += ["--from-date", a, "--to-date", b]
                if stage != "train":
                    argv += ["--source-run", str(_verified_run(job, "train"))]
            if Path("/usr/bin/nice").is_file():
                argv = ["/usr/bin/nice", "-n", "10"] + argv
            env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                   "MKL_NUM_THREADS": "1", "GIT_TERMINAL_PROMPT": "0"}
            inherited = int(os.environ.get("V08_LOCK_FD", "-1"))
            shared = int(os.environ.get("V08_SHARED_LOCK_FD", "-1"))
            last_dir = None
            log = (job / state["logFile"]).open("ab", buffering=0)
            try:
                with subprocess.Popen(argv, cwd=str(APP), env=env, stdin=subprocess.DEVNULL,
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
                            if isinstance(record, dict) and "resultsDir" in record:
                                last_dir = record["resultsDir"]
                        except (ValueError, UnicodeDecodeError):
                            pass
                    code = child.wait()
            finally:
                log.close()
            if code:
                raise ValueError("V08_RUN_EXIT_" + str(code))
            folder = Path(last_dir or "")
            if not folder.is_dir() or folder.is_symlink() or folder.resolve().parent != RESULTS.resolve():
                raise ValueError("V08_RESULT_PATH_MISSING")
            if _read(folder / "run.json").get("phase") != "completed":
                raise ValueError("V08_RESULT_NOT_COMPLETED")
            state["runDir"] = str(folder)
        state.update(phase="completed", endedAt=_now(), exitCode=0)
    except BaseException as exc:
        state.update(phase="failed", endedAt=_now(), exitCode=1,
                     error=f"{type(exc).__name__}: {exc}"[:250])
        print(state["error"], flush=True)
    _write(statefile, state)


def report():
    _storage()
    job = _job()
    if not job:
        return {"researchOnly": True, "finalOosGate": "CLOSED", "stages": {}, "runs": {},
                "jobId": None, "message": "V0.8 시험 학습 전"}
    states = {k: _state(job, k) for k in STAGES}
    runs, history = {}, []
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
            if k == "train":
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
                        "uniqueGenomes": sel.get("uniqueGenomes")})
    space = shutil.disk_usage(job)
    return {"researchOnly": True, "finalOosGate": "CLOSED", "jobId": job.name,
            "stages": states, "runs": runs, "generations": history,
            "diskFreeBytes": space.free, "downloadAvailable":
                states["export"]["phase"] == "completed" and (job / "v08-results.zip").is_file()}


def download():
    _storage()
    job = _job()
    if not job or _state(job, "export")["phase"] != "completed":
        raise ValueError("V08_EXPORT_NOT_READY")
    p = job / "v08-results.zip"
    if p.is_symlink() or not p.is_file():
        raise ValueError("V08_UNSAFE_EXPORT")
    return p


def console(parts):
    if parts in (["v08"], ["v08", "help"]):
        return 0, "v08 smoke | train | holdout | validation | export | status | log | report | retry <stage>. 2026 OOS CLOSED."
    if parts == ["v08", "report"]:
        return 0, json.dumps(report(), ensure_ascii=False)
    if parts == ["v08", "status"]:
        return 0, json.dumps(report(), ensure_ascii=False, indent=2)
    if parts == ["v08", "log"]:
        job = _job()
        return 0, legacy._tail(_last_log(job)) if job else "(V0.8 작업 없음)"
    if len(parts) == 2 and parts[1] in STAGES:
        return 0, start(parts[1])
    if len(parts) == 3 and parts[1] == "retry" and parts[2] in STAGES:
        return 0, start(parts[2], retry=True)
    raise ValueError("V08_COMMAND_NOT_ALLOWED")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker" or sys.argv[2] not in STAGES:
        sys.exit("WORKER_COMMAND_NOT_ALLOWED")
    _storage()
    job = _job()
    if not job or str(job) != sys.argv[3]:
        sys.exit("WORKER_JOB_INVALID")
    worker(sys.argv[2], job)
