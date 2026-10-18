"""One-shot V083 worker. Runs only within a reviewed Spot image/bundle.

The launcher cloud-init calls this, bounded by GNU timeout, then always
shuts down the instance. This module never submits real-money trades.
Requires pre-staged, audited data and a pinned Python virtual environment.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import hashlib

SAFE_JOB_PREFIX = "v083-"
SAFE_DATE_START = "2022-01-01"
SAFE_DATE_END = "2023-12-31"


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def execute(cmd, *, cwd, log_file, status_file, stage):
    with Path(log_file).open("ab") as log:
        print(json.dumps({"stage":stage,"status":"running","at":now()}),file=log,flush=True)
        result=subprocess.run(cmd,cwd=str(cwd),stdin=subprocess.DEVNULL,stdout=log,
                              stderr=subprocess.STDOUT,check=False)
        print(json.dumps({"stage":stage,"status":"completed" if result.returncode==0 else "failed",
                          "exit_code":result.returncode,"at":now()}),file=log,flush=True)
    Path(status_file).write_text(json.dumps({"stage":stage,"phase":"completed" if result.returncode==0 else "failed",
                                             "code":result.returncode,"finalOosGate":"CLOSED",
                                             "at":now()},indent=2)+"\n")
    if result.returncode:
        raise ValueError(f"SPOT_WORKER_STAGE_{stage}_FAILED_{result.returncode}")


def run(*, workspace, job_id, population, source_commit):
    if not job_id.startswith(SAFE_JOB_PREFIX) or type(population) is not int or not 2<=population<=50:
        raise ValueError("SPOT_JOB_OR_POPULATION_INVALID")
    if len(source_commit)!=40 or any(c not in "0123456789abcdef" for c in source_commit):
        raise ValueError("SPOT_SOURCE_COMMIT_INVALID")
    workspace=Path(workspace).resolve()
    bundle=workspace/"bundle"
    repo=bundle/"source"
    plan=bundle/"selection"/"selection_plan.json"
    code=repo/"research/evolution/run_v083.py"
    calibration=repo/"research/evolution/calibrate_v083.py"
    info=bundle/"bundle_manifest.json"
    output=workspace/"output"
    output.mkdir(mode=0o700,parents=True,exist_ok=True)
    if any(not p.is_file() or p.is_symlink() for p in (plan,code,calibration,info)):
        raise ValueError("SPOT_PINNED_BUNDLE_LAYOUT_REQUIRED")
    meta=json.loads(info.read_text())
    if (meta.get("job_id")!=job_id or meta.get("source_commit")!=source_commit or
            meta.get("population")!=population or meta.get("finalOosGate")!="CLOSED" or
            meta.get("train_dates")!=[SAFE_DATE_START,SAFE_DATE_END]):
        raise ValueError("SPOT_MANIFEST_MISMATCH_OR_FINAL_OOS_BLOCKED")
    # Payload must provide the known selection plan and two audited directories.
    # Rebased plans are verified with the same engine function as the live runner.
    sys.path.insert(0,str(repo))
    from research.evolution.available_data import verify_plan
    verified, paths=verify_plan(plan)
    if verified["finalOosGate"]!="CLOSED" or not paths or not all(
        "2022-01-01" <= row["sessionDate"] < "2026-01-01" for row in verified["sessions"]
    ):
        raise ValueError("SPOT_TRAINING_DATA_GATE_VIOLATION")
    for file, expected in meta.get("source_files",{}).items():
        relative=Path(file)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("SPOT_UNSAFE_SOURCE_PATH")
        actual=repo/relative
        if not actual.is_file() or sha256(actual)!=expected:
            raise ValueError("SPOT_SOURCE_FILE_HASH_MISMATCH")
    if not meta.get("source_files") or "research/evolution/run_v083.py" not in meta["source_files"]:
        raise ValueError("SPOT_ENGINE_HASH_REQUIRED")
    python=Path(sys.executable)
    base=[str(python)]
    try:
        execute(base+[str(calibration),
                "--selection-plan",str(plan),
                "--population",str(population),
                "--out",str(output/"capacity"),"--data-origin","toss",
                "--seed","42"],cwd=repo,log_file=output/"calibrate.log",
                status_file=output/"calibrate_status.json",stage="calibrate")
        capacity=output/"capacity"/"capacity.json"
        if not capacity.is_file() or json.loads(capacity.read_text()).get("status")!="MEASURED_SAFE":
            raise ValueError("SPOT_CAPACITY_CERTIFICATE_NOT_SAFE")
        execute(base+[str(code),
                "--selection-plan",str(plan),
                "--population",str(population),
                "--from-date",SAFE_DATE_START,"--to-date",SAFE_DATE_END,
                "--capacity-report",str(capacity),"--data-origin","toss",
                "--results-dir",str(output/"train"),"--seed","42"],
                cwd=repo,log_file=output/"train.log",
                status_file=output/"train_status.json",stage="train")
        (output/"final_status.json").write_text(json.dumps({
            "job_id":job_id,"phase":"completed","finalOosGate":"CLOSED","at":now()
        },indent=2)+"\n")
    except BaseException as err:
        (output/"final_status.json").write_text(json.dumps({
            "job_id":job_id,"phase":"failed","error":str(err)[:250],
            "finalOosGate":"CLOSED","at":now()
        },indent=2)+"\n")
        raise


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace",type=Path,required=True)
    p.add_argument("--job-id",required=True)
    p.add_argument("--population",type=int,required=True)
    p.add_argument("--source-commit",required=True)
    a=p.parse_args(argv)
    run(workspace=a.workspace,job_id=a.job_id,population=a.population,source_commit=a.source_commit)


if __name__=="__main__":
    main()
