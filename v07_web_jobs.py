"""Authenticated admin-console V0.7 research jobs; NO arbitrary shell execution.

A detached worker owns each fixed research stage. An HTTP/SSM disconnect never
terminates that worker. Only the fixed 2026-OOS-CLOSED revision is executed.
No live trading/collector/systemd commands are exposed.
"""
from __future__ import annotations

import fcntl
import hashlib
import zipfile
import uuid
from itertools import islice
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

TOOLS = Path("/mnt/research/collection-deploy-tools-20261012")
REVIEW = Path("/mnt/research/v07-availability-review")
ROOT = Path("/mnt/research/v07-web-jobs")
DATA = Path("/home/ubuntu/market-career-dashboard/data/toss_1m")
PYTHON = Path("/home/ubuntu/market-career-dashboard/.venv/bin/python")
MANAGER_VERSION = "V07_WEB_MANAGED_1"
REV = "60b0f43fc6cf2a3eafea823e7c2ef2dbcc811edc"
STAGES = ("prepare", "audit", "select", "smoke", "analyze", "train", "holdout", "validation", "export")
PREVIOUS = {"prepare": None, "audit": "prepare", "select": "audit", "smoke": "select",
            "analyze": "smoke", "train": "smoke", "holdout": "train", "validation": "holdout", "export": "train"}
SCRIPT = {
    "audit": "research/evolution/audit_available_data.py",
    "select": "research/evolution/select_v07_windows.py",
    **{s: "research/evolution/run_v07_available.py" for s in ("smoke", "train", "holdout", "validation")},
}


def _json(path):
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 32*1024**2:
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path, data):
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _guard():
    if not ROOT.parent.is_dir() or ROOT.parent.stat().st_dev == Path("/").stat().st_dev:
        raise ValueError("RESEARCH_EBS_MOUNT_REQUIRED")
    if ROOT.is_symlink():
        raise ValueError("UNSAFE_JOB_ROOT")
    ROOT.mkdir(mode=0o700, parents=False, exist_ok=True)
    os.chmod(ROOT, 0o700)


def _current():
    pointer = _json(ROOT / "current.json")
    name = pointer.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"job-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", name):
        return None
    job = ROOT / name
    if not job.is_dir() or job.is_symlink():
        return None
    return job


def _state(job, stage):
    s = _json(job / (stage + ".json"))
    if not s:
        return {"stage": stage, "phase": "not_started"}
    if s.get("phase") in {"starting", "running"}:
        pid = s.get("pid")
        if pid and isinstance(pid, int):
            try:
                raw = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
            except OSError:
                raw = ""
            identity = _process_identity(pid)
            if (not all(token in raw.split("\x00") for token in ("--worker", stage, str(job)))
                or "v07_web_jobs.py" not in raw
                or (s.get("processIdentity") and identity != s["processIdentity"])):
                return {**s, "phase": "interrupted", "error": "WORKER_NOT_RUNNING"}
        elif s["phase"] == "starting":
            # Avoid a false negative during the brief Popen -> worker startup race.
            if time.time() - float(s.get("startedEpoch", 0)) > 60:
                return {**s, "phase": "interrupted", "error": "WORKER_START_TIMEOUT"}
        elif s["phase"] == "running":
            return {**s, "phase": "interrupted", "error": "WORKER_IDENTITY_MISSING"}
    return s


def _latest_log(job):
    states = [_json(job/(s+".json")) for s in STAGES]
    latest = max(states, key=lambda x: x.get("startedEpoch", 0), default={})
    return job/latest.get("logFile", latest.get("stage", "prepare")+".log")


def _tail(path, lines=35, limit=12000):
    if not path or not path.is_file() or path.is_symlink():
        return "(로그 없음)"
    # Constant maximum memory use regardless of whole-file size.
    with path.open("rb") as fp:
        fp.seek(0, 2)
        fp.seek(max(0, fp.tell() - 45000))
        raw = fp.read(45000).decode("utf-8", "replace")
    return "\n".join(raw.splitlines()[-lines:])[-limit:]


