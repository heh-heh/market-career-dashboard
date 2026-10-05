#!/usr/bin/env python3
"""Shared Toss Securities OAuth token cache for server and background workers."""
from __future__ import annotations
import base64
import fcntl
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://openapi.tossinvest.com"
TOKEN_URL = BASE + "/oauth2/token"
CACHE = Path("/tmp/market-career-dashboard-toss-token.json")
LOCK = Path("/tmp/market-career-dashboard-toss-token.lock")

def get_token(cfg: dict, force: bool = False) -> str:
    toss = cfg.get("toss", {}) if isinstance(cfg, dict) else {}
    cid = str(toss.get("app_key") or "").strip()
    sec = str(toss.get("app_secret") or "").strip()
    if not cid or not sec:
        return ""

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        now = time.time()
        if not force and CACHE.exists():
            try:
                data = json.loads(CACHE.read_text(encoding="utf-8"))
                token = str(data.get("access_token") or "")
                expires_at = float(data.get("expires_at") or 0)
                if token and now < expires_at - 60:
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
            json.dumps({"access_token": token, "expires_at": now + expires}),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        os.replace(tmp, CACHE)
        return token
