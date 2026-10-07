#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/ubuntu/market-career-dashboard"
UNIT_SRC="$ROOT/backtest-multistrategy.service"
UNIT_DST="/etc/systemd/system/backtest-multistrategy.service"
STATE_DIR="/var/lib/market-career-dashboard"

install -d -o ubuntu -g ubuntu -m 0755 "$STATE_DIR"
install -m 0644 "$UNIT_SRC" "$UNIT_DST"
systemctl daemon-reload

# Start or restart asynchronously under systemd. The SSM shell can be closed
# immediately after this command returns.
systemctl stop backtest-multistrategy.service >/dev/null 2>&1 || true
rm -f "$STATE_DIR/backtest_multistrategy_v1_state.json"
systemctl start backtest-multistrategy.service

# Reload the API so /api/backtest/monitor immediately exposes the new state.
if systemctl list-unit-files market-career-dashboard.service >/dev/null 2>&1; then
  systemctl restart market-career-dashboard.service || true
fi

echo "Started backtest-multistrategy.service"
echo "Status: systemctl status backtest-multistrategy.service --no-pager"
echo "Log:    tail -f $STATE_DIR/backtest_multistrategy_v1.log"