def status():
    _guard()
    job = _current()
    if job is None:
        return "V0.7 연구 작업 없음. v07 prepare 실행 후 순서대로 진행하세요."
    parts = [f"V0.7 research job: {job.name}", "2026 Final OOS: CLOSED", f"data: {DATA}"]
    for stage in STAGES:
        s = _state(job, stage)
        parts.append(f"{stage:8} {s['phase']:<12} exit={s.get('exitCode', '-')} "
                     f"pid={s.get('pid','-')} {s.get('error','')}")
    if (job / "selection" / "selection_plan.json").is_file():
        plan = _json(job / "selection" / "selection_plan.json")
        parts.append(f"usable windows: {plan.get('usableNonoverlappingWindows','?')}; "
                     f"smoke range: {plan.get('smokeRange')}")
    parts.append(f"output: {job}")
    return "\n".join(parts)


def _process_identity(pid):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        return dict(startTicks=raw.rsplit(")", 1)[1].split()[19],
                    bootId=Path("/proc/sys/kernel/random/boot_id").read_text().strip())
    except (OSError, IndexError):
        return None


def _stage_root(job, stage):
    name = _json(job/(stage+".json")).get("resultsRoot", stage)
    root = job/name
    if root.is_symlink() or job.resolve() not in root.resolve().parents:
        raise ValueError("UNSAFE_STAGE_OUTPUT_ROOT")
    return root


def _completed_run(job, stage):
    candidates = []
    for path in _stage_root(job, stage).glob("*-available-*"):
        if path.is_symlink():
            raise ValueError("SYMLINK_RESEARCH_RUN")
        info = _json(path/"run.json")
        if info.get("phase") == "completed":
            candidates.append(path)
    if len(candidates) != 1:
        raise ValueError("EXACTLY_ONE_COMPLETED_RUN_REQUIRED: " + stage)
    return candidates[0]


def _train_source(job):
    if _state(job, "train")["phase"] != "completed":
        raise ValueError("COMPLETED_TRAINING_REQUIRED")
    source = _completed_run(job, "train")
    info = _json(source/"run.json")
    if (info.get("fromDate") != "2022-01-01" or info.get("toDate") != "2023-12-31"
        or not info.get("completedTrainGenerations") or not info.get("resultsValid")
        or not info.get("inputHashesVerified") or info.get("finalOosGate") != "CLOSED"):
        raise ValueError("VALID_COMPLETED_FULL_RANGE_TRAINING_REQUIRED")
    seal = _json(job/"frozen_training.json")
    for filename in ("run.json", "final_population.json"):
        if seal.get(filename) != _hash(source/filename):
            raise ValueError("FROZEN_TRAINING_HASH_MISMATCH")
    return source


def _hash(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024**2), b""):
            h.update(chunk)
    return h.hexdigest()


def _seal_training(job):
    source = _completed_run(job, "train")
    info = _json(source/"run.json")
    if (info.get("fromDate"), info.get("toDate")) != ("2022-01-01", "2023-12-31") or not info.get("inputHashesVerified") or not info.get("resultsValid") or not info.get("completedTrainGenerations") or info.get("finalOosGate") != "CLOSED":
        raise ValueError("FULL_RANGE_TRAINING_NOT_VERIFIED")
    seal = {name:_hash(source/name) for name in ("run.json", "final_population.json")}
    existing = _json(job/"frozen_training.json")
    if existing and existing != seal:
        raise ValueError("EXISTING_FROZEN_TRAINING_DIFFERS")
    if not existing:
        _write(job/"frozen_training.json", seal)


def _analyze(job):
    from v07_research_report import analyze
    reports = {}
    for stage in ("smoke", "train", "holdout", "validation"):
        if _state(job, stage)["phase"] == "completed":
            reports[stage] = analyze(_completed_run(job, stage), job/"audit")
    _write(job/"analysis.json", reports)  # Derived report only, never source artifacts.
    return reports


