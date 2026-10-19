"""Generate secret-free bootstrap telemetry. No AWS clients on import."""
import json
import textwrap


def telemetry_shell(cfg):
    # Only allowlisted stage/error codes and exit metadata leave the instance.
    # Raw command output remains private on the disposable guest, never uploaded.
    return textwrap.dedent('''\
        stage=bootstrap_init
        error_code=NONE
        failed_line=0
        started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        finish() {
          rc=$1
          trap - EXIT ERR
          set +e
          if [ "$rc" -eq 0 ]; then phase=COMPLETED; else phase=FAILED; fi
          emit "$phase" "$rc"
          if command -v aws >/dev/null 2>&1; then
            printf '%s\\n' "$rc" > "$JOB/exit-code.txt"
            timeout 30 aws s3 cp "$JOB/exit-code.txt" "$OUTPUT_URI/exit-code.txt" --region "$AWS_DEFAULT_REGION" --sse AES256 --no-progress >/dev/null 2>&1
          fi
          sync
          shutdown -h now
          exit "$rc"
        }
        emit() {
          phase=$1
          code=$2
          observed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
          printf '{"schema":"V083_BOOTSTRAP_1","job_id":"%s","finalOosGate":"CLOSED","phase":"%s","stage":"%s","exitCode":%s,"errorCode":"%s","line":%s,"startedAt":"%s","observedAt":"%s","rawLogsExported":false}\\n' "$JOB_ID" "$phase" "$stage" "$code" "$error_code" "$failed_line" "$started_at" "$observed_at" > "$JOB/bootstrap-status.tmp"
          mv "$JOB/bootstrap-status.tmp" "$JOB/bootstrap-status.json"
          printf 'V083_BOOTSTRAP_DIAGNOSTIC '
          cat "$JOB/bootstrap-status.json"
          cat "$JOB/bootstrap-status.json" >> "$JOB/bootstrap-events.jsonl"
          if command -v aws >/dev/null 2>&1; then
            timeout 30 aws s3 cp "$JOB/bootstrap-status.json" "$OUTPUT_URI/bootstrap/status.json" --region "$AWS_DEFAULT_REGION" --sse AES256 --no-progress >/dev/null 2>&1 || true
          fi
        }
        infer_error() {
          error_code=STAGE_COMMAND_FAILED
          # Never copy a matching line: emit only fixed codes, no URLs/tokens.
          local log="$JOB/raw-$stage.log"
          if grep -Eq 'Unable to find a match|No match for argument' "$log"; then error_code=PACKAGE_NOT_AVAILABLE
          elif grep -q 'No matching distribution found' "$log"; then error_code=PIP_NO_MATCHING_DISTRIBUTION
          elif grep -Eq 'AccessDenied|Forbidden' "$log"; then error_code=ACCESS_DENIED
          elif grep -q 'NoSuchVersion' "$log"; then error_code=S3_VERSION_MISSING
          elif grep -q 'NoSuchKey' "$log"; then error_code=S3_OBJECT_MISSING
          elif grep -Eq 'Could not resolve|Temporary failure in name resolution' "$log"; then error_code=DNS_RESOLUTION_FAILED
          elif grep -Eq 'timed out|ReadTimeout' "$log"; then error_code=NETWORK_TIMEOUT
          elif grep -q 'ModuleNotFoundError' "$log"; then error_code=PYTHON_MODULE_MISSING
          elif grep -q 'TypeError' "$log"; then error_code=PYTHON_TYPE_ERROR
          fi
          for known in SPOT_MANIFEST_MISMATCH_OR_FINAL_OOS_BLOCKED SPOT_REVIEWED_BUNDLE_V2_REQUIRED SPOT_BUNDLE_PAYLOAD_HASH_MISMATCH SPOT_SELECTION_PLAN_HASH_MISMATCH SPOT_PINNED_BUNDLE_LAYOUT_REQUIRED SPOT_NEW_SOURCE_REQUIRES_SEPARATE_RUNTIME SPOT_RUNTIME_SOURCE_OR_GATE_MISMATCH SPOT_RUNTIME_ENGINE_REQUIRED SPOT_RUNTIME_MANIFEST_REQUIRED SPOT_RUNTIME_UNDECLARED_OR_MISSING_FILE SPOT_WORKER_STAGE_CALIBRATE_FAILED SPOT_WORKER_STAGE_TRAIN_FAILED SPOT_RUNTIME_PAYLOAD_HASH_MISMATCH SPOT_TRAINING_DATA_GATE_VIOLATION SPOT_SINGLE_CAPACITY_CERTIFICATE_REQUIRED SPOT_CAPACITY_CERTIFICATE_NOT_SAFE SPOT_UNSAFE_ARCHIVE SPOT_ARCHIVE_EXPANSION_LIMIT SPOT_ARCHIVE_DISK_RESERVE_REQUIRED; do
            if grep -Fq "$known" "$log"; then error_code=$known; break; fi
          done
          case "$stage" in
            bundle_hash|runtime_hash) error_code=SHA256_MISMATCH ;;
          esac
        }
        run_stage() {
          stage=$1
          shift
          error_code=NONE
          emit RUNNING 0
          if "$@" > "$JOB/raw-$stage.log" 2>&1; then
            emit RUNNING 0
          else
            rc=$?
            failed_line=${BASH_LINENO[0]:-$LINENO}
            infer_error
            exit "$rc"
          fi
        }
        trap 'finish $?' EXIT
        trap 'failed_line=$LINENO; error_code=BOOTSTRAP_COMMAND_FAILED' ERR
        emit RUNNING 0
        ''')


def extraction_python():
    return '''\
import pathlib,sys,tarfile,shutil
archive=pathlib.Path(sys.argv[1]);dest=pathlib.Path(sys.argv[2]).resolve();prefix=sys.argv[3]
if archive.stat().st_size > 24 * 1024**3: raise SystemExit('SPOT_BUNDLE_ARCHIVE_TOO_LARGE')
seen=set();total=0
with tarfile.open(archive,'r|gz') as tar:
    for m in tar:
        name=pathlib.PurePosixPath(m.name)
        if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0]!=prefix:
            raise SystemExit('SPOT_UNSAFE_ARCHIVE')
        if not(m.isdir() or m.isfile()) or str(name) in seen or m.size<0:
            raise SystemExit('SPOT_UNSAFE_ARCHIVE')
        seen.add(str(name));total+=m.size
        if len(seen)>200000 or total>30*1024**3:raise SystemExit('SPOT_ARCHIVE_EXPANSION_LIMIT')
        disk=shutil.disk_usage(dest)
        if disk.free-m.size<max(10*1024**3,disk.total//10):raise SystemExit('SPOT_ARCHIVE_DISK_RESERVE_REQUIRED')
        path=dest.joinpath(*name.parts)
        if path.exists() or path.is_symlink():raise SystemExit('SPOT_DUPLICATE_OR_EXISTING_ARCHIVE_ENTRY')
        if m.isdir():path.mkdir(parents=True,exist_ok=False)
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as src,path.open('xb') as out:shutil.copyfileobj(src,out,1024*1024)
'''
