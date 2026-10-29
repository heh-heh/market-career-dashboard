#!/usr/bin/env python3
import math
import tempfile
from pathlib import Path
import collector

assert collector.compute_cpu((100,20),(200,80))==40.0
assert collector.compute_cpu(None,(1,1)) is None
assert collector.compute_rate((0,0),(125000,250000),1)==(1.0,2.0)
assert collector.compute_rate(None,(125000,0),1)==(None,None)
assert collector.compute_package_power((900000,1000000),(100000,1000000),1)==0.2
assert collector.compute_package_power(None,(100000,1000000),1) is None
with tempfile.TemporaryDirectory() as tmp:
    collector.OUTPUT=Path(tmp)/"output.json"
    collector.write_atomic({"schemaVersion":1,"history":[]})
    assert collector.read_previous()==[]
    assert collector.OUTPUT.stat().st_mode & 0o777 == 0o644
print("HOST_TELEMETRY_OFFLINE_TEST=PASS")
