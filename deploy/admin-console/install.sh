#!/usr/bin/env bash
# Upgrade the SSH-only admin portal without touching public Docker/Funnel services.
set -Eeuo pipefail
umask 077

REPO=/srv/apps/market-career-dashboard
BASE=/srv/apps/_deploy/dashboard/admin-console
UNIT=market-dashboard-admin-console.service
[[ "$(id -un)" == heh-heh ]] || { echo "RUN_AS_HEH_HEH"; exit 1; }
for tool in git python3 sudo curl tar; do command -v "$tool" >/dev/null || { echo "MISSING:$tool"; exit 1; }; done
[[ "$(git -C "$REPO" remote get-url origin)" == *heh-heh/market-career-dashboard* ]] || { echo "WRONG_REPO"; exit 1; }

git -C "$REPO" fetch origin main
REV=$(git -C "$REPO" rev-parse refs/remotes/origin/main)
RELEASE="$BASE/releases/$REV"
mkdir -p "$RELEASE" "$BASE/private"
chmod 700 "$BASE/private"
git -C "$REPO" archive "$REV" deploy/admin-console | tar -xf - -C "$RELEASE"
SRC="$RELEASE/deploy/admin-console"
git -C "$REPO" show "$REV:server-metrics.js" > "$SRC/server-metrics.js"
git -C "$REPO" show "$REV:server-metrics.css" > "$SRC/server-metrics.css"
python3 -m py_compile "$SRC/console.py" "$SRC/mfa_auth.py" "$SRC/setup_mfa.py"
(cd "$SRC" && python3 test_mfa.py && python3 test_history_progress.py)
[[ -s "$SRC/index.html" && -s "$SRC/app.js" && -s "$SRC/server-metrics.js" && -s "$SRC/server-metrics.css" ]] || { echo "ASSETS_MISSING"; exit 1; }

if [[ ! -f "$BASE/private/credentials.json" ]]; then
  [[ -t 0 ]] || { echo 'FIRST_INSTALL_REQUIRES_INTERACTIVE_SSH_FOR_PASSWORD'; exit 1; }
  python3 - "$BASE/private/credentials.json" <<'PY'
import base64
import getpass
import hashlib
import json
import os
import secrets
import sys
p=sys.argv[1]
pw=getpass.getpass("관리자 비밀번호 설정 (12자 이상): ")
again=getpass.getpass("관리자 비밀번호 확인: ")
if len(pw)<12 or len(pw)>256 or pw!=again:
    raise SystemExit("PASSWORD_TOO_SHORT_OR_MISMATCH")
salt=secrets.token_bytes(16)
iterations=400_000
digest=hashlib.pbkdf2_hmac("sha256",pw.encode(),salt,iterations)
record={"version":1,"iterations":iterations,
        "salt":base64.b64encode(salt).decode(),
        "digest":base64.b64encode(digest).decode()}
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,"w",encoding="utf-8") as f:
    json.dump(record,f)
print("ADMIN_PASSWORD_CONFIGURED")
PY
fi
chmod 600 "$BASE/private/credentials.json"
OLD=""
[[ ! -L "$BASE/current" ]] || OLD=$(readlink "$BASE/current")
ln -sfn "$SRC" "$BASE/current.new"
mv -Tf "$BASE/current.new" "$BASE/current"

UNITPATH=/etc/systemd/system/$UNIT
HAD_UNIT=false
if [[ -f "$UNITPATH" ]]; then
  sudo cp -p "$UNITPATH" "$BASE/service-before-$REV"
  HAD_UNIT=true
fi
sudo install -m 0644 "$SRC/admin-console.service" "$UNITPATH"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT" >/dev/null
RESTART_OK=true
if ! sudo systemctl restart "$UNIT"; then
  RESTART_OK=false
fi
READY=false
if $RESTART_OK; then
  for attempt in {1..15}; do
    if curl -fsS --max-time 2 http://127.0.0.1:18085/api/health | grep -q '"mode": "authenticated-admin"'; then
      READY=true
      break
    fi
    sleep 1
  done
fi
# Health 200 alone is insufficient: Task Manager previously rendered without
# its same-origin stylesheet because admin CSP blocked external CSS.
if $READY; then
  if ! python3 - <<'PY'
from urllib.request import urlopen

base = "http://127.0.0.1:18085"
with urlopen(base + "/", timeout=5) as response:
    html = response.read().decode("utf-8")
    csp = response.headers.get("Content-Security-Policy", "")
assert 'href="/server-metrics.css?v=' in html, "ADMIN_STYLESHEET_LINK_MISSING"
assert "style-src 'self' 'unsafe-inline'" in csp, "ADMIN_STYLESHEET_BLOCKED_BY_CSP"
with urlopen(base + "/server-metrics.css", timeout=5) as response:
    css = response.read().decode("utf-8")
    assert "text/css" in response.headers.get("Content-Type", ""), "BAD_STYLESHEET_MIME"
assert ".metric-workbench" in css and ".metric-resource-list" in css, "TASK_MANAGER_CSS_MISSING"
with urlopen(base + "/server-metrics.js", timeout=5) as response:
    js = response.read().decode("utf-8")
assert "window.ServerMetrics" in js, "TASK_MANAGER_SCRIPT_MISSING"
print("ADMIN_TASK_MANAGER_ASSETS=PASS")
PY
  then
    echo "ADMIN_TASK_MANAGER_ASSETS_FAILED"
    READY=false
  fi
fi
if ! $READY; then
  echo "ADMIN_CONSOLE_HEALTH_FAILED"
  if [[ -n "$OLD" ]]; then
    ln -sfn "$OLD" "$BASE/current.new"
    mv -Tf "$BASE/current.new" "$BASE/current"
  fi
  if $HAD_UNIT; then
    sudo install -m 0644 "$BASE/service-before-$REV" "$UNITPATH"
    sudo systemctl daemon-reload
  fi
  sudo systemctl restart "$UNIT" || true
  sudo systemctl status "$UNIT" --no-pager || true
  exit 1
fi
echo "ADMIN_PORTAL_READY=http://127.0.0.1:18085"
echo "PRIVATE_SSH_ONLY=true"
echo "RELEASE=$REV"
