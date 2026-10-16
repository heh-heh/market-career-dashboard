"""Authenticated admin-console V0.7 research jobs; NO arbitrary shell execution.

A detached worker owns each fixed research stage. An HTTP/SSM disconnect never
terminates that worker. Only the fixed 2026-OOS-CLOSED revision is executed.
No live trading/collector/systemd commands are exposed.
"""
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
from datetime import datetime, timezone

TOOLS = Path("/mnt/research/collection-deploy-tools-20261012")
REVIEW = Path("/mnt/research/v07-availability-review")
ROOT = Path("/mnt/research/v07-web-jobs")
DATA = Path("/home/ubuntu/market-career-dashboard/data/toss_1m")
PYTHON = Path("/home/ubuntu/market-career-dashboard/.venv/bin/python")
REV = "60b0f43fc6cf2a3eafea823e7c2ef2dbcc811edc"
STAGES = ("prepare", "audit", "select", "smoke")
PREVIOUS = {"prepare": None, "audit": "prepare", "select": "audit", "smoke": "select"}
SCRIPT = {
    "audit": "research/evolution/audit_available_data.py",
    "select": "research/evolution/select_v07_windows.py",
    "smoke": "research/evolution/run_v07_available.py",
}


def _json(path):
    try:
        if not path.is_file() or path.is_symlink():
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
            if not all(token in raw for token in ("v07_web_jobs.py", "--worker", stage, str(job))):
                return {**s, "phase": "interrupted", "error": "WORKER_NOT_RUNNING"}
        elif s["phase"] == "starting":
            # Avoid a false negative during the brief Popen -> worker startup race.
            if time.time() - float(s.get("startedEpoch", 0)) > 60:
                return {**s, "phase": "interrupted", "error": "WORKER_START_TIMEOUT"}
    return s


def _latest_log(job):
    for stage in reversed(STAGES):
        if (job / (stage + ".json")).is_file():
            return job / (stage + ".log")
    return None


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


def _run_fixed(stage, job):
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
    if sha != REV or REVIEW.is_symlink():
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
    else:
        plan_path = job / "selection" / "selection_plan.json"
        plan = _json(plan_path)
        if not plan or plan.get("smokeRange") is None:
            raise ValueError("NO_TEN_CONSECUTIVE_TRAIN_SESSIONS")
        argv = [str(PYTHON), SCRIPT[stage], "--selection-plan", str(plan_path),
                "--smoke", "--population", "20", "--seed", "42",
                "--results-dir", str(job / "smoke"), "--require-ebs"]
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
           "MKL_NUM_THREADS": "1", "GIT_TERMINAL_PROMPT": "0"}
    if Path("/usr/bin/nice").is_file():
        argv = ["/usr/bin/nice", "-n", "10"] + argv
    result = subprocess.run(argv, cwd=str(REVIEW), env=env, timeout=14400, check=False)
    if result.returncode:
        raise ValueError(f"STAGE_EXIT_CODE_{result.returncode}")


def worker(stage, job):
    state_file = job / (stage + ".json")
    state = {"stage": stage, "phase": "running", "pid": os.getpid(),
             "startedAt": _now(), "startedEpoch": time.time()}
    _write(state_file, state)
    try:
        _run_fixed(stage, job)
        state.update(phase="completed", exitCode=0, endedAt=_now())
    except BaseException as exc:
        state.update(phase="failed", exitCode=1, endedAt=_now(),
                     error=f"{type(exc).__name__}: {str(exc)[:250]}")
        print(state["error"], flush=True)
    _write(state_file, state)


def start(stage):
    if stage not in STAGES:
        raise ValueError("V07_COMMAND_NOT_ALLOWED")
    _guard()
    lockfile = ROOT / "start.lock"
    with lockfile.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
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
            raise ValueError("V07_STAGE_ALREADY_STARTED: run status and log, do not overwrite")
        _write(job / (stage + ".json"), {"stage": stage, "phase": "starting",
                                         "startedEpoch": time.time()})
        logfile = job / (stage + ".log")
        try:
            with logfile.open("xb") as log:
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", stage, str(job)],
                                 cwd=str(job), stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True,
                                 close_fds=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        except (OSError, ValueError):
            (job / (stage + ".json")).unlink(missing_ok=True)
            raise
    return f"STARTED {stage}: {job}\n브라우저/SSM 종료 후에도 실행됩니다. v07 status / v07 log 확인."


def console(parts):
    if parts in (["v07"], ["v07", "help"]):
        return 0, "v07 prepare | audit | select | smoke | status | log\n" \
                  "순서대로 한 단계씩 실행. 새 작업만 생성; 원본/수집기/매매 변경 없음."
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
        worker(sys.argv[2], root)
    else:
        raise SystemExit("V07_WORKER_ONLY")
