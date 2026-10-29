#!/usr/bin/env bash
# Dedicated real-time trade tick collection switch. Does not change the
# historical collector, shared scheduler permit, saved ticks or any orders.
set -Eeuo pipefail
umask 077
BASE=/srv/apps/_deploy/stock-collection
CONFIG=/srv/ai-data/stock-collection/runtime/collection/tick-control.json
SCHEDULER=/srv/ai-data/stock-collection/runtime/collection/status.json
[[ "$(id -un)" == "heh-heh" ]] || { echo "ERROR=RUN_AS_HEH_HEH"; exit 1; }
action=${1:-status}
case "$action" in
  status)
    python3 - "$CONFIG" "$SCHEDULER" <<'PY'
import json,sys
from pathlib import Path
for key,raw,allowed in (("TICK",sys.argv[1],("enabled","reason","updatedAt")),
                        ("SCHEDULER",sys.argv[2],("mode","collector","reason","updatedAt"))):
    p=Path(raw)
    try:
        if p.is_symlink():raise ValueError("SYMLINK_BLOCKED")
        data=json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data,dict):raise ValueError("NOT_AN_OBJECT")
        print(key+"="+json.dumps({k:data[k] for k in allowed if k in data},ensure_ascii=False))
    except (OSError,ValueError) as exc:
        print(key+"_UNAVAILABLE="+type(exc).__name__)
PY
    ;;
  pause|resume)
    [[ -f "$BASE/current/research_collection_scheduler.py" ]] || { echo "ERROR=COLLECTOR_RELEASE_MISSING"; exit 1; }
    grep -q 'from collector_policy import enabled, choose_worker' "$BASE/current/research_collection_scheduler.py" || {
      echo "ERROR=TICK_PAUSE_GATE_NOT_INSTALLED"; exit 1;
    }
    python3 - "$CONFIG" "$action" <<'PY'
import json,os,secrets,sys
from datetime import datetime,timezone
from pathlib import Path
p=Path(sys.argv[1]);action=sys.argv[2]
if p.is_symlink() or not p.parent.is_dir() or p.parent.is_symlink():
    raise SystemExit("ERROR=TICK_CONTROL_UNSAFE")
if p.exists():
    state=json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(state,dict) or type(state.get("enabled")) is not bool:
        raise SystemExit("ERROR=TICK_CONTROL_INVALID")
data={"enabled":action=="resume",
      "reason":"CLI_TICKS_RESUME" if action=="resume" else "CLI_TICKS_PAUSE",
      "updatedAt":datetime.now(timezone.utc).isoformat()}
temporary=p.with_name(p.name+"."+secrets.token_hex(8)+".tmp")
fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
try:
    with os.fdopen(fd,"w",encoding="utf-8") as out:
        json.dump(data,out);out.flush();os.fsync(out.fileno())
    os.replace(temporary,p)
finally:
    temporary.unlink(missing_ok=True)
print("TICKS_ENABLED="+str(data["enabled"]).lower())
PY
    ;;
  *) echo "USAGE: bash tick-control.sh [status|pause|resume]"; exit 2 ;;
esac
