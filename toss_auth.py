#!/usr/bin/env python3
"""Shared Toss Securities OAuth token cache for server and background workers."""
from __future__ import annotations
import base64
import fcntl
import json
import os
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://openapi.tossinvest.com"
TOKEN_URL = BASE + "/oauth2/token"
_UID = os.getuid()
CACHE = Path(f"/tmp/market-career-dashboard-toss-token-{_UID}.json")
LOCK = Path(f"/tmp/market-career-dashboard-toss-token-{_UID}.lock")

def broker_token(socket_path: str, force: bool = False, rejected_token: str = "") -> str:
    """Retrieve a shared token; never fall back to independent minting on failure."""
    request = json.dumps({"force": force, "rejected_token": rejected_token}).encode("utf-8") + b"
"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(15)
        sock.connect(socket_path)
        sock.sendall(request)
        with sock.makefile("rb") as stream:
            response = stream.readline(16384)
    if not response:
        raise RuntimeError("TOKEN_BROKER_EMPTY_RESPONSE")
    data = json.loads(response)
    if not data.get("ok") or not data.get("token"):
        raise RuntimeError("TOKEN_BROKER_UNAVAILABLE")
    return str(data["token"])


def get_token(cfg: dict, force: bool = False, *, rejected_token: str = "") -> str:
    socket_path = os.environ.get("TOSS_TOKEN_SOCKET", "").strip()
    if socket_path:
        return broker_token(socket_path, force=force, rejected_token=rejected_token)
    toss = cfg.get("toss", {}) if isinstance(cfg, dict) else {}
    cid = str(toss.get("app_key") or "").strip()
    sec = str(toss.get("app_secret") or "").strip()
    if not cid or not sec:
        return ""

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        now = time.time()
        if CACHE.exists():
            try:
                data = json.loads(CACHE.read_text(encoding="utf-8"))
                token = str(data.get("access_token") or "")
                expires_at = float(data.get("expires_at") or 0)
                issued_at = float(data.get("issued_at") or 0)
                if token and now < expires_at - 60 and (
                    (rejected_token and token != rejected_token) or
                    (not force and not rejected_token) or
                    (force and now - issued_at < 10)
                ):
                    return token
            except Exception:
                pass

        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": sec,
        }).encode()
        req = urllib.request.Request(
            TOKEN_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "market-career-dashboard/1.5",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            obj = json.loads(r.read())

        token = str(obj.get("access_token") or "")
        if not token:
            raise RuntimeError("Toss OAuth response did not contain access_token")
        expires = max(120, int(obj.get("expires_in") or 3600))
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"access_token": token, "expires_at": now + expires, "issued_at": now}),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        os.replace(tmp, CACHE)
        return token
