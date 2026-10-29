#!/usr/bin/env python3
"""Fail-closed overlay for the frozen strategy-engine-v3 scheduler.

Never edits research branch or the tick worker. Applied only to an isolated
release staged under /srv/apps/_deploy/stock-collection/releases/.
"""
from pathlib import Path
import sys

START="2015-01-01T00:00:00-05:00"
END="2026-10-01T23:59:59-04:00"

def replace_once(body, before, after):
    if body.count(before)!=1:
        raise SystemExit("UNREVIEWED_UPSTREAM_SCHEDULER_DO_NOT_DEPLOY")
    return body.replace(before,after,1)

def apply(release):
    f=Path(release)/"research_collection_scheduler.py"
    body=f.read_text(encoding="utf-8")
    if "HISTORY_50_EXPLICIT_START_REQUIRED" in body:
        raise SystemExit("UNREVIEWED_UPSTREAM_ALREADY_PATCHED")

    old="        desired = {'MARKET_OPEN': 'tick', 'MARKET_CLOSED': 'backfill'}.get(decision['mode'])\n"
    new=old+"""        # Separate historical/tick permissions; the operator disabled ticks.
        # Missing/invalid configuration fails closed for *tick* collection,
        # without blocking the off-hours historical one-minute worker.
        if desired == 'tick':
            try:
                config_path = Path(os.environ["TICK_COLLECTION_CONTROL_FILE"])
                if config_path.is_symlink():
                    raise ValueError("CONTROL_SYMLINK")
                tick_control = json.loads(config_path.read_text(encoding="utf-8"))
                tick_enabled = isinstance(tick_control, dict) and tick_control.get("enabled") is True
            except (OSError, ValueError, KeyError):
                tick_enabled = False
            if not tick_enabled:
                desired = None
                decision = {**decision, 'mode': 'TICKS_PAUSED',
                            'reason': 'TICKS_DISABLED_BY_OPERATOR'}
        # 50-symbol historical backfill requires independent, explicit consent.
        if desired == 'backfill':
            try:
                config_path = Path(os.environ["HISTORY_50_CONTROL_FILE"])
                if config_path.is_symlink():
                    raise ValueError("CONTROL_SYMLINK")
                approved = json.loads(config_path.read_text(encoding="utf-8"))
                enabled = isinstance(approved, dict) and approved.get("enabled") is True
            except (OSError, ValueError, KeyError):
                enabled = False
            if not enabled:
                desired = None
                decision = {**decision, 'mode': 'HISTORY_PAUSED',
                            'reason': 'HISTORY_50_EXPLICIT_START_REQUIRED'}
"""
    body=replace_once(body,old,new)
    old="""            if desired == 'backfill' and self.args.symbols_file:
                cmd += ['--symbols-file', str(self.args.symbols_file)]
"""
    new="""            if desired == 'backfill' and self.args.symbols_file:
                cmd += ['--symbols-file', str(self.args.symbols_file)]
                # Fixed US-market-local research window; no future/OOS access.
                cmd += ['--since', '2015-01-01T00:00:00-05:00',
                        '--until', '2026-10-01T23:59:59-04:00']
"""
    body=replace_once(body,old,new)
    f.write_text(body,encoding="utf-8")
    print("HISTORY_50_SCHEDULER_GUARD_PATCH=PASS")
if __name__=="__main__":
    if len(sys.argv)!=2:
        raise SystemExit("USAGE: patch-history-50.py STAGED_RELEASE")
    apply(sys.argv[1])
