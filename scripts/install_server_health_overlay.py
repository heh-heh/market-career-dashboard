#!/usr/bin/env python3
"""Guarded EC2 install of the authenticated server-health API (ONLY two files).

Never touches Tick/1m databases, the collector scheduler, Toss auth, .env,
Git working tree, other services, research data or the 2026 OOS gate.
Usage: sudo python3 <this file> --check | --apply
"""
import argparse
import ast
import datetime
import fcntl
import hashlib
import json
import os
import pwd
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path("/mnt/research/collection-deploy-tools-20261012")
TARGET = Path("/home/ubuntu/market-career-dashboard")
RELEASE = "ec4400fd25e034f3de9c0360c47db2fc19eaeb14"
BASELINES = ("27e18db643224ede828daa899f0ccc4c8d57277f",)
FILES = ("server.py", "server_health.py")
BACKUPS = Path("/mnt/research/server-health-overlays")
SERVICE = "market-career-dashboard.service"


class Blocked(RuntimeError):
    pass


def shell(*args, timeout=25):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       check=False, timeout=timeout)
    if p.returncode:
        raise Blocked("COMMAND_FAILED: " + args[0] + " " + args[1])
    return p.stdout


def git_blob(revision, name):
    return shell("git", "-c", "safe.directory=" + str(REPO),
                 "-C", str(REPO), "show", revision + ":" + name)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_path(p):
    if any(q.is_symlink() for q in (p, *p.parents)):
        raise Blocked("SYMLINK_BLOCKED: " + p.name)
    if p.exists() and not p.is_file():
        raise Blocked("NOT_REGULAR_FILE: " + p.name)


def plan():
    payload = {}
    result = []
    for name in FILES:
        dest = TARGET / name
        check_path(dest)
        source = git_blob(RELEASE, name)
        ast.parse(source.decode("utf8"), name)
        old = dest.read_bytes() if dest.exists() else None
        if old == source:
            action = "UNCHANGED"
        elif name == "server_health.py":
            if old is not None:
                raise Blocked("EXISTING_SERVER_HEALTH_MODIFIED")
            action = "CREATE"
        elif old is not None and any(old == git_blob(base, name) for base in BASELINES):
            action = "UPDATE"
        else:
            raise Blocked("SERVER_PY_NOT_KNOWN_BASELINE - operator changes preserved")
        payload[name] = source
        result.append({"file": name, "action": action,
                       "oldSha256": digest(old) if old is not None else None,
                       "newSha256": digest(source)})
    return payload, result


def safe_window():
    """Do not restart the API during a weekday New York cash market session."""
    now = datetime.datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0):
        raise Blocked("US_MARKET_SESSION - do not restart production API during market hours")


def atomic_write(dest, data, metadata):
    check_path(dest)
    fd, temp = tempfile.mkstemp(prefix="." + dest.name + "-health-", dir=dest.parent)
    try:
        os.fchown(fd, metadata.st_uid, metadata.st_gid)
        os.fchmod(fd, stat.S_IMODE(metadata.st_mode))
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, dest)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def active_api():
    p = subprocess.run(["systemctl", "is-active", SERVICE],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       timeout=10, check=False)
    return p.stdout.decode().strip() == "active"


def ready():
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=2) as response:
            return response.status == 200 and json.load(response).get("ok") is True
    except (OSError, ValueError):
        return False


def apply():
    if os.geteuid() != 0:
        raise Blocked("SUDO_REQUIRED")
    safe_window()
    if not active_api():
        raise Blocked("PRODUCTION_API_NOT_ACTIVE")
    payload, changes = plan()
    if all(x["action"] == "UNCHANGED" for x in changes):
        print(json.dumps({"phase": "ALREADY_APPLIED"}))
        return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUPS / (stamp + "-" + os.urandom(5).hex())
    backup.mkdir(mode=0o700, parents=True)
    os.chmod(backup, 0o700)
    for change in changes:
        src = TARGET / change["file"]
        if src.is_file():
            shutil.copy2(src, backup / change["file"])
    manifest = {"phase": "PREPARED", "release": RELEASE, "changes": changes}
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
    installed = []
    try:
        safe_window()
        for change in changes:
            dest = TARGET / change["file"]
            original = dest.read_bytes() if dest.exists() else None
            if (digest(original) if original is not None else None) != change["oldSha256"]:
                raise Blocked("FILE_CHANGED_SINCE_CHECK: " + change["file"])
        for change in changes:
            if change["action"] == "UNCHANGED":
                continue
            dest = TARGET / change["file"]
            metadata = dest.stat() if dest.exists() else (TARGET / "server.py").stat()
            installed.append(change["file"])
            manifest.update(phase="INSTALLING", installed=installed)
            (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
            atomic_write(dest, payload[change["file"]], metadata)
        shell("systemctl", "restart", SERVICE, timeout=40)
        for _ in range(16):
            if active_api() and ready():
                manifest["phase"] = "COMMITTED"
                (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
                print(json.dumps({"phase": "COMMITTED", "backup": str(backup),
                                  "api": "HEALTHY", "changes": installed}))
                return
            time.sleep(1)
        raise Blocked("API_HEALTH_CHECK_FAILED")
    except Exception:
        # Roll back only files whose hash still matches this release.
        for name in reversed(installed):
            dest = TARGET / name
            new_hash = digest(dest.read_bytes()) if dest.exists() else None
            if new_hash != digest(payload[name]):
                continue
            src = backup / name
            if src.is_file():
                atomic_write(dest, src.read_bytes(), src.stat())
            else:
                dest.unlink()
        manifest["phase"] = "ROLLED_BACK"
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
        try:
            shell("systemctl", "restart", SERVICE, timeout=40)
        except (Blocked, OSError, subprocess.TimeoutExpired):
            pass
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.check:
            _, changes = plan()
            print(json.dumps({"phase": "SAFE_TO_APPLY", "release": RELEASE,
                              "changes": changes}, indent=2))
        else:
            apply()
    except (Blocked, OSError, SyntaxError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"phase": "BLOCKED", "reason": str(exc)}))
        raise SystemExit(2)
