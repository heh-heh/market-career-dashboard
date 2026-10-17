#!/usr/bin/env python3
"""Guarded, surgical EC2 installer for the admin V0.7 console.

Reads fixed research modules and SHA-verified V0.7 UI from a Git fetch checkout,
preserves unrelated server/admin customizations, backs up changed files and
restarts ONLY market-career-dashboard.service outside US market hours.
Never merges, resets, cleans, overwrites custom server logic, or touches the
collector, any research data, databases, .env, credentials or trade state.

sudo python3 /mnt/research/install_v07_web_console_overlay.py --check
sudo python3 /mnt/research/install_v07_web_console_overlay.py --apply
"""
from __future__ import annotations
import argparse
import re
from contextlib import contextmanager
import fcntl
import ast
from html.parser import HTMLParser
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
UI_MANIFEST = "scripts/v07_web_bundle_manifest.json"
LAST_VALIDATION = None
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



class HtmlParts(HTMLParser):
    """Locate actual elements, not ID-like text inside scripts/comments."""
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.lines = [0]
        self.lines += [m.end() for m in re.finditer('\n', text)]
        self.stack = []; self.elements = []; self.ids = {}; self.scripts = []
        self.feed(text); self.close()

    def absolute_pos(self):
        line, col = self.getpos()
        return self.lines[line-1]+col

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        node = dict(tag=tag, attrs=a, start=self.absolute_pos(), openEnd=self.absolute_pos()+len(self.get_starttag_text()),
                    closeStart=None, end=None, parent=self.stack[-1] if self.stack else None)
        self.elements.append(node)
        if a.get('id'):
            self.ids.setdefault(a['id'], []).append(node)
        if tag == 'script': self.scripts.append(node)
        if tag in self.VOID:
            node['end'] = node['openEnd']; node['closeStart'] = node['openEnd']
        else: self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            node=self.stack.pop(); node['end']=node['openEnd']; node['closeStart']=node['openEnd']

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i]['tag'] == tag:
                node=self.stack[i]; node['closeStart']=self.absolute_pos()
                node['end']=self.text.index('>', self.absolute_pos())+1
                del self.stack[i:]
                return

    def one(self, identifier, required=True):
        found=self.ids.get(identifier, [])
        if len(found)>1: raise Blocked('DUPLICATE_DOM_ID: '+identifier)
        if not found:
            if required: raise Blocked('DOM_ANCHOR_MISSING: '+identifier)
            return None
        if found[0]['end'] is None: raise Blocked('DOM_ELEMENT_UNCLOSED: '+identifier)
        return found[0]

    def raw(self, node):
        return self.text[node['start']:node['end']]


def runtime_block(text):
    begin='// V07_RUNTIME_BEGIN\n'; end='// V07_RUNTIME_END'
    if text.count(begin)!=1 or text.count(end)!=1:
        raise Blocked('SOURCE_RUNTIME_MARKER_INVALID')
    if text.index(end)<text.index(begin):raise Blocked('RUNTIME_MARKER_ORDER_INVALID')
    return text.split(begin,1)[1].split(end,1)[0]


def bundle_parts(text):
    doc=HtmlParts(text)
    return {'runtime':runtime_block(text),
            'webCard':doc.raw(doc.one('v07WebPanel')),
            'managedCard':doc.raw(doc.one('v07ManagedPanel'))}


def verify_bundle(wanted, manifest):
    if manifest.get('version')!=1 or manifest.get('validationContract')!='NODE_SYNTAX_AND_OFFLINE_SMOKE_IN_CI':
        raise Blocked('UI_BUNDLE_MANIFEST_INVALID')
    parts=bundle_parts(wanted)
    if set(manifest.get('parts', {}))!=set(parts):
        raise Blocked('UI_BUNDLE_MANIFEST_INVALID')
    for key, value in parts.items():
        if sha(value.encode())!=manifest['parts'][key]:
            raise Blocked('V07_BUNDLE_HASH_MISMATCH: '+key)
    if '</script' in parts['runtime'].lower():
        raise Blocked('RUNTIME_SCRIPT_BOUNDARY_INVALID')
    return parts


