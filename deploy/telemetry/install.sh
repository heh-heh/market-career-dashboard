#!/usr/bin/env bash
# Host telemetry only. Does not modify data collection or expose admin control.
set -Eeuo pipefail
umask 077
REPO=/srv/apps/market-career-dashboard
BASE=/srv/apps/_deploy/server-telemetry
DATA=/srv/ai-data/market-dashboard
UNIT=market-server-telemetry.service
[[ "$(id -un)" == "heh-heh" ]] || { echo "ERROR=RUN_AS_HEH_HEH"; exit 1; }
[[ -d "$DATA" && ! -L "$DATA" ]] || { echo "ERROR=DASHBOARD_DATA_NOT_READY"; exit 1; }
git -C "$REPO" fetch origin main
REV=$(git -C "$REPO" rev-parse refs/remotes/origin/main)
RELEASE="$BASE/releases/$REV"
mkdir -p "$RELEASE"
git -C "$REPO" show "$REV:deploy/telemetry/collector.py" > "$RELEASE/collector.py"
git -C "$REPO" show "$REV:deploy/telemetry/market-server-telemetry.service" > "$RELEASE/market-server-telemetry.service"
git -C "$REPO" show "$REV:deploy/telemetry/test_collector.py" > "$RELEASE/test_collector.py"
python3 -m py_compile "$RELEASE/collector.py" "$RELEASE/test_collector.py"
(cd "$RELEASE" && python3 test_collector.py)
OLD=""
[[ ! -L "$BASE/current" ]] || OLD=$(readlink "$BASE/current")
ln -sfn "$RELEASE" "$BASE/current.new"
mv -Tf "$BASE/current.new" "$BASE/current"
sudo install -m 0644 "$RELEASE/market-server-telemetry.service" "/etc/systemd/system/$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT" >/dev/null
sudo systemctl restart "$UNIT"
sleep 2
test "$(systemctl is-active "$UNIT")" = active
echo "HOST_TELEMETRY_SERVICE=active"
echo "PUBLIC_FILE=$DATA/server-telemetry.json"
echo "NOTE=CPU_POWER_IS_NOT_SYSTEM_WALL_POWER"
