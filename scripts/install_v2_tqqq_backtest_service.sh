#!/usr/bin/env bash
set -euo pipefail
ROOT="/home/ubuntu/market-career-dashboard"
STATE="/var/lib/market-career-dashboard"
UNIT="/etc/systemd/system/backtest-v2-tqqq.service"

install -d -o ubuntu -g ubuntu -m 0755 "$STATE"
if [ -f "$ROOT/data/toss_1m/TQQQ.csv.gz" ]; then
  chown ubuntu:ubuntu "$ROOT/data/toss_1m/TQQQ.csv.gz"
fi
install -m 0644 "$ROOT/backtest-v2-tqqq.service" "$UNIT"
rm -f "$STATE/backtest_v2_tqqq_mr.json" "$STATE/backtest_v2_tqqq_mr_state.json" "$STATE/backtest_v2_tqqq_mr.log"
systemctl daemon-reload
systemctl stop backtest-v2-tqqq.service >/dev/null 2>&1 || true
systemctl start backtest-v2-tqqq.service
systemctl restart market-career-dashboard.service || true
echo "Started backtest-v2-tqqq.service"
