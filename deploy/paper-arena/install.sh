#!/usr/bin/env bash
# Isolated paper research service; never modifies stock collector or trading apps.
set -Eeuo pipefail
umask 077
REPO=/srv/apps/market-career-dashboard
BASE=/srv/apps/_deploy/paper-arena
DATA=/srv/ai-data/paper-arena
UNIT=market-paper-arena.service

[[ "$(id -un)" == "heh-heh" ]] || { echo "ERROR=RUN_AS_HEH_HEH"; exit 1; }
[[ -d /srv/ai-data/stock-collection/ticks ]] || { echo "ERROR=TICK_COLLECTOR_STORAGE_MISSING"; exit 1; }
git -C "$REPO" fetch origin main
REV=$(git -C "$REPO" rev-parse refs/remotes/origin/main)
RELEASE="$BASE/releases/$REV"
mkdir -p "$RELEASE" "$DATA"
chmod 700 "$DATA"
git -C "$REPO" show "$REV:deploy/paper-arena/paper_arena.py" > "$RELEASE/paper_arena.py"
git -C "$REPO" show "$REV:deploy/paper-arena/test_paper_arena.py" > "$RELEASE/test_paper_arena.py"
git -C "$REPO" show "$REV:deploy/paper-arena/paper-arena.service" > "$RELEASE/paper-arena.service"
python3 -m py_compile "$RELEASE/paper_arena.py"
(cd "$RELEASE" && python3 test_paper_arena.py)
[[ ! -L "$DATA/control.json" ]] || { echo "ERROR=CONTROL_SYMLINK_REJECTED"; exit 1; }
[[ ! -L "$DATA/ledger.sqlite3" ]] || { echo "ERROR=DATABASE_SYMLINK_REJECTED"; exit 1; }
# First install never trades: admin or CLI resume must be an explicit action.
if [[ ! -f "$DATA/control.json" ]]; then
  printf '{"paused":true,"reason":"FIRST_INSTALL_PAUSED"}\n' > "$DATA/control.json"
  chmod 600 "$DATA/control.json"
fi
python3 "$RELEASE/paper_arena.py" --once
OLD=""
[[ ! -L "$BASE/current" ]] || OLD=$(readlink "$BASE/current")
ln -sfn "$RELEASE" "$BASE/current.new"
mv -Tf "$BASE/current.new" "$BASE/current"

sudo install -m 0644 "$RELEASE/paper-arena.service" "/etc/systemd/system/$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT" >/dev/null
sudo systemctl restart "$UNIT"
sleep 1
STATE=$(systemctl is-active "$UNIT" || true)
echo "PAPER_SERVICE=$STATE"
if [[ "$STATE" != active ]]; then
  echo "ERROR=PAPER_SERVICE_NOT_ACTIVE"
  exit 1
fi
python3 - "$DATA/status.json" <<'PY'
import json,sys
from pathlib import Path
x=json.loads(Path(sys.argv[1]).read_text())
print("PAPER_STATE="+x.get("state","UNKNOWN"))
print("PAPER_BOTS="+str(x.get("bots","UNKNOWN")))
print("REAL_ORDERS_ENABLED="+str(x.get("realOrdersEnabled","UNKNOWN")).lower())
PY
echo "PAPER_ADMIN_DATA=$DATA"
echo "PAPER_INSTALL_COMPLETE=true"
