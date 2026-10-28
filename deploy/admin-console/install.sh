#!/usr/bin/env bash
# Install only the private dashboard operator console. No changes to public API, Funnel, or other services.
set -Eeuo pipefail
umask 077

REPO=/srv/apps/market-career-dashboard
BASE=/srv/apps/_deploy/dashboard/admin-console
UNIT=market-dashboard-admin-console.service
[[ "$(id -un)" == heh-heh ]] || { echo "RUN_AS_HEH_HEH"; exit 1; }
command -v git >/dev/null
command -v python3 >/dev/null
command -v sudo >/dev/null
command -v curl >/dev/null
[[ "$(git -C "$REPO" remote get-url origin)" == *heh-heh/market-career-dashboard* ]] || { echo "WRONG_REPO"; exit 1; }

git -C "$REPO" fetch origin main
REV=$(git -C "$REPO" rev-parse refs/remotes/origin/main)
RELEASE="$BASE/releases/$REV"
mkdir -p "$RELEASE"
git -C "$REPO" archive "$REV" deploy/admin-console | tar -xf - -C "$RELEASE"
SRC="$RELEASE/deploy/admin-console"
python3 -m py_compile "$SRC/console.py"
[[ -s "$SRC/index.html" && -s "$SRC/app.js" ]] || { echo "ASSETS_MISSING"; exit 1; }

OLD=""
[[ ! -L "$BASE/current" ]] || OLD=$(readlink "$BASE/current")
ln -sfn "$SRC" "$BASE/current.new"
mv -Tf "$BASE/current.new" "$BASE/current"

UNITPATH=/etc/systemd/system/$UNIT
if [[ -f "$UNITPATH" ]]; then
    sudo cp -p "$UNITPATH" "$BASE/service-before-$REV"
fi
sudo install -m 0644 "$SRC/admin-console.service" "$UNITPATH"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT" >/dev/null
if ! sudo systemctl restart "$UNIT"; then
    if [[ -n "$OLD" ]]; then ln -sfn "$OLD" "$BASE/current"; fi
    echo "ADMIN_SERVICE_START_FAILED"
    exit 1
fi

for attempt in {1..15}; do
    if curl -fsS --max-time 2 http://127.0.0.1:18085/api/health >/dev/null; then
        echo "ADMIN_CONSOLE_READY=http://127.0.0.1:18085"
        echo "RELEASE=$REV"
        exit 0
    fi
    sleep 1
done
sudo systemctl status "$UNIT" --no-pager || true
echo "ADMIN_CONSOLE_HEALTH_FAILED"
exit 1
