#!/usr/bin/env python3
"""Install read-only V082 bot roster without changing the pinned evolution runner.

The running campaign's source_revision.json and controller are never changed.
Only new admin console routing and the passive roster module are installed.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request

TOOLS = Path("/mnt/research/collection-deploy-tools-20261012")
APP = Path("/home/ubuntu/market-career-dashboard")
SERVER = APP/"server.py"
MODULE = APP/"v082_bot_roster.py"
PROOF = Path("/mnt/research/v082-bots-install-proof.json")
BACKUPS = Path("/mnt/research/v082-bots-install-backups")
ANCHOR = '    if parts and parts[0]=="v082":\n        from v082_web_jobs import console as v082_console\n        return v082_console(parts)\n'
ADD = '    if parts and parts[0]=="bots":\n        from v082_bot_roster import console as bots_console\n        return bots_console(parts)\n\n'
WORKERS = (b"v07_web_jobs.py",b"v08_web_jobs.py",b"v081_web_jobs.py",b"v082_web_jobs.py",
           b"run_v07_available.py",b"run_v081.py",b"run_v082.py",b"calibrate_v081.py",
           b"calibrate_v082.py",b"run_v08_profit_only.py")

def sha(data):
    return hashlib.sha256(data).hexdigest()

def git(*args):
    return subprocess.check_output(["git","-c",f"safe.directory={TOOLS}","-C",str(TOOLS),*args],
                                   text=True, timeout=30).strip()

def inspect():
    if any(x.is_symlink() for x in (TOOLS,SERVER,MODULE,PROOF,BACKUPS)):
        raise ValueError("UNSAFE_BOT_INSTALL_PATH")
    if not TOOLS.is_dir() or not SERVER.is_file() or TOOLS.stat().st_dev == Path("/").stat().st_dev:
        raise ValueError("RESEARCH_EBS_OR_SERVER_MISSING")
    if not re.search(r"github\.com[:/]heh-heh/market-career-dashboard(?:\.git)?$",git("remote","get-url","origin")):
        raise ValueError("UNEXPECTED_GITHUB_REPO")
    rev = git("rev-parse","origin/strategy-engine-v3")
    if not re.fullmatch(r"[0-9a-f]{40}",rev):
        raise ValueError("INVALID_BRANCH")
    body = subprocess.check_output(["git","-c",f"safe.directory={TOOLS}","-C",str(TOOLS),
                                    "show",rev+":v082_bot_roster.py"],timeout=15)
    compile(body, "v082_bot_roster.py", "exec")
    raw = SERVER.read_bytes()
    source = raw.decode("utf-8")
    if ADD not in source:
        if source.count(ANCHOR)!=1:
            raise ValueError("BOT_CONSOLE_ANCHOR_MISMATCH")
        source = source.replace(ANCHOR,ADD+ANCHOR)
    compile(source,"server.py","exec")
    old = MODULE.read_bytes() if MODULE.is_file() else None
    existing = json.loads(PROOF.read_text()) if PROOF.is_file() else {}
    if old is not None and old!=body and sha(old)!=existing.get("moduleSha"):
        raise ValueError("LOCALLY_MODIFIED_BOT_ROSTER")
    mode = subprocess.check_output(["systemctl","show","market-career-dashboard.service",
                                    "--property=KillMode","--value"],text=True,timeout=10).strip()
    if mode!="process":
        raise ValueError("API_LIFETIME_REQUIRES_KILLMODE_PROCESS")
    next_server=source.encode()
    audit=dict(commit=rev,serverBefore=sha(raw),serverAfter=sha(next_server),
               serverChanged=next_server!=raw,moduleBefore=sha(old) if old else None,
               moduleSha=sha(body),moduleChanged=old!=body,
               proofChanged=existing.get("commit")!=rev or existing.get("moduleSha")!=sha(body))
    return audit,next_server,body

def live_worker():
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():continue
        try: cmd=(proc/"cmdline").read_bytes()
        except OSError:continue
        if b"--worker" in cmd and any(w in cmd for w in WORKERS[:4]):return True
        if any(w in cmd for w in WORKERS[4:]):return True
    return False

def atomic(path,raw,reference=None):
    fd,tmp=tempfile.mkstemp(dir=str(path.parent),prefix="."+path.name+".")
    try:
        with os.fdopen(fd,"wb") as output:
            output.write(raw);output.flush();os.fsync(output.fileno())
        if reference:
            stat=reference.stat()
            os.chmod(tmp,stat.st_mode&0o777)
            os.chown(tmp,stat.st_uid,stat.st_gid)
        else:os.chmod(tmp,0o644)
        os.replace(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)

def healthy():
    for attempt in range(10):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/api/health",timeout=2) as resp:
                if resp.status==200 and json.load(resp).get("ok") is True:return True
        except (OSError,ValueError,TimeoutError):pass
        time.sleep(1)
    return False

def apply(audit,new_server,module):
    from zoneinfo import ZoneInfo
    local=datetime.now(ZoneInfo("America/New_York"))
    if local.weekday()<5 and (9,30)<=(local.hour,local.minute)<(16,0):
        raise ValueError("US_REGULAR_SESSION_DEPLOY_BLOCKED")
    if live_worker():
        raise ValueError("RESEARCH_WORKER_RUNNING")
    locks=[]
    try:
        for base in (Path("/mnt/research"),Path("/mnt/research-extra")):
            for suffix in ("v07","v08","v081","v082"):
                p=base/(suffix+"-web-jobs")/"research.lock"
                if p.is_symlink():raise ValueError("UNSAFE_RESEARCH_LOCK")
                if p.is_file():
                    stream=p.open("rb")
                    try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:
                        stream.close()
                        raise ValueError("RESEARCH_WORKER_RUNNING")
                    locks.append(stream)
        check,_,_=inspect()
        if check!=audit:raise ValueError("SOURCE_CHANGED_DURING_PREFLIGHT")
        if not any(audit[k] for k in ("serverChanged","moduleChanged","proofChanged")):
            print("ALREADY_INSTALLED");return
        BACKUPS.mkdir(parents=True,exist_ok=True,mode=0o700)
        backup=BACKUPS/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+os.urandom(4).hex())
        backup.mkdir(mode=0o700)
        shutil.copy2(SERVER,backup/"server.py")
        if MODULE.is_file():shutil.copy2(MODULE,backup/"v082_bot_roster.py")
        if PROOF.is_file():shutil.copy2(PROOF,backup/"proof.json")
        (backup/"audit.json").write_text(json.dumps(audit,indent=2))
        try:
            if audit["moduleChanged"]:atomic(MODULE,module,MODULE if MODULE.is_file() else None)
            if audit["serverChanged"]:atomic(SERVER,new_server,SERVER)
            if audit["proofChanged"]:
                atomic(PROOF,json.dumps({"commit":audit["commit"],"moduleSha":audit["moduleSha"]}).encode(),
                       PROOF if PROOF.is_file() else None)
            if sha(SERVER.read_bytes())!=audit["serverAfter"] or sha(MODULE.read_bytes())!=audit["moduleSha"]:
                raise ValueError("POST_WRITE_HASH_MISMATCH")
            if live_worker():raise ValueError("RESEARCH_STARTED_DURING_INSTALL")
            subprocess.run(["systemctl","restart","market-career-dashboard.service"],check=True,timeout=30)
            if not healthy():raise ValueError("API_HEALTH_FAILED")
        except BaseException:
            atomic(SERVER,(backup/"server.py").read_bytes(),backup/"server.py")
            if (backup/"v082_bot_roster.py").is_file():
                atomic(MODULE,(backup/"v082_bot_roster.py").read_bytes(),backup/"v082_bot_roster.py")
            else:MODULE.unlink(missing_ok=True)
            if (backup/"proof.json").is_file():
                atomic(PROOF,(backup/"proof.json").read_bytes(),backup/"proof.json")
            else:PROOF.unlink(missing_ok=True)
            subprocess.run(["systemctl","restart","market-career-dashboard.service"],check=False,timeout=30)
            raise
        print(json.dumps({"status":"COMMITTED","backup":str(backup),"apiHealth":"PASS",
                          "commit":audit["commit"],"moduleSha":audit["moduleSha"]}))
    finally:
        for lock in reversed(locks):
            lock.close()

def main():
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check",action="store_true")
    mode.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    audit,server,module=inspect()
    print(json.dumps({"status":"READY" if any(audit[x] for x in ("serverChanged","moduleChanged","proofChanged"))
                      else "ALREADY_INSTALLED",**audit},indent=2))
    if args.apply:apply(audit,server,module)

if __name__=="__main__":
    main()
