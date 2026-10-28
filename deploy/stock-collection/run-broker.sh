#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE=/srv/apps/_deploy/stock-collection
DATA=/srv/ai-data/stock-collection
SOCKET="$DATA/runtime/toss-broker.sock"
CREDENTIALS="$BASE/private/toss-broker.json"
[[ -f "$CREDENTIALS" && ! -L "$CREDENTIALS" ]] || { echo "BROKER_CREDENTIALS_MISSING"; exit 1; }
unset TOSS_TOKEN_SOCKET || true
cd "$BASE/current"
exec "$BASE/venv/bin/python" toss_token_broker.py \
 --socket "$SOCKET" --secrets "$CREDENTIALS" \
 --group-gid "$(id -g)" --allow-uid "$(id -u)"
