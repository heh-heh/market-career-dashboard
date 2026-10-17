#!/usr/bin/env python3
"""Read-only Git analysis + journaled application overlay, never a Git reset/merge.

Broker/config/data are preserved. Only an explicit --apply starts maintenance.
All diagnostic bundles are private, outside both source and production trees.
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.request import urlopen
from urllib.error import HTTPError

COLLECTORS = ['research-collection-scheduler.service', 'collect-toss-ticks.service', 'collect-expanded-universe.service']
API = 'market-career-dashboard.service'
BROKER = 'toss-token-broker.service'
PROCESS_NAMES = {'research_collection_scheduler.py', 'collect_toss_ticks.py', 'collect_toss_1m.py', 'collect_research_1m.py', 'collect_expanded_universe.py'}
AUTH = {'toss_auth.py', 'toss_token_broker.py'}
PROTECTED = ('data/', 'research/results/', 'research/evolution/results/', '.git/', '.venv/', 'venv/')
TESTS = ['research.test_research_collection_scheduler', 'research.test_collection_deployment', 'research.test_collect_toss_ticks', 'research.test_toss_auth_v2']
STAGE_ONLY = ['data/dashboard.json','.github/workflows/pages.yml','research/fixtures/toss_asyncapi_1_2_2.json','portfolio/index.html']


class Blocked(RuntimeError): pass


def digest(data): return hashlib.sha256(data).hexdigest()
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()


def safe(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in [path, *path.parents]): raise Blocked('SYMLINK_BLOCKED: '+str(path))
    return path


def read(path):
    path = safe(path)
    if not path.exists(): return None
    if not path.is_file(): raise Blocked('REGULAR_FILE_REQUIRED: '+str(path))
    with path.open('rb') as f: return f.read()


def atomic(path, data, mode=0o600, uid=None, gid=None):
    path = safe(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name+'.deploy-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            if uid is not None: os.fchown(stream.fileno(), uid, gid)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)  # only our exclusive new temporary


def write_json(path, obj): atomic(path, json.dumps(obj, indent=2, ensure_ascii=True).encode())


@contextlib.contextmanager
def lock(path, worker_owner=None):
    path = safe(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Blocked('DEPLOY_OR_COLLECTOR_LOCKED') from None
        if worker_owner is not None:
            # Deployment runs as root, collection workers run as ubuntu.
            # Keep the same inode and lock, but make it writable by the worker.
            os.fchown(fd, *worker_owner)
            os.fchmod(fd, 0o660)
        yield
    finally: os.close(fd)


def git(root, *args, optional=False):
    cmd = ['git', '-c', 'safe.directory='+str(root), '-C', str(root), *args]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode and not optional: raise Blocked('GIT_READ_FAILED: '+args[0])
    return result


def blob(root, sha, path):
    r = git(root, 'show', sha+':'+path, optional=True)
    return r.stdout if r.returncode == 0 else None


def tracked(root, sha):
    names = git(root, 'ls-tree', '-rz', '--name-only', sha).stdout.split(b'\0')
    return [x.decode() for x in names if x]


def application(name):
    p = Path(name)
    if p.is_absolute() or '..' in p.parts: raise Blocked('INVALID_TRACKED_PATH')
    if name.startswith(PROTECTED) or any(x in name.lower() for x in ('secret', 'credentials', '.env', '.pem', '.key', 'token_cache', '.control_token')): return False
    if name in AUTH: return True  # analyzed, but never replaced
    if p.suffix in ('.py','.html','.css','.js','.cjs','.sh'):
        return len(p.parts) == 1 or p.parts[0] in ('scripts','research')
    return name == 'requirements.txt' or name == 'research/universe_v3.json' or (name.startswith('research/') and 'configs' in p.parts and p.suffix == '.json')


def attributes(path):
    if read(path) is None: return None
    st = path.stat()
    return {'mode':stat.S_IMODE(st.st_mode), 'uid':st.st_uid, 'gid':st.st_gid, 'mtimeNs':st.st_mtime_ns}


def hashes(data): return digest(data) if data is not None else None


def merge_bytes(local, base, new, directory):
    with tempfile.TemporaryDirectory(dir=directory) as d:
        paths=[Path(d)/x for x in ('local','base','new')]
        for p,b in zip(paths,(local,base,new)): p.write_bytes(b)
        r=subprocess.run(['git','merge-file','-p',*map(str,paths)],capture_output=True)
    return r.returncode,r.stdout


def change_context(local,base,new,directory):
    left=local!=base;right=new!=base
    context={'localChanged':left,'sourceChanged':right,'overlapConflict':False}
    if left and right and local!=new:
        context['overlapConflict'] = any(x is None for x in (local,base,new)) or merge_bytes(local,base,new,directory)[0]!=0
    return context


def scheduler_configuration(data,layout):
    # Preserve customization only when it still runs THIS release with the same
    # single worker lock. A custom unit pointing to an older checkout is unsafe.
    text=data.decode()
    values={}
    for line in text.splitlines():
        key,sep,value=line.strip().partition('=')
        if sep: values[key]=value
    expected={'User':'ubuntu','Group':'ubuntu','WorkingDirectory':str(layout.target),'EnvironmentFile':str(layout.env)}
    if any(values.get(k)!=v for k,v in expected.items()): return False
    command=values.get('ExecStart','').split()
    if len(command)!=2 or command[0]!=str(layout.target/'.venv/bin/python') or command[1] not in ('research_collection_scheduler.py',str(layout.target/'research_collection_scheduler.py')): return False
    required={'RESEARCH_COLLECTION_STATE_DIR':str(layout.state),'RESEARCH_COLLECTION_WORKER_LOCK':str(layout.state/'worker.lock'),
              'TOSS_RATE_LIMIT_FILE':str(layout.target/'data/.toss_api_rate.lock'),'TOSS_TOKEN_SOCKET':str(layout.socket)}
    env={}
    for line in text.splitlines():
        if line.startswith('Environment='):
            key,sep,value=line[len('Environment='):].partition('=')
            if sep: env[key]=value
    return all(env.get(k)==v for k,v in required.items())


class Layout:
    def __init__(self, target, systemd='/etc/systemd/system', state='/var/lib/market-career-dashboard/collection',
                 env='/etc/market-career-dashboard/collection.env', socket_path='/run/market-career-dashboard-toss/token.sock'):
        self.target, self.systemd, self.state, self.env, self.socket = map(safe, (target,systemd,state,env,socket_path))
    def json(self): return {k:str(v) for k,v in self.__dict__.items()}


class Host:
    """Narrow operations: known systemd units, local GETs and no-token socket probe."""
    def __init__(self, layout, api_url='http://127.0.0.1:8080'):
        from urllib.parse import urlsplit
        u = urlsplit(api_url)
        if u.scheme != 'http' or u.hostname not in ('127.0.0.1','localhost') or u.username or u.password or u.path not in ('','/'):
            raise Blocked('LOCAL_API_URL_REQUIRED')
        self.layout, self.api_url = layout, api_url.rstrip('/')
    def ctl(self, action, unit=None, required=True):
        if unit and unit not in COLLECTORS+[API,BROKER]: raise Blocked('UNIT_NOT_ALLOWED')
        r = subprocess.run(['systemctl',action]+([unit] if unit else []),capture_output=True,timeout=200)
        if required and r.returncode: raise Blocked('SYSTEMD_FAILED: '+action+' '+str(unit))
        return r.stdout.decode().strip(), r.returncode
    def snapshot(self):
        result = {}
        for unit in COLLECTORS+[API]:
            loaded = subprocess.run(['systemctl','show',unit,'--property=LoadState','--value'],capture_output=True,timeout=10).stdout.decode().strip()
            active, _ = self.ctl('is-active',unit,False); enabled, _ = self.ctl('is-enabled',unit,False)
            if loaded not in ('loaded','not-found'): raise Blocked('UNIT_LOAD_STATE_UNSAFE: '+unit)
            if enabled in ('masked','masked-runtime'): raise Blocked('MASKED_UNIT_REQUIRES_REVIEW: '+unit)
            if active not in ('active','inactive','failed','unknown',''): raise Blocked('UNIT_TRANSITION_IN_PROGRESS: '+unit)
            result[unit] = {'exists':loaded=='loaded','active':active=='active','enabled':enabled}
        if sum(result[u]['active'] for u in COLLECTORS) > 1: raise Blocked('PREEXISTING_COLLECTOR_OVERLAP')
        return result
    def broker(self):
        self.ctl('is-active',BROKER)
        if not stat.S_ISSOCK(self.layout.socket.stat().st_mode): raise Blocked('BROKER_SOCKET_REQUIRED')
        unit=subprocess.run(['systemctl','show',BROKER,'--property=ExecStart','--value'],capture_output=True,timeout=10).stdout.decode()
        worker_uid=pwd.getpwnam('ubuntu').pw_uid
        if str(self.layout.socket) not in unit or not re.search(r'--allow-uid(?:=|\s+)'+str(worker_uid)+r'(?:\s|;|\}|$)',unit):
            raise Blocked('BROKER_PEER_CONFIGURATION_REQUIRES_REVIEW')
        # Empty connection only: never request a token or initiate OAuth mint.
        code='import socket,sys,json; s=socket.socket(socket.AF_UNIX); s.settimeout(3); s.connect(sys.argv[1]); s.shutdown(socket.SHUT_WR); r=json.loads(s.recv(4096)); s.close(); assert r.get("ok") is False and "token" not in r'
        result = subprocess.run(['runuser','-u','ubuntu','--',str(self.layout.target/'.venv/bin/python'),'-c',code,str(self.layout.socket)],capture_output=True,timeout=5)
        if result.returncode: raise Blocked('BROKER_SOCKET_NOT_ACCESSIBLE_TO_WORKER')
    def no_workers(self, allow_scheduler=False):
        allowed_pid = None
        if allow_scheduler:
            result=subprocess.run(['systemctl','show',COLLECTORS[0],'--property=MainPID','--value'],capture_output=True,timeout=10)
            value=result.stdout.decode().strip()
            if not value.isdigit() or int(value)==0: raise Blocked('SCHEDULER_MAIN_PID_REQUIRED')
            allowed_pid=value
        for p in Path('/proc').iterdir():
            if not p.name.isdigit(): continue
            try: args=p.joinpath('cmdline').read_bytes().split(b'\0')
            except FileNotFoundError: continue
            except PermissionError: raise Blocked('PROCESS_SCAN_PERMISSION_DENIED') from None
            names={Path(a.decode(errors='ignore')).name for a in args}
            if p.name==allowed_pid and 'research_collection_scheduler.py' in names: continue
            if names & PROCESS_NAMES:
                raise Blocked('COLLECTOR_STILL_RUNNING pid='+p.name)
    def no_api(self):
        state,_=self.ctl('is-active',API,False)
        result=subprocess.run(['systemctl','show',API,'--property=MainPID','--value'],capture_output=True,timeout=10)
        if state=='active' or result.stdout.decode().strip() not in ('','0'): raise Blocked('API_NOT_STOPPED')
    def research_idle(self):
        for p in Path('/proc').iterdir():
            if not p.name.isdigit(): continue
            try: args=p.joinpath('cmdline').read_bytes().split(b'\0')
            except FileNotFoundError: continue
            except PermissionError: raise Blocked('PROCESS_SCAN_PERMISSION_DENIED') from None
            names=[Path(a.decode(errors='ignore')).name for a in args]
            if any(n.endswith('.py') and n.startswith(('backtest_','run_v0','run_v4','run_evolution','run_research_loop','evaluate_population','rebuild_v07','build_shortlist')) for n in names):
                raise Blocked('RESEARCH_RUNNING pid='+p.name)
    def system_identity(self): return 0,0
    def check_environment(self):
        expected={'RESEARCH_COLLECTION_STATE_DIR':str(self.layout.state),'RESEARCH_COLLECTION_WORKER_LOCK':str(self.layout.state/'worker.lock'),
                  'TOSS_TOKEN_SOCKET':str(self.layout.socket),'TOSS_RATE_LIMIT_FILE':str(self.layout.target/'data/.toss_api_rate.lock'),
                  'COLLECTION_50_DIR':'/mnt/research/collection-50-v1'}
        for path in (self.layout.target/'.env',self.layout.env):
            data=read(path)
            if data is None: continue
            for line in data.decode().splitlines():
                key,sep,value=line.strip().removeprefix('export ').partition('=')
                if sep and key in expected and value.strip().strip('"\'')!=expected[key]:
                    raise Blocked('EXISTING_ENVIRONMENT_REQUIRES_REVIEW: '+key)
    def app_service_matches(self):
        for key,expected in (('WorkingDirectory',str(self.layout.target)),('ExecStart',str(self.layout.target/'server.py'))):
            r=subprocess.run(['systemctl','show',API,'--property='+key,'--value'],capture_output=True,timeout=10)
            if r.returncode or expected not in r.stdout.decode(): raise Blocked('API_UNIT_TARGET_MISMATCH: '+key)
    def graceful_stop_settings(self, units):
        for unit in units:
            for prop,expected in (('SendSIGKILL','no'),('KillMode','process' if unit==API else 'control-group')):
                r=subprocess.run(['systemctl','show',unit,'--property='+prop,'--value'],capture_output=True,timeout=10)
                if r.returncode or r.stdout.decode().strip()!=expected: raise Blocked('GRACEFUL_STOP_OVERRIDE_NOT_EFFECTIVE: '+unit)
    def before_apply(self):
        if not os.path.ismount('/mnt/research'): raise Blocked('RESEARCH_MOUNT_REQUIRED')
        root = safe('/mnt/research/collection-50-v1')
        if not root.is_dir(): raise Blocked('EXISTING_RESEARCH_DIRECTORY_REQUIRED')
        user = pwd.getpwnam('ubuntu')
        code='import os,sys,re; from pathlib import Path; p=Path(sys.argv[1]); s=Path(sys.argv[2]); assert os.access(p,os.W_OK|os.X_OK); assert all(not f.is_symlink() and os.access(f,os.R_OK|os.W_OK) for f in p.iterdir() if re.fullmatch(r"[A-Z0-9]{1,8}\\.sqlite3(?:-wal|-shm)?",f.name)); assert all(os.access(f,os.R_OK) for f in p.iterdir() if re.fullmatch(r"_scheduler.*\\.json|_collection_progress\\.json|[A-Z0-9]{1,8}\\.scheduler-backup\\.json",f.name)); assert not s.exists() or os.access(s,os.W_OK|os.X_OK)'
        r=subprocess.run(['runuser','-u','ubuntu','--',str(self.layout.target/'.venv/bin/python'),'-c',code,str(root),str(self.layout.state)],capture_output=True,timeout=10)
        if r.returncode: raise Blocked('RESEARCH_STORAGE_NOT_ACCESSIBLE; ownership unchanged')
        return user.pw_uid,user.pw_gid
    def verify(self, expected_page):
        self.broker(); self.ctl('is-active',API); self.ctl('is-active',COLLECTORS[0]); self.no_workers(allow_scheduler=True)
        last='HEALTH_TIMEOUT'
        for _ in range(30):
            try:
                with urlopen(self.api_url+'/api/research/collection50/status',timeout=3) as r: status=json.load(r)
                with urlopen(self.api_url+'/api/research/collection50/page',timeout=3) as r: page=r.read()
                s=status.get('scheduler',{})
                if (status.get('ok') and status.get('finalOosGate')=='CLOSED' and s.get('mode')=='PAUSED'
                        and s.get('automatic') is True and not s.get('stale') and not s.get('collector') and digest(page)==expected_page):
                    for unit in COLLECTORS[1:]:
                        active,_=self.ctl('is-active',unit,False)
                        if active=='active': raise Blocked('LEGACY_COLLECTOR_ACTIVE')
                    return {'api':True,'pageSha256':digest(page),'scheduler':'PAUSED','broker':True,'finalOosGate':'CLOSED'}
                last='API_PAGE_OR_PAUSED_STATE_MISMATCH'
            except (OSError,ValueError): last='API_UNAVAILABLE'
            time.sleep(1)
        raise Blocked(last)
    def recovery_verify(self, active_before):
        self.broker();self.no_workers()
        if not active_before: return {'api':'RESTORED_INACTIVE','brokerTransport':True}
        for _ in range(15):
            self.ctl('is-active',API)
            try:
                with urlopen(self.api_url+'/api/backtest/engines',timeout=3) as r: code=r.status
            except HTTPError as exc: code=exc.code
            except OSError: time.sleep(1);continue
            if code in (200,401,403,404,503): return {'api':'RESPONDING','httpStatus':code,'brokerTransport':True}
            time.sleep(1)
        raise Blocked('PREVIOUS_API_RECOVERY_UNVERIFIED')
    def validate_stage(self, stage, log):
        python=self.layout.target/'.venv/bin/python'
        if not python.is_file(): raise Blocked('PRODUCTION_VENV_REQUIRED')
        # No install/upgrade of the production venv; missing dependencies block.
        deps='''from importlib.metadata import version
from pathlib import Path
from pip._vendor.packaging.requirements import Requirement
import sys
for line in Path(sys.argv[1]).read_text().splitlines():
 line=line.strip()
 if not line or line.startswith('#'): continue
 r=Requirement(line)
 if r.marker is None or r.marker.evaluate():
  assert not r.url and version(r.name) in r.specifier
import exchange_calendars,numpy,psutil,websockets
'''
        if subprocess.run([str(python),'-c',deps,str(stage/'requirements.txt')],capture_output=True).returncode: raise Blocked('RUNTIME_DEPENDENCIES_MISSING_OR_INCOMPATIBLE')
        # Compatibility tests may execute locally customized auth code. Block
        # outbound traffic and production Unix sockets even if that code ignores
        # TOSS_TOKEN_SOCKET. Synthetic loopback servers/temp sockets remain usable.
        guard=b'''import socket, tempfile\nfrom pathlib import Path\noriginal=socket.socket.connect\ndef connect(s,a):\n if isinstance(a,(str,bytes)):\n  p=Path(a.decode() if isinstance(a,bytes) else a).resolve()\n  if not p.is_relative_to(Path(tempfile.gettempdir()).resolve()): raise PermissionError("PRODUCTION_SOCKET_BLOCKED_IN_TEST")\n elif a[0] not in ("127.0.0.1","::1","localhost") or a[1] in (80,443,8080): raise PermissionError("OUTBOUND_NETWORK_BLOCKED_IN_TEST")\n return original(s,a)\nsocket.socket.connect=connect\n'''
        with tempfile.TemporaryDirectory(prefix='collection-tests-') as d:
            sandbox=Path(d)/'guard';sandbox.mkdir();atomic(sandbox/'sitecustomize.py',guard)
            env={**os.environ,'TRADING_MODE':'paper','LIVE_TRADING_ENABLED':'false',
                 'TOSS_TOKEN_SOCKET':d+'/no-broker.sock','PAPER_V3_DIR':d+'/paper','SIMPLE_PAPER_DATA_DIR':d+'/simple',
                 'PERSISTENT_DATA_DIR':d,'BACKTEST_DATA_DIR':d+'/backtest','COLLECTION_50_DIR':d+'/data',
                 'COLLECTION_DOWNLOAD_DIR':d+'/downloads','RESEARCH_COLLECTION_STATE_DIR':d+'/scheduler',
                 'RESEARCH_COLLECTION_WORKER_LOCK':d+'/worker.lock','PYTHONPATH':str(sandbox)+os.pathsep+str(stage)}
            results=[]
            for args in (['-m','compileall','-q',str(stage)],['-m','unittest',*TESTS]):
                started=time.monotonic()
                result=subprocess.run([str(python),*args],cwd=stage,env=env,capture_output=True,timeout=180)
                count=re.findall(rb'Ran (\d+) tests',result.stderr)
                failed=re.findall(rb'^(?:ERROR|FAIL): (test_[A-Za-z0-9_]+)',result.stderr,re.M)
                kinds=re.findall(rb'^([A-Za-z][A-Za-z0-9_]*(?:Error|Exception)):',result.stderr,re.M)
                frames=re.findall(rb'File "[^"\n]*/([A-Za-z0-9_.]+\.py)", line (\d+)',result.stderr)
                results.append({'command':args[:2],'exitCode':result.returncode,'seconds':round(time.monotonic()-started,3),'tests':int(count[-1]) if count else None,'failingTests':[n.decode() for n in failed],'errorTypes':[k.decode() for k in kinds],'traceLocations':[{ 'file':f.decode(),'line':int(n)} for f,n in frames[-6:]]})
                write_json(log,{'results':results,'rawOutputPersisted':False})
                if result.returncode: raise Blocked('COMPOSITE_CODE_TESTS_FAILED; see sanitized test results')


class Deployment:
    def __init__(self, source, layout, backup_root, host=None):
        self.source,self.layout,self.backup_root= safe(source),layout,safe(backup_root)
        for root in (self.source,layout.target):
            if root==self.backup_root or root in self.backup_root.parents or self.backup_root in root.parents:
                raise Blocked('BACKUPS_MUST_BE_OUTSIDE_BOTH_CHECKOUTS')
        for root in map(Path,('/home/ubuntu/evolution-results','/home/ubuntu/v4-research','/mnt/research/collection-50-v1','/var/lib/market-career-dashboard')):
            if root==self.backup_root or root in self.backup_root.parents or self.backup_root in root.parents: raise Blocked('BACKUPS_INSIDE_PROTECTED_STORAGE')
        if self.source==layout.target: raise Blocked('SEPARATE_SOURCE_WORKTREE_REQUIRED')
        self.host=host or Host(layout)
    def analyze(self):
        target=self.layout.target
        if git(target,'branch','--show-current').stdout.strip()!=b'strategy-engine-v3': raise Blocked('TARGET_BRANCH_REQUIRED')
        for args in (('diff','--quiet'),('diff','--cached','--quiet')):
            if git(self.source,*args,optional=True).returncode: raise Blocked('SOURCE_TRACKED_EDITS_BLOCKED')
        sha=git(self.source,'rev-parse','HEAD').stdout.decode().strip()
        if git(self.source,'merge-base','--is-ancestor',sha,'origin/strategy-engine-v3',optional=True).returncode:
            raise Blocked('SOURCE_NOT_ON_RESEARCH_BRANCH_HISTORY')
        base=git(target,'rev-parse','HEAD').stdout.decode().strip()
        source_names=set(tracked(self.source,sha)); target_names=set(tracked(target,base))
        self.backup_root.mkdir(parents=True,exist_ok=True)
        disk=shutil.disk_usage(self.backup_root)
        if disk.free < max(10*1024**3,disk.total*.1)+64*1024**2: raise Blocked('DISK_RESERVE_REQUIRED')
        txn=self.backup_root/(dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex)
        txn.mkdir(mode=0o700); stage=txn/'stage';stage.mkdir(mode=0o700)
        records=[];blocked=[]
        for name in sorted(source_names|target_names):
            if not application(name): continue
            old=blob(target,base,name);new=blob(self.source,sha,name);local=read(target/name)
            row={'path':name,'baseSha256':hashes(old),'localSha256':hashes(local),'sourceSha256':hashes(new)}
            row.update(change_context(local,old,new,txn))
            data=new
            if name in AUTH:
                # Standalone broker may exist only under /opt on EC2. Preserve
                # its absence in production but stage the committed broker as
                # an isolated test fixture for integration tests that spawn it.
                row['action']='PRESERVE_AUTH'; data=local
                if local is None and name == 'toss_auth.py':
                    blocked.append(name+': MISSING_LOCAL_AUTH')
                if local is None and name == 'toss_token_broker.py' and new is not None:
                    row['stageOnly']=True
                    data=new
            elif new==local: row['action']='UNCHANGED'
            elif new==old: row['action']='PRESERVE_LOCAL'; data=local
            elif new is None:
                row['action']='BLOCKED_SOURCE_DELETION';blocked.append(name+': SOURCE_DELETION_REQUIRES_REVIEW');data=local
            elif old is None and local is not None:
                row['action']='BLOCKED_UNTRACKED_COLLISION';blocked.append(name+': UNTRACKED_APPLICATION_COLLISION');data=local
            elif local==old: row['action']='UPDATE'
            elif local is None:
                row['action']='BLOCKED_LOCAL_DELETION';blocked.append(name+': LOCAL_DELETION_CONFLICT')
            else:
                code,merged=merge_bytes(local,old,new,txn)
                if code:
                    row['action']='BLOCKED_CONFLICT';blocked.append(name+': LOCAL_REMOTE_CONFLICT');data=local
                else: row['action']='MERGED';data=merged
            row['deployedSha256']=hashes(local) if row.get('stageOnly') else hashes(data)
            records.append(row)
            if data is not None: atomic(stage/name,data,0o644)
        # Static test fixtures only, never deployed or read from production data.
        for name in STAGE_ONLY:
            fixture=blob(self.source,sha,name)
            if fixture is not None: atomic(stage/name,fixture)
        service=self.source/'ops/research-collection-scheduler.service'
        dropin=self.source/'ops/collection-worker-dropin/10-scheduler.conf'
        for p in (service,dropin):
            data=read(p)
            if data is None or data != blob(self.source,sha,str(p.relative_to(self.source))): raise Blocked('SOURCE_UNIT_NOT_COMMITTED')
            atomic(stage/p.relative_to(self.source),data)
        configurations=[]
        for name,template in [(COLLECTORS[0],'ops/research-collection-scheduler.service')]+[(u+'.d/10-scheduler.conf','ops/collection-worker-dropin/10-scheduler.conf') for u in COLLECTORS[1:]]:
            old=blob(target,base,template);new=read(stage/template)
            def render(data):
                if data is None: return None
                for previous,current in [('/home/ubuntu/market-career-dashboard',target),('/var/lib/market-career-dashboard/collection',self.layout.state),('/etc/market-career-dashboard/collection.env',self.layout.env),('/run/market-career-dashboard-toss/token.sock',self.layout.socket)]:
                    data=data.replace(previous.encode(),str(current).encode())
                return data
            old,new=render(old),render(new);local=read(self.layout.systemd/name)
            if local is None or local==old or local==new: data=new; action='UPDATE' if local!=new else 'UNCHANGED'
            elif new==old: data=local;action='PRESERVE_LOCAL'
            elif old is None: data=local;action='BLOCKED';blocked.append(name+': EXISTING_SYSTEMD_CONFIG_REQUIRES_REVIEW')
            else:
                code,data=merge_bytes(local,old,new,txn);action='MERGED'
                if code: data=local;action='BLOCKED';blocked.append(name+': SYSTEMD_LOCAL_REMOTE_CONFLICT')
            atomic(stage/'systemd'/name,data)
            configurations.append({'name':name,'action':action,'beforeSha256':hashes(local),'afterSha256':digest(data)})
            if name==COLLECTORS[0] and not scheduler_configuration(data,self.layout): blocked.append(name+': SCHEDULER_CONFIG_REQUIRES_REVIEW')
        plan={'version':1,'source':str(self.source),'sourceCommit':sha,'targetCommit':base,'layout':self.layout.json(),
              'createdAt':now(),'transaction':str(txn),'files':records,'blocked':blocked,
              'configurations':configurations,'environmentSha256':hashes(read(self.layout.env)),
              'operationalEnvironmentSha256':hashes(read(target/'.env')),
              'units':{str(p.relative_to(self.source)):digest(read(p)) for p in (service,dropin)},
              'preserved':['data/**','research/results/**','all untracked data/logs/backups','auth/Broker code/config/credentials','V0.7 artifacts','2026 Final OOS CLOSED']}
        # Hash/status metadata only; no credential values or code diff in report.
        plan['operationalLocalChanges']=[x for x in ('data/dashboard.json','toss_auth.py') if hashes(read(target/x))!=hashes(blob(target,base,x))]
        name='data/dashboard.json';local=read(target/name);old=blob(target,base,name);new=blob(self.source,sha,name)
        plan['preservedRuntimeFiles']=[{'path':name,'action':'PRESERVE_RUNTIME_DATA','baseSha256':hashes(old),'localSha256':hashes(local),'sourceSha256':hashes(new),**change_context(local,old,new,txn)}]
        write_json(txn/'plan.json',plan)
        if blocked: return txn,plan
        self.host.validate_stage(stage,txn/'tests.private.log')
        plan['testsPassed']=True;write_json(txn/'plan.json',plan)
        return txn,plan
    def load(self, txn):
        txn=safe(txn)
        if txn.parent!=self.backup_root: raise Blocked('UNKNOWN_TRANSACTION_DIRECTORY')
        if txn.stat().st_uid!=os.geteuid() or stat.S_IMODE(txn.stat().st_mode)&0o077: raise Blocked('PRIVATE_OWNED_TRANSACTION_REQUIRED')
        plan=json.loads(read(txn/'plan.json'))
        if plan['source']!=str(self.source) or plan['layout']!=self.layout.json() or plan['transaction']!=str(txn): raise Blocked('PLAN_ROOT_MISMATCH')
        return txn,plan
    def recheck(self,txn,plan):
        if not plan.get('testsPassed') or plan['blocked']: raise Blocked('PREFLIGHT_NOT_PASSED')
        if git(self.source,'rev-parse','HEAD').stdout.decode().strip()!=plan['sourceCommit']: raise Blocked('SOURCE_COMMIT_CHANGED')
        if git(self.layout.target,'rev-parse','HEAD').stdout.decode().strip()!=plan['targetCommit']: raise Blocked('PRODUCTION_HEAD_CHANGED')
        if git(self.layout.target,'branch','--show-current').stdout.strip()!=b'strategy-engine-v3': raise Blocked('TARGET_BRANCH_CHANGED')
        for name,sha in plan['units'].items():
            if name not in ('ops/research-collection-scheduler.service','ops/collection-worker-dropin/10-scheduler.conf') or hashes(read(txn/'stage'/name))!=sha:
                raise Blocked('UNIT_STAGE_HASH_MISMATCH')
        allowed_configs={COLLECTORS[0]}|{u+'.d/10-scheduler.conf' for u in COLLECTORS[1:]}
        if {r['name'] for r in plan['configurations']}!=allowed_configs: raise Blocked('INVALID_SYSTEMD_PLAN')
        for row in plan['configurations']:
            if hashes(read(self.layout.systemd/row['name']))!=row['beforeSha256']: raise Blocked('SYSTEMD_CHANGED_AFTER_ANALYSIS')
            if hashes(read(txn/'stage/systemd'/row['name']))!=row['afterSha256']: raise Blocked('SYSTEMD_STAGE_HASH_MISMATCH')
        if hashes(read(self.layout.env))!=plan['environmentSha256']: raise Blocked('ENVIRONMENT_CHANGED_AFTER_ANALYSIS')
        if hashes(read(self.layout.target/'.env'))!=plan['operationalEnvironmentSha256']: raise Blocked('OPERATIONAL_ENVIRONMENT_CHANGED_AFTER_ANALYSIS')
        for row in plan['files']:
            if not application(row['path']): raise Blocked('UNSAFE_PLAN_PATH')
            if hashes(read(self.layout.target/row['path']))!=row['localSha256']: raise Blocked('LOCAL_FILE_CHANGED: '+row['path'])
            expected_stage = row['sourceSha256'] if row.get('stageOnly') else row['deployedSha256']
            if hashes(read(txn/'stage'/row['path']))!=expected_stage: raise Blocked('STAGE_HASH_MISMATCH: '+row['path'])
        self.host.validate_stage(txn/'stage',txn/'apply-tests.private.log')
    def remember(self,txn,journal,path,after,mode=0o644,uid=None,gid=None,volatile=False):
        path=safe(path);data=read(path);attrs=attributes(path)
        key=str(len(journal['files']));backup=txn/'backups'/key
        if data is not None: atomic(backup,data)
        row={'path':str(path),'backup':key if data is not None else None,'beforeSha256':hashes(data),
             'afterSha256':hashes(after),'attrs':attrs,'mode':mode,'uid':uid,'gid':gid,'volatile':volatile,'intent':False}
        journal['files'].append(row);write_json(txn/'journal.json',journal)
        return row
    def install(self,txn,journal,row,data):
        if not row['volatile'] and hashes(read(row['path']))!=row['beforeSha256']: raise Blocked('FILE_CHANGED_AFTER_BACKUP')
        if hashes(data)!=row['afterSha256']: raise Blocked('INSTALL_HASH_MISMATCH')
        row['intent']=True;write_json(txn/'journal.json',journal)  # before replace: crash recovery
        a=row['attrs'] or row
        atomic(row['path'],data,a['mode'],a['uid'],a['gid'])
        # Timestamp-based .pyc files key on seconds + size. Same-second,
        # same-length replacement must not execute the pre-deployment bytecode.
        path=Path(row['path'])
        if row['attrs'] and path.suffix=='.py':
            stamp=path.stat().st_mtime_ns
            if stamp//10**9==row['attrs']['mtimeNs']//10**9:
                os.utime(path,ns=(stamp-10**9,stamp-10**9))
    def pause(self,uid,gid):
        atomic(self.layout.state/'control.json',b'{"paused":true}\n',0o640,uid,gid)
        atomic(self.layout.state/'permit.json',b'{"mode":"PAUSED"}\n',0o640,uid,gid)
    def stop_all(self,snapshot):
        for unit in COLLECTORS:
            if snapshot[unit]['exists']: self.host.ctl('stop',unit)
        self.host.no_workers()
        if snapshot[API]['exists']: self.host.ctl('stop',API)
        self.host.no_api()
    def apply(self,txn):
        txn,plan=self.load(txn);self.recheck(txn,plan)
        if (txn/'journal.json').exists(): raise Blocked('TRANSACTION_ALREADY_STARTED; use rollback')
        uid,gid=self.host.before_apply();self.host.broker();self.host.check_environment();self.host.app_service_matches();self.host.research_idle();snapshot=self.host.snapshot()
        unit_uid,unit_gid=self.host.system_identity()
        journal={'version':1,'phase':'PREPARED','sourceCommit':plan['sourceCommit'],'services':snapshot,'files':[],'startedAt':now()}
        write_json(txn/'journal.json',journal)
        try:
            # Back up every changed file BEFORE the first service/config mutation.
            installs=[]
            for row in plan['files']:
                if row['action'] not in ('UPDATE','MERGED'): continue
                data=read(txn/'stage'/row['path'])
                dest=self.layout.target/row['path']
                remembered=self.remember(txn,journal,dest,data,uid=uid,gid=gid)
                if remembered['beforeSha256']!=row['localSha256']: raise Blocked('LOCAL_FILE_CHANGED_AFTER_PREFLIGHT: '+row['path'])
                installs.append((remembered,data))
            for row in plan['configurations']:
                if row['action'] in ('UPDATE','MERGED'):
                    data=read(txn/'stage/systemd'/row['name'])
                    remembered=self.remember(txn,journal,self.layout.systemd/row['name'],data,uid=unit_uid,gid=unit_gid)
                    if remembered['beforeSha256']!=row['beforeSha256']: raise Blocked('SYSTEMD_CHANGED_AFTER_PREFLIGHT')
                    installs.append((remembered,data))
            metadata={}
            if not self.layout.state.exists():
                self.layout.state.mkdir(parents=True,mode=0o750)
                os.chown(self.layout.state,uid,gid)
            for name in ('control.json','ownership.json'):
                metadata[name]=self.remember(txn,journal,self.layout.state/name,b'{"paused":true}\n' if name=='control.json' else b'{}',uid=uid,gid=gid,mode=0o640,volatile=True)
            if not self.layout.env.exists(): installs.append((self.remember(txn,journal,self.layout.env,b'',uid=unit_uid,gid=unit_gid),b''))
            graceful=[]
            for u in COLLECTORS+[API]:
                dest=self.layout.systemd/(u+'.d')/'zzzz-collection-deployment-stop.conf'
                if dest.exists(): raise Blocked('GRACEFUL_OVERRIDE_ALREADY_EXISTS')
                data=b'[Service]\nTimeoutStopSec=180\nSendSIGKILL=no\nKillMode='+ (b'process' if u==API else b'control-group')+b'\n'
                graceful.append((self.remember(txn,journal,dest,data,uid=unit_uid,gid=unit_gid),data))
            for row,data in graceful: self.install(txn,journal,row,data)
            self.host.ctl('daemon-reload')
            self.host.graceful_stop_settings([u for u in COLLECTORS+[API] if snapshot[u]['exists']])
            journal['phase']='STOPPING';write_json(txn/'journal.json',journal)
            self.stop_all(snapshot)
            self.host.research_idle()  # catch a job launched just before API stopped
            for row in plan['files']:
                if hashes(read(self.layout.target/row['path']))!=row['localSha256']: raise Blocked('LOCAL_FILE_CHANGED_DURING_MAINTENANCE')
            if hashes(read(self.layout.env))!=plan['environmentSha256'] or hashes(read(self.layout.target/'.env'))!=plan['operationalEnvironmentSha256']:
                raise Blocked('ENVIRONMENT_CHANGED_DURING_MAINTENANCE')
            # No worker may enter until commit/rollback has verified quiescence.
            with lock(self.layout.state/'worker.lock', worker_owner=(uid,gid)):
                journal['phase']='APPLYING';write_json(txn/'journal.json',journal)
                # This is a control lock, not market data. Never truncate its
                # cooldowns; only set the exact file's group/mode after quiescence.
                rate=self.layout.target/'data/.toss_api_rate.lock'
                rate_data=read(rate)
                r=self.remember(txn,journal,rate,rate_data if rate_data is not None else b'',uid=uid,gid=gid,mode=0o660,volatile=True)
                r['permissionsOnly']=True;r['intent']=True;write_json(txn/'journal.json',journal)
                if rate_data is None: atomic(rate,b'',0o660,uid,gid)
                else: os.chown(rate,-1,gid);os.chmod(rate,0o660)
                for row,data in installs: self.install(txn,journal,row,data)
                for row in metadata.values(): row['intent']=True
                write_json(txn/'journal.json',journal)
                self.pause(uid,gid)
                # Ownership is generated by the new scheduler; journal as volatile.
                for u in COLLECTORS[1:]:
                    if snapshot[u]['exists']: self.host.ctl('disable',u)
                self.host.ctl('daemon-reload');self.host.ctl('enable',COLLECTORS[0]);self.host.ctl('start',COLLECTORS[0]);self.host.ctl('start',API)
                journal['phase']='VERIFYING';write_json(txn/'journal.json',journal)
                verification=self.host.verify(digest(read(txn/'stage/collection.html')))
                for row in plan['files']:
                    if hashes(read(self.layout.target/row['path']))!=row['deployedSha256']: raise Blocked('DEPLOYED_FILE_HASH_MISMATCH')
                for row,data in graceful:
                    if read(row['path'])!=data: raise Blocked('GRACEFUL_OVERRIDE_CHANGED_EXTERNALLY')
                    Path(row['path']).unlink()  # only our newly-created temporary dropins
                self.host.ctl('daemon-reload')
                journal['phase']='COMMITTED';journal['verification']=verification;journal['finishedAt']=now()
                write_json(txn/'journal.json',journal)
            return journal
        except BaseException:
            try: self.rollback(txn)
            except BaseException:
                journal['phase']='RECOVERY_BLOCKED';write_json(txn/'journal.json',journal)
            raise
    def rollback(self,txn):
        txn,plan=self.load(txn)
        raw=read(txn/'journal.json')
        if raw is None: raise Blocked('TRANSACTION_NOT_STARTED')
        journal=json.loads(raw)
        if journal['phase']=='ROLLED_BACK': return journal
        if not any(row['intent'] for row in journal['files']):
            journal['phase']='ROLLED_BACK';journal['maintenanceStarted']=False;journal['finishedAt']=now()
            write_json(txn/'journal.json',journal);return journal
        allowed={str(self.layout.target/r['path']) for r in plan['files'] if r['action'] in ('UPDATE','MERGED')}
        allowed |= {str(self.layout.systemd/r['name']) for r in plan['configurations']}
        allowed |= {str(self.layout.systemd/(u+'.d')/'zzzz-collection-deployment-stop.conf') for u in COLLECTORS+[API]}
        allowed |= {str(self.layout.state/x) for x in ('control.json','ownership.json')}|{str(self.layout.env)}
        allowed.add(str(self.layout.target/'data/.toss_api_rate.lock'))
        for row in journal['files']:
            if row['path'] not in allowed or (row['backup'] is not None and not str(row['backup']).isdigit()): raise Blocked('INVALID_ROLLBACK_DESTINATION')
        snapshot=journal['services'];uid,gid=self.host.before_apply()
        journal['phase']='ROLLING_BACK';write_json(txn/'journal.json',journal)
        # Include newly-created scheduler unit, even if absent before deployment.
        try:
            self.host.research_idle()
            # Reinstall only our API stop override for explicit post-commit recovery.
            api_guard=next((r for r in journal['files'] if r['path']==str(self.layout.systemd/(API+'.d')/'zzzz-collection-deployment-stop.conf')),None)
            if api_guard and api_guard['intent'] and read(api_guard['path']) is None:
                identity=self.host.system_identity()
                atomic(api_guard['path'],b'[Service]\nTimeoutStopSec=180\nSendSIGKILL=no\nKillMode=process\n',0o644,*identity)
                self.host.ctl('daemon-reload')
            for u in COLLECTORS:
                if u==COLLECTORS[0] or snapshot[u]['exists']: self.host.ctl('stop',u,required=False)
            self.host.no_workers()
            if snapshot[API]['exists']: self.host.ctl('stop',API,required=False)
            self.host.no_api()
            self.host.research_idle()
            with lock(self.layout.state/'worker.lock', worker_owner=(uid,gid)):
                for row in reversed(journal['files']):
                    if not row['intent']: continue
                    path=safe(row['path']);data=read(path)
                    if not row['volatile'] and hashes(data) not in (row['beforeSha256'],row['afterSha256']): raise Blocked('ROLLBACK_EXTERNAL_EDIT: '+str(path))
                    if row['backup'] is not None:
                        original=read(txn/'backups'/row['backup'])
                        if digest(original)!=row['beforeSha256']: raise Blocked('ROLLBACK_BACKUP_HASH_MISMATCH')
                        a=row['attrs']
                        if row.get('permissionsOnly'):
                            os.chown(path,a['uid'],a['gid']);os.chmod(path,a['mode'])
                        else: atomic(path,original,a['mode'],a['uid'],a['gid']);os.utime(path,ns=(a['mtimeNs'],a['mtimeNs']))
                    elif data is not None: path.unlink()  # only files created by this transaction
                self.pause(uid,gid)  # rollback also leaves collection explicitly paused
                self.host.ctl('daemon-reload')
                for u in COLLECTORS:
                    if snapshot[u]['exists'] or u==COLLECTORS[0]: self.host.ctl('disable',u,required=False)
                if snapshot[API]['active']: self.host.ctl('start',API)
                journal['recoveryVerification']=self.host.recovery_verify(snapshot[API]['active'])
            journal['phase']='ROLLED_BACK';journal['collectorsPaused']=True;journal['finishedAt']=now()
        except BaseException:
            journal['phase']='RECOVERY_BLOCKED';write_json(txn/'journal.json',journal);raise
        write_json(txn/'journal.json',journal);return journal


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True,help='Clean committed worktree; detached HEAD accepted')
    p.add_argument('--target',type=Path,default=Path('/home/ubuntu/market-career-dashboard'))
    p.add_argument('--backup-root',type=Path,default=Path('/mnt/research/collection-deployments'))
    group=p.add_mutually_exclusive_group();group.add_argument('--analyze',action='store_true',help='Default: stage/analyze/test only');group.add_argument('--apply',type=Path,metavar='TRANSACTION');group.add_argument('--rollback',type=Path,metavar='TRANSACTION')
    p.add_argument('--api-url',default='http://127.0.0.1:8080')
    args=p.parse_args();layout=Layout(args.target);deploy=Deployment(args.source,layout,args.backup_root,Host(layout,args.api_url))
    if os.geteuid()!=0: raise Blocked('RUN_PREFLIGHT_APPLY_ROLLBACK_WITH_SUDO')
    def interrupted(signum, _frame): raise Blocked('DEPLOY_INTERRUPTED_SIGNAL_'+str(signum))
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    canonical=git(layout.target,'rev-parse','--path-format=absolute','--git-path','collection-deploy.lock').stdout.decode().strip()
    with lock(canonical):
        if args.apply: result=deploy.apply(args.apply);print(json.dumps({'phase':result['phase'],'sourceCommit':result['sourceCommit'],'transaction':str(args.apply),'verification':result.get('verification')}))
        elif args.rollback: result=deploy.rollback(args.rollback);print(json.dumps({'phase':result['phase'],'collectorsPaused':True}))
        else:
            txn,plan=deploy.analyze()
            print(json.dumps({'transaction':str(txn),'plan':str(txn/'plan.json'),'testsPassed':plan.get('testsPassed',False),'blocked':plan['blocked'],
                              'changedFiles':[{k:r[k] for k in ('path','action','localChanged','sourceChanged','overlapConflict')} for r in plan['files'] if r['action']!='UNCHANGED'],
                              'preservedRuntimeFiles':plan['preservedRuntimeFiles'],'sourceCommit':plan['sourceCommit']}))
            if plan['blocked']: raise SystemExit(2)


if __name__=='__main__':
    try: main()
    except Blocked as exc: print(str(exc),file=sys.stderr);raise SystemExit(2)
