"""Research-only one-shot worker: validate, calibrate, train, persist, exit.

Must run in the separately approved Spot AMI. No infrastructure or broker APIs.
The launcher owns instance termination and an independent TTL reaper. Durable
checkpoint commits upload each completed generation, not only process exit.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time

# Support execution by absolute file path from a prebuilt bundle.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.spot.worker_storage import DurableStore, digest, safe_path

SAFE_DATE_START = '2022-01-01'
SAFE_DATE_END = '2023-12-31'


def now(): return datetime.now(timezone.utc).isoformat()
def sha256(p): return digest(p)


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    os.replace(temp, path)


def execute(cmd, *, cwd, log_file, status_file, stage, observe=None,
            interrupted=None, deadline=None, popen=subprocess.Popen):
    """Poll own child only; never signal unrelated research/collector services."""
    with Path(log_file).open('a', encoding='utf-8') as log:
        log.write(json.dumps({'stage': stage, 'status': 'running', 'at': now()}) + '\n'); log.flush()
        child = popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log,
                      stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while child.poll() is None:
                if interrupted and interrupted(): raise ValueError('SPOT_INTERRUPTION_OR_TERMINATION_REQUESTED')
                if deadline is not None and time.monotonic() >= deadline: raise ValueError('SPOT_MAX_RUNTIME_EXCEEDED')
                if observe: observe()
                time.sleep(1)
            if observe: observe()
        except BaseException:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try: child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL); child.wait(timeout=10)
            raise
    atomic_json(status_file, {'stage': stage, 'phase': 'completed' if child.returncode == 0 else 'failed',
                             'code': child.returncode, 'finalOosGate': 'CLOSED', 'at': now()})
    if child.returncode: raise ValueError(f'SPOT_WORKER_STAGE_{stage.upper()}_FAILED_{child.returncode}')


def validate_bundle(bundle, job_id, population, source_commit):
    bundle = Path(bundle)
    repo = bundle / 'source'
    required = [bundle / 'selection/selection_plan.json', bundle / 'bundle_manifest.json',
                repo / 'research/evolution/run_v083.py', repo / 'research/evolution/calibrate_v083.py']
    if any(not p.is_file() or p.is_symlink() for p in required):
        raise ValueError('SPOT_PINNED_BUNDLE_LAYOUT_REQUIRED')
    meta = json.loads((bundle / 'bundle_manifest.json').read_text())
    permitted = meta.get('population_bounds') == [2, 50] or meta.get('population') == population
    if (meta.get('job_id') != job_id or meta.get('source_commit') != source_commit or not permitted or
        meta.get('finalOosGate') != 'CLOSED' or meta.get('train_dates') != [SAFE_DATE_START, SAFE_DATE_END]):
        raise ValueError('SPOT_MANIFEST_MISMATCH_OR_FINAL_OOS_BLOCKED')
    # New transport schema is mandatory: legacy bundles lacked audit/data verification.
    if meta.get('version') != 2 or not meta.get('payload_files'):
        raise ValueError('SPOT_REVIEWED_BUNDLE_V2_REQUIRED')
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    if actual != set(meta['payload_files']) | {'bundle_manifest.json'}:
        raise ValueError('SPOT_BUNDLE_UNDECLARED_OR_MISSING_FILE')
    for name, expected in meta['payload_files'].items():
        path = safe_path(bundle, name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError('SPOT_BUNDLE_PAYLOAD_HASH_MISMATCH')
    if digest(bundle / 'selection/selection_plan.json') != meta['selection_plan_sha256']:
        raise ValueError('SPOT_SELECTION_PLAN_HASH_MISMATCH')
    return meta


def validate_runtime(directory, source_commit):
    """Verify separate code-only archive; immutable data bundle is never edited."""
    root = Path(directory)
    manifest_path = root / 'runtime_manifest.json'
    if root.is_symlink() or not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError('SPOT_RUNTIME_MANIFEST_REQUIRED')
    meta = json.loads(manifest_path.read_text())
    if (meta.get('version') != 1 or meta.get('source_commit') != source_commit or
            meta.get('finalOosGate') != 'CLOSED' or meta.get('researchOnly') is not True):
        raise ValueError('SPOT_RUNTIME_SOURCE_OR_GATE_MISMATCH')
    payload = meta.get('payload_files')
    if not isinstance(payload, dict) or not payload or len(payload) > 10000:
        raise ValueError('SPOT_RUNTIME_PAYLOAD_REQUIRED')
    if any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('SPOT_RUNTIME_SYMLINK_BLOCKED')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual != set(payload) | {'runtime_manifest.json'}:
        raise ValueError('SPOT_RUNTIME_UNDECLARED_OR_MISSING_FILE')
    total = 0
    for name, expected in payload.items():
        if not name.startswith('source/research/') or not name.endswith(('.py', '.json')):
            raise ValueError('SPOT_RUNTIME_CODE_ONLY_REQUIRED')
        path = safe_path(root, name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError('SPOT_RUNTIME_PAYLOAD_HASH_MISMATCH')
        total += path.stat().st_size
        if total > 128 * 1024**2: raise ValueError('SPOT_RUNTIME_TOO_LARGE')
    required = {'source/research/evolution/run_v083.py', 'source/research/evolution/calibrate_v083.py',
                'source/research/spot/v083_spot_worker.py', 'source/research/spot/worker_storage.py'}
    if not required <= set(payload): raise ValueError('SPOT_RUNTIME_ENGINE_REQUIRED')
    return root / 'source', meta


def capacity_files(output):
    base=Path(output)/'capacity'
    return sorted([*base.glob('capacity.json'),*base.glob('*/capacity.json')])


def capacity_summary(output):
    files=capacity_files(output)
    if len(files)!=1 or files[0].is_symlink() or files[0].stat().st_size>2*1024**2:return None
    try:
        data=json.loads(files[0].read_text());trials=data.get('trials',[])
        error=re.match(r'[A-Z][A-Z0-9_]+',str(data.get('error','')))
        return dict(status=data.get('status'),population=data.get('population'),completedTrials=len(trials),
            errorCode=error.group(0) if error else None,
            trials=[dict(label=t.get('label'),resourceReport=t.get('resourceReport')) for t in trials])
    except (ValueError,OSError):return None


def run(*, workspace, job_id, population, source_commit, bucket=None, prefix=None,
        store=None, max_seconds=28500, interrupted=None, resume_reference=None, resume_prefix=None,
        bundle_job_id=None, bundle_source_commit=None, runtime_dir=None):
    if not re.fullmatch(r'v083-[a-f0-9]{16}', job_id) or type(population) is not int or not 2 <= population <= 50:
        raise ValueError('SPOT_JOB_OR_POPULATION_INVALID')
    if not re.fullmatch(r'[a-f0-9]{40}', source_commit): raise ValueError('SPOT_SOURCE_COMMIT_INVALID')
    workspace = Path(workspace).resolve()
    bundle = workspace / 'bundle'; repo = bundle / 'source'
    bundle_job_id = bundle_job_id or job_id
    bundle_source_commit = bundle_source_commit or source_commit
    if not re.fullmatch(r'v083-[a-f0-9]{16}', bundle_job_id): raise ValueError('SPOT_BUNDLE_JOB_ID_INVALID')
    if not re.fullmatch(r'[a-f0-9]{40}', bundle_source_commit): raise ValueError('SPOT_BUNDLE_SOURCE_COMMIT_INVALID')
    meta = validate_bundle(bundle, bundle_job_id, population, bundle_source_commit)
    if runtime_dir:
        repo, runtime_meta = validate_runtime(runtime_dir, source_commit)
    elif source_commit != bundle_source_commit:
        raise ValueError('SPOT_NEW_SOURCE_REQUIRES_SEPARATE_RUNTIME')
    provenance = dict(sourceCommit=source_commit, bundleSourceCommit=bundle_source_commit,
                      bundleJobId=bundle_job_id, runtimeSeparated=bool(runtime_dir))
    if not store:
        if not bucket or prefix != 'v083/runs/' + job_id: raise ValueError('SPOT_DURABLE_OUTPUT_REQUIRED')
        import boto3
        from botocore.config import Config
        store = DurableStore(boto3.client('s3', region_name='ap-northeast-1',
            config=Config(connect_timeout=5, read_timeout=15, retries={'max_attempts':1})), bucket, prefix)
    # Hashes are verified BEFORE importing any bundled engine modules.
    sys.path.insert(0, str(repo))
    from research.evolution.available_data import verify_plan
    plan = bundle / 'selection/selection_plan.json'
    verified, paths = verify_plan(plan)
    if verified['finalOosGate'] != 'CLOSED' or not paths or any(
            not SAFE_DATE_START <= row['sessionDate'] <= SAFE_DATE_END for row in verified['sessions']):
        raise ValueError('SPOT_TRAINING_DATA_GATE_VIOLATION')
    output = workspace / 'output'
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(output.iterdir()): raise ValueError('SPOT_OUTPUT_ALREADY_EXISTS_USE_EXPLICIT_RESUME')
    atomic_json(output / 'source_provenance.json', provenance)
    if not 0 < max_seconds <= 28800: raise ValueError('SPOT_MAX_RUNTIME_MUST_BE_WITHIN_8_HOURS')
    # Verify durable writes before starting any calibration/training child.
    raw = json.dumps({'jobId':job_id,'job_id':job_id,'phase':'starting','at':now(),'finalOosGate':'CLOSED',**provenance}).encode()
    store._put(store.prefix + '/output/live_status.json', raw, hashlib.sha256(raw).hexdigest())
    deadline = time.monotonic() + max_seconds
    seen = {}; last_status = [0.0]; latest_commit = [None]; current_stage = ['calibrate']
    def observe():
        disk = shutil.disk_usage(workspace)
        if disk.free < max(10 * 1024**3, disk.total * .1):
            raise ValueError('SPOT_DISK_RESERVE_INSUFFICIENT')
        for path in sorted((output / 'train').rglob('checkpoint.json')):
            value = digest(path)
            if seen.get(str(path)) != value:
                committed = store.checkpoint(path.parent, job_id=job_id, source_commit=source_commit)
                if committed:
                    seen[str(path)] = value; latest_commit[0] = {k: v for k, v in committed.items() if k != 'manifest'}
                    atomic_json(output / 'durable_checkpoint.json', latest_commit[0])
        if time.monotonic() - last_status[0] >= 15:
            status = {'jobId': job_id, 'job_id': job_id, 'phase': current_stage[0], 'at': now(), 'finalOosGate': 'CLOSED',
                      'checkpoint': latest_commit[0], 'researchOnly': True, 'calibration':capacity_summary(output), **provenance}
            for path in sorted((output / 'train').rglob('status.json')):
                try: status['training'] = json.loads(path.read_text())
                except (ValueError, OSError): pass # concurrent atomic writer, next observation retries
            atomic_json(output / 'worker_status.json', status)
            raw = json.dumps(status).encode()
            store._put(store.prefix + '/output/live_status.json', raw, hashlib.sha256(raw).hexdigest())
            last_status[0] = time.monotonic()
    resume_args = []
    if resume_reference:
        if not resume_prefix: raise ValueError('SPOT_EXPLICIT_RESUME_PREFIX_REQUIRED')
        previous = DurableStore(store.client, store.bucket, resume_prefix)
        reference = json.loads(Path(resume_reference).read_text())
        restored = workspace / 'resume'
        record = previous.restore(reference, restored)
        if record['sourceCommit'] != source_commit: raise ValueError('SPOT_RESUME_SOURCE_MISMATCH')
        runs = [p.parent for p in restored.rglob('checkpoint.json')]
        if len(runs) != 1: raise ValueError('SPOT_RESUME_SINGLE_COMMITTED_RUN_REQUIRED')
        resume_args = ['--resume-from', str(runs[0])]
    try:
        python = str(Path(sys.executable))
        common = dict(cwd=repo, observe=observe, interrupted=interrupted, deadline=deadline)
        execute([python, str(repo / 'research/evolution/calibrate_v083.py'), '--selection-plan', str(plan),
                 '--population', str(population), '--out', str(output / 'capacity'), '--data-origin', 'toss', '--seed', '42'],
                log_file=output / 'calibrate.log', status_file=output / 'calibrate_status.json', stage='calibrate', **common)
        current_stage[0] = 'train'; last_status[0] = 0
        certificates=capacity_files(output)
        if len(certificates)!=1:raise ValueError('SPOT_SINGLE_CAPACITY_CERTIFICATE_REQUIRED')
        capacity=certificates[0]
        if capacity.is_symlink() or json.loads(capacity.read_text()).get('status') != 'MEASURED_SAFE':
            raise ValueError('SPOT_CAPACITY_CERTIFICATE_NOT_SAFE')
        execute([python, str(repo / 'research/evolution/run_v083.py'), '--selection-plan', str(plan),
                 '--population', str(population), '--from-date', SAFE_DATE_START, '--to-date', SAFE_DATE_END,
                 '--capacity-report', str(capacity), '--data-origin', 'toss', '--results-dir', str(output / 'train'),
                 '--seed', '42', *resume_args], log_file=output / 'train.log',
                status_file=output / 'train_status.json', stage='train', **common)
        atomic_json(output / 'final_status.json', {'job_id': job_id, 'phase': 'completed', 'finalOosGate': 'CLOSED', 'at': now()})
        committed = store.snapshot(output, phase='completed', job_id=job_id, source_commit=source_commit)
        final = {'job_id':job_id, 'jobId':job_id, 'phase':'completed', 'at':now(),
                 'finalOosGate':'CLOSED', 'checkpoint':latest_commit[0],
                 'resultCommit':{k:v for k,v in committed.items() if k != 'manifest'},'calibration':capacity_summary(output), **provenance}
        # Child has exited: publish the actual final training summary even if
        # the last 15-second heartbeat preceded the final generation.
        for path in sorted((output / 'train').rglob('status.json')):
            final['training'] = json.loads(path.read_text())
        raw = json.dumps(final).encode()
        store._put(store.prefix + '/output/live_status.json', raw, hashlib.sha256(raw).hexdigest())
        return final['resultCommit']
    except BaseException as error:
        atomic_json(output / 'final_status.json', {'job_id': job_id, 'phase': 'failed', 'error': type(error).__name__ + ': ' + str(error)[:200],
                                                'finalOosGate': 'CLOSED', 'at': now()})
        try:
            last_status[0]=0;observe()
            failed_commit = store.snapshot(output, phase='failed', job_id=job_id, source_commit=source_commit)
            raw = json.dumps({'job_id':job_id,'phase':'failed','finalOosGate':'CLOSED',
                              'errorType':type(error).__name__,'errorCode':(re.match(r'[A-Z][A-Z0-9_]+',str(error)).group(0) if re.match(r'[A-Z][A-Z0-9_]+',str(error)) else type(error).__name__),'calibration':capacity_summary(output),'checkpoint':latest_commit[0],
                              'resultCommit':{k:v for k,v in failed_commit.items() if k != 'manifest'}}).encode()
            store._put(store.prefix + '/output/live_status.json', raw, hashlib.sha256(raw).hexdigest())
        except Exception as upload_error:
            atomic_json(output / 'durability_error.json', {'phase': 'DURABLE_UPLOAD_FAILED', 'errorType': type(upload_error).__name__, 'at': now()})
        raise


class InterruptionNotice:
    """Best-effort IMDSv2 notice polling; absence is never a restart signal."""
    def __init__(self): self.checked = -100.; self.requested = False
    def __call__(self):
        if self.requested or time.monotonic() - self.checked < 15: return self.requested
        self.checked = time.monotonic()
        import urllib.request
        import urllib.error
        try:
            token_request = urllib.request.Request('http://169.254.169.254/latest/api/token', method='PUT',
                headers={'X-aws-ec2-metadata-token-ttl-seconds':'60'})
            with urllib.request.urlopen(token_request, timeout=1) as response:
                token = response.read(4096).decode()
            request = urllib.request.Request('http://169.254.169.254/latest/meta-data/spot/instance-action',
                                            headers={'X-aws-ec2-metadata-token':token})
            with urllib.request.urlopen(request, timeout=1) as response:
                action = json.loads(response.read(4096))
                self.requested = action.get('action') in {'terminate','stop','hibernate'}
        except (OSError, ValueError, UnicodeError):
            pass # IMDS unavailable: independent timeout/reaper still enforce deadline.
        return self.requested


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', type=Path, required=True); p.add_argument('--job-id', required=True)
    p.add_argument('--population', type=int, required=True); p.add_argument('--source-commit', required=True)
    p.add_argument('--bucket', required=True); p.add_argument('--prefix', required=True)
    p.add_argument('--max-seconds', type=int, default=28500)
    p.add_argument('--resume-reference', type=Path); p.add_argument('--resume-prefix')
    p.add_argument('--bundle-job-id'); p.add_argument('--bundle-source-commit')
    p.add_argument('--runtime-dir', type=Path)
    a = p.parse_args(argv)
    if not 0 < a.max_seconds <= 28800: p.error('SPOT_MAX_RUNTIME_MUST_BE_WITHIN_8_HOURS')
    cancelled = [False]
    def request_stop(*_): cancelled[0] = True
    signal.signal(signal.SIGTERM, request_stop); signal.signal(signal.SIGINT, request_stop)
    notice = InterruptionNotice()
    run(**vars(a), interrupted=lambda: cancelled[0] or notice())


if __name__ == '__main__': main()