def _export(job):
    # Small allowlisted research artifacts only. No recursion into raw sources,
    # credentials, logs or arbitrary paths; ZIP64 streams one file at a time.
    _analyze(job)
    files = [job/"analysis.json"]
    if (job/"frozen_training.json").is_file():
        files.append(job/"frozen_training.json")
    names = {"run.json", "status.json", "windows.jsonl", "excluded_sessions.jsonl",
             "used_sessions.json", "evaluation_sessions.json", "generations.jsonl",
             "final_population.json", "last_evaluated_population.json", "lineage.jsonl",
             "validation_summary.json"}
    for stage in ("smoke", "train", "holdout", "validation"):
        if _state(job, stage)["phase"] != "completed":
            continue
        run = _completed_run(job, stage)
        files.extend(p for p in run.iterdir() if p.name in names or p.name.startswith("window_") and p.suffix == ".json")
        for attempt in run.glob("attempt_*"):
            if attempt.is_symlink():
                raise ValueError("SYMLINK_EXPORT_BLOCKED")
            files.extend(p for p in attempt.iterdir() if p.name in {"metrics.json", "trades.csv"})
    for p in files:
        if p.is_symlink() or not p.is_file() or job.resolve() not in p.resolve().parents:
            raise ValueError("UNSAFE_EXPORT_SOURCE")
    estimated = sum(p.stat().st_size for p in files)
    space = shutil.disk_usage(job)
    if space.free - estimated < max(10*1024**3, space.total*.1):
        raise ValueError("RESOURCE_GUARD_PAUSED: export disk reserve")
    out = job/"research-results.zip"
    tmp = job/("export-"+uuid.uuid4().hex+".tmp")
    manifest = []
    try:
        with zipfile.ZipFile(tmp, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            for p in files:
                sha = hashlib.sha256()
                with p.open("rb") as f, z.open(str(p.relative_to(job)), "w", force_zip64=True) as dest:
                    for block in iter(lambda: f.read(1024**2), b""):
                        sha.update(block); dest.write(block)
                manifest.append(dict(path=str(p.relative_to(job)), sha256=sha.hexdigest(), bytes=p.stat().st_size))
            z.writestr("export_manifest.json", json.dumps(dict(researchOnly=True, finalOosGate="CLOSED", files=manifest)))
        if out.exists():
            raise ValueError("EXPORT_ALREADY_EXISTS")
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)


