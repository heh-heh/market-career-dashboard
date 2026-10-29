#!/usr/bin/env python3
"""Security regression: public route returns ONLY allowlisted telemetry fields."""
import json
import os
import tempfile
from pathlib import Path

os.environ["DASHBOARD_ONLY"]="true"
import server

class Reply:
    def __init__(self):
        self.status=None
        self.data=None
    def send_json(self,obj,status=200):
        self.status=status
        self.data=obj

def main():
    with tempfile.TemporaryDirectory() as temp:
        folder=Path(temp)
        old=server.DATA
        server.DATA=folder/"dashboard.json"
        try:
            (folder/"server-telemetry.json").write_text(json.dumps({
                "schemaVersion":1,
                "updatedAt":"2026-10-29T00:00:00+00:00",
                "extraPrivateHostname":"EXPOSED_IF_VULNERABLE",
                "metrics":{"memoryUsedGiB":4,"memoryTotalGiB":8,
                           "diskUsedGiB":100,"diskTotalGiB":200,
                           "dataDiskUsedGiB":100,"dataDiskTotalGiB":200,
                           "secretToken":"DO_NOT_RETURN"},
                "history":[{"at":"2026-10-29T00:00:00+00:00",
                            "cpuPct":22,"memoryPct":50,"networkRxMbps":3,
                            "networkTxMbps":1,"diskPct":50,
                            "dataDiskPct":60,"cpuPackageWatts":13,
                            "secretApiKey":"DO_NOT_RETURN"}]
            }),encoding="utf-8")
            stub=Reply()
            server.Handler.public_telemetry(stub)
            assert stub.status==200,(stub.status,stub.data)
            raw=json.dumps(stub.data)
            assert "EXPOSED_IF_VULNERABLE" not in raw
            assert "DO_NOT_RETURN" not in raw
            assert stub.data["metrics"]["systemWatts"] is None
            assert stub.data["history"][0]["cpuPct"]==22
            assert stub.data["mode"]=="PUBLIC_READ_ONLY_AGGREGATED"
            # Check the public API cannot reinterpret missing telemetry as success.
            (folder/"server-telemetry.json").unlink()
            missing=Reply()
            server.Handler.public_telemetry(missing)
            assert missing.status==503
        finally:
            server.DATA=old
    print("PUBLIC_METRICS_SCHEMA_TEST=PASS")

if __name__=="__main__":
    main()
