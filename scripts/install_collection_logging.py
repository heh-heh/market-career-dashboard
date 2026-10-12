#!/usr/bin/env python3
"""Surgical deployment of collection-activity logging onto a previously overlaid EC2.

Only six explicitly named app files are replaced; no git reset/clean/merge,
systemd unit edits, credentials, data, trading/research files, or .env changes.
Apply requires a PAUSED scheduler and no collection/research workers.
"""
import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

RELEASE = "27e18db643224ede828daa899f0ccc4c8d57277f"
DEFAULT_SOURCE = Path("/mnt/research/collection-log-release-20261012")
DEFAULT_TARGET = Path("/home/ubuntu/market-career-dashboard")
BACKUPS = Path("/mnt/research/collection-log-overlays")
KNOWN = {
    "server.py": ("903fed1", "0237cb3"),
    "collection.html": ("0237cb3",),
    "research_collection_scheduler.py": ("903fed1", "0237cb3"),
    "scripts/collect_research_1m.py": ("903fed1", "0237cb3"),
    "scripts/collect_toss_ticks.py": ("903fed1", "0237cb3"),
    "research/collection_activity.py": (),
}
API = "market-career-dashboard.service"
SCHEDULER = "research-collection-scheduler.service"
BROKER = "toss-token-broker.service"
WORKERS = frozenset({
    "collect_toss_ticks.py", "collect_research_1m.py",
    "collect_expanded_universe.py", "collect_toss_1m.py",
})
RESEARCH_PREFIXES = ("backtest_", "run_v0", "run_v4", "run_evolution",
                     "run_research_loop", "evaluate_population", "rebuild_v07",
                     "build_shortlist")
STATUS = "http://127.0.0.1:8080/api/research/collection50/status"
LOGS = "http://127.0.0.1:8080/api/research/collection50/logs"


class Blocked(RuntimeError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(source, *args):
    result = subprocess.run(["git", "-c", "safe.directory=" + str(source),
                             "-C", str(source), *args], capture_output=True, timeout=15)
    if result.returncode:
        raise Blocked("GIT_SOURCE_READ_FAILED")
    return result.stdout


def check_file(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise Blocked("SYMLINK_BLOCKED")
    if path.exists() and not path.is_file():
        raise Blocked("NOT_A_FILE")


def source_files(source):
    if git(source, "rev-parse", "HEAD").decode().strip() != RELEASE:
        raise Blocked("SOURCE_RELEASE_CHANGED")
    payload = {}
    for name in KNOWN:
        data = git(source, "show", RELEASE + ":" + name)
        if name.endswith(".py"):
            ast.parse(data.decode("utf-8"), filename=name)
        payload[name] = data
    return payload


def plan(source, target):
    payload = source_files(source)
    entries = []
    for name, current in payload.items():
        path = target / name
        check_file(path)
        local = path.read_bytes() if path.exists() else None
        if local == current:
            action = "UNCHANGED"
        elif local is None and not KNOWN[name]:
            action = "CREATE"
        elif local is None:
            raise Blocked("MISSING_EXISTING_FILE: " + name)
        elif not KNOWN[name]:
            raise Blocked("UNKNOWN_EXISTING_ACTIVITY_MODULE: " + name)
        elif any(local == git(source, "show", baseline + ":" + name)
                 for baseline in KNOWN[name]):
            action = "UPDATE"
        else:
            raise Blocked("LOCAL_CODE_DIFFERENT: " + name)
        entries.append({"path": name, "action": action,
                        "beforeSha256": sha(local) if local is not None else None,
                        "afterSha256": sha(current)})
    return payload, entries


def systemctl(*args):
    p = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=90)
    if p.returncode:
        raise Blocked("SYSTEMD_FAILED: " + " ".join(args) + " " + p.stderr.strip()[:160])
    return p.stdout.strip()


def json_url(url):
    with urllib.request.urlopen(url, timeout=4) as response:
        return json.load(response)


def check_idle():
    for unit in (API, SCHEDULER, BROKER):
        if systemctl("is-active", unit) != "active":
            raise Blocked("INACTIVE_SERVICE: " + unit)
    for unit in ("collect-toss-ticks.service", "collect-expanded-universe.service"):
        p = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True)
        if p.stdout.strip() == "active":
            raise Blocked("LEGACY_COLLECTOR_ACTIVE: " + unit)
    state = json_url(STATUS)
    s = state.get("scheduler", {})
    if not (state.get("ok") and state.get("finalOosGate") == "CLOSED"
            and s.get("mode") == "PAUSED" and s.get("automatic") is True
            and s.get("stale") is False and not s.get("collector")):
        raise Blocked("PAUSE_AND_WAIT_FOR_WORKER_EXIT_BEFORE_APPLY")
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal() or int(proc.name) == os.getpid():
            continue
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
        except (OSError, PermissionError):
            continue
        scripts = [Path(a.decode("utf8", errors="replace")).name for a in args
                   if a.endswith(b".py")]
        for name in scripts:
            if name in WORKERS:
                raise Blocked("COLLECTION_PROCESS_STILL_RUNNING pid=" + proc.name)
            if name.startswith(RESEARCH_PREFIXES):
                raise Blocked("RESEARCH_PROCESS_RUNNING pid=" + proc.name)


