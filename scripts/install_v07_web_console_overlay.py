#!/usr/bin/env python3
"""Guarded, surgical EC2 installer for the admin V0.7 console.

Reads exactly one new Python module from the pre-existing Git fetch checkout,
inserts one allowlisted dispatch into existing server.py, backs up old files and
restarts ONLY market-career-dashboard.service outside US market hours.
Never merges, resets, cleans, overwrites custom server logic, or touches the
collector, any research data, databases, .env, credentials or trade state.

sudo python3 /mnt/research/install_v07_web_console_overlay.py --check
sudo python3 /mnt/research/install_v07_web_console_overlay.py --apply
"""
from __future__ import annotations
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo

TOOLS = Path("/mnt/research/collection-deploy-tools-20261012")
BRANCH = "origin/strategy-engine-v3"
TARGET = Path("/home/ubuntu/market-career-dashboard")
SERVICE = "market-career-dashboard.service"
BACKUP_ROOT = Path("/mnt/research/v07-web-install-backups")
MODULE = "v07_web_jobs.py"
ANCHOR = '    if parts==["pwd"]:\n'
INSERT = ('    if parts and parts[0]=="v07":\n'
          '        from v07_web_jobs import console as v07_console\n'
          '        return v07_console(parts)\n\n')


class Blocked(Exception):
    pass


def command(*args, timeout=45):
    p = subprocess.run(args, capture_output=True, check=False, timeout=timeout)
    if p.returncode:
        raise Blocked(f"COMMAND_FAILED: {args[0]} {args[1] if len(args)>1 else ''}")
    return p.stdout


def sha(b):
    return hashlib.sha256(b).hexdigest()


def git(*args):
    return command("git", "-c", f"safe.directory={TOOLS}", "-C", str(TOOLS), *args)


def check_regular(path):
    if path.is_symlink() or not path.is_file():
        raise Blocked("REGULAR_FILE_REQUIRED: " + path.name)
    if path.parent.is_symlink():
        raise Blocked("SYMLINK_PARENT_BLOCKED")


def payload():
    if not TOOLS.is_dir() or TOOLS.is_symlink():
        raise Blocked("TOOLS_GIT_WORKTREE_MISSING")
    # The installer is given the already-fetched branch. --apply will not fetch
    # arbitrary refs or change a live Git working tree.
    branch_sha = git("rev-parse", BRANCH).decode().strip()
    pinned = "60b0f43fc6cf2a3eafea823e7c2ef2dbcc811edc"
    command("git", "-c", f"safe.directory={TOOLS}", "-C", str(TOOLS),
            "merge-base", "--is-ancestor", pinned, branch_sha)
    data = git("show", f"{BRANCH}:{MODULE}")
    compile(data.decode("utf-8"), MODULE, "exec")
    if f'REV = "{pinned}"'.encode() not in data:
        raise Blocked("PINNED_RESEARCH_REVISION_MISMATCH")
    return data, branch_sha


def plan():
    root = TARGET / "server.py"
    check_regular(root)
    raw = root.read_bytes()
    before = raw.decode("utf-8")
    if "def run_admin_console(command):" not in before or "if parts and parts[0]==\"v07\":" in before and INSERT not in before:
        raise Blocked("SERVER_CONSOLE_ANCHOR_MISMATCH")
    if INSERT not in before:
        if before.count(ANCHOR) != 1:
            raise Blocked("SERVER_CONSOLE_ANCHOR_AMBIGUOUS")
        new = before.replace(ANCHOR, INSERT + ANCHOR, 1).encode()
    else:
        new = raw
    ast.parse(new.decode("utf-8"), filename="server.py")
    module, branch_sha = payload()
    target_module = TARGET / MODULE
    if target_module.is_symlink() or (target_module.exists() and not target_module.is_file()):
        raise Blocked("MODULE_PATH_UNSAFE")
    existing = target_module.read_bytes() if target_module.exists() else None
    if existing is not None and existing != module:
        raise Blocked("EXISTING_WEB_MODULE_DIFFERS - preserve local edits")
    return {
        "server.py": (raw, new),
        MODULE: (existing, module)
    }, branch_sha


