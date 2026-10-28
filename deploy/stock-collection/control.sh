#!/usr/bin/env bash
# Market-data collection pause/resume; no broker orders or shell injection.
set -Eeuo pipefail
umask 077
DATA=/srv/ai-data/stock-collection
CONFIG="$DATA/runtime/collection/control.json"
STATUS="$DATA/runtime/collection/status.json"
[[ "$(id -un)" == "heh-heh" ]] || { echo "ERROR=RUN_AS_HEH_HEH"; exit 1; }
ACTION=${1:-status}
case "$ACTION" in
  status)
    python3 - "$CONFIG" "$STATUS" <<'PY'
import json,sys
from pathlib import Path
for name,raw_path in (("control",sys.argv[1]),("scheduler",sys.argv[2])):
    path=Path(raw_path)
    try:
        if path.is_symlink(): raise ValueError("SYMLINK_REJECTED")
        data=json.loads(path.read_text())
        if not isinstance(data,dict):raise ValueError("UNEXPECTED_STATE")
        allow={k:data[k] for k in ("paused","mode","collector","sessionDate","reason","updatedAt","calendarConfirmed","workerFailures") if k in data}
        print(name.upper()+"="+json.dumps(allow,ensure_ascii=False))
    except (OSError,ValueError) as e:
        print(name.upper()+"_UNAVAILABLE="+type(e).__name__)
PY
    echo "BROKER_SERVICE=$(systemctl is-active market-stock-token-broker.service || true)"
    echo "COLLECTION_SERVICE=$(systemctl is-active market-stock-collection.service || true)"
    ;;
  pause|resume)
    if [[ "$ACTION" == resume ]]; then
      [[ "$(systemctl is-active market-stock-token-broker.service)" == active ]] || { echo "ERROR=BROKER_SERVICE_NOT_ACTIVE"; exit 1; }
      [[ "$(systemctl is-active market-stock-collection.service)" == active ]] || { echo "ERROR=COLLECTION_SERVICE_NOT_ACTIVE"; exit 1; }
    fi
    python3 - "$CONFIG" "$ACTION" <<'PY'
import json,os,secrets,sys
from datetime import datetime,timezone
from pathlib import Path
p=Path(sys.argv[1]);action=sys.argv[2]
if p.is_symlink() or not p.parent.is_dir():raise SystemExit("INVALID_CONTROL_PATH")
value={"paused":action=="pause","updatedAt":datetime.now(timezone.utc).isoformat(),
       "reason":"USER_PAUSED" if action=="pause" else "USER_ENABLED"}
tmp=p.with_name(p.name+"."+secrets.token_hex(8)+".tmp")
fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
try:
  with os.fdopen(fd,"w") as f:
    json.dump(value,f);f.flush();os.fsync(f.fileno())
  os.replace(tmp,p)
finally:
  tmp.unlink(missing_ok=True)
print("COLLECTION_REQUEST="+action.upper())
print("CHILD_TRANSITION_MAY_TAKE_A_FEW_SECONDS=true")
PY
    ;;
  *) echo "USAGE: bash control.sh [status|pause|resume]"; exit 2;;
esac
