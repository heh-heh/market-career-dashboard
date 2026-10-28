#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE=/srv/apps/_deploy/stock-collection
DATA=/srv/ai-data/stock-collection
export TOSS_TOKEN_SOCKET="$DATA/runtime/toss-broker.sock"
export TOSS_RATE_LIMIT_FILE="$DATA/runtime/toss-api-rate.lock"
export RESEARCH_COLLECTION_STATE_DIR="$DATA/runtime/collection"
export RESEARCH_COLLECTION_WORKER_LOCK="$DATA/runtime/collection/worker.lock"
export COLLECTION_50_DIR="$DATA/historical"
export PYTHONDONTWRITEBYTECODE=1
# systemd After= orders process start, not the Unix socket's readiness.
# Wait briefly for the credential broker on boot rather than fail/restart.
BROKER_READY=false
for _ in {1..40}; do
  if [[ -S "$TOSS_TOKEN_SOCKET" ]]; then
    BROKER_READY=true
    break
  fi
  sleep 0.5
done
if ! $BROKER_READY; then
  echo "TOKEN_BROKER_NOT_READY_AFTER_20S"
  exit 1
fi
[[ -f "$BASE/private/symbols.json" ]] || { echo "SYMBOLS_FILE_MISSING"; exit 1; }
cd "$BASE/current"
exec "$BASE/venv/bin/python" research_collection_scheduler.py \
 --state-dir "$RESEARCH_COLLECTION_STATE_DIR" \
 --data-dir "$DATA/historical" \
 --tick-dir "$DATA/ticks" \
 --symbols-file "$BASE/private/symbols.json" \
 --preopen-seconds 90