def _run_fixed(stage, job):
    if stage == "analyze":
        _analyze(job); return
    if stage == "export":
        _export(job); return
    if stage == "prepare":
        if REVIEW.is_symlink():
            raise ValueError("UNSAFE_REVIEW_SYMLINK")
        if REVIEW.exists():
            if not (REVIEW / ".git").exists():
                raise ValueError("REVIEW_EXISTS_NOT_WORKTREE")
            git = ["git", "-c", f"safe.directory={REVIEW}", "-C", str(REVIEW)]
            sha = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
            if sha != REV:
                raise ValueError("REVIEW_SHA_MISMATCH: refusing to overwrite")
            return
        if not TOOLS.is_dir() or TOOLS.is_symlink():
            raise ValueError("TOOLS_REPO_NOT_FOUND")
        git = ["git", "-c", f"safe.directory={TOOLS}", "-C", str(TOOLS)]
        remote = subprocess.check_output(git + ["remote", "get-url", "origin"], text=True).strip()
        if not re.search(r"github\.com[:/]heh-heh/market-career-dashboard(?:\.git)?$", remote):
            raise ValueError("UNEXPECTED_GIT_ORIGIN")
        subprocess.run(git + ["fetch", "origin", "refs/heads/strategy-engine-v3"], check=True,
                       timeout=180)
        if subprocess.check_output(git + ["cat-file", "-t", REV], text=True).strip() != "commit":
            raise ValueError("PINNED_V07_COMMIT_NOT_FOUND")
        subprocess.run(git + ["worktree", "add", "--detach", str(REVIEW), REV], check=True, timeout=50)
        return

    git = ["git", "-c", f"safe.directory={REVIEW}", "-C", str(REVIEW)]
    sha = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
    if sha != REV or REVIEW.is_symlink() or subprocess.check_output(git + ["status", "--porcelain", "--untracked-files=no"], text=True).strip():
        raise ValueError("REVIEW_VERSION_NOT_PINNED")
    if not PYTHON.is_file() or not DATA.is_dir() or not (REVIEW / SCRIPT[stage]).is_file():
        raise ValueError("PYTHON_DATA_OR_RESEARCH_SCRIPT_MISSING")
    free = shutil.disk_usage(job)
    if free.free < max(10 * 1024**3, free.total * .1):
        raise ValueError("RESOURCE_GUARD_PAUSED: insufficient research disk free")

    if stage == "audit":
        argv = [str(PYTHON), SCRIPT[stage], "--data-dir", str(DATA),
                "--timestamp-kind", "end", "--from-date", "2022-01-01",
                "--to-date", "2025-12-31", "--out-dir", str(job / "audit"),
                "--require-ebs"]
    elif stage == "select":
        if not (job / "audit" / "audit.json").is_file():
            raise ValueError("AUDIT_ARTIFACT_MISSING")
        argv = [str(PYTHON), SCRIPT[stage], "--audit-dir", str(job / "audit"),
                "--out-dir", str(job / "selection"), "--require-ebs"]
    elif stage == "smoke":
        plan_path = job / "selection" / "selection_plan.json"
        plan = _json(plan_path)
        if not plan or plan.get("smokeRange") is None:
            raise ValueError("NO_TEN_CONSECUTIVE_TRAIN_SESSIONS")
        argv = [str(PYTHON), SCRIPT[stage], "--selection-plan", str(plan_path),
                "--smoke", "--population", "20", "--seed", "42",
                "--results-dir", str(_stage_root(job, stage)), "--require-ebs"]
    else:
        plan_path = job / "selection" / "selection_plan.json"
        if not plan_path.is_file():
            raise ValueError("SELECTION_PLAN_MISSING")
        first, last = {"train": ("2022-01-01", "2023-12-31"),
                       "holdout": ("2024-01-01", "2024-12-31"),
                       "validation": ("2025-01-01", "2025-12-31")}[stage]
        argv = [str(PYTHON), SCRIPT[stage], "--selection-plan", str(plan_path),
                "--from-date", first, "--to-date", last, "--population", "20",
                "--seed", "42", "--results-dir", str(_stage_root(job, stage)), "--require-ebs"]
        if stage != "train":
            argv += ["--source-run", str(_train_source(job))]
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
           "MKL_NUM_THREADS": "1", "GIT_TERMINAL_PROMPT": "0"}
    if Path("/usr/bin/nice").is_file():
        argv = ["/usr/bin/nice", "-n", "10"] + argv
    # Pass the inherited flock to the child: even a killed worker cannot allow
    # a second job while its research child continues. stdout is bounded per stage.
    lock_fd = int(os.environ.get("V07_JOB_LOCK_FD", "-1"))
    inherited = (lock_fd,) if lock_fd >= 0 else ()
    with subprocess.Popen(argv, cwd=str(REVIEW), env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, pass_fds=inherited) as child:
        state = _json(job/(stage+".json"))
        state.update(childPid=child.pid, childIdentity=_process_identity(child.pid))
        print(f"{_now()} {stage} launched child={child.pid}; progress: persisted status.json (15s web polling)", flush=True)
        _write(job/(stage+".json"), state)
        log_path = job/state.get("logFile", stage+".log")
        log = log_path.open("ab", buffering=0)
        try:
            for chunk in iter(lambda: child.stdout.read(8192), b""):
                if log.tell()+len(chunk) > 8*1024**2:
                    log.close()
                    os.replace(log_path, job/(log_path.name+".previous"))
                    log = log_path.open("ab", buffering=0)
                log.write(chunk)
        finally:
            log.close()
        code = child.wait()
    if code:
        raise ValueError(f"STAGE_EXIT_CODE_{code}")


def worker(stage, job):
    state_file = job / (stage + ".json")
    state = {**_json(state_file), "stage": stage, "phase": "running", "pid": os.getpid(),
             "startedAt": _now(), "startedEpoch": time.time(),
             "processIdentity": _process_identity(os.getpid()),
             "managerVersion": MANAGER_VERSION, "managerCodeSha256": _hash(Path(__file__))}
    _write(state_file, state)
    try:
        _run_fixed(stage, job)
        if stage == "train":
            _seal_training(job)
        state.update(phase="completed", exitCode=0, endedAt=_now())
    except BaseException as exc:
        state.update(phase="failed", exitCode=1, endedAt=_now(),
                     error=f"{type(exc).__name__}: {str(exc)[:250]}")
        print(state["error"], flush=True)
    _write(state_file, state)
    if state["phase"] == "completed" and stage in {"smoke", "train", "holdout", "validation"}:
        try:
            _analyze(job)
        except (OSError, ValueError, KeyError) as exc:
            state["analysisError"] = str(exc)[:250]
            _write(state_file, state)


def _check_service_lifetime():
    # A new session escapes a terminal, NOT a systemd control group. Fail closed
    # if the API restart would kill an already launched research child.
    try:
        group = Path("/proc/self/cgroup").read_text()
    except OSError:
        raise ValueError("CANNOT_VERIFY_SERVICE_LIFETIME")
    if "market-career-dashboard.service" in group:
        mode = subprocess.check_output(["systemctl", "show", "market-career-dashboard.service",
                                        "--property=KillMode", "--value"], text=True, timeout=5).strip()
        if mode != "process":
            raise ValueError("API_CGROUP_WOULD_KILL_RESEARCH: reviewed KillMode=process required before launch")


