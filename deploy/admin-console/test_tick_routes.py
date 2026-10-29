#!/usr/bin/env python3
"""Private data-inventory/tick-control endpoints must never be public."""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import console


def run():
    server=ThreadingHTTPServer(("127.0.0.1",0),console.Handler)
    host=f"127.0.0.1:{server.server_port}"
    console.ALLOWED_HOSTS.add(host)
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    url=f"http://{host}"
    token="test-inventory-control-only"
    csrf="test-csrf-tick"
    try:
        try:
            urllib.request.urlopen(url+"/api/data/inventory",timeout=5)
            raise AssertionError("unauthenticated inventory leaked")
        except urllib.error.HTTPError as exc:
            assert exc.code==401
        try:
            r=urllib.request.Request(url+"/api/ticks/control",method="POST",
                data=b'{"enabled":false}',
                headers={"Content-Type":"application/json"})
            urllib.request.urlopen(r,timeout=5)
            raise AssertionError("unauthenticated tick control succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code==403
        console.SESSIONS[token]={"expires":time.time()+90,"csrf":csrf}
        fixture={"kind":"PRIVATE_COLLECTOR_STORAGE_INVENTORY","history":{"symbols":[]},"ticks":{}}
        with patch.object(console,"inventory",return_value=fixture):
            request=urllib.request.Request(url+"/api/data/inventory",
                headers={"Cookie":console.SESSION_COOKIE+"="+token})
            with urllib.request.urlopen(request,timeout=5) as response:
                assert json.load(response)==fixture
        # Authenticated but missing CSRF must not mutate real control.
        try:
            r=urllib.request.Request(url+"/api/ticks/control",method="POST",
                data=b'{"enabled":false}',
                headers={"Cookie":console.SESSION_COOKIE+"="+token,
                         "Content-Type":"application/json"})
            urllib.request.urlopen(r,timeout=5)
            raise AssertionError("CSRF-less tick-control succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code==403
        print("TICK_INVENTORY_PRIVATE_ROUTES=PASS")
    finally:
        console.SESSIONS.pop(token,None)
        console.ALLOWED_HOSTS.discard(host)
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


if __name__=="__main__":
    run()
