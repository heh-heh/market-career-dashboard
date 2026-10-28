#!/usr/bin/env bash
# Stage and install market-data-only services; first boot remains PAUSED.
set -Eeuo pipefail
umask 077
REPO=/srv/apps/market-career-dashboard
BASE=/srv/apps/_deploy/stock-collection
DATA=/srv/ai-data/stock-collection
[[ "$(id -un)" == "heh-heh" ]] || { echo "ERROR=RUN_AS_HEH_HEH"; exit 1; }
[[ -f "$BASE/current/research_collection_scheduler.py" ]] || { echo "ERROR=RUN_SETUP_FIRST"; exit 1; }
[[ -f "$BASE/private/toss-broker.json" ]] || { echo "ERROR=BROKER_CONFIG_MISSING"; exit 1; }
for tool in git sudo python3 curl; do command -v "$tool" >/dev/null || { echo "ERROR=MISSING_TOOL:$tool"; exit 1; }; done
git -C "$REPO" fetch origin main
REV=$(git -C "$REPO" rev-parse refs/remotes/origin/main)
mkdir -p "$BASE/bin" "$BASE/private" "$DATA/runtime/collection" "$DATA/historical" "$DATA/ticks"
chmod 700 "$BASE/private" "$DATA/runtime/collection" "$DATA/historical" "$DATA/ticks"
for name in run-broker.sh run-scheduler.sh; do
  git -C "$REPO" show "$REV:deploy/stock-collection/$name" > "$BASE/bin/$name.new"
  bash -n "$BASE/bin/$name.new"
  mv -f "$BASE/bin/$name.new" "$BASE/bin/$name"
  chmod 700 "$BASE/bin/$name"
done
for name in market-stock-token-broker.service market-stock-collection.service; do
  git -C "$REPO" show "$REV:deploy/stock-collection/$name" > "$BASE/bin/$name"
done
# Preserve the operator's existing symbol list and pause state on reinstallation.
python3 - "$BASE/private/symbols.json" "$DATA/runtime/collection/control.json" <<'PY'
import json, os, sys
from pathlib import Path
symbols, control = (Path(s) for s in sys.argv[1:])
for path, content in (
    (symbols, {"symbols":["NVDA","SOXL","SOXS","SPY"]}),
    (control, {"paused":True,"reason":"INITIAL_INSTALL_DEFAULT_PAUSED"})):
    if path.is_symlink():
        raise SystemExit("ERROR=UNSAFE_SYMLINK:"+path.name)
    if not path.exists():
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,"w") as f: json.dump(content,f)
    os.chmod(path,0o600)
print("INITIAL_SYMBOLS_PRESERVED=true")
print("PAUSE_CONTROL_PRESERVED=true")
PY
# No existing services from this new install, and no change to dashboard/chess.
sudo install -m 0644 "$BASE/bin/market-stock-token-broker.service" /etc/systemd/system/
sudo install -m 0644 "$BASE/bin/market-stock-collection.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable market-stock-token-broker.service market-stock-collection.service >/dev/null
sudo systemctl restart market-stock-token-broker.service
sudo systemctl restart market-stock-collection.service
for i in {1..12}; do
  if [[ "$(systemctl is-active market-stock-token-broker.service)" == "active" && "$(systemctl is-active market-stock-collection.service)" == "active" ]]; then
    break
  fi
  sleep 1
done
echo "BROKER_SERVICE=$(systemctl is-active market-stock-token-broker.service || true)"
echo "COLLECTION_SERVICE=$(systemctl is-active market-stock-collection.service || true)"
python3 - "$DATA/runtime/collection/control.json" <<'PY'
import json,sys
from pathlib import Path
d=json.loads(Path(sys.argv[1]).read_text())
print("COLLECTION_REQUESTED="+("PAUSED" if d.get("paused",True) else "ENABLED"))
PY
echo "DATA=$DATA"
echo "SCHEDULER_INSTALL_COMPLETE=true"
