#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
DEPLOY=${DASHBOARD_DEPLOY_DIR:-/srv/apps/_deploy/dashboard}
BACKUP=${1:-$(cat "$DEPLOY/last-backup")}
[[ $(realpath "$BACKUP") == "$(realpath "$DEPLOY")/backups/"* ]] || { echo BACKUP_PATH_INVALID; exit 1; }
exec 9>"$DEPLOY/install.lock"; flock -n 9 || { echo DEPLOY_ALREADY_RUNNING; exit 1; }
for name in market-dashboard-collect-once market-dashboard-collector market-dashboard-api; do
  if [[ $(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$name" 2>/dev/null || true) == market-dashboard-home ]]; then
    docker stop -t 15 "$name" >/dev/null
  fi
done
if [[ -f "$BACKUP/home.env" ]]; then
  OLD=$(sed -n 's/^DASHBOARD_SOURCE_DIR=//p' "$BACKUP/home.env")
  [[ -f "$OLD/deploy/dashboard/compose.yml" ]] || { echo OLD_RELEASE_MISSING; exit 1; }
  cp -p "$BACKUP/home.env" "$DEPLOY/home.env"
  docker compose --env-file "$DEPLOY/home.env" -f "$OLD/deploy/dashboard/compose.yml" -p market-dashboard-home up -d --no-build
fi
for name in market-collector-test market-dashboard-api-stage-1; do
  if [[ -f "$BACKUP/$name.json" ]]; then docker start "$name" >/dev/null; fi
done
echo 'ROLLED_BACK dashboard code/services only; original and latest data preserved.'
