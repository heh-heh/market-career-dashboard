#!/usr/bin/env bash
set -euo pipefail

APP="/home/ubuntu/market-career-dashboard"
UNIT_SRC="$APP/market-career-dashboard.service"
UNIT_DST="/etc/systemd/system/market-career-dashboard.service"
WATCHDOG_SRC="$APP/backtest-hybrid-watchdog.service"
WATCHDOG_DST="/etc/systemd/system/backtest-hybrid-watchdog.service"

if [ ! -d "$APP/.git" ]; then
  echo "[ERROR] Git repository not found: $APP" >&2
  exit 1
fi

echo "[1/8] Stop service"
sudo systemctl stop market-career-dashboard || true

echo "[2/8] Mark repository as safe for root Git"
sudo git config --global --add safe.directory "$APP" || true

echo "[3/8] Sync tracked files to GitHub main"
sudo git -C "$APP" fetch origin main
sudo git -C "$APP" reset --hard origin/main

echo "[4/8] Ensure Python environment"
if [ ! -x "$APP/.venv/bin/python" ]; then
  sudo python3 -m venv "$APP/.venv"
fi
sudo "$APP/.venv/bin/python" -m pip install --upgrade pip
sudo "$APP/.venv/bin/python" -m pip install -r "$APP/requirements.txt"

echo "[5/8] Install current systemd units"
if ! command -v g++ >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo apt-get install -y g++
fi
sudo install -m 644 "$UNIT_SRC" "$UNIT_DST"
sudo install -m 644 "$WATCHDOG_SRC" "$WATCHDOG_DST"
sudo systemctl daemon-reload

echo "[6/8] Start service"
sudo systemctl restart market-career-dashboard
sleep 2
sudo systemctl is-active --quiet market-career-dashboard

echo "[7/8] Verify API + realtime listener"
curl -fsS http://127.0.0.1:8080/api/health >/dev/null
sudo ss -lnt | grep -q '127.0.0.1:8081'

echo "[8/8] Status"
echo
sudo systemctl --no-pager --full status market-career-dashboard | sed -n '1,16p'
echo
echo "Git: $(sudo git -C "$APP" rev-parse --short HEAD)"
echo "Dashboard API: OK"
echo "Realtime WebSocket: 127.0.0.1:8081 LISTENING"
echo "Update complete."
