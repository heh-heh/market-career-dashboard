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

    old="        desired = {'MARKET_OPEN': 'tick', 'MARKET_CLOSED': 'backfill'}.get(decision['mode'])\\n"
    new="""        # One scheduler, two separate workers and two independent permissions.
        # The policy module authorizes historical-only collection even during
        # US regular hours when official calendars match.
        from collector_policy import enabled, choose_worker
        tick_allowed = enabled(os.environ.get('TICK_COLLECTION_CONTROL_FILE'))
        history_allowed = enabled(os.environ.get('HISTORY_50_CONTROL_FILE'))
        desired, decision = choose_worker(decision, ticks=tick_allowed,
                                          history=history_allowed)
"""
    body=replace_once(body,old,new)
    old="""        # During every transition/shutdown invalidate REST permission before SIGTERM.
"""
    new="""        # History-only mode grants a short renewed lease during market hours.
        # It does not change the exclusive worker lock or tick priority.
        if decision['mode'] == 'HISTORY_ONLY_RUNNING':
            permit['requestDeadline'] = (now+timedelta(seconds=4)).isoformat()
        # During every transition/shutdown invalidate REST permission before SIGTERM.
"""
    body=replace_once(body,old,new)
    old="""        return (state['mode'] == 'MARKET_CLOSED' and
                now < aware(state['leaseExpiresAt']) and
                now < aware(state['requestDeadline']) and
                not regular_clock(now)['isOpen'])
"""
    new="""        from collector_policy import enabled, rest_allowed
        return rest_allowed(
            state, now, is_market_open=regular_clock(now)['isOpen'],
            ticks=enabled(os.environ.get('TICK_COLLECTION_CONTROL_FILE')),
            history=enabled(os.environ.get('HISTORY_50_CONTROL_FILE')))
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
