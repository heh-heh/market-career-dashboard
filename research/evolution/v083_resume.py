"""Committed-generation recovery, isolated from legacy evolution policies.

A checkpoint commits artifact byte prefixes, not the mutable run/status files.
Interrupted partial windows stay in the original run and are replayed exactly once
in a NEW run directory. No input is modified by recovery.
"""
import hashlib
import json
import os
from pathlib import Path

from research.backtest_intraday_v4 import atomic_json

VERSION = 'V083_RESUME_1'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def tuples(value):
    return tuple(tuples(x) for x in value) if isinstance(value, list) else value


def commit(directory, payload):
    directory = Path(directory)
    artifacts = []
    for path in sorted(directory.rglob('*')):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise ValueError('RESUME_ARTIFACT_SYMLINK')
        if not path.is_file() or relative in {'checkpoint.json', 'run.json', 'status.json', 'resources.json'}:
            continue
        sha=hashlib.sha256()
        with path.open('rb') as handle:
            os.fsync(handle.fileno())
            for chunk in iter(lambda:handle.read(1024*1024),b''):sha.update(chunk)
        artifacts.append(dict(path=relative, bytes=path.stat().st_size, sha256=sha.hexdigest(), appendOnly=path.suffix == '.jsonl'))
    atomic_json(directory / 'checkpoint.json', dict(payload, resumeVersion=VERSION, artifacts=artifacts, commitState='COMMITTED'))
    with (directory/'checkpoint.json').open('rb') as handle:os.fsync(handle.fileno())
    fd=os.open(directory,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def verify(source, *, code_hashes, config, plan_hash, first, last, seed, population):
    source = Path(source)
    if source.is_symlink():
        raise ValueError('RESUME_SOURCE_SYMLINK')
    source = source.resolve(strict=True)
    checkpoint = source / 'checkpoint.json'
    if checkpoint.is_symlink():
        raise ValueError('RESUME_CHECKPOINT_SYMLINK')
    data = json.loads(checkpoint.read_text())
    if data.get('resumeVersion') != VERSION or data.get('commitState') != 'COMMITTED':
        raise ValueError('RESUME_CHECKPOINT_NOT_COMMITTED_OR_UNSUPPORTED')
    info = data['runInfo']
    expected = dict(codeHashes=code_hashes, config=config, sourcePlanSha256=plan_hash,
                    fromDate=first, toDate=last, seed=seed, population=population,
                    version='V083_TOP_TWO_1', finalOosGate='CLOSED')
    for key, value in expected.items():
        if info.get(key) != value:
            raise ValueError('RESUME_MISMATCH_' + key.upper())
    if data.get('sourcePlanSha256') != plan_hash or data.get('finalOosGate') != 'CLOSED':
        raise ValueError('RESUME_PLAN_OR_OOS_MISMATCH')
    generation = data['completedGeneration']
    dates = data['state']['selected']
    if type(generation) is not int or generation < 0 or len(dates) != generation * 5 or len(set(dates)) != len(dates) or dates != sorted(dates):
        raise ValueError('RESUME_DUPLICATE_OR_INVALID_COMMITTED_DATES')
    if data['throughDate'] != (dates[-1] if dates else None):
        raise ValueError('RESUME_THROUGH_DATE_MISMATCH')
    if len(data['population']) != population or len({b['bot_id'] for b in data['population']}) != population:
        raise ValueError('RESUME_POPULATION_INVALID')
    names = set()
    for artifact in data['artifacts']:
        name = artifact['path']; rel = Path(name)
        if rel.is_absolute() or '..' in rel.parts or name in names:
            raise ValueError('RESUME_ARTIFACT_PATH_INVALID')
        names.add(name)
        path = source / rel
        if any(p.is_symlink() for p in [path, *path.parents] if p != source.parent):
            raise ValueError('RESUME_ARTIFACT_SYMLINK')
        if not path.is_file() or not path.resolve().is_relative_to(source):
            raise ValueError('RESUME_ARTIFACT_MISSING')
        size = artifact['bytes']
        sha=hashlib.sha256(); remaining=size
        with path.open('rb') as handle:
            while remaining:
                chunk=handle.read(min(1024*1024,remaining))
                if not chunk:break
                sha.update(chunk);remaining-=len(chunk)
        if remaining or sha.hexdigest() != artifact['sha256'] or (not artifact['appendOnly'] and path.stat().st_size != size):
            raise ValueError('RESUME_ARTIFACT_INTEGRITY_FAILED:' + name)
    if generation and (f'window_{generation-1:04d}.json' not in names or 'lineage.jsonl' not in names or 'generations.jsonl' not in names):
        raise ValueError('RESUME_COMMITTED_LEDGER_MISSING')
    return source, data


def restore_artifacts(source, checkpoint, target):
    """Copy verified committed prefixes, never interrupted trailing data."""
    for item in checkpoint['artifacts']:
        destination = Path(target) / item['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        with (Path(source) / item['path']).open('rb') as handle, destination.open('xb') as out:
            remaining = item['bytes']
            sha = hashlib.sha256()
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError('RESUME_SOURCE_CHANGED_DURING_COPY')
                sha.update(chunk); out.write(chunk); remaining -= len(chunk)
            if sha.hexdigest() != item['sha256']:
                raise ValueError('RESUME_SOURCE_CHANGED_DURING_COPY')
