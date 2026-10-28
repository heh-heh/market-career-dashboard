#!/usr/bin/env bash
# One-time low-volume backfill. No orders, no daemon, no public API, no tick stream.
set -Eeuo pipefail
umask 077

BASE=/srv/apps/_deploy/stock-collection
DATA=/srv/ai-data/stock-collection
RELEASE=$(readlink -f "$BASE/current")
PY="$BASE/venv/bin/python"
BROKER="$BASE/private/toss-broker.json"
SOCKET="$DATA/runtime/toss-broker.sock"
OUT="$DATA/historical-smoke"
[[ "$(id -un)" == "heh-heh" ]] || { echo "ERROR=RUN_AS_HEH_HEH"; exit 1; }
[[ -n "$RELEASE" && -f "$RELEASE/toss_token_broker.py" && -x "$PY" ]] || { echo "ERROR=SETUP_REQUIRED"; exit 1; }
[[ -s "$BROKER" && ! -L "$BROKER" ]] || { echo "ERROR=BROKER_CREDENTIALS_MISSING"; exit 1; }
mkdir -p "$OUT" "$DATA/runtime"
chmod 700 "$DATA/runtime"
export TOSS_RATE_LIMIT_FILE="$DATA/runtime/toss-api-rate.lock"
export RESEARCH_COLLECTION_STATE_DIR="$DATA/runtime/collection"
export RESEARCH_COLLECTION_WORKER_LOCK="$DATA/runtime/collection/worker.lock"
unset TOSS_TOKEN_SOCKET || true

cd "$RELEASE"
"$PY" toss_token_broker.py \
    --socket "$SOCKET" \
    --secrets "$BROKER" \
    --group-gid "$(id -g)" \
    --allow-uid "$(id -u)" \
    >"$DATA/runtime/toss-broker.log" 2>&1 &
BROKER_PID=$!
cleanup() {
  kill "$BROKER_PID" 2>/dev/null || true
  wait "$BROKER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for i in {1..30}; do
  [[ -S "$SOCKET" ]] && break
  if ! kill -0 "$BROKER_PID" 2>/dev/null; then
    echo "ERROR=BROKER_EXITED"
    exit 1
  fi
  sleep 0.2
done
[[ -S "$SOCKET" ]] || { echo "ERROR=BROKER_SOCKET_NOT_READY"; exit 1; }
export TOSS_TOKEN_SOCKET="$SOCKET"

# Toss candle timestamps use +09:00. Keep the collector's lexical bounds in the
# same fixed timezone; otherwise its existing string comparisons can drop rows.
SINCE="2026-10-01T00:00:00+09:00"
UNTIL=$(date -u -d '+9 hours' '+%Y-%m-%dT%H:%M:%S+09:00')
echo "STARTING_RESEARCH_BACKFILL=NVDA,SOXL,SOXS,SPY"
echo "PAGES_PER_SYMBOL=3"
echo "SINCE=$SINCE"
echo "UNTIL=$UNTIL"
echo "DATA_DIR=$OUT"

"$PY" scripts/collect_toss_1m.py \
  --symbols NVDA,SOXL,SOXS,SPY \
  --since "$SINCE" \
  --until "$UNTIL" \
  --pages 3 \
  --sleep 0.5 \
  --batch-pages 150 \
  --batch-sleep 10 \
  --out-dir "$OUT"

"$PY" - "$OUT" <<'PY'
import sqlite3, sys
from pathlib import Path
root=Path(sys.argv[1])
all_good=True
for symbol in ("NVDA","SOXL","SOXS","SPY"):
    db=root/(symbol+".sqlite3")
    try:
        with sqlite3.connect(f"file:{db}?mode=ro",uri=True) as con:
            count,lo,hi=con.execute("SELECT COUNT(*),MIN(timestamp),MAX(timestamp) FROM candles").fetchone()
    except sqlite3.Error as e:
        print(f"{symbol}: sqlite error {type(e).__name__}")
        all_good=False
        continue
    print(f"{symbol}: rows={count}, first={lo}, last={hi}")
    if not count:all_good=False
print("SMOKE_RESULT="+("ROWS_STORED" if all_good else "REVIEW_REQUIRED"))
PY
echo "TICK_COLLECTOR_STARTED=false"
echo "PERIODIC_COLLECTOR_STARTED=false"
