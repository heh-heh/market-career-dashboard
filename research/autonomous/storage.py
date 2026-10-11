"""Cheap filesystem observations that respect bind mounts; no directory walks."""
import os
from pathlib import Path
import re
import shutil


def filesystem_status(path, mountinfo=None):
    logical = Path(path).absolute()
    existing = logical
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    usage = shutil.disk_usage(existing)
    if mountinfo is None:
        try:
            mountinfo = Path('/proc/self/mountinfo').read_text()
        except OSError:
            mountinfo = ''
    matches = []
    decode = lambda value: re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
    for line in mountinfo.splitlines():
        try:
            left, right = line.split(' - ', 1)
            fields, fs = left.split(), right.split()
            mount = Path(decode(fields[4]))
            if existing == mount or mount in existing.parents:
                matches.append((len(mount.parts), mount, decode(fs[1]), decode(fields[3]), fs[0]))
        except (ValueError, IndexError):
            continue
    _, mount, device, bind_root, filesystem = max(matches, default=(0, Path('/'), None, None, None), key=lambda x:x[0])
    return dict(path=str(logical), exists=logical.exists(), measuredPath=str(existing),
                device=device, mount=str(mount), bindRoot=bind_root, filesystem=filesystem,
                deviceId=os.stat(existing).st_dev, totalBytes=usage.total, usedBytes=usage.used,
                freeBytes=usage.free, usedPct=round(100*usage.used/usage.total, 2) if usage.total else None,
                sizeBytes=None, sizeStatus='NOT_SCANNED_NO_RECURSIVE_WALK')


def research_storage(registry, repo):
    paths = dict(registry=registry, cache=Path(registry)/'cache',
                 export=os.getenv('RESEARCH_EXPORT_DIR', '/home/ubuntu/research-exports'),
                 results=os.getenv('EVOLUTION_RESULTS_DIR', '/home/ubuntu/evolution-results'),
                 toss_1m=Path(repo)/'data/toss_1m', toss_ticks=Path(repo)/'data/toss_ticks')
    result = {}
    for name, path in paths.items():
        try:
            result[name] = filesystem_status(path)
        except OSError as exc:
            result[name] = dict(available=False, code=type(exc).__name__)
    return result
