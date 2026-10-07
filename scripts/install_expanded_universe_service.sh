#!/usr/bin/env bash
set -euo pipefail
ROOT="/home/ubuntu/market-career-dashboard"
STATE="/var/lib/market-career-dashboard"
UNIT="/etc/systemd/system/collect-expanded-universe.service"

install -d -o ubuntu -g ubuntu -m 0755 "$STATE"
install -d -m 0755 "$ROOT/data/toss_1m"
install -m 0644 "$ROOT/collect-expanded-universe.service" "$UNIT"
systemctl daemon-reload
systemctl stop collect-expanded-universe.service >/dev/null 2>&1 || true
rm -f "$STATE/expanded_universe.log"
systemctl start collect-expanded-universe.service
systemctl restart market-career-dashboard.service || true
echo "Started collect-expanded-universe.service"
