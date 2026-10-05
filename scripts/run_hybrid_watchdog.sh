#!/usr/bin/env bash
set -u
ROOT="/home/ubuntu/market-career-dashboard"
PY="$ROOT/.venv/bin/python"
COLLECTOR="$ROOT/scripts/collect_toss_1m.py"
BIN="$ROOT/bin/backtest_toss_v5"
LOG="/tmp/toss_hybrid.log"
STATE="$ROOT/data/toss_hybrid_watchdog.json"
SYMBOLS="NVDA AMD INTC SOXL SOXS TQQQ"
mkdir -p "$ROOT/data"
umask 077
FINALIZED=0

write_state() {
  local phase="$1" current="$2" attempt="$3" msg="$4"
  "$PY" - "$STATE" "$phase" "$current" "$attempt" "$msg" <<'PY'
import json, sys, time
from pathlib import Path
p=Path(sys.argv[1])
obj={"phase":sys.argv[2],"currentSymbol":sys.argv[3] or None,
     "attempt":int(sys.argv[4]),"message":sys.argv[5],"updatedAt":time.time()}
tmp=p.with_suffix(".tmp")
tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
tmp.replace(p)
PY
}
log() { printf '[WATCHDOG %s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$*" >> "$LOG"; }
trap 'if [ "$FINALIZED" -eq 0 ]; then write_state "stopped" "" 0 "watchdog stopped"; fi; log "watchdog stopped"' EXIT

cd "$ROOT" || exit 1
export HOME=/root
if [ -f "$ROOT/research/backtest_toss_v5_result.json" ]; then
  write_state "completed" "" 0 "existing backtest result found"
  log "existing result found; watchdog is idle"
  FINALIZED=1
  exit 0
fi
write_state "starting" "" 0 "hybrid watchdog starting"
log "starting hybrid collector/backtest watchdog"

while ! command -v g++ >/dev/null 2>&1; do
  write_state "building" "" 0 "g++ not installed"
  log "g++ not installed; retry in 60s"
  sleep 60
done

while ! bash "$ROOT/scripts/build_backtest_cpp.sh" >>"$LOG" 2>&1; do
  write_state "building" "" 0 "C++ build failed; retrying"
  log "C++ build failed; retry in 60s"
  sleep 60
done

for symbol in $SYMBOLS; do
  attempt=1
  while true; do
    write_state "collecting" "$symbol" "$attempt" "collecting $symbol"
    log "collecting $symbol (attempt $attempt/20)"
    if "$PY" "$COLLECTOR" --symbols "$symbol" --since "2021-01-01T00:00:00+00:00" >>"$LOG" 2>&1; then
      write_state "collecting" "$symbol" "$attempt" "$symbol collection completed"
      log "$symbol collection completed"
      break
    fi
    log "$symbol collection failed"
    if [ "$attempt" -ge 20 ]; then
      write_state "error" "$symbol" "$attempt" "$symbol exceeded retry limit"
      log "$symbol exceeded retry limit"
      exit 2
    fi
    sleep_for=$((15 * attempt))
    [ "$sleep_for" -gt 300 ] && sleep_for=300
    write_state "retrying" "$symbol" "$attempt" "$symbol failed; retry in ${sleep_for}s"
    log "$symbol retry scheduled in ${sleep_for}s"
    sleep "$sleep_for"
    attempt=$((attempt + 1))
  done
done

write_state "backtest" "" 0 "running C++ backtest"
log "all symbols collected; starting C++ backtest"
attempt=1
while ! "$BIN" --symbols "NVDA,AMD,INTC,SOXL,SOXS,TQQQ" --min-days 100 >>"$LOG" 2>&1; do
  write_state "retrying_backtest" "" "$attempt" "backtest failed; retrying"
  log "backtest failed; retry in 60s"
  sleep 60
  attempt=$((attempt + 1))
done
write_state "completed" "" "$attempt" "backtest completed"
log "backtest completed successfully"
