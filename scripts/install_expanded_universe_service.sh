#!/usr/bin/env bash
set -euo pipefail
ROOT="/home/ubuntu/market-career-dashboard"
STATE="/var/lib/market-career-dashboard"
UNIT="/etc/systemd/system/collect-expanded-universe.service"

echo "[1/4] 상태 디렉터리 준비"
install -d -m 0755 "$STATE"
install -d -m 0755 "$ROOT/data/toss_1m"

echo "[2/4] systemd 서비스 설치"
install -m 0644 "$ROOT/collect-expanded-universe.service" "$UNIT"
systemctl daemon-reload

echo "[3/4] 확장 Universe 수집 백그라운드 시작"
systemctl restart --no-block collect-expanded-universe.service

echo "[4/4] 대시보드 API 재시작"
systemctl restart market-career-dashboard.service || true

echo "Started collect-expanded-universe.service"
echo "확인은: systemctl status collect-expanded-universe.service --no-pager"
