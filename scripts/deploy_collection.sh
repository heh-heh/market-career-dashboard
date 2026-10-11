#!/usr/bin/env bash
# Operator-only EC2 deployment. No reset/clean, data deletion or trading controls.
set -euo pipefail
cd "$(dirname "$0")/.."
test "$(git branch --show-current)" = strategy-engine-v3
git diff --quiet && git diff --cached --quiet || { echo 'Tracked edits present; deployment stopped.' >&2; exit 1; }
test "$PWD" = /home/ubuntu/market-career-dashboard || { echo 'Use the production checkout /home/ubuntu/market-career-dashboard.' >&2; exit 1; }
deploy_lock="$(git rev-parse --git-path collection-deploy.lock)"
test ! -L "$deploy_lock"
exec 9>"$deploy_lock"
flock -n 9 || { echo 'Another collection deployment is running.' >&2; exit 1; }
git fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3
git merge --ff-only origin/strategy-engine-v3
mountpoint -q /mnt/research || { echo '/mnt/research must be mounted; refusing to fill the root disk.' >&2; exit 1; }
test ! -L /mnt/research/collection-50-v1
if ! test -e /mnt/research/collection-50-v1; then
  sudo install -d -o ubuntu -g ubuntu -m 0750 /mnt/research/collection-50-v1
fi
test -x .venv/bin/python
sudo systemctl is-active --quiet toss-token-broker.service
.venv/bin/python -m pip install -r requirements.txt
sudo -u ubuntu .venv/bin/python - <<'PY'
import os
from pathlib import Path
from scripts.collect_research_1m import safe_research_root, check_disk, selected_symbols
root=safe_research_root('/mnt/research/collection-50-v1')
if not os.access(root, os.W_OK|os.X_OK): raise SystemExit('RESEARCH_DIRECTORY_NOT_WRITABLE; existing ownership is left unchanged.')
check_disk(root)
for symbol in selected_symbols(None):
    for suffix in ('.sqlite3','.sqlite3-wal','.sqlite3-shm'):
        path=root/(symbol+suffix)
        if path.is_symlink(): raise SystemExit('RESEARCH_DATABASE_SYMLINK_BLOCKED')
        if path.exists() and not os.access(path,os.R_OK|os.W_OK):
            raise SystemExit('RESEARCH_DATABASE_NOT_WRITABLE symbol='+symbol+'; no automatic ownership changes.')
PY
.venv/bin/python -m py_compile server.py research_collection_scheduler.py scripts/collect_research_1m.py scripts/collect_toss_ticks.py
.venv/bin/python - <<'PY'
import os, subprocess, sys, tempfile
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='collection-deploy-smoke-') as directory:
    root=Path(directory)
    env={**os.environ, 'TRADING_MODE':'paper', 'LIVE_TRADING_ENABLED':'false',
         'TOSS_TOKEN_SOCKET':str(root/'no-real-broker.sock'),
         'PAPER_V3_DIR':str(root/'paper'), 'SIMPLE_PAPER_DATA_DIR':str(root/'simple'),
         'PERSISTENT_DATA_DIR':str(root), 'BACKTEST_DATA_DIR':str(root/'backtest'),
         'RESEARCH_COLLECTION_STATE_DIR':str(root/'scheduler'),
         'RESEARCH_COLLECTION_WORKER_LOCK':str(root/'worker.lock'),
         'COLLECTION_50_DIR':str(root/'data'), 'COLLECTION_DOWNLOAD_DIR':str(root/'download')}
    subprocess.run([sys.executable,'-m','unittest',
        'research.test_research_collection_scheduler','research.test_collection_deployment',
        'research.test_collect_toss_ticks','research.test_toss_auth_v2'],env=env,check=True)
PY
# Apply graceful-stop limits before stopping an older in-memory worker.
sudo install -d -m 0755 /etc/systemd/system/collect-toss-ticks.service.d /etc/systemd/system/collect-expanded-universe.service.d
sudo install -m 0644 ops/collection-worker-dropin/10-scheduler.conf /etc/systemd/system/collect-toss-ticks.service.d/10-scheduler.conf
sudo install -m 0644 ops/collection-worker-dropin/10-scheduler.conf /etc/systemd/system/collect-expanded-universe.service.d/10-scheduler.conf
sudo systemctl daemon-reload
for unit in research-collection-scheduler.service collect-toss-ticks.service collect-expanded-universe.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then sudo systemctl stop "$unit"; fi
done
# Refuse unowned/manual collectors. Never kill arbitrary processes or fake a lock.
.venv/bin/python - <<'PY'
from pathlib import Path
from research_collection_scheduler import exclusive_lock
names = {'collect_toss_ticks.py', 'collect_toss_1m.py', 'collect_expanded_universe.py', 'collect_research_1m.py', 'research_collection_scheduler.py'}
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try: args = p.joinpath('cmdline').read_bytes().split(b'\0')
    except (FileNotFoundError, PermissionError): continue
    if any(Path(a.decode(errors='ignore')).name in names for a in args):
        raise SystemExit('MANUAL_COLLECTOR_RUNNING pid='+p.name+'; stop it gracefully before deploying.')
with exclusive_lock(): pass
PY
for unit in collect-toss-ticks.service collect-expanded-universe.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then sudo systemctl disable "$unit"; fi
done
sudo install -d -o ubuntu -g ubuntu -m 0750 /var/lib/market-career-dashboard/collection
sudo install -d -m 0755 /etc/market-career-dashboard
sudo touch /etc/market-career-dashboard/collection.env data/.toss_api_rate.lock
sudo chgrp ubuntu data/.toss_api_rate.lock
sudo chmod 0660 data/.toss_api_rate.lock
sudo install -m 0644 ops/research-collection-scheduler.service /etc/systemd/system/research-collection-scheduler.service
# Preserve existing credentials/env; install only the single-issuer broker code.
sudo install -m 0644 toss_auth.py toss_token_broker.py /opt/market-career-toss-auth/
sudo systemctl restart toss-token-broker.service
sudo systemctl is-active --quiet toss-token-broker.service
# Deployment prepares the UI/service but collection starts only on a user click.
# Keep the existing operator pause/resume choice on subsequent deployments.
sudo -u ubuntu .venv/bin/python - <<'PY'
from research_collection_scheduler import STATE_DIR, save
p = STATE_DIR/'control.json'
if not p.exists(): save(p, {'paused': True})
PY
sudo systemctl daemon-reload
sudo systemctl enable --now research-collection-scheduler.service
sudo systemctl restart market-career-dashboard.service
sudo systemctl is-active --quiet research-collection-scheduler.service market-career-dashboard.service
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 http://127.0.0.1:8080/api/research/collection50/status
printf '\nDeploy complete. Open your API origin + /api/research/collection50/page, log in, then click 수집 시작.\n'
