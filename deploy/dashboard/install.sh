#!/usr/bin/env bash
# Snapshot selected dashboard files; never reset/clean/stash or modify other projects.
set -Eeuo pipefail
umask 077
REPO=${REPO_DIR:-/srv/apps/market-career-dashboard}
DEPLOY=${DASHBOARD_DEPLOY_DIR:-/srv/apps/_deploy/dashboard}
DATA=${DASHBOARD_DATA_DIR:-/srv/ai-data/market-dashboard}
FUNNEL=false
if [[ ${1:-} == --funnel ]]; then FUNNEL=true; elif [[ $# != 0 ]]; then echo 'Usage: install.sh [--funnel]'; exit 2; fi
for tool in git docker python3 curl flock; do command -v "$tool" >/dev/null || { echo "MISSING_TOOL:$tool"; exit 1; }; done
docker compose version >/dev/null
[[ $(git -C "$REPO" remote get-url origin) == *heh-heh/market-career-dashboard* ]] || { echo WRONG_REPOSITORY; exit 1; }
REV=$(git -C "$REPO" rev-parse refs/remotes/origin/main)
[[ $(git -C "$REPO" branch --show-current) == main ]] || { echo 'OPERATING_REPO_NOT_MAIN: no files changed'; exit 1; }
mkdir -p "$DEPLOY" "$DATA"
exec 9>"$DEPLOY/install.lock"
flock -n 9 || { echo DEPLOY_ALREADY_RUNNING; exit 1; }
[[ ! -L "$DEPLOY" && ! -L "$DATA" ]] || { echo SYMLINK_ROOT_BLOCKED; exit 1; }
if command -v systemctl >/dev/null && systemctl is-active --quiet market-career-dashboard.service; then
  echo 'LEGACY_SYSTEMD_ACTIVE: review the dashboard-only service before migrating'; exit 1
fi
# Unknown host collectors must be reviewed, never killed by this installer.
if pgrep -af '[p]ython[^ ]* /srv/apps/market-career-dashboard/scripts/(update_data|collect_dashboard)\.py' >/dev/null; then
  echo 'HOST_COLLECTOR_ACTIVE: stop only the known dashboard collector before retrying'; exit 1
fi
python3 - "$DEPLOY" "$DATA" <<'PYDISK'
import os,sys
for path in sys.argv[1:]:
    s=os.statvfs(path)
    if s.f_bavail*s.f_frsize<1024**3: raise SystemExit('DISK_SPACE_GUARD:'+path)
PYDISK
STAMP=$(date -u +%Y%m%dT%H%M%SZ)-$$
BACKUP="$DEPLOY/backups/$STAMP"
RELEASE="$DEPLOY/releases/$REV"
mkdir -p "$BACKUP" "$RELEASE"
docker ps -a --format '{{.Names}} {{.Image}} {{.Status}} {{.Ports}}' > "$BACKUP/containers-before.txt"
for name in market-dashboard-api market-dashboard-collector market-dashboard-collect-once; do
  if docker inspect "$name" >/dev/null 2>&1; then
    [[ $(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$name") == market-dashboard-home ]] || { echo "CONTAINER_NAME_CONFLICT:$name"; exit 1; }
  fi
done
OLD_RELEASE=''
if [[ -f "$DEPLOY/home.env" ]]; then
  cp -p "$DEPLOY/home.env" "$BACKUP/home.env"
  OLD_RELEASE=$(sed -n 's/^DASHBOARD_SOURCE_DIR=//p' "$DEPLOY/home.env")
fi
for file in dashboard.json dashboard.previous.json collector_status.json job_detail_cache.json; do
  if [[ -f "$DATA/$file" ]]; then cp -p "$DATA/$file" "$BACKUP/$file"; fi
done
# Git archive reads the reviewed main revision, without touching the dirty working tree.
# Extract into a fresh staging directory, never over the operating checkout.
if [[ ! -f "$RELEASE/.ready" ]]; then
  STAGE=$(mktemp -d "$DEPLOY/releases/.stage-XXXXXX")
  git -C "$REPO" archive "$REV" server.py dashboard_data.py scripts/update_data.py scripts/collect_dashboard.py scripts/configure_dashboard_api.py .dockerignore deploy/dashboard | tar -xf - -C "$STAGE"
  cp -a "$STAGE/." "$RELEASE/"
  # Staging source is intentionally retained for inspection; it contains no live data.
  printf '%s\n' "$REV" > "$RELEASE/.ready"
fi
python3 - "$REPO" "$REV" "$RELEASE" <<'PYVERIFY'
import hashlib,subprocess,sys
from pathlib import Path
repo,rev,release=sys.argv[1:]
for rel in ('server.py','dashboard_data.py','scripts/update_data.py','scripts/collect_dashboard.py','scripts/configure_dashboard_api.py','.dockerignore','deploy/dashboard/Dockerfile','deploy/dashboard/compose.yml'):
    expected=subprocess.check_output(['git','-C',repo,'show',rev+':'+rel])
    actual=(Path(release)/rel).read_bytes()
    if hashlib.sha256(actual).digest()!=hashlib.sha256(expected).digest():
        raise SystemExit('RELEASE_HASH_MISMATCH:'+rel)
PYVERIFY
find "$RELEASE" -type f ! -name .ready -print0 | sort -z | xargs -0 sha256sum > "$BACKUP/release.sha256"
UID_VALUE=$(id -u); GID_VALUE=$(id -g)
[[ "$UID_VALUE" != 0 ]] || { echo 'RUN_AS_HEH_HEH: run without sudo (docker group required)'; exit 1; }
# Copy a validated seed ONLY into an absent persistent dashboard file.
if [[ ! -e "$DATA/dashboard.json" && -f "$REPO/data/dashboard.json" ]]; then
  PYTHONPATH="$RELEASE" python3 - "$REPO/data/dashboard.json" "$DATA/dashboard.json" <<'PY'
import sys
from dashboard_data import read_dashboard,atomic_json
atomic_json(sys.argv[2],read_dashboard(sys.argv[1]))
PY
fi
printf 'DASHBOARD_SOURCE_DIR=%s\nDASHBOARD_DATA_DIR=%s\nDASHBOARD_UID=%s\nDASHBOARD_GID=%s\nDASHBOARD_REVISION=%s\n' "$RELEASE" "$DATA" "$UID_VALUE" "$GID_VALUE" "$REV" > "$DEPLOY/home.env.new"
mv "$DEPLOY/home.env.new" "$DEPLOY/home.env"
COMPOSE=(docker compose --env-file "$DEPLOY/home.env" -f "$RELEASE/deploy/dashboard/compose.yml" -p market-dashboard-home)
STOPPED=(); CHANGED=false
rollback() {
  local code=$?
  trap - ERR INT TERM
  echo "DEPLOY_FAILED:$code backup=$BACKUP"
  if [[ $(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' market-dashboard-collect-once 2>/dev/null || true) == market-dashboard-home ]]; then
    docker stop -t 15 market-dashboard-collect-once >/dev/null || true
  fi
  if $CHANGED; then "${COMPOSE[@]}" stop collector api || true; fi
  if [[ -n "$OLD_RELEASE" && -f "$BACKUP/home.env" ]]; then
    cp -p "$BACKUP/home.env" "$DEPLOY/home.env"
    docker compose --env-file "$DEPLOY/home.env" -f "$OLD_RELEASE/deploy/dashboard/compose.yml" -p market-dashboard-home up -d --no-build || true
  fi
  for name in "${STOPPED[@]}"; do docker start "$name" >/dev/null || true; done
  echo 'Original data preserved; backups not removed.'
  exit "$code"
}
trap rollback ERR
trap 'false' INT TERM
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" build api
CHANGED=true
"${COMPOSE[@]}" up -d --no-deps api
for attempt in {1..20}; do
  if curl -fsS --max-time 4 http://127.0.0.1:18084/api/health > "$BACKUP/health.json"; then break; fi
  sleep 2
done
python3 - "$BACKUP/health.json" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]));assert s['mode']=='dashboard-only' and not s['tradingEnabled']
PY
# Stop ONLY named legacy dashboard containers, once a replacement API is healthy.
# Portainer, Chess and PostgreSQL are never stopped/recreated.
for name in market-collector-test market-dashboard-api-stage-1; do
  if [[ $(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || true) == true ]]; then
    docker inspect "$name" > "$BACKUP/$name.json"
    STOPPED+=("$name"); docker stop -t 15 "$name" >/dev/null
  fi
done
# Pause our old scheduler before an immediate one-shot collection (same shared flock).
"${COMPOSE[@]}" stop collector
"${COMPOSE[@]}" run --rm --no-deps --name market-dashboard-collect-once collector python scripts/collect_dashboard.py --once
"${COMPOSE[@]}" up -d --no-deps collector
curl -fsS --max-time 10 http://127.0.0.1:18084/api/dashboard > "$BACKUP/dashboard-health.json"
PYTHONPATH="$RELEASE" python3 - "$BACKUP/dashboard-health.json" <<'PY'
import sys
from dashboard_data import read_dashboard
s=read_dashboard(sys.argv[1]);print({k:len(s[k]) for k in ('stocks','indices','news','jobPostings')})
PY
[[ $(curl -s -o /dev/null -w '%{http_code}' --max-time 4 -X POST http://127.0.0.1:18084/api/trading/live/arm) == 403 ]]
trap - ERR INT TERM
printf '%s\n' "$BACKUP" > "$DEPLOY/last-backup"
echo "DEPLOYED revision=$REV api=http://127.0.0.1:18084 backup=$BACKUP"
"${COMPOSE[@]}" ps
if $FUNNEL; then
  command -v tailscale >/dev/null || { echo 'HTTPS_PENDING: Tailscale CLI unavailable'; exit 0; }
  tailscale serve status --json > "$BACKUP/tailscale-before.json"
  # Preserve any existing Serve/Funnel configuration belonging to another project.
  if ! python3 - "$BACKUP/tailscale-before.json" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]));sys.exit(0 if isinstance(s,dict) and not any(s.get(k) for k in ('TCP','Web','AllowFunnel','Services')) else 1)
PY
  then echo 'HTTPS_PENDING: existing Tailscale Serve config; review without overwriting'; exit 0; fi
  tailscale funnel --bg --https=443 http://127.0.0.1:18084 || { echo 'HTTPS_PENDING: approve Funnel/HTTPS in Tailscale admin'; exit 0; }
  tailscale funnel status
fi
