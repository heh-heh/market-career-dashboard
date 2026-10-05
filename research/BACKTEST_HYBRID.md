# Hybrid Toss Backtest

Python collect_toss_1m.py collects authenticated Toss 1-minute history. The persisted gzip CSV files are consumed by the C++17 engine backtest_toss_v5.cpp.

The C++ engine aggregates 1-minute data into 3-minute regular-session bars, calculates EMA9/EMA21, ATR14, RSI14, relative volume and VWAP, runs the V4 parameter grid, uses next-open execution with ATR stop and 0.02% per-side slippage, and performs a 70/30 time split.

Build: bash scripts/build_backtest_cpp.sh
Run: bin/backtest_toss_v5 --symbols NVDA,AMD,INTC,SOXL,SOXS,TQQQ --min-days 100
Output: research/backtest_toss_v5_result.json

Python V4 remains available as the reference implementation for cross-checking.
