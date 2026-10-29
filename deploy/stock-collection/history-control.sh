#!/usr/bin/env bash
# Manage the 50-symbol historical permission only; never orders, broker credentials,
# or modifications to the shared tick scheduler's master pause state.
set -Eeuo pipefail
umask 077
BASE=/srv/apps/_deploy/stock-collection
ROOT=/srv/ai-data/stock-collection/runtime/collection
HISTORY="$ROOT/historical-control.json"
MASTER="$ROOT/control.json"
STATE="$ROOT/status.json"
ACTION=${1:-status}
[[ "$(id -un)" == heh-heh ]] || { echo "ERROR=RUN_AS_HEH_HEH"; exit 1; }

case "$ACTION" in
  status)
    python3 - "$HISTORY" "$MASTER" "$STATE" <<'PY'
import json,sys
from pathlib import Path
for tag,raw in zip(("HISTORY","MASTER","SCHEDULER"),sys.argv[1:]):
    path=Path(raw)
    try:
        if path.is_symlink():raise ValueError("SYMLINK_REJECTED")
        data=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data,dict):raise ValueError("NOT_OBJECT")
        allow={"HISTORY":("enabled","reason","updatedAt"),
               "MASTER":("paused","reason","updatedAt"),
               "SCHEDULER":("mode","collector","reason","calendarConfirmed","workerFailures","updatedAt")}[tag]
        print(tag+"="+json.dumps({k:data[k] for k in allow if k in data},ensure_ascii=False))
    except (OSError,ValueError) as e:
        print(tag+"_UNAVAILABLE="+type(e).__name__)
PY
    echo "BROKER_SERVICE=$(systemctl is-active market-stock-token-broker.service || true)"
    echo "COLLECTION_SERVICE=$(systemctl is-active market-stock-collection.service || true)"
    ;;
  resume|pause)
    if [[ "$ACTION" == resume ]]; then
      [[ "$(systemctl is-active market-stock-token-broker.service)" == active ]] || { echo "ERROR=TOKEN_BROKER_NOT_ACTIVE"; exit 1; }
      [[ "$(systemctl is-active market-stock-collection.service)" == active ]] || { echo "ERROR=COLLECTION_SCHEDULER_NOT_ACTIVE"; exit 1; }
      [[ -f "$BASE/current/research_collection_scheduler.py" ]] || { echo "ERROR=SCHEDULER_RELEASE_NOT_FOUND"; exit 1; }
      grep -q 'from collector_policy import enabled, choose_worker' "$BASE/current/research_collection_scheduler.py" || {
        echo "ERROR=HISTORY_50_SAFETY_GATE_NOT_DEPLOYED"; exit 1;
      }
    fi
    python3 - "$HISTORY" "$ACTION" <<'PY'
import json,os,secrets,sys
from datetime import datetime,timezone
from pathlib import Path
p=Path(sys.argv[1]);action=sys.argv[2]
if p.is_symlink() or not p.is_file() or p.parent.is_symlink():
    raise SystemExit("ERROR=HISTORICAL_CONFIG_MISSING_OR_SYMLINK")
prior=json.loads(p.read_text(encoding="utf-8"))
if not isinstance(prior,dict) or type(prior.get("enabled")) is not bool:
    raise SystemExit("ERROR=HISTORICAL_CONFIG_INVALID")
data={"enabled":action=="resume",
      "reason":"CLI_HISTORY_RESUME" if action=="resume" else "CLI_HISTORY_PAUSE",
      "updatedAt":datetime.now(timezone.utc).isoformat()}
tmp=p.with_name(p.name+"."+secrets.token_hex(8)+".tmp")
fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
try:
    with os.fdopen(fd,"w",encoding="utf-8") as out:
        json.dump(data,out)
        out.flush();os.fsync(out.fileno())
    os.replace(tmp,p)
finally:
    tmp.unlink(missing_ok=True)
print("HISTORY_ENABLED="+str(data["enabled"]).lower())
print("NOTE=Master scheduler must also be unpaused, and market calendar authorized.")
PY
    ;;
  *) echo "USAGE: bash history-control.sh [status|resume|pause]"; exit 2 ;;
esac