def start(stage, *, retry=False):
    if stage not in STAGES:
        raise ValueError("V07_COMMAND_NOT_ALLOWED")
    _guard()
    _check_service_lifetime()
    lockfile = ROOT / "start.lock"
    with lockfile.open("a+") as lock, (ROOT/"research.lock").open("a+") as running_lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            fcntl.flock(running_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            running_lock.close()
            raise ValueError("V07_BUSY: research flock held (worker or surviving child)")
        job = _current()
        if job:
            for name in STAGES:
                if _state(job, name)["phase"] in {"starting", "running"}:
                    raise ValueError(f"V07_BUSY: {name} already running")
        if stage == "prepare":
            name = datetime.now(timezone.utc).strftime("job-%Y%m%dT%H%M%SZ-") + os.urandom(6).hex()
            job = ROOT / name
            job.mkdir(mode=0o700)
            _write(ROOT / "current.json", {"name": name})
        elif job is None:
            raise ValueError("V07_PREPARE_REQUIRED")
        if stage != "prepare":
            previous = PREVIOUS[stage]
            if _state(job, previous).get("phase") != "completed":
                raise ValueError("V07_STAGE_ORDER: finish " + previous)
        if (job / (stage + ".json")).exists():
            old_phase = _state(job, stage)["phase"]
            if stage == "analyze" and old_phase == "completed":
                retry = True  # Refresh a derived report, preserving prior stage evidence.
            if not retry or old_phase not in ({"failed", "interrupted", "completed"} if stage == "analyze" else {"failed", "interrupted"}):
                raise ValueError("V07_STAGE_ALREADY_STARTED: run status and log, do not overwrite")
            if stage == "train" and (job/"frozen_training.json").exists():
                raise ValueError("SEALED_TRAINING_EXISTS: preserve it; use a new job instead")
            archive = job/"retries"/(stage+"-"+uuid.uuid4().hex)
            archive.mkdir(parents=True)
            # Copy state/log evidence only. Existing run directories never move.
            old = _json(job/(stage+".json"))
            for name in (stage+".json", old.get("logFile", stage+".log")):
                p = job/name
                if p.is_symlink():
                    raise ValueError("UNSAFE_RETRY_SOURCE")
                if p.exists():
                    shutil.copy2(p, archive/p.name)
        elif retry:
            raise ValueError("NO_FAILED_STAGE_TO_RETRY")
        logfile = job / (stage + ("-"+uuid.uuid4().hex if retry else "") + ".log")
        _write(job / (stage + ".json"), {"stage": stage, "phase": "starting",
                                         "logFile": logfile.name, "startedEpoch": time.time(),
                                         "resultsRoot": stage+("/retry-"+uuid.uuid4().hex if retry else "")})
        try:
            with logfile.open("xb") as log:
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", stage, str(job)],
                                 cwd=str(job), stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True,
                                 close_fds=True, pass_fds=(running_lock.fileno(),),
                                 env={**os.environ, "PYTHONUNBUFFERED": "1", "V07_JOB_LOCK_FD": str(running_lock.fileno())})
        except (OSError, ValueError) as exc:
            _write(job/(stage+".json"), dict(stage=stage, phase="failed", logFile=logfile.name,
                   error="WORKER_LAUNCH_FAILED: "+str(exc)[:200], endedAt=_now()))
            raise
    return f"STARTED {stage}: {job}\n브라우저/SSM 종료 후에도 실행됩니다. v07 status / v07 log 확인."


