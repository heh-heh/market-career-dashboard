#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/ubuntu/market-career-dashboard"
STATE_DIR="/var/lib/market-career-dashboard"
SRC="$STATE_DIR/backtest_multistrategy_v1_result.json"
DST="$ROOT/research/backtest_multistrategy_v1_result.json"

if [ ! -s "$SRC" ]; then
  echo "Backtest result not found: $SRC" >&2
  exit 1
fi

cd "$ROOT"

# Keep raw 1-minute history private/local. Only publish the derived result JSON.
install -m 0644 "$SRC" "$DST"

git config user.name "market-career-dashboard-bot"
git config user.email "market-career-dashboard-bot@users.noreply.github.com"

git add research/backtest_multistrategy_v1_result.json

if git diff --cached --quiet -- research/backtest_multistrategy_v1_result.json; then
  echo "No backtest result changes to publish."
  exit 0
fi

git commit -m "backtest: publish multi-strategy result"
git push origin strategy-engine-v3

echo "Published: research/backtest_multistrategy_v1_result.json"
