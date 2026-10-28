#!/usr/bin/env bash
# Stage historical/tick collection code safely. Does NOT start any collectors.
set -Eeuo pipefail
umask 077

REPO=/srv/apps/market-career-dashboard
BASE=/srv/apps/_deploy/stock-collection
DATA=/srv/ai-data/stock-collection
VAULT=/srv/apps/_deploy/dashboard/admin-console/private/api_keys.json

[[ "$(id -un)" == "heh-heh" ]] || { echo "ERROR=RUN_AS_HEH_HEH"; exit 1; }
for tool in git python3 tar; do command -v "$tool" >/dev/null || { echo "ERROR=MISSING_$tool"; exit 1; }; done
[[ -f "$VAULT" && ! -L "$VAULT" ]] || { echo "ERROR=ADMIN_API_KEY_VAULT_MISSING"; exit 1; }
[[ "$(git -C "$REPO" remote get-url origin)" == *heh-heh/market-career-dashboard* ]] || { echo "ERROR=WRONG_REPO"; exit 1; }

git -C "$REPO" fetch origin strategy-engine-v3
REV=$(git -C "$REPO" rev-parse refs/remotes/origin/strategy-engine-v3)
RELEASE="$BASE/releases/$REV"
mkdir -p "$RELEASE" "$BASE/private" "$DATA/runtime" "$DATA/historical-smoke"
chmod 700 "$BASE/private" "$DATA/runtime"
git -C "$REPO" archive "$REV" \
  requirements.txt toss_auth.py toss_rate_limit.py toss_token_broker.py \
  research_collection_scheduler.py research \
  scripts/collect_toss_1m.py scripts/collect_toss_ticks.py \
  scripts/collect_research_1m.py \
  | tar -xf - -C "$RELEASE"

python3 -m py_compile "$RELEASE/toss_auth.py" "$RELEASE/toss_token_broker.py" \
  "$RELEASE/scripts/collect_toss_1m.py" "$RELEASE/scripts/collect_toss_ticks.py"

if [[ ! -x "$BASE/venv/bin/python" ]]; then
  python3 -m venv "$BASE/venv" || { echo "ERROR=VENV_SETUP_FAILED (try sudo apt install python3-venv)"; exit 1; }
fi
"$BASE/venv/bin/python" -m pip install --disable-pip-version-check -r "$RELEASE/requirements.txt" 'pandas>=2.2,<3'

python3 - "$VAULT" "$BASE/private/toss-broker.json" <<'PY'
import json, os, stat, secrets, sys
from pathlib import Path
vault, output = map(Path,sys.argv[1:])
if vault.is_symlink() or not vault.is_file() or stat.S_IMODE(vault.stat().st_mode)&0o077:
    raise SystemExit("ERROR=VAULT_UNSAFE_PERMISSIONS")
data=json.loads(vault.read_text(encoding="utf-8"))
cid=data.get("TOSS_CLIENT_ID")
sec=data.get("TOSS_CLIENT_SECRET")
if not isinstance(cid,str) or not isinstance(sec,str) or not cid.strip() or not sec.strip():
    raise SystemExit("ERROR=MISSING_TOSS_CLIENT_ID_OR_SECRET")
if output.is_symlink():
    raise SystemExit("ERROR=BROKER_SECRET_SYMLINK")
temp=output.parent / (output.name+"."+secrets.token_hex(8))
fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
try:
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        json.dump({"toss":{"app_key":cid.strip(),"app_secret":sec.strip()}},f)
        f.flush(); os.fsync(f.fileno())
    os.replace(temp,output)
finally:
    temp.unlink(missing_ok=True)
print("TOSS_BROKER_CREDENTIALS_READY=true")
PY

ln -sfn "$RELEASE" "$BASE/current.new"
mv -Tf "$BASE/current.new" "$BASE/current"
echo "STOCK_COLLECTOR_STAGED=true"
echo "REVISION=$REV"
echo "DATA_DIR=$DATA"
echo "COLLECTOR_STARTED=false"
