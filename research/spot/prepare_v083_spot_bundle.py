"""Create a transportable, hash-pinned V083 train-only bundle OFFLINE.

This does not upload files, create resources, launch instances or use AWS keys.
It copies only audited provider files and necessary research code; if a source
file contains timestamped 2026 rows, fail closed rather than moving Final OOS.

Destination paths are fixed by the one-shot launcher at /opt/v083-one-shot.
"""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

from research.evolution.available_data import verify_plan
from research.backtest_v4_strategies import file_sha256

DEST = Path("/opt/v083-one-shot/bundle")
MAX_RAW_GIB = 64
MAX_BOTS = 50


def json_bytes(obj):
    return (json.dumps(obj,indent=2,ensure_ascii=False,sort_keys=True)+"\n").encode("utf-8")


def add_data(tar, name, raw):
    b=json_bytes(raw)
    item=tarfile.TarInfo(name)
    item.size=len(b);item.mode=0o600;item.mtime=0
    tar.addfile(item,io.BytesIO(b))
    return hashlib.sha256(b).hexdigest()


def scan_before_oos(file):
    file=Path(file)
    opener=gzip.open if file.name.endswith(".gz") else open
    with opener(file,"rt",encoding="utf-8",newline="") as handle:
        reader=csv.DictReader(handle)
        if "timestamp" not in (reader.fieldnames or []):
            raise ValueError("SPOT_SOURCE_TIMESTAMP_COLUMN_MISSING")
        for row in reader:
            stamp=row["timestamp"]
            if not stamp or len(stamp)<10 or not re.match(r"\d{4}-\d{2}-\d{2}",stamp):
                raise ValueError("SPOT_UNKNOWN_TIMESTAMP")
            if stamp[:10]>="2026-01-01":
                raise ValueError("SPOT_2026_SOURCE_ROW_BLOCKED: "+file.name)
    return True


def files_for_worker(repo):
    # Intentionally excludes any server, live-trading or autonomous operation
    # modules; expands only if reviewed imports require additional research code.
    files = [
        f for f in (repo/"research").glob("*.py") if not f.name.startswith("test_")
    ]
    files += list((repo/"research/evolution").glob("*.py"))
    files += list((repo/"research/evolution").glob("*.json"))
    files += [repo/"research/spot/v083_spot_worker.py"]
    valid=[]
    for f in files:
        if f.is_symlink() or not f.is_file():
            raise ValueError("SPOT_SOURCE_MISSING_OR_SYMLINKED: "+str(f))
        valid.append(f)
    return sorted(set(valid))


def build(*, repo, selection_plan, job_id, population, output):
    repo=Path(repo).resolve()
    selection_plan=Path(selection_plan).resolve()
    output=Path(output).resolve()
    if not re.fullmatch("v083-[a-f0-9]{16}",job_id) or not 2<=population<=50:
        raise ValueError("SPOT_JOB_OR_POPULATION_INVALID")
    if output.exists() or output.is_symlink():
        raise ValueError("SPOT_ARCHIVE_ALREADY_EXISTS")
    plan,paths=verify_plan(selection_plan)
    if plan["finalOosGate"]!="CLOSED" or plan["fromDate"]>"2022-01-01" or plan["toDate"]<"2023-12-31" or plan["toDate"]>="2026-01-01":
        raise ValueError("SPOT_TRAIN_SELECTION_DATE_GATE")
    audit_dir=Path(plan["auditDir"]).resolve()
    source_json=json.loads((audit_dir/"audit.json").read_text())
    if source_json["finalOosGate"]!="CLOSED":
        raise ValueError("SPOT_AUDIT_GATE")
    if any(not f.is_file() or f.is_symlink() for f in paths.values()):
        raise ValueError("SPOT_PROVIDER_SOURCE_INVALID")
    total=sum(f.stat().st_size for f in paths.values())
    if total > MAX_RAW_GIB*1024**3:
        raise ValueError("SPOT_DATA_EXCEEDS_STAGING_LIMIT_64_GIB")
    for f in paths.values():
        scan_before_oos(f)
    commit=subprocess.run(["git","rev-parse","HEAD"],cwd=str(repo),
                          capture_output=True,text=True,check=True).stdout.strip()
    dirty=subprocess.run(["git","status","--porcelain","--untracked-files=no"],
                         cwd=str(repo),capture_output=True,text=True,check=True).stdout.strip()
    if not re.fullmatch("[a-f0-9]{40}",commit) or dirty:
        raise ValueError("SPOT_SOURCE_NOT_CLEAN_AND_PINNED")
    source_files=files_for_worker(repo)
    source_hashes={str(f.relative_to(repo)):file_sha256(f) for f in source_files}
    if "research/evolution/run_v083.py" not in source_hashes:
        raise ValueError("SPOT_ENGINE_MISSING")
    rebased_audit={**source_json,"dataRoot":str(DEST/"data")}
    rebased_plan={**plan,"dataRoot":str(DEST/"data"),"auditDir":str(DEST/"audit")}
    audit_bytes=json_bytes(rebased_audit)
    rebased_plan["auditSha256"]=hashlib.sha256(audit_bytes).hexdigest()
    manifest={"job_id":job_id,"source_commit":commit,"population":population,
              "finalOosGate":"CLOSED","train_dates":["2022-01-01","2023-12-31"],
              "source_files":source_hashes,
              "selected_provider_files":{p.name:file_sha256(p) for p in paths.values()},
              "selection_plan_sha256":hashlib.sha256(json_bytes(rebased_plan)).hexdigest()}
    output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(output,mode="w:gz") as t:
        for f in source_files:
            t.add(f,arcname="bundle/source/"+str(f.relative_to(repo)),recursive=False)
        for f in paths.values():
            t.add(f,arcname="bundle/data/"+f.name,recursive=False)
        for name in ("sessions.jsonl","symbol_sessions.jsonl"):
            artifact=audit_dir/name
            if artifact.is_symlink() or file_sha256(artifact)!=source_json["files"][name]["sha256"]:
                raise ValueError("SPOT_AUDIT_EVIDENCE_CHANGED")
            t.add(artifact,arcname="bundle/audit/"+name,recursive=False)
        for archive_name,byte_content in (
            ("bundle/audit/audit.json",audit_bytes),
            ("bundle/selection/selection_plan.json",json_bytes(rebased_plan)),
            ("bundle/bundle_manifest.json",json_bytes(manifest)),
        ):
            info=tarfile.TarInfo(archive_name);info.size=len(byte_content);info.mode=0o600;info.mtime=0
            t.addfile(info,io.BytesIO(byte_content))
    return {"archive":str(output),"bytes":output.stat().st_size,
            "sha256":file_sha256(output),"source_commit":commit,
            "population":population,"provider_files":len(paths),
            "final_oos_gate":"CLOSED","next_step":"Manual immutable S3 versioned upload; no automatic AWS actions"}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo",type=Path,required=True)
    p.add_argument("--selection-plan",type=Path,required=True)
    p.add_argument("--job-id",required=True)
    p.add_argument("--population",type=int,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args(argv)
    print(json.dumps(build(repo=a.repo,selection_plan=a.selection_plan,
                           job_id=a.job_id,population=a.population,output=a.out),indent=2))


if __name__=="__main__":
    main()
