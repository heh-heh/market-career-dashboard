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
[[ -S "$TOSS_TOKEN_SOCKET" ]] || { echo "TOKEN_BROKER_NOT_READY"; exit 1; }
[[ -f "$BASE/private/symbols.json" ]] || { echo "SYMBOLS_FILE_MISSING"; exit 1; }
cd "$BASE/current"
exec "$BASE/venv/bin/python" research_collection_scheduler.py \
 --state-dir "$RESEARCH_COLLECTION_STATE_DIR" \
 --data-dir "$DATA/historical" \
 --tick-dir "$DATA/ticks" \
 --symbols-file "$BASE/private/symbols.json" \
 --preopen-seconds 90
