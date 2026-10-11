#!/usr/bin/env bash
# Compatibility launcher: analysis by default, explicit --apply/--rollback only.
# Safe from a clean detached worktree; never Git-merge the production checkout.
set -euo pipefail
script_dir="$(cd "$(dirname "$0")" && pwd)"
python_bin="${COLLECTION_DEPLOY_PYTHON:-/home/ubuntu/market-career-dashboard/.venv/bin/python}"
if [ "$#" -eq 0 ]; then
  set -- --source "$(dirname "$script_dir")" --target /home/ubuntu/market-career-dashboard
fi
exec "$python_bin" "$script_dir/deploy_collection_worktree.py" "$@"
