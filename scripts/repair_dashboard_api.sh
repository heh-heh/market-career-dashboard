#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/ubuntu/market-career-dashboard"
UNIT_SRC="$ROOT/market-career-dashboard.service"
UNIT_DST="/etc/systemd/system/market-career-dashboard.service"

echo "[1/5] dashboard systemd unit 설치"
install -m 0644 "$UNIT_SRC" "$UNIT_DST"
systemctl daemon-reload

echo "[2/5] Python 실행환경 확인"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/python" -m pip install --upgrade pip
  "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
fi

echo "[3/5] API 서비스 재시작"
systemctl restart market-career-dashboard.service
sleep 2

echo "[4/5] localhost API 확인"
if curl -fsS --max-time 5 http://127.0.0.1:8080/api/health; then
  echo
  echo "LOCAL_API=OK"
else
  echo
  echo "LOCAL_API=FAIL"
  systemctl --no-pager --full status market-career-dashboard.service || true
  journalctl -u market-career-dashboard.service -n 40 --no-pager || true
  exit 1
fi

echo "[5/5] HTTPS 프록시 확인"
if systemctl list-unit-files nginx.service >/dev/null 2>&1; then
  systemctl restart nginx.service || true
  sleep 1
  echo "NGINX=$(systemctl is-active nginx.service || true)"
fi

echo "DASHBOARD=$(systemctl is-active market-career-dashboard.service || true)"
echo "PORTS:"
ss -lntp | grep -E ':(443|8080|8081)\b' || true