def card_signature(text):
    # Whitespace indentation is irrelevant; attribute/text changes are not.
    class Tokens(HTMLParser):
        def __init__(self): super().__init__(convert_charrefs=False); self.items=[]
        def handle_starttag(self,t,a): self.items.append(('start',t,sorted(a)))
        def handle_endtag(self,t): self.items.append(('end',t))
        def handle_startendtag(self,t,a): self.items.append(('empty',t,sorted(a)))
        def handle_data(self,d):
            d=re.sub(r'\s+',' ',d).strip()
            if d:self.items.append(('text',d))
        def handle_entityref(self,n):self.items.append(('entity',n))
        def handle_charref(self,n):self.items.append(('char',n))
        def handle_comment(self,d):self.items.append(('comment',d.strip()))
    parser=Tokens();parser.feed(text);return parser.items


def owned_runtime(text):
    """Return an exact owned runtime span or none; partial/unknown code blocks."""
    doc=HtmlParts(text)
    dedicated=doc.one('v07ManagedRuntime', required=False)
    if dedicated:
        if re.search(r'(?m)^let v07WebPending=false;', text[:dedicated['start']]+text[dedicated['end']:]):
            raise Blocked('DUPLICATE_V07_RUNTIME')
        if dedicated['tag']!='script' or dedicated['attrs']!={'id':'v07ManagedRuntime'}:
            raise Blocked('LOCAL_V07_SCRIPT_ELEMENT_CONFLICT')
        body=text[dedicated['openEnd']:dedicated['closeStart']]
        return dedicated['start'],dedicated['end'],body,'dedicated'
    if '// V07_RUNTIME_BEGIN' in text or '// V07_RUNTIME_END' in text:
        block=runtime_block(text)
        start=text.index('// V07_RUNTIME_BEGIN\n')
        end=text.index('// V07_RUNTIME_END',start)+len('// V07_RUNTIME_END')
        while end<len(text) and text[end].isspace():end+=1
        if not any(n['openEnd']<=start<end<=n['closeStart'] for n in doc.scripts if n['closeStart'] is not None):
            raise Blocked('LOCAL_V07_RUNTIME_OUTSIDE_SCRIPT')
        return start,end,block,'inline'
    # Published pre-marker releases are supported only with an approved hash.
    matches=list(re.finditer(r'(?m)^let v07WebPending=false;',text))
    if matches:
        if len(matches)!=1:raise Blocked('LOCAL_V07_JS_CONFLICT: duplicate runtime')
        a=matches[0].start()
        following=re.search(r'\bfunction\s+appendConsole\s*\(',text[a:])
        if not following:raise Blocked('LOCAL_V07_JS_CONFLICT: incomplete runtime')
        b=a+following.start()
        if not any(s['openEnd']<=a<b<=s['closeStart'] for s in doc.scripts if s['closeStart'] is not None):
            raise Blocked('LOCAL_V07_JS_CONFLICT: runtime outside script')
        return a,b,text[a:b],'inline'
    if re.search(r'\b(?:(?:let|var|const)\s+v07WebPending|function\s+(?:refreshV07Web|renderV07Managed)\s*\()', '\n'.join(
            text[s['openEnd']:s['closeStart']] for s in doc.scripts if s['closeStart'] is not None)):
        raise Blocked('LOCAL_V07_JS_CONFLICT: partial or unknown runtime')
    return None


