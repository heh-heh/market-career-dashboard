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
from contextlib import contextmanager
import fcntl
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
MANAGED = False
BASE_REV = "759238f"
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
    data = git("show", f"{branch_sha}:{MODULE}")
    compile(data.decode("utf-8"), MODULE, "exec")
    if f'REV = "{pinned}"'.encode() not in data:
        raise Blocked("PINNED_RESEARCH_REVISION_MISMATCH")
    return data, branch_sha



def merge_managed_admin(local_bytes, wanted_bytes, base_bytes):
    """Strict V0.7-only overlay. Preserve every unrelated EC2 admin customization.

    Deliberately do not 3-way merge broad overlapping JS/nav hunks. Only insert or
    upgrade exact known V0.7 blocks, checked against the original release.
    Any unknown modification to a V0.7-owned block remains a hard conflict.
    """
    local = local_bytes.decode("utf-8")
    wanted = wanted_bytes.decode("utf-8")
    base = base_bytes.decode("utf-8")

    def slice_between(text, start, end):
        if text.count(start) != 1 or text.count(end) != 1:
            raise Blocked("MANAGED_ANCHOR_NOT_UNIQUE: " + start[:50])
        a = text.index(start)
        b = text.index(end, a)
        return text[a:b]

    card = slice_between(wanted, "    <!-- V07_MANAGED_CARD_BEGIN -->",
                         "    <!-- V07_MANAGED_CARD_END -->")
    card += "    <!-- V07_MANAGED_CARD_END -->\n"
    if "id=\"v07ManagedPanel\"" not in local:
        if local.count('id="aiBaselineSelectionSummary"') != 1:
            raise Blocked("AI_BASELINE_INSERTION_ANCHOR_MISSING")
        before = local.index('id="aiBaselineSelectionSummary"')
        pos = local.rfind('    <div class="card">', 0, before)
        if pos < 0 or before - pos > 1000:
            raise Blocked("AI_CARD_CONTAINER_AMBIGUOUS")
        local = local[:pos] + card + local[pos:]
    elif local.count('id="v07ManagedPanel"') != 1 or card not in local:
        raise Blocked("LOCAL_MANAGED_CARD_CONFLICT")

    # Existing web console card: preserve local layout if its fixed controls
    # are present. Otherwise insert the source-verified card, not arbitrary HTML.
    if 'id="v07WebPanel"' not in local:
        if local.count('<div id="consoleOut"') != 1:
            raise Blocked("CONSOLE_OUTPUT_ANCHOR_MISSING")
        web = slice_between(wanted,
                            '        <div class="card" id="v07WebPanel">',
                            '        <div id="consoleOut"')
        # EC2 admin.html may indent this known element differently from
        # GitHub. Locate the unique element by ID and preserve its entire line;
        # never depend on exactly eight leading spaces.
        console_pos = local.index('<div id="consoleOut"')
        pos = local.rfind("\n", 0, console_pos) + 1
        if local[pos:console_pos].strip() or "<section" not in local[:console_pos]:
            raise Blocked("CONSOLE_OUTPUT_ANCHOR_UNSAFE")
        local = local[:pos] + web + local[pos:]
    for identifier in ('v07WebPanel', 'v07WebStatus', 'v07WebLog', 'v07WebRefresh',
                       'v07ManagedRefresh', 'v07ManagedDownload', 'v07ManagedSummary',
                       'v07ManagedGraph', 'v07AuditDetail', 'v07ManagedAnalysis'):
        if local.count('id="' + identifier + '"') != 1:
            raise Blocked("V07_DOM_ANCHOR_INVALID: " + identifier)

    for old, updated in (
        (" if(name==='ai')refreshAI();",
         " if(name==='ai'){refreshAI();refreshV07Web();}"),
        (" if(name==='console')refreshGit();",
         " if(name==='console'){refreshGit();refreshV07Web();}")
    ):
        if updated not in local:
            if local.count(old) != 1:
                raise Blocked("LOCAL_NAVIGATION_CONFLICT: " + old.strip())
            local = local.replace(old, updated, 1)

    js_start = "let v07WebPending=false;"
    js_end = "function appendConsole(v)"
    remote_js = slice_between(wanted, js_start, js_end)
    old_js = slice_between(base, js_start, js_end)
    if js_start in local:
        installed = slice_between(local, js_start, js_end)
        if installed == remote_js:
            pass
        elif installed == old_js:
            local = local.replace(installed, remote_js, 1)
        else:
            raise Blocked("LOCAL_V07_JS_CONFLICT: preserve local JavaScript edits")
    else:
        if local.count(js_end) != 1:
            raise Blocked("LOCAL_CONSOLE_SCRIPT_ANCHOR_MISSING")
        local = local.replace(js_end, remote_js + js_end, 1)

    if local.count("function renderV07Managed(d)") != 1:
        raise Blocked("V07_MANAGED_UI_NOT_INSTALLED")
    return local.encode("utf-8")



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
        # Only the published predecessor may be upgraded automatically. Unknown
        # EC2 local edits remain a hard conflict, even with --managed.
        if existing != git("show", f"{BASE_REV}:{MODULE}"):
            raise Blocked("EXISTING_WEB_MODULE_DIFFERS - preserve local edits")
    files = {"server.py": (raw, new), MODULE: (existing, module)}
    if b"from v07_research_report import" in module:
        name = "v07_research_report.py"
        data = git("show", f"{branch_sha}:{name}")
        compile(data.decode(), name, "exec")
        dest = TARGET/name
        if dest.is_symlink() or dest.exists() and not dest.is_file():
            raise Blocked("REPORT_MODULE_UNSAFE")
        old = dest.read_bytes() if dest.exists() else None
        if old is not None and old != data:
            raise Blocked("REPORT_MODULE_LOCAL_EDITS")
        files[name] = (old, data)
    if MANAGED:
        # Three-way merge just our new server route and UI. Unknown overlapping
        # local customizations fail before any write; unrelated logic is retained.
        for name in ("server.py", "admin.html"):
            dest = TARGET/name
            check_regular(dest)
            original = dest.read_bytes()
            local = new if name == "server.py" else original
            base = git("show", f"{BASE_REV}:{name}")
            wanted = git("show", f"{branch_sha}:{name}")
            if name == "admin.html":
                merged_bytes = merge_managed_admin(local, wanted, base)
                import re
                js = "\n".join(re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", merged_bytes.decode(), re.S))
                test = subprocess.run(["node", "--check"], input=js.encode(), capture_output=True)
                if test.returncode:
                    raise Blocked("ADMIN_JAVASCRIPT_SYNTAX_ERROR")
            else:
                with tempfile.TemporaryDirectory() as temp:
                    paths = [Path(temp)/n for n in ("local", "base", "wanted")]
                    for path, value in zip(paths, (local, base, wanted)):
                        path.write_bytes(value)
                    merged = subprocess.run(["git", "merge-file", "-p", *map(str, paths)], capture_output=True)
                    if merged.returncode:
                        raise Blocked("LOCAL_MERGE_CONFLICT: " + name)
                    merged_bytes = merged.stdout
                ast.parse(merged_bytes.decode(), filename=name)
            files[name] = (original, merged_bytes)
    return files, branch_sha


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


@contextmanager
def research_idle():
    path = Path("/mnt/research/v07-web-jobs/research.lock")
    handle = None
    try:
        if path.parent.exists():
            if path.is_symlink() or path.parent.is_symlink():
                raise Blocked("UNSAFE_RESEARCH_LOCK")
            handle = path.open("a+")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Blocked("RESEARCH_CHILD_ACTIVE")
        yield
    finally:
        if handle is not None:
            handle.close()


def apply():
    with research_idle():
        _apply()


def _apply():
    if os.geteuid():
        raise Blocked("ROOT_REQUIRED")
    market_guard()
    if command("systemctl", "is-active", SERVICE).decode().strip() != "active" or not health():
        raise Blocked("API_NOT_HEALTHY_BEFORE_UPDATE")
    for proc in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            cmd = proc.read_bytes()
        except OSError:
            continue
        if b"v07_web_jobs.py\x00--worker\x00" in cmd:
            raise Blocked("V07_WORKER_ACTIVE: wait before overlay update")
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
            st = (TARGET/name).stat()
            os.chown(backup/name, st.st_uid, st.st_gid)
    manifest = {"phase": "PREPARED", "sourceHead": git_head,
                "changedFiles": changed, "oldHashes": {k:sha(v[0]) if v[0] is not None else None for k,v in parts.items()},
                "newHashes": {k:sha(v[1]) for k,v in parts.items()}}
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



def rollback(backup):
    """Explicit rollback of this install's files only; reject subsequent edits."""
    if os.geteuid():
        raise Blocked("ROOT_REQUIRED")
    market_guard()
    backup = Path(backup)
    if backup.is_symlink() or backup.parent.resolve() != BACKUP_ROOT.resolve():
        raise Blocked("UNSAFE_BACKUP_PATH")
    check_regular(backup/"manifest.json")
    manifest = json.loads((backup/"manifest.json").read_text())
    names = manifest.get("changedFiles", [])
    if not names or not set(names) <= {"server.py", "admin.html", MODULE, "v07_research_report.py"}:
        raise Blocked("INVALID_ROLLBACK_MANIFEST")
    with research_idle():
        for name in names:
            check_regular(TARGET/name)
            if sha((TARGET/name).read_bytes()) != manifest.get("newHashes", {}).get(name):
                raise Blocked("POST_INSTALL_EDITS: " + name)
            if manifest["oldHashes"].get(name) is not None:
                check_regular(backup/name)
                if sha((backup/name).read_bytes()) != manifest["oldHashes"][name]:
                    raise Blocked("BACKUP_HASH_MISMATCH")
        for name in reversed(names):
            if manifest["oldHashes"].get(name) is None:
                (TARGET/name).unlink()  # Only the newly installed application module.
            else:
                atomically(TARGET/name, (backup/name).read_bytes(), (backup/name).stat())
        command("systemctl", "restart", SERVICE, timeout=40)
        healthy = False
        for _ in range(18):
            if health():
                healthy = True; break
            time.sleep(1)
        manifest["phase"] = "ROLLED_BACK" if healthy else "ROLLBACK_HEALTH_FAILED"
        (backup/"manifest.json").write_text(json.dumps(manifest, indent=2))
        if not healthy:
            raise Blocked("ROLLBACK_HEALTH_FAILED")
        print(json.dumps(dict(phase="ROLLED_BACK", backup=str(backup))))


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--rollback", type=Path, help="Restore only hash-verified application backups")
    p.add_argument("--managed", action="store_true", help="Also merge the reviewed management UI and authenticated download route")
    a = p.parse_args()
    global MANAGED
    MANAGED = a.managed
    try:
        if a.check:
            parts, head = plan()
            print(json.dumps({"phase": "READY", "sourceHead": head,
                              "changes": [{"file":k,"mode":"UNCHANGED" if v[0]==v[1] else "CREATE" if v[0] is None else "PATCH",
                                           "beforeSha256":sha(v[0]) if v[0] is not None else None}
                                          for k,v in parts.items()]}))
        elif a.rollback:
            rollback(a.rollback)
        else:
            apply()
    except (Blocked, OSError, ValueError, SyntaxError, subprocess.TimeoutExpired) as e:
        print(json.dumps({"phase":"BLOCKED","reason":str(e)}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