def atomically(path, data, st):
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + "-v07-", dir=path.parent)
    try:
        os.fchown(fd, st.st_uid, st.st_gid)
        os.fchmod(fd, stat.S_IMODE(st.st_mode))
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def market_guard():
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() < 5 and (now.hour, now.minute) >= (9,30) and (now.hour, now.minute) < (16,0):
        raise Blocked("US_CASH_SESSION: API restart not allowed during US regular market")


def health():
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=2) as r:
            return r.status == 200 and json.load(r).get("ok") is True
    except (OSError, ValueError):
        return False


def apply():
    if os.geteuid():
        raise Blocked("ROOT_REQUIRED")
    market_guard()
    if command("systemctl", "is-active", SERVICE).decode().strip() != "active" or not health():
        raise Blocked("API_NOT_HEALTHY_BEFORE_UPDATE")
    parts, git_head = plan()
    changed = [key for key, (before, after) in parts.items() if before != after]
    if not changed:
        print(json.dumps({"phase": "ALREADY_INSTALLED", "sourceHead": git_head}))
        return
    if os.stat("/").st_dev == os.stat("/mnt/research").st_dev:
        raise Blocked("RESEARCH_MOUNT_REQUIRED")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_ROOT / (stamp + "-" + os.urandom(5).hex())
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup, 0o700)
    for name, (before, _) in parts.items():
        if before is not None:
            shutil.copy2(TARGET / name, backup / name)
    manifest = {"phase": "PREPARED", "sourceHead": git_head,
                "changedFiles": changed, "oldHashes": {k:sha(v[0]) if v[0] is not None else None for k,v in parts.items()}}
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
    installed = []
    try:
        market_guard()
        for name in changed:
            original, new = parts[name]
            dest = TARGET / name
            if (dest.read_bytes() if dest.exists() else None) != original:
                raise Blocked("CHANGED_SINCE_PREFLIGHT: " + name)
        for name in changed:
            original, new = parts[name]
            dest = TARGET / name
            st = dest.stat() if dest.exists() else (TARGET / "server.py").stat()
            installed.append(name)
            atomically(dest, new, st)
        command("systemctl", "restart", SERVICE, timeout=40)
        for _ in range(18):
            if health():
                manifest["phase"] = "COMMITTED"
                (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
                print(json.dumps({"phase": "COMMITTED", "backup": str(backup),
                                  "changed": changed, "service": "healthy"}))
                return
            time.sleep(1)
        raise Blocked("API_HEALTH_CHECK_FAILED")
    except BaseException:
        for name in reversed(installed):
            original, new = parts[name]
            dest = TARGET / name
            if dest.exists() and sha(dest.read_bytes()) == sha(new):
                if original is not None:
                    atomically(dest, (backup / name).read_bytes(), (backup / name).stat())
                else:
                    dest.unlink()
        manifest["phase"] = "ROLLED_BACK"
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
        try:
            command("systemctl", "restart", SERVICE, timeout=40)
        except Exception:
            pass
        raise


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = p.parse_args()
    try:
        if a.check:
            parts, head = plan()
            print(json.dumps({"phase": "READY", "sourceHead": head,
                              "changes": [{"file":k,"mode":"UNCHANGED" if v[0]==v[1] else "CREATE" if v[0] is None else "PATCH",
                                           "beforeSha256":sha(v[0]) if v[0] is not None else None}
                                          for k,v in parts.items()]}))
        else:
            apply()
    except (Blocked, OSError, ValueError, SyntaxError, subprocess.TimeoutExpired) as e:
        print(json.dumps({"phase":"BLOCKED","reason":str(e)}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
