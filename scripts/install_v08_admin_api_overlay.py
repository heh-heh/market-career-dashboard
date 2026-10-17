#!/usr/bin/env python3
"""Safe opt-in EC2 overlay for V0.8 web API only. No V0.7 result writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

TOOLS=Path("/mnt/research/collection-deploy-tools-20261012")
APP=Path("/home/ubuntu/market-career-dashboard")
SOURCE=Path("/mnt/research/v08-source")
BACKUPS=Path("/mnt/research/v08-web-install-backups")
PIN="eed5a21d3bf7418daf7fc18f042eda43b586c0dc"
TARGET=APP/"server.py"
CONTROLLER=APP/"v08_web_jobs.py"

CONSOLE_ANCHOR='    if parts and parts[0]=="v07":\n        from v07_web_jobs import console as v07_console\n        return v07_console(parts)\n'
CONSOLE_ADD='    if parts and parts[0]=="v08":\n        from v08_web_jobs import console as v08_console\n        return v08_console(parts)\n'
DOWNLOAD_ANCHOR='        if path=="/api/research/v07/download":'
DOWNLOAD_ADD='''        if path=="/api/research/v08/download":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                from v08_web_jobs import download as v08_download
                self.send_export_file(v08_download(), "v08-research-results.zip")
            except (ValueError,OSError) as exc:
                self.send_json({"ok":False,"error":str(exc)},409)
            return
'''
def git(*args):
    return subprocess.check_output(["git","-c",f"safe.directory={TOOLS}","-C",str(TOOLS),*args],
                                   text=True,timeout=30).strip()

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def guard():
    if not TOOLS.is_dir() or TOOLS.is_symlink() or not TARGET.is_file():
        raise ValueError("DEPLOY_TOOLS_OR_SERVER_MISSING")
    if TOOLS.stat().st_dev == Path("/").stat().st_dev:
        raise ValueError("RESEARCH_EBS_NOT_MOUNTED")
    remote=git("remote","get-url","origin")
    if not re.search(r"github\.com[:/]heh-heh/market-career-dashboard(?:\.git)?$",remote):
        raise ValueError("UNEXPECTED_REPOSITORY")
    rev=git("rev-parse","origin/strategy-engine-v3")
    if not re.fullmatch(r"[0-9a-f]{40}",rev):
        raise ValueError("INVALID_SOURCE_REV")
    body=subprocess.check_output(["git","-c",f"safe.directory={TOOLS}","-C",str(TOOLS),
                                  "show",rev+":v08_web_jobs.py"],timeout=15)
    compile(body, "v08_web_jobs.py", "exec")
    if CONTROLLER.exists():
        if CONTROLLER.is_symlink() or CONTROLLER.read_bytes()!=body:
            raise ValueError("LOCAL_V08_CONTROLLER_CONFLICT")
    if SOURCE.exists():
        if SOURCE.is_symlink() or git_with_source("rev-parse","HEAD")!=PIN or git_with_source("status","--porcelain","--untracked-files=no"):
            raise ValueError("V08_PINNED_WORKTREE_CONFLICT")
    else:
        git("cat-file","-e",PIN+"^{commit}")
    raw=TARGET.read_bytes()
    if not raw or TARGET.is_symlink():
        raise ValueError("UNSAFE_SERVER_SOURCE")
    text=raw.decode("utf-8")
    if CONSOLE_ADD not in text:
        if text.count(CONSOLE_ANCHOR)!=1:raise ValueError("LOCAL_V08_CONSOLE_CONFLICT")
        text=text.replace(CONSOLE_ANCHOR,CONSOLE_ANCHOR+CONSOLE_ADD)
    if DOWNLOAD_ADD not in text:
        if text.count(DOWNLOAD_ANCHOR)!=1:raise ValueError("LOCAL_V08_DOWNLOAD_CONFLICT")
        text=text.replace(DOWNLOAD_ANCHOR,DOWNLOAD_ADD+DOWNLOAD_ANCHOR)
    compile(text,"server.py","exec")
    # KillMode review is required before a web-launched child is allowed to outlive API.
    mode=subprocess.check_output(["systemctl","show","market-career-dashboard.service",
        "--property=KillMode","--value"],text=True,timeout=10).strip()
    if mode!="process":raise ValueError("API_CGROUP_WOULD_KILL_RESEARCH: set reviewed KillMode=process first")
    return dict(serverBefore=sha(raw),serverAfter=sha(text.encode()),controllerSha=sha(body),
                branchCommit=rev,serverChanged=raw!=text.encode(),controllerNeeded=not CONTROLLER.exists(),
                sourceNeeded=not SOURCE.exists()),text.encode(),body

def git_with_source(*args):
    return subprocess.check_output(["git","-c",f"safe.directory={SOURCE}","-C",str(SOURCE),*args],text=True,timeout=15).strip()

def active_workers():
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():continue
        try:cmd=(proc/"cmdline").read_bytes()
        except OSError:continue
        if b"v07_web_jobs.py" in cmd or b"v08_web_jobs.py" in cmd:
            if b"--worker" in cmd:return True
    return False

def atomic_write(path,data,reference=None):
    fd,tmp=tempfile.mkstemp(prefix="."+path.name+".",dir=str(path.parent))
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        if reference is not None:
            st=reference.stat()
            os.chmod(tmp,st.st_mode & 0o777)
            os.chown(tmp,st.st_uid,st.st_gid)
        else:
            os.chmod(tmp,0o644)
        os.replace(tmp,path)
    finally:
        Path(tmp).unlink(missing_ok=True)

def health():
    for _ in range(10):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/api/health",timeout=2) as response:
                if response.status==200 and json.load(response).get("ok") is True:return True
        except (OSError,ValueError,TimeoutError):
            pass
        time.sleep(1)
    return False

def main():
    p=argparse.ArgumentParser()
    exclusive=p.add_mutually_exclusive_group(required=True)
    exclusive.add_argument("--check",action="store_true")
    exclusive.add_argument("--apply",action="store_true")
    a=p.parse_args()
    audit,server,controller=guard()
    print(json.dumps({"status":"READY" if any((audit["serverChanged"],audit["controllerNeeded"],audit["sourceNeeded"])) else "ALREADY_INSTALLED",**audit},indent=2))
    if a.check:return
    if active_workers():
        raise ValueError("RESEARCH_WORKER_RUNNING: refuse API restart")
    # Reverify before mutation; current service may contain local modifications.
    audit2,_,_=guard()
    if audit2!=audit:raise ValueError("SOURCE_CHANGED_DURING_PREFLIGHT")
    if audit["sourceNeeded"]:
        subprocess.run(["git","-c",f"safe.directory={TOOLS}","-C",str(TOOLS),"worktree","add","--detach",
                        str(SOURCE),PIN],check=True,timeout=90)
    if not audit["serverChanged"] and not audit["controllerNeeded"]:
        print("ALREADY_INSTALLED");return
    BACKUPS.mkdir(mode=0o700,parents=True,exist_ok=True)
    backup=BACKUPS/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+os.urandom(4).hex())
    backup.mkdir(mode=0o700)
    shutil.copy2(TARGET,backup/"server.py")
    if CONTROLLER.exists():shutil.copy2(CONTROLLER,backup/"v08_web_jobs.py")
    (backup/"manifest.json").write_text(json.dumps(audit,indent=2))
    changed=True
    try:
        atomic_write(CONTROLLER,controller,CONTROLLER if CONTROLLER.exists() else None)
        if audit["serverChanged"]:
            atomic_write(TARGET,server,TARGET)
        if sha(TARGET.read_bytes())!=audit["serverAfter"] or sha(CONTROLLER.read_bytes())!=audit["controllerSha"]:
            raise ValueError("POST_WRITE_HASH_MISMATCH")
        if active_workers():raise ValueError("WORKER_STARTED_DURING_INSTALL")
        subprocess.run(["systemctl","restart","market-career-dashboard.service"],check=True,timeout=30)
        if not health():raise ValueError("API_HEALTH_CHECK_FAILED")
    except BaseException:
        if changed:
            atomic_write(TARGET,(backup/"server.py").read_bytes(),backup/"server.py")
            if (backup/"v08_web_jobs.py").exists():
                atomic_write(CONTROLLER,(backup/"v08_web_jobs.py").read_bytes(),backup/"v08_web_jobs.py")
            else:
                CONTROLLER.unlink(missing_ok=True)
            subprocess.run(["systemctl","restart","market-career-dashboard.service"],check=False,timeout=30)
        raise
    print(json.dumps({"status":"COMMITTED","backup":str(backup),"serverSha256":audit["serverAfter"],
        "controllerSha256":audit["controllerSha"],"apiHealth":"PASS"}))

if __name__=="__main__":
    main()
