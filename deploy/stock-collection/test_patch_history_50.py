#!/usr/bin/env python3
"""No-network stage patch regression using exact upstream scheduler anchors."""
import importlib.util
import tempfile
from pathlib import Path

p=Path(__file__).with_name("patch-history-50.py")
spec=importlib.util.spec_from_file_location("history50patch",p)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source='''import os, json
from pathlib import Path
class Scheduler:
    def step(self, decision):
        desired = {'MARKET_OPEN': 'tick', 'MARKET_CLOSED': 'backfill'}.get(decision['mode'])
        if self.stopping: desired = None
        if desired == 'tick':
            cmd = ['tick']
        else:
            cmd = ['history']
            if desired == 'backfill' and self.args.symbols_file:
                cmd += ['--symbols-file', str(self.args.symbols_file)]
'''
with tempfile.TemporaryDirectory() as root:
    f=Path(root)/"research_collection_scheduler.py"
    f.write_text(source)
    module.apply(root)
    result=f.read_text()
    assert 'HISTORY_50_EXPLICIT_START_REQUIRED' in result
    assert 'TICKS_DISABLED_BY_OPERATOR' in result
    assert 'TICK_COLLECTION_CONTROL_FILE' in result
    assert "if desired == 'tick':" in result
    assert "if desired == 'backfill':" in result
    assert result.index("if desired == 'tick':") < result.index("if desired == 'backfill':")
    # A deleted/invalid tick-control.json must fail closed; historical gate
    # remains an independent decision and cannot be turned off by this setting.
    assert "'2015-01-01T00:00:00-05:00'" in result
    assert "'2026-10-01T23:59:59-04:00'" in result
    assert "if desired == 'tick':" in result
    assert "cmd = ['tick']" in result
    try:
        module.apply(root)
        raise AssertionError("Patch applied twice instead of failing closed")
    except SystemExit as exc:
        assert "UNREVIEWED_UPSTREAM" in str(exc)
print("HISTORY_50_STAGE_PATCH_TEST=PASS")