def snapshot():
    _guard()
    job = _current()
    if not job:
        return dict(researchOnly=True, finalOosGate="CLOSED", stages={}, message="작업 없음")
    stages = {s: _state(job, s) for s in STAGES}
    runs = {}
    for stage in ("smoke", "train", "holdout", "validation"):
        paths = sorted(_stage_root(job, stage).glob("*-available-*"))
        if len(paths) == 1 and not paths[0].is_symlink():
            runs[stage] = _json(paths[0]/"status.json")
            # Persisted state, never assume completion from a zero exit/PID alone.
            if stages[stage]["phase"] in {"failed", "interrupted"}:
                runs[stage]["workerPhase"] = stages[stage]["phase"]
    audit = _json(job/"audit"/"audit.json")
    plan = _json(job/"selection"/"selection_plan.json")
    from v07_research_report import rows, distribution
    graph = []
    train_paths = list(_stage_root(job, "train").glob("*-available-*"))
    if len(train_paths) == 1 and not train_paths[0].is_symlink():
        for p in sorted(train_paths[0].glob("window_*.json")):
            w = _json(p); bots = w.get("bots", [])
            graph.append(dict(generation=len(graph)+1, dates=w.get("dates"),
                returns=distribution([b.get("windowReturn") for b in bots]),
                fitness=distribution([b.get("fitness") for b in bots]),
                mdd=distribution([b.get("windowMaxDrawdown", b.get("maxDrawdown")) for b in bots]),
                selection=w.get("selection")))
    space = shutil.disk_usage(job)
    try:
        import psutil
        vm = psutil.virtual_memory(); swap = psutil.swap_memory()
        resources = dict(cpuLoad1m=os.getloadavg()[0], memoryPct=vm.percent, swapPct=swap.percent)
    except ImportError:
        resources = dict(status="RESOURCE_MONITOR_UNAVAILABLE")
    return dict(researchOnly=True, finalOosGate="CLOSED", jobId=job.name, stages=stages, runs=runs,
        analysis=_json(job/"analysis.json"), generations=graph,
        audit={"perSymbol": audit.get("perSymbol"), "fromDate": audit.get("fromDate"), "toDate": audit.get("toDate")},
        plan={k:plan.get(k) for k in ("usableNonoverlappingWindows", "interruptedPartialWindows", "excludedSessionCount", "smokeRange")},
        excludedSamples=list(islice((r for r in rows(job/"audit"/"sessions.jsonl") if not r.get("eligible")), 20)),
        resources=resources, diskFreeBytes=space.free, diskRequiredBytes=max(10*1024**3, int(space.total*.1)),
        downloadAvailable=stages["export"]["phase"] == "completed" and (job/"research-results.zip").is_file())


def download():
    _guard()
    job = _current()
    if not job or _state(job, "export")["phase"] != "completed":
        raise ValueError("EXPORT_NOT_READY")
    p = job/"research-results.zip"
    if p.is_symlink() or not p.is_file():
        raise ValueError("UNSAFE_EXPORT_PATH")
    return p


def console(parts):
    if parts in (["v07"], ["v07", "help"]):
        return 0, "v07 prepare | audit | select | smoke | analyze | train | holdout | validation | export | report | status | log\nv07 retry train|holdout|validation|analyze|export (failed/interrupted only; clean rerun, no checkpoint resume)\n" \
                  "순서대로 한 단계씩 실행. 새 작업만 생성; 원본/수집기/매매 변경 없음."
    if parts == ["v07", "report"]:
        return 0, json.dumps(snapshot(), ensure_ascii=False)
    if len(parts) == 3 and parts[:2] == ["v07", "retry"] and parts[2] in {"train", "holdout", "validation", "analyze", "export"}:
        return 0, start(parts[2], retry=True)
    if parts == ["v07", "status"]:
        return 0, status()
    if parts == ["v07", "log"]:
        _guard()
        return 0, _tail(_latest_log(_current())) if _current() else "(작업 없음)"
    if len(parts) == 2 and parts[1] in STAGES:
        return 0, start(parts[1])
    raise ValueError("V07_COMMAND_NOT_ALLOWED: use v07 help")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker" and sys.argv[2] in STAGES:
        root = Path(sys.argv[3])
        # Worker accepts only the active immutable job dir under fixed EBS root.
        if root.parent != ROOT or root != _current():
            raise SystemExit("INVALID_WORKER_JOB")
        _guard()
        if "V07_JOB_LOCK_FD" in os.environ:
            lockfd = int(os.environ["V07_JOB_LOCK_FD"])
            if os.fstat(lockfd).st_ino != (ROOT/"research.lock").stat().st_ino:
                raise SystemExit("INVALID_RESEARCH_LOCK")
            worker(sys.argv[2], root)
        else:
            with (ROOT/"research.lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.environ["V07_JOB_LOCK_FD"] = str(lock.fileno())
                worker(sys.argv[2], root)
    else:
        raise SystemExit("V07_WORKER_ONLY")
