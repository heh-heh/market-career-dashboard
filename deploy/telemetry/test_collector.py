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
from unittest.mock import patch
from subprocess import CompletedProcess, TimeoutExpired

good=CompletedProcess([],0,stdout="12, 256, 6144, 48, 31.5\n",stderr="")
with patch.object(collector.subprocess,"run",return_value=good):
    assert collector.gpu_measurements()=={
        "gpuPct":12.0,"gpuMemoryUsedMiB":256.0,"gpuMemoryTotalMiB":6144.0,
        "gpuTempC":48.0,"gpuWatts":31.5}
with patch.object(collector.subprocess,"run",side_effect=TimeoutExpired("nvidia-smi",3)):
    assert collector.gpu_measurements()["gpuPct"] is None
print("HOST_TELEMETRY_OFFLINE_TEST=PASS")
