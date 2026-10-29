#!/usr/bin/env python3
"""Reproduce start/stop under authenticated HTTP; verifies start never pauses.

Runs against isolated JSON control files. Never touches stock services or
credentials on the host and never opens a market API connection.
"""
from __future__ import annotations
import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import console


def call(base, token, csrf, endpoint, value):
    body=json.dumps(value).encode("utf-8")
    req=urllib.request.Request(base+endpoint,method="POST",data=body,
        headers={"Cookie":console.SESSION_COOKIE+"="+token,
                 "X-CSRF-Token":csrf,"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=5) as response:
        assert response.status==200
        return json.load(response)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        runtime=Path(tmp)
        (runtime/"historical-control.json").write_text(json.dumps({"enabled":False,"reason":"ADMIN_HISTORY_PAUSE"}))
        (runtime/"control.json").write_text(json.dumps({"paused":True,"reason":"USER_PAUSED"}))
        (runtime/"tick-control.json").write_text(json.dumps({"enabled":False,"reason":"USER_REQUESTED_TICKS_PAUSED"}))
        (runtime/"status.json").write_text(json.dumps({
            "mode":"TICKS_PAUSED","collector":None,
            "calendarConfirmed":True,"updatedAt":"2026-10-29T23:00:00Z"
        }))
        server=ThreadingHTTPServer(("127.0.0.1",0),console.Handler)
        host=f"127.0.0.1:{server.server_port}"
        base=f"http://{host}"
        console.ALLOWED_HOSTS.add(host)
        token="test-history-start-one-way"
        csrf="test-history-start-csrf"
        console.SESSIONS[token]={"expires":time.time()+90,"csrf":csrf}
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch.object(console,"COLLECTION_RUNTIME",runtime), \
                 patch.object(console,"service_state",return_value="running"), \
                 patch.object(console,"progress_snapshot",return_value={
                    "configured":True,"enabled":False,"symbols":[],"symbolCount":50
                 }):
                # Unauthorized calls cannot manipulate the master or history controls.
                req=urllib.request.Request(base+"/api/history/start",method="POST",data=b'{}',
                    headers={"Content-Type":"application/json"})
                try:
                    urllib.request.urlopen(req,timeout=5)
                    raise AssertionError("unauthenticated start succeeded")
                except urllib.error.HTTPError as exc:
                    assert exc.code==403
                result=call(base,token,csrf,"/api/history/start",{})
                assert result["enabled"] is True
                assert result["masterPaused"] is False
                assert result["mode"]=="TICKS_PAUSED"
                assert result["queued"] is True
                assert json.loads((runtime/"historical-control.json").read_text())["reason"]=="ADMIN_HISTORY_START"
                assert json.loads((runtime/"control.json").read_text())["paused"] is False
                assert json.loads((runtime/"tick-control.json").read_text())["enabled"] is False
                # Start is idempotent. No second click can implicitly pause collection.
                result=call(base,token,csrf,"/api/history/start",{})
                assert result["enabled"] is True
                assert json.loads((runtime/"historical-control.json").read_text())["enabled"] is True
                # Only the explicit STOP route with enabled=false may pause history.
                result=call(base,token,csrf,"/api/history/control",{"enabled":False})
                assert result["enabled"] is False
                assert result["lastAction"]=="ADMIN_HISTORY_PAUSE"
                assert json.loads((runtime/"historical-control.json").read_text())["enabled"] is False
                assert json.loads((runtime/"control.json").read_text())["paused"] is False
                assert json.loads((runtime/"tick-control.json").read_text())["enabled"] is False
                # Start again: old pause reason must not reappear.
                call(base,token,csrf,"/api/history/start",{})
                assert json.loads((runtime/"historical-control.json").read_text())["reason"]=="ADMIN_HISTORY_START"
        finally:
            console.SESSIONS.pop(token,None)
            console.ALLOWED_HOSTS.discard(host)
            server.shutdown();server.server_close();thread.join(timeout=5)
    print("HISTORY_START_PAUSE_HTTP_E2E=PASS")


if __name__=="__main__":
    main()
