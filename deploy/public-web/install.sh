#!/usr/bin/env bash
# Deploy ONLY the public web frontend. No existing API, collector, admin,
# Tailscale, stock database or Chess service is changed.
set -Eeuo pipefail
umask 077
REPO=/srv/apps/market-career-dashboard
BASE=/srv/apps/_deploy/dashboard/public-web
PROJECT=market-dashboard-public-web
REF=refs/remotes/origin/main
if [[ -n "$(printenv PUBLIC_WEB_REF || true)" ]]; then REF=$(printenv PUBLIC_WEB_REF); fi
[[ "$(id -un)" == "heh-heh" ]] || { echo "RUN_AS_HEH_HEH"; exit 1; }
[[ "$REF" =~ ^refs/remotes/origin/[a-zA-Z0-9/._-]+$ ]] || { echo "UNSAFE_REF"; exit 1; }
for tool in docker git curl python3 tar; do command -v "$tool" >/dev/null || { echo "MISSING_TOOL:$tool"; exit 1; }; done
docker compose version >/dev/null
[[ "$(git -C "$REPO" remote get-url origin)" == *heh-heh/market-career-dashboard* ]] || { echo "WRONG_REPOSITORY"; exit 1; }
REV=$(git -C "$REPO" rev-parse --verify "$REF^{commit}")
[[ -n "$REV" ]] || { echo "REV_NOT_FOUND"; exit 1; }
API=http://127.0.0.1:18084
curl -fsS --max-time 5 "$API/api/health" | python3 -c \
  'import json,sys; d=json.load(sys.stdin); assert d["ok"] and d["mode"]=="dashboard-only" and not d["tradingEnabled"]'
mkdir -p "$BASE/releases"
RELEASE="$BASE/releases/$REV"
SOURCE="$RELEASE/source"
SITE="$RELEASE/site"
mkdir -p "$SOURCE" "$SITE/data"
if [[ ! -f "$RELEASE/.ready" ]]; then
  git -C "$REPO" archive "$REV" \
    index.html admin.html admin-login.js server-status.html server-status.js \
    server-metrics.js server-metrics.css research-export.css backtest-v2.html \
    data/dashboard.json deploy/public-web | tar -xf - -C "$SOURCE"
  for asset in index.html admin.html admin-login.js server-status.html server-status.js \
    server-metrics.js server-metrics.css research-export.css backtest-v2.html; do
    cp "$SOURCE/$asset" "$SITE/$asset"
  done
  cp "$SOURCE/data/dashboard.json" "$SITE/data/dashboard.json"
  cat > "$SITE/dashboard-config.js" <<'JS'
"use strict";
// Same-origin frontend/API: never use an old AWS URL or expose private ports.
window.DASHBOARD_CONFIG = {apiBase: window.location.origin, publicReadOnly: true};
JS
  chmod 755 "$BASE" "$BASE/releases" "$RELEASE" "$SOURCE" "$SITE" "$SITE/data"
  find "$SITE" -type f -exec chmod 644 {} +
  chmod 644 "$SOURCE/deploy/public-web/nginx.conf" "$SOURCE/deploy/public-web/compose.yml"
  printf '%s\n' "$REV" > "$RELEASE/.ready"
fi
cat > "$RELEASE/web.env" <<ENV
PUBLIC_SITE_ROOT=$SITE
PUBLIC_SITE_CONFIG=$SOURCE/deploy/public-web/nginx.conf
ENV
chmod 600 "$RELEASE/web.env"
COMPOSE="$SOURCE/deploy/public-web/compose.yml"
docker compose --env-file "$RELEASE/web.env" -f "$COMPOSE" -p "$PROJECT" config --quiet

if docker inspect market-dashboard-web >/dev/null 2>&1; then
  OWNER=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' market-dashboard-web)
  [[ "$OWNER" == "$PROJECT" ]] || { echo "CONTAINER_NAME_CONFLICT"; exit 1; }
fi
OLD_RELEASE=""
[[ ! -f "$BASE/active-release" ]] || OLD_RELEASE=$(cat "$BASE/active-release")
rollback() {
  local code=$?
  trap - ERR
  echo "PUBLIC_WEB_DEPLOY_FAILED status=$code"
  if [[ -n "$OLD_RELEASE" && -f "$OLD_RELEASE/web.env" && -f "$OLD_RELEASE/source/deploy/public-web/compose.yml" ]]; then
    docker compose --env-file "$OLD_RELEASE/web.env" \
      -f "$OLD_RELEASE/source/deploy/public-web/compose.yml" -p "$PROJECT" \
      up -d --no-deps --force-recreate web || true
    echo "PREVIOUS_PUBLIC_WEB_RESTORED=$OLD_RELEASE"
  fi
  exit "$code"
}
trap rollback ERR
docker compose --env-file "$RELEASE/web.env" -f "$COMPOSE" -p "$PROJECT" up -d --no-deps web

# Validate local static UI, same-origin public API and non-exposed private paths.
curl -fsS --max-time 8 http://127.0.0.1:18086/ | grep -Fq 'href="./server-status.html"'
curl -fsS --max-time 8 http://127.0.0.1:18086/dashboard-config.js | grep -Fq 'window.location.origin'
curl -fsS --max-time 8 http://127.0.0.1:18086/server-status.html >/dev/null
curl -fsS --max-time 8 http://127.0.0.1:18086/api/health | python3 -c \
  'import json,sys; d=json.load(sys.stdin); assert d["ok"] and d["mode"]=="dashboard-only"'
curl -fsS --max-time 8 http://127.0.0.1:18086/api/server-telemetry | python3 -c \
  'import json,sys; d=json.load(sys.stdin); assert d["ok"] and d["mode"]=="PUBLIC_READ_ONLY_AGGREGATED"'
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:18086/api/login)
[[ "$code" == 404 ]] || { echo "PRIVATE_API_ROUTE_EXPOSED=$code"; false; }
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 -X POST http://127.0.0.1:18086/api/dashboard)
[[ "$code" != 200 ]] || { echo "PUBLIC_POST_UNEXPECTEDLY_ALLOWED"; false; }
printf '%s\n' "$RELEASE" > "$BASE/active-release"
trap - ERR
echo "PUBLIC_WEB_READY=http://127.0.0.1:18086/"
echo "PUBLIC_WEB_REVISION=$REV"
echo "FUNNEL_NOT_CHANGED=true"
echo "STOCK_AND_ADMIN_UNCHANGED=true"
