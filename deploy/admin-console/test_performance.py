#!/usr/bin/env python3
"""Regression for private Task Manager telemetry read without exposing controls."""
from __future__ import annotations
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import console

def main():
    sample={"ok":True,"mode":"PUBLIC_READ_ONLY_AGGREGATED",
            "metrics":{"cpuPct":31.0},"history":[{"at":"2026-10-29T00:00:00+00:00","cpuPct":31.0}]}
    srv=ThreadingHTTPServer(("127.0.0.1",0),console.Handler)
    host=f"127.0.0.1:{srv.server_port}"
    console.ALLOWED_HOSTS.add(host)
    worker=threading.Thread(target=srv.serve_forever,daemon=True)
    worker.start()
    url=f"http://{host}/api/telemetry"
    token="test-performance-session-only"
    try:
        with patch.object(console,"telemetry_snapshot",return_value=sample):
            try:
                urllib.request.urlopen(url,timeout=5)
                raise AssertionError("unauthenticated telemetry accessible")
            except urllib.error.HTTPError as exc:
                assert exc.code==401,exc.code
            console.SESSIONS[token]={"expires":time.time()+90,"csrf":"test-csrf"}
            request=urllib.request.Request(url,headers={"Cookie":console.SESSION_COOKIE+"="+token})
            with urllib.request.urlopen(request,timeout=5) as response:
                result=json.load(response)
                assert response.status==200
            assert result==sample
        print("PRIVATE_PERFORMANCE_AUTH_TEST=PASS")
    finally:
        console.SESSIONS.pop(token,None)
        console.ALLOWED_HOSTS.discard(host)
        srv.shutdown()
        srv.server_close()
        worker.join(timeout=5)

if __name__=="__main__":
    main()