def write_atomic(path, data, metadata):
    check_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".logging-", dir=path.parent)
    try:
        os.fchmod(fd, stat.S_IMODE(metadata.st_mode))
        os.fchown(fd, metadata.st_uid, metadata.st_gid)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_manifest(directory, manifest):
    temp = directory / "manifest.tmp"
    temp.write_text(json.dumps(manifest, indent=2), encoding="utf8")
    os.replace(temp, directory / "manifest.json")


def apply(source, target):
    if os.geteuid() != 0:
        raise Blocked("SUDO_REQUIRED")
    payload, entries = plan(source, target)
    check_idle()
    if not any(row["action"] != "UNCHANGED" for row in entries):
        print(json.dumps({"phase": "ALREADY_APPLIED", "release": RELEASE}))
        return
    directory = BACKUPS / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                            + "-" + uuid.uuid4().hex[:12])
    directory.mkdir(mode=0o700, parents=True)
    os.chmod(directory, 0o700)
    backup = directory / "before"
    backup.mkdir(mode=0o700)
    manifest = {"phase": "PREPARED", "release": RELEASE,
                "target": str(target), "files": entries}
    for row in entries:
        path = target / row["path"]
        if path.exists():
            saved = backup / row["path"]
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved, follow_symlinks=False)
            os.chown(saved, path.stat().st_uid, path.stat().st_gid)
    save_manifest(directory, manifest)
    installed = []
    try:
        # Re-check all hashes immediately before first replacement.
        check_idle()
        for row in entries:
            path = target / row["path"]
            before = path.read_bytes() if path.exists() else None
            if (sha(before) if before is not None else None) != row["beforeSha256"]:
                raise Blocked("FILE_CHANGED_DURING_DEPLOY: " + row["path"])
        for row in entries:
            if row["action"] == "UNCHANGED":
                continue
            path = target / row["path"]
            metadata = path.stat() if path.exists() else (target / "server.py").stat()
            # Intent is persisted before replace so an interruption cannot
            # leave an unjournaled file replacement.
            installed.append(row["path"])
            manifest["phase"] = "INSTALLING"
            manifest["installed"] = installed[:]
            save_manifest(directory, manifest)
            write_atomic(path, payload[row["path"]], metadata)
        systemctl("restart", SCHEDULER)
        systemctl("restart", API)
        for _ in range(30):
            try:
                result = json_url(STATUS)
                log = json_url(LOGS)
                scheduler = result.get("scheduler", {})
                if (result.get("ok") and result.get("finalOosGate") == "CLOSED"
                        and scheduler.get("mode") == "PAUSED" and not scheduler.get("stale")
                        and not scheduler.get("collector") and log.get("ok")
                        and isinstance(log.get("events"), list)
                        and systemctl("is-active", BROKER) == "active"):
                    manifest["phase"] = "COMMITTED"
                    save_manifest(directory, manifest)
                    print(json.dumps({"phase": "COMMITTED", "transaction": str(directory),
                                      "logCount": len(log["events"]), "scheduler": "PAUSED",
                                      "finalOosGate": "CLOSED"}))
                    return
            except (OSError, ValueError, Blocked):
                pass
            time.sleep(1)
        raise Blocked("API_LOGS_VERIFICATION_TIMEOUT")
    except Exception:
        manifest["phase"] = "RECOVERY_REQUIRED"
        save_manifest(directory, manifest)
        # Preserve the backup; manual --rollback can restore only our files.
        print(json.dumps({"phase": "RECOVERY_REQUIRED", "transaction": str(directory)}),
              file=sys.stderr)
        raise


