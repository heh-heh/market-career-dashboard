#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
DATA=/srv/ai-data/paper-arena
[[ "$(id -un)" == "heh-heh" ]] || { echo "ERROR=RUN_AS_HEH_HEH"; exit 1; }
ACTION=${1:-status}
case "$ACTION" in
 status)
   python3 - "$DATA/control.json" "$DATA/status.json" <<'PY'
import json,sys
from pathlib import Path
for label,raw in (("CONTROL",sys.argv[1]),("PAPER_STATUS",sys.argv[2])):
 try:
  data=json.loads(Path(raw).read_text())
  if label=="PAPER_STATUS":
   data={k:data.get(k) for k in ("state","bots","cashUsd","equityUsd",
        "returnPct","positionsCount","uniqueHoldings","tradeCount","updatedAt",
        "realOrdersEnabled")}
  print(label+"="+json.dumps(data,ensure_ascii=False))
 except (OSError,ValueError):print(label+"=UNAVAILABLE")
PY
   echo "PAPER_SERVICE=$(systemctl is-active market-paper-arena.service || true)"
   ;;
 pause|resume)
   if [[ "$ACTION" == resume && "$(systemctl is-active market-paper-arena.service)" != active ]]; then
     echo "ERROR=PAPER_SERVICE_NOT_ACTIVE"; exit 1
   fi
   python3 - "$DATA/control.json" "$ACTION" <<'PY'
import json,os,secrets,sys
from datetime import datetime,timezone
from pathlib import Path
p=Path(sys.argv[1]);action=sys.argv[2]
if p.is_symlink() or not p.parent.is_dir():raise SystemExit("ERROR=BAD_CONTROL_PATH")
tmp=p.with_name(p.name+"."+secrets.token_hex(8)+".tmp")
fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
try:
 with os.fdopen(fd,"w") as f:
  json.dump({"paused":action=="pause","reason":"CLI_"+action.upper(),
             "updatedAt":datetime.now(timezone.utc).isoformat()},f)
  f.flush();os.fsync(f.fileno())
 os.replace(tmp,p)
finally:tmp.unlink(missing_ok=True)
print("PAPER_ACTION="+action.upper())
PY
   ;;
 *) echo "USAGE: bash control.sh [status|pause|resume]"; exit 2 ;;
esac
