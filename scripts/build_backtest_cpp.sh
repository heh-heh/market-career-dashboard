#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/bin"
g++ -O3 -march=native -std=c++17 "$ROOT/research/backtest_toss_v5.cpp" -o "$ROOT/bin/backtest_toss_v5"
echo "Built: $ROOT/bin/backtest_toss_v5"