def rollback(directory, target):
    if os.geteuid() != 0:
        raise Blocked("SUDO_REQUIRED")
    check_file(directory / "manifest.json")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf8"))
    if manifest.get("release") != RELEASE or manifest.get("target") != str(target):
        raise Blocked("TRANSACTION_IDENTITY_MISMATCH")
    if manifest.get("phase") not in ("INSTALLING", "RECOVERY_REQUIRED", "COMMITTED"):
        raise Blocked("TRANSACTION_NOT_APPLIED")
    # Rollback also works when the new API cannot start: consult the on-disk
    # scheduler status and process list instead of requiring a healthy HTTP API.
    state_path = Path("/var/lib/market-career-dashboard/collection/status.json")
    try:
        state = json.loads(state_path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        raise Blocked("ROLLBACK_SCHEDULER_STATUS_UNAVAILABLE")
    if state.get("mode") != "PAUSED" or state.get("collector"):
        raise Blocked("ROLLBACK_REQUIRES_PAUSED_SCHEDULER")
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal() or int(proc.name) == os.getpid():
            continue
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
        except (OSError, PermissionError):
            continue
        scripts = [Path(a.decode("utf8", errors="replace")).name for a in args
                   if a.endswith(b".py")]
        if any(name in WORKERS or name.startswith(RESEARCH_PREFIXES) for name in scripts):
            raise Blocked("ROLLBACK_WORKER_STILL_RUNNING pid=" + proc.name)
    # Refuse to trample any edits made since installation.
    for row in manifest["files"]:
        if row["path"] not in manifest.get("installed", []):
            continue
        path = target / row["path"]
        check_file(path)
        if not path.is_file() or sha(path.read_bytes()) != row["afterSha256"]:
            raise Blocked("FILE_CHANGED_SINCE_INSTALL: " + row["path"])
    for row in reversed(manifest["files"]):
        if row["path"] not in manifest.get("installed", []):
            continue
        path = target / row["path"]
        saved = directory / "before" / row["path"]
        if saved.exists():
            write_atomic(path, saved.read_bytes(), saved.stat())
        else:
            path.unlink()
    systemctl("restart", SCHEDULER)
    systemctl("restart", API)
    manifest["phase"] = "ROLLED_BACK"
    save_manifest(directory, manifest)
    print(json.dumps({"phase": "ROLLED_BACK", "transaction": str(directory)}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    p.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--rollback", type=Path, metavar="TRANSACTION")
    a = p.parse_args()
    try:
        if a.check:
            _, entries = plan(a.source, a.target)
            print(json.dumps({"release": RELEASE, "safeFiles": entries,
                              "next": "PAUSE_COLLECTION_THEN_APPLY"}, indent=2))
        elif a.apply:
            apply(a.source, a.target)
        else:
            rollback(a.rollback, a.target)
    except (Blocked, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"phase": "BLOCKED", "reason": str(exc)}), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
