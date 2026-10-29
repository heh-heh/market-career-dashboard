#!/usr/bin/env python3
"""Fail-closed release overlay test with upstream-compatible scheduler anchors."""
import importlib.util
import tempfile
from pathlib import Path

p=Path(__file__).with_name("patch-history-50.py")
spec=importlib.util.spec_from_file_location("stock_overlay",p)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source="""import os, json
from pathlib import Path
def backfill_permitted(path,now):
    try:
        state=json.loads(Path(path).read_text())
        return (state['mode'] == 'MARKET_CLOSED' and
                now < aware(state['leaseExpiresAt']) and
                now < aware(state['requestDeadline']) and
                not regular_clock(now)['isOpen'])
    except Exception:
        return False
class Scheduler:
    def step(self,decision):
        desired = {'MARKET_OPEN': 'tick', 'MARKET_CLOSED': 'backfill'}.get(decision['mode'])
        if self.stopping: desired = None
        permit = {}
        # During every transition/shutdown invalidate REST permission before SIGTERM.
        if desired != 'backfill' or self.stopping: permit['mode'] = 'SWITCHING'
        if desired == 'tick':
            cmd = ['tick']
        else:
            cmd = ['history']
            if desired == 'backfill' and self.args.symbols_file:
                cmd += ['--symbols-file', str(self.args.symbols_file)]
"""
with tempfile.TemporaryDirectory() as root:
    f=Path(root)/"research_collection_scheduler.py"
    f.write_text(source)
    module.apply(root)
    result=f.read_text()
    assert "from collector_policy import enabled, choose_worker" in result
    assert "from collector_policy import enabled, rest_allowed" in result
    assert "'HISTORY_ONLY_RUNNING'" in result
    assert "if desired != 'backfill' or self.stopping:" in result
    assert "'2015-01-01T00:00:00-05:00'" in result
    assert "'2026-10-01T23:59:59-04:00'" in result
    assert "cmd = ['tick']" in result
    assert "cmd = ['history']" in result
    assert result.index("choose_worker") < result.index("if self.stopping")
    try:
        module.apply(root)
        raise AssertionError("Patch applied twice instead of failing closed")
    except SystemExit as exc:
        assert "UNREVIEWED_UPSTREAM" in str(exc)
print("STOCK_SCHEDULER_POLICY_STAGE_PATCH=PASS")
