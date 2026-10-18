"""Bounded, version-pinned transport for research-only Spot workers.

No credentials or network clients are constructed on import. Upload failures are
fatal: a completed local run is not a completed durable run. Native AWS object
versions plus SHA256 are the identity, not an untrusted ETag.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import uuid
import tempfile


MAX_FILES = 200000
MAX_UNPACKED_BYTES = 30 * 1024**3
SECRET = re.compile(r'(^\.|token|credential|secret|private|\.pem$|\.key$|\.env$)', re.I)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_path(root, name):
    rel = PurePosixPath(name)
    if not name or rel.is_absolute() or '..' in rel.parts or '\\' in name or any(SECRET.search(p) for p in rel.parts):
        raise ValueError('SPOT_UNSAFE_ARTIFACT_PATH')
    p = Path(root).joinpath(*rel.parts)
    if not p.resolve().is_relative_to(Path(root).resolve()) or any(parent.is_symlink() for parent in [p, *p.parents] if parent != Path(root).parent):
        raise ValueError('SPOT_UNSAFE_ARTIFACT_PATH')
    return p


def extract_archive(archive, destination, *, max_files=MAX_FILES, max_bytes=MAX_UNPACKED_BYTES):
    """Streaming extraction, regular files/directories only, never overwrite."""
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    count = total = 0
    seen = set()
    with tarfile.open(archive, 'r|gz') as tar:
        for member in tar:
            count += 1
            total += member.size
            if count > max_files or total > max_bytes or member.size < 0:
                raise ValueError('SPOT_ARCHIVE_LIMIT_EXCEEDED')
            target = safe_path(root, member.name)
            if member.name in seen or not (member.isfile() or member.isdir()):
                raise ValueError('SPOT_UNSAFE_ARCHIVE')
            seen.add(member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open('xb') as out:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    out.write(block)
    return {'files': count, 'bytes': total}


class DurableStore:
    """Immutable, version-addressed objects followed by atomic commit marker.

    Every put requires versioning; a job-specific IAM prefix prevents source
    mutation. No delete operation exists. A separate reader can fetch the exact
    manifest version and restore all objects without listing a mutable prefix.
    """
    def __init__(self, client, bucket, prefix, *, max_upload_bytes=8 * 1024**3, max_puts=100000):
        if not re.fullmatch(r'v083/runs/v083-[a-f0-9]{16}', prefix):
            raise ValueError('SPOT_OUTPUT_PREFIX_INVALID')
        self.client, self.bucket, self.prefix = client, bucket, prefix
        self.known = {}
        self.upload_bytes = 0; self.put_count = 0
        self.max_upload_bytes = max_upload_bytes; self.max_puts = max_puts

    def _put(self, key, body, sha):
        size = os.fstat(body.fileno()).st_size - body.tell() if hasattr(body, 'fileno') else len(body)
        if self.upload_bytes + size > self.max_upload_bytes or self.put_count >= self.max_puts:
            raise ValueError('SPOT_DURABLE_UPLOAD_BUDGET_EXCEEDED')
        # Count attempted puts too: a network timeout may already have stored data.
        self.upload_bytes += size; self.put_count += 1
        result = self.client.put_object(Bucket=self.bucket, Key=key, Body=body,
                                      Metadata={'sha256': sha}, ServerSideEncryption='AES256')
        version = result.get('VersionId')
        if not version or version == 'null':
            raise ValueError('SPOT_S3_VERSIONING_REQUIRED')
        checked = self.client.head_object(Bucket=self.bucket, Key=key, VersionId=version)
        if checked.get('Metadata', {}).get('sha256') != sha:
            raise ValueError('SPOT_DURABLE_HASH_MISMATCH')
        return {'key': key, 'versionId': version, 'sha256': sha}

    def snapshot(self, root, *, phase, job_id, source_commit):
        root = Path(root)
        entries = []; total = 0
        for p in sorted(root.rglob('*')):
            if not p.is_file():
                if p.is_symlink(): raise ValueError('SPOT_UNSAFE_ARTIFACT_PATH')
                continue
            relative = p.relative_to(root).as_posix()
            safe_path(root, relative)
            if len(entries) >= MAX_FILES: raise ValueError('SPOT_ARCHIVE_LIMIT_EXCEEDED')
            before = p.stat()
            total += before.st_size
            if total > MAX_UNPACKED_BYTES: raise ValueError("SPOT_ARCHIVE_LIMIT_EXCEEDED")
            sha = digest(p)
            identity = sha
            if identity not in self.known:
                with p.open('rb') as body:
                    self.known[identity] = self._put(f'{self.prefix}/objects/{sha}', body, sha)
            after = p.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('SPOT_ARTIFACT_CHANGED_DURING_UPLOAD')
            entries.append({'path': relative, 'bytes': after.st_size, **self.known[identity]})
        manifest = {'version': 1, 'jobId': job_id, 'sourceCommit': source_commit,
                    'phase': phase, 'researchOnly': True, 'finalOosGate': 'CLOSED', 'files': entries}
        raw = json.dumps(manifest, sort_keys=True).encode()
        committed = self._put(f'{self.prefix}/commits/{uuid.uuid4().hex}.json', raw, hashlib.sha256(raw).hexdigest())
        return {**committed, 'manifest': manifest}

    def checkpoint(self, run_dir, *, job_id, source_commit):
        """Freeze exactly checkpoint-committed ledger prefixes, never next-gen rows."""
        run_dir = Path(run_dir)
        raw = (run_dir / 'checkpoint.json').read_bytes()
        checkpoint = json.loads(raw)
        if checkpoint.get('resumeVersion') != 'V083_RESUME_1' or checkpoint.get('commitState') != 'COMMITTED':
            return None
        with tempfile.TemporaryDirectory(prefix='v083-commit-') as temp:
            root = Path(temp) / run_dir.name
            root.mkdir()
            (root / 'checkpoint.json').write_bytes(raw)
            for item in checkpoint['artifacts']:
                src = safe_path(run_dir, item['path'])
                dst = safe_path(root, item['path'])
                dst.parent.mkdir(parents=True, exist_ok=True)
                remaining = item['bytes']
                with src.open('rb') as source, dst.open('xb') as target:
                    while remaining:
                        data = source.read(min(1024 * 1024, remaining))
                        if not data: raise ValueError('SPOT_CHECKPOINT_TRUNCATED_LEDGER')
                        target.write(data)
                        remaining -= len(data)
                if digest(dst) != item['sha256']:
                    raise ValueError('SPOT_CHECKPOINT_LEDGER_HASH_MISMATCH')
            return self.snapshot(Path(temp), phase='checkpoint', job_id=job_id, source_commit=source_commit)

    def restore(self, reference, destination):
        """Only a separately approved successor receives an exact commit ref."""
        if (not reference.get('key', '').startswith(self.prefix + '/commits/') or
                not re.fullmatch(r'[a-f0-9]{64}', str(reference.get('sha256', ''))) or
                not reference.get('versionId') or reference['versionId'] == 'null'):

            raise ValueError('SPOT_RESTORE_PREFIX_MISMATCH')
        response = self.client.get_object(Bucket=self.bucket, Key=reference['key'], VersionId=reference['versionId'])
        raw = response['Body'].read(16 * 1024**2 + 1)
        if len(raw) > 16 * 1024**2 or hashlib.sha256(raw).hexdigest() != reference['sha256']:
            raise ValueError('SPOT_RESTORE_MANIFEST_HASH_MISMATCH')
        manifest = json.loads(raw)
        if manifest.get('finalOosGate') != 'CLOSED' or manifest.get('researchOnly') is not True:
            raise ValueError('SPOT_RESTORE_GATE_MISMATCH')
        if len(manifest['files']) > MAX_FILES: raise ValueError('SPOT_ARCHIVE_LIMIT_EXCEEDED')
        total = 0; seen = set()
        for item in manifest['files']:
            if (type(item['bytes']) is not int or item['bytes'] < 0 or item['path'] in seen or
                    not re.fullmatch(r'[a-f0-9]{64}', str(item['sha256']))):
                raise ValueError('SPOT_RESTORE_INVALID_OBJECT')
            seen.add(item['path'])
            total += item['bytes']
            if total > MAX_UNPACKED_BYTES or item['key'] != self.prefix + '/objects/' + item['sha256']:
                raise ValueError('SPOT_RESTORE_LIMIT_OR_PREFIX')
            path = safe_path(destination, item['path'])
            path.parent.mkdir(parents=True, exist_ok=True)
            obj = self.client.get_object(Bucket=self.bucket, Key=item['key'], VersionId=item['versionId'])
            count = 0
            with path.open('xb') as handle:
                for data in iter(lambda: obj['Body'].read(1024 * 1024), b''):
                    count += len(data)
                    if count > item['bytes']: raise ValueError('SPOT_RESTORE_SIZE_MISMATCH')
                    handle.write(data)
            if count != item['bytes'] or digest(path) != item['sha256']:
                raise ValueError('SPOT_RESTORE_DATA_HASH_MISMATCH')
        return manifest
