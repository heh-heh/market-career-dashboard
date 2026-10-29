#!/usr/bin/env bash
# Stage and install market-data-only services; first boot remains PAUSED.
set -Eeuo pipefail
umask 077
REPO=/srv/apps/market-career-dashboard
BASE=/srv/apps/_deploy/stock-collection
DATA=/srv/ai-data/stock-collection
[[ "$(id -un)" == "heh-heh" ]] || { echo "ERROR=RUN_AS_HEH_HEH"; exit 1; }
[[ -f "$BASE/current/research_collection_scheduler.py" ]] || { echo "ERROR=RUN_SETUP_FIRST"; exit 1; }
[[ -f "$BASE/private/toss-broker.json" ]] || { echo "ERROR=BROKER_CONFIG_MISSING"; exit 1; }
for tool in git sudo python3 curl; do command -v "$tool" >/dev/null || { echo "ERROR=MISSING_TOOL:$tool"; exit 1; }; done
git -C "$REPO" fetch origin main
REV=$(git -C "$REPO" rev-parse refs/remotes/origin/main)
mkdir -p "$BASE/bin" "$BASE/private" "$DATA/runtime/collection" "$DATA/historical" "$DATA/historical-50-2015-2026" "$DATA/ticks"
chmod 700 "$BASE/private" "$DATA/runtime/collection" "$DATA/historical" "$DATA/historical-50-2015-2026" "$DATA/ticks"
for name in run-broker.sh run-scheduler.sh; do
  git -C "$REPO" show "$REV:deploy/stock-collection/$name" > "$BASE/bin/$name.new"
  bash -n "$BASE/bin/$name.new"
  mv -f "$BASE/bin/$name.new" "$BASE/bin/$name"
  chmod 700 "$BASE/bin/$name"
done
for name in market-stock-token-broker.service market-stock-collection.service; do
  git -C "$REPO" show "$REV:deploy/stock-collection/$name" > "$BASE/bin/$name"
done
# Preserve the operator's existing symbol list and pause state on reinstallation.
python3 - "$BASE/private/symbols.json" "$DATA/runtime/collection/control.json" <<'PY'
import json, os, sys
from pathlib import Path
symbols, control = (Path(s) for s in sys.argv[1:])
for path, content in (
    (symbols, {"symbols":["NVDA","SOXL","SOXS","SPY"]}),
    (control, {"paused":True,"reason":"INITIAL_INSTALL_DEFAULT_PAUSED"})):
    if path.is_symlink():
        raise SystemExit("ERROR=UNSAFE_SYMLINK:"+path.name)
    if not path.exists():
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,"w") as f: json.dump(content,f)
    os.chmod(path,0o600)
print("INITIAL_SYMBOLS_PRESERVED=true")
print("PAUSE_CONTROL_PRESERVED=true")
PY
# Historical target lives in a *different* config and storage from tick data.
# Do not overwrite existing per-operator symbols, or start backfill on update.
python3 - "$BASE/private/historical-symbols.json" "$DATA/runtime/collection/historical-control.json" <<'PY'
import json,os,sys,re
from pathlib import Path
symbols_path, control_path = (Path(raw) for raw in sys.argv[1:])
default="AAPL MSFT NVDA AMD INTC AVGO TSM MU QCOM AMAT LRCX KLAC ASML MRVL ARM GOOGL GOOG AMZN META TSLA NFLX PLTR ORCL ADBE CRM NOW SNOW UBER COIN HOOD JPM BAC GS V MA WMT COST UNH LLY XOM SPY QQQ IWM DIA SOXX SMH SOXL SOXS TQQQ SQQQ".split()
assert len(default)==50 and len(set(default))==50
for p, obj in ((symbols_path,{"symbols":default}), (control_path,{"enabled":False,"reason":"EXPLICIT_HISTORICAL_START_REQUIRED"})):
    if p.is_symlink():
        raise SystemExit("ERROR=UNSAFE_HISTORICAL_CONTROL")
    if not p.exists():
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(obj,f)
            f.flush();os.fsync(f.fileno())
    os.chmod(p,0o600)
raw=json.loads(symbols_path.read_text(encoding="utf-8"))
symbols=raw.get("symbols") if isinstance(raw,dict) else None
if not isinstance(symbols,list) or len(symbols)!=50 or len(set(symbols))!=50 or any(
    not isinstance(x,str) or not re.fullmatch(r"[A-Z0-9]{1,8}",x) for x in symbols):
    raise SystemExit("ERROR=HISTORICAL_SYMBOLS_MUST_BE_50_UNIQUE")
control=json.loads(control_path.read_text(encoding="utf-8"))
if not isinstance(control,dict) or type(control.get("enabled")) is not bool:
    raise SystemExit("ERROR=HISTORICAL_ENABLE_STATE_INVALID")
print("HISTORICAL_SYMBOL_COUNT=50")
print("HISTORICAL_ACTIVE="+str(control["enabled"]).lower())
print("HISTORICAL_START=2015-01-01")
print("HISTORICAL_END=2026-10-01")
PY
# No existing services from this new install, and no change to dashboard/chess.
sudo install -m 0644 "$BASE/bin/market-stock-token-broker.service" /etc/systemd/system/
sudo install -m 0644 "$BASE/bin/market-stock-collection.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable market-stock-token-broker.service market-stock-collection.service >/dev/null
sudo systemctl restart market-stock-token-broker.service
sudo systemctl restart market-stock-collection.service
for i in {1..12}; do
  if [[ "$(systemctl is-active market-stock-token-broker.service)" == "active" && "$(systemctl is-active market-stock-collection.service)" == "active" ]]; then
    break
  fi
  sleep 1
done
BROKER_STATE=$(systemctl is-active market-stock-token-broker.service || true)
COLLECTION_STATE=$(systemctl is-active market-stock-collection.service || true)
echo "BROKER_SERVICE=$BROKER_STATE"
echo "COLLECTION_SERVICE=$COLLECTION_STATE"
if [[ "$BROKER_STATE" != "active" || "$COLLECTION_STATE" != "active" ]]; then
  echo "ERROR=STOCK_COLLECTOR_UNITS_NOT_HEALTHY"
  exit 1
fi
python3 - "$DATA/runtime/collection/control.json" <<'PY'
import json,sys
from pathlib import Path
d=json.loads(Path(sys.argv[1]).read_text())
print("COLLECTION_REQUESTED="+("PAUSED" if d.get("paused",True) else "ENABLED"))
PY
echo "DATA=$DATA"
echo "SCHEDULER_INSTALL_COMPLETE=true"