def merge_managed_admin(local_bytes, wanted_bytes, base_bytes, manifest=None):
    """Install only approved cards/runtime; navigation and other scripts survive."""
    local=local_bytes.decode('utf-8');wanted=wanted_bytes.decode('utf-8');base=base_bytes.decode('utf-8')
    manifest=manifest or json.loads(git('show', f'{BRANCH}:{UI_MANIFEST}'))
    parts=verify_bundle(wanted,manifest)
    old_doc=HtmlParts(base)
    for identifier,key in (('v07ManagedPanel','managedCard'),('v07WebPanel','webCard')):
        doc=HtmlParts(local); existing=doc.one(identifier,required=False)
        if existing:
            approved=[parts[key]]
            old=old_doc.one(identifier,required=False)
            if old:approved.append(old_doc.raw(old))
            known_legacy=(manifest.get('legacyManagedCardHashes', []) if key=='managedCard' else [])
            if not any(card_signature(doc.raw(existing))==card_signature(a) for a in approved) and sha(doc.raw(existing).encode()) not in known_legacy:
                raise Blocked('LOCAL_V07_CARD_CONFLICT: '+identifier)
            # Preserve a semantically identical installed card byte-for-byte.
            if card_signature(doc.raw(existing))!=card_signature(parts[key]):
                local=local[:existing['start']]+parts[key]+local[existing['end']:]
        else:
            if key=='managedCard':
                section=doc.one('ai')
                if section['tag']!='section':raise Blocked('AI_SECTION_ANCHOR_INVALID')
                baseline=doc.one('aiBaselineSelectionSummary',required=False)
                container=baseline
                while container and 'card' not in container['attrs'].get('class','').split():
                    container=container['parent']
                pos=container['start'] if container and section['start']<container['start']<section['closeStart'] else section['closeStart']
            else:
                output=doc.one('consoleOut');pos=output['start']
                if output['tag']!='div':raise Blocked('CONSOLE_OUTPUT_ANCHOR_INVALID')
            line_start=local.rfind('\n',0,pos)+1
            if not local[line_start:pos].strip():pos=line_start
            local=local[:pos]+'\n'+parts[key]+'\n'+local[pos:]
    owned=owned_runtime(local)
    if owned:
        a,b,body,kind=owned
        approved=set(manifest.get('legacyRuntimeHashes', []))|{manifest['parts']['runtime']}
        if sha(body.encode()) not in approved:
            raise Blocked('LOCAL_V07_JS_CONFLICT: unapproved runtime hash')
        if body!=parts['runtime']:
            replacement=(f'<script id="v07ManagedRuntime">{parts["runtime"]}</script>' if kind=='dedicated'
                         else '// V07_RUNTIME_BEGIN\n'+parts['runtime']+'// V07_RUNTIME_END\n')
            local=local[:a]+replacement+local[b:]
    else:
        doc=HtmlParts(local)
        matches=list(re.finditer(r'\bfunction\s+appendConsole\s*\(',local))
        if len(matches)!=1:raise Blocked('LOCAL_CONSOLE_SCRIPT_ANCHOR_MISSING')
        scripts=[n for n in doc.scripts if n['closeStart'] is not None and n['openEnd']<=matches[0].start()<n['closeStart']]
        if len(scripts)!=1:raise Blocked('LOCAL_CONSOLE_SCRIPT_ANCHOR_MISSING')
        pos=scripts[0]['start']
        # Install definitions before existing startup/navigation executes, after
        # all target controls exist. Never edit or wrap the application's JS.
        if any(doc.one(i)['end']>pos for i in HtmlParts(parts['webCard']+parts['managedCard']).ids):
            raise Blocked('V07_RUNTIME_DOM_ORDER_UNSAFE')
        local=local[:pos]+'\n<script id="v07ManagedRuntime">'+parts['runtime']+'</script>\n'+local[pos:]
    doc=HtmlParts(local)
    expected=HtmlParts(parts['webCard']+parts['managedCard'])
    for identifier in expected.ids: doc.one(identifier)
    if len(re.findall(r'\bfunction\s+renderV07Managed\s*\(',local))!=1:
        raise Blocked('V07_MANAGED_UI_NOT_INSTALLED')
    return local.encode('utf-8')


def unrelated_scripts(text):
    owned=owned_runtime(text)
    if owned:
        a,b,_,kind=owned
        text=text[:a]+text[b:]
    doc=HtmlParts(text)
    return [(n['attrs'],text[n['openEnd']:n['closeStart']]) for n in doc.scripts if n['closeStart'] is not None]


def validate_admin(local_bytes, merged_bytes, wanted_bytes, manifest):
    # Required even when Node exists: it must not authorize arbitrary changes.
    verify_bundle(wanted_bytes.decode('utf-8'),manifest)
    owned=owned_runtime(merged_bytes.decode('utf-8'))
    if not owned or sha(owned[2].encode())!=manifest['parts']['runtime']:
        raise Blocked('V07_INSTALLED_RUNTIME_HASH_MISMATCH')
    if unrelated_scripts(local_bytes.decode('utf-8'))!=unrelated_scripts(merged_bytes.decode('utf-8')):
        raise Blocked('NON_V07_JAVASCRIPT_CHANGED')
    node=shutil.which('node')
    if not node:
        return 'VERIFIED_V07_SHA256_EXISTING_JS_PRESERVED'
    doc=HtmlParts(merged_bytes.decode('utf-8'))
    js='\n;\n'.join(doc.text[n['openEnd']:n['closeStart']] for n in doc.scripts
                      if n['closeStart'] is not None and not n['attrs'].get('src'))
    try: test=subprocess.run([node,'--check'],input=js.encode(),capture_output=True,timeout=30)
    except FileNotFoundError:raise Blocked('NODE_DISAPPEARED_DURING_CHECK')
    except OSError:raise Blocked('NODE_VALIDATOR_UNAVAILABLE')
    if test.returncode:raise Blocked('ADMIN_JAVASCRIPT_SYNTAX_ERROR')
    return 'NODE_SYNTAX_AND_V07_SHA256'

