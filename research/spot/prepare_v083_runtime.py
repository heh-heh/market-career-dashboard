"""Build a small reviewed code-only runtime archive OFFLINE; never upload/launch.

Reuses the existing worker-source allowlist, not raw historical data. The original
versioned bundle is read-only and need not be rebuilt when worker code changes.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.spot.prepare_v083_spot_bundle import files_for_worker, json_bytes
from research.spot.worker_storage import digest


def build(*, repo, output):
    repo = Path(repo).resolve()
    if Path(output).is_symlink(): raise ValueError("SPOT_RUNTIME_ARCHIVE_ALREADY_EXISTS")
    output = Path(output).resolve()
    if output.exists() or output.is_symlink(): raise ValueError('SPOT_RUNTIME_ARCHIVE_ALREADY_EXISTS')
    commit = subprocess.check_output(['git','rev-parse','HEAD'], cwd=repo, text=True).strip()
    dirty = subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=repo,text=True).strip()
    if not re.fullmatch(r'[a-f0-9]{40}',commit) or dirty: raise ValueError('SPOT_RUNTIME_SOURCE_NOT_CLEAN_AND_PINNED')
    files = files_for_worker(repo)
    tracked = set(subprocess.check_output(["git","ls-files","-z"],cwd=repo).decode().split("\0"))
    if any(p.relative_to(repo).as_posix() not in tracked for p in files):
        raise ValueError("SPOT_RUNTIME_UNTRACKED_SOURCE_BLOCKED")
    if len(files)>10000 or sum(p.stat().st_size for p in files)>128*1024**2:
        raise ValueError('SPOT_RUNTIME_TOO_LARGE')
    hashes = {'source/'+p.relative_to(repo).as_posix():digest(p) for p in files}
    manifest = {'version':1,'source_commit':commit,'payload_files':hashes,'researchOnly':True,'finalOosGate':'CLOSED'}
    output.parent.mkdir(parents=True,exist_ok=True)
    partial=output.with_name(output.name+'.partial')
    with tarfile.open(partial,'x:gz') as tar:
        for file in files:
            tar.add(file,arcname='runtime/source/'+file.relative_to(repo).as_posix(),recursive=False)
        raw=json_bytes(manifest);item=tarfile.TarInfo('runtime/runtime_manifest.json')
        item.size=len(raw);item.mode=0o600;item.mtime=0
        tar.addfile(item,io.BytesIO(raw))
    for file in files:
        if digest(file)!=hashes['source/'+file.relative_to(repo).as_posix()]:
            raise ValueError('SPOT_RUNTIME_SOURCE_CHANGED_DURING_PACKAGING')
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()!=commit:
        raise ValueError('SPOT_RUNTIME_COMMIT_CHANGED_DURING_PACKAGING')
    os.link(partial,output);partial.unlink()
    return {'archive':str(output),'sha256':digest(output),'bytes':output.stat().st_size,
            'source_commit':commit,'payloadFiles':len(files),'researchOnly':True,'finalOosGate':'CLOSED',
            'nextStep':'Separate approved immutable S3 upload; no AWS operation performed'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv)
    print(json.dumps(build(repo=args.repo,output=args.out),indent=2))


if __name__=='__main__':main()