def plan():
    global LAST_VALIDATION
    LAST_VALIDATION = None
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
                manifest=json.loads(git("show", f"{branch_sha}:{UI_MANIFEST}"))
                merged_bytes = merge_managed_admin(local, wanted, base, manifest)
                LAST_VALIDATION = validate_admin(local, merged_bytes, wanted, manifest)
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
        print(json.dumps({"phase": "ALREADY_INSTALLED", "sourceHead": git_head, "javascriptValidation": LAST_VALIDATION}))
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
            if sha((backup/name).read_bytes()) != sha(before):
                raise Blocked("CHANGED_DURING_BACKUP: "+name)
    manifest = {"phase": "PREPARED", "sourceHead": git_head,
                "changedFiles": changed, "oldHashes": {k:sha(v[0]) if v[0] is not None else None for k,v in parts.items()},
                "newHashes": {k:sha(v[1]) for k,v in parts.items()}, "javascriptValidation": LAST_VALIDATION}
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
        for name, (_, expected) in parts.items():
            check_regular(TARGET/name)
            if sha((TARGET/name).read_bytes()) != sha(expected):
                raise Blocked("INSTALLED_HASH_MISMATCH: " + name)
        command("systemctl", "restart", SERVICE, timeout=40)
        for _ in range(18):
            if health():
                manifest["phase"] = "COMMITTED"
                (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
                print(json.dumps({"phase": "COMMITTED", "backup": str(backup),
                                  "changed": changed, "service": "healthy", "javascriptValidation": LAST_VALIDATION,
                                  "installedHashes":manifest["newHashes"]}))
                return
            time.sleep(1)
        raise Blocked("API_HEALTH_CHECK_FAILED")
    except BaseException:
        if not installed:
            manifest["phase"]="ABORTED_BEFORE_WRITES"
            (backup/"manifest.json").write_text(json.dumps(manifest, indent=2))
            raise
        conflicts=[]; wrote_any=False
        for name in reversed(installed):
            original, new = parts[name]
            dest = TARGET/name
            current = dest.read_bytes() if dest.is_file() and not dest.is_symlink() else None
            if current == original:
                continue  # failed before atomic replacement; nothing to undo
            if current != new:
                conflicts.append(name); continue  # preserve a concurrent local edit
            wrote_any=True
            try:
                if original is not None:
                    atomically(dest, (backup/name).read_bytes(), (backup/name).stat())
                else:
                    dest.unlink()  # newly installed module only, never research data
            except OSError:
                conflicts.append(name+":RESTORE_FAILED")
        manifest["phase"] = "RECOVERY_BLOCKED" if conflicts else "ROLLED_BACK"
        manifest["rollbackConflicts"] = conflicts
        if not conflicts and wrote_any:
            try:
                command("systemctl", "restart", SERVICE, timeout=40)
                if not health():manifest["phase"]="ROLLBACK_HEALTH_FAILED"
            except Exception:
                manifest["phase"]="ROLLBACK_HEALTH_FAILED"
        (backup/"manifest.json").write_text(json.dumps(manifest, indent=2))
        if manifest["phase"] != "ROLLED_BACK":
            raise Blocked(manifest["phase"] + (": "+",".join(conflicts) if conflicts else ""))
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
            print(json.dumps({"phase": "READY", "sourceHead": head, "javascriptValidation": LAST_VALIDATION,
                              "changes": [{"file":k,"mode":"UNCHANGED" if v[0]==v[1] else "CREATE" if v[0] is None else "PATCH",
                                           "beforeSha256":sha(v[0]) if v[0] is not None else None, "afterSha256":sha(v[1])}
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
