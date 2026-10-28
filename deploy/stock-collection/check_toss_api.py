#!/usr/bin/env python3
"""Safe, one-shot Toss Market Data preflight for the SSH-only admin key vault.

Default is offline (no API calls). --test performs exactly one OAuth token request
and one 1-minute candle request, prints no credentials or bearer tokens.
Does not run the historical/tick collectors, create orders, or copy secrets.
"""
import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VAULT = Path("/srv/apps/_deploy/dashboard/admin-console/private/api_keys.json")
BASE = "https://openapi.tossinvest.com"


def load():
    if not VAULT.exists():
        return {}
    if VAULT.is_symlink():
        raise RuntimeError("VAULT_SYMLINK_REJECTED")
    if VAULT.stat().st_mode & 0o077:
        raise RuntimeError("VAULT_FILE_PERMISSIONS_TOO_OPEN")
    raw = json.loads(VAULT.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def request(url, data=None, headers=None, method=None):
    req = urllib.request.Request(
        url, data=data, headers=headers or {}, method=method
    )
    with urllib.request.urlopen(req, timeout=12) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="test OAuth and 1m candle via two API calls")
    parser.add_argument("--symbol", default="NVDA", choices=["NVDA", "SOXL", "SOXS", "SPY"])
    args = parser.parse_args()

    try:
        cfg = load()
    except (OSError, ValueError, RuntimeError) as e:
        print("VAULT_ERROR=" + type(e).__name__)
        return 2

    client_id = str(cfg.get("TOSS_CLIENT_ID") or "").strip()
    secret = str(cfg.get("TOSS_CLIENT_SECRET") or "").strip()
    if not client_id or not secret:
        print("TOSS_CREDENTIALS_MISSING")
        print("Admin -> Settings -> API Key -> TOSS_CLIENT_ID and TOSS_CLIENT_SECRET")
        return 2

    print("TOSS_CREDENTIALS_STORED=true")
    if not args.test:
        print("API_CALLS=0")
        return 0

    # This generates exactly one token; do not repeat while another client is using it.
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": secret
    }).encode("ascii")
    try:
        obj = request(BASE + "/oauth2/token", body, {
            "Content-Type": "application/x-www-form-urlencoded"
        }, "POST")
        tok = obj.get("access_token") if isinstance(obj, dict) else None
        if not isinstance(tok, str) or not tok:
            print("OAUTH_RESULT=NO_ACCESS_TOKEN")
            return 3
        print("OAUTH_RESULT=OK")
    except urllib.error.HTTPError as e:
        print(f"OAUTH_HTTP_STATUS={e.code}")
        if e.code == 403:
            print("ACTION=Register your home's PUBLIC outgoing IP in Toss WTS Open API allowlist")
        elif e.code == 401:
            print("ACTION=Check the client credentials in private admin UI")
        return 3
    except (OSError, ValueError) as e:
        print("OAUTH_ERROR=" + type(e).__name__)
        return 3

    query = urllib.parse.urlencode({"symbol": args.symbol, "interval": "1m", "count": "3"})
    try:
        obj = request(BASE + "/api/v1/candles?" + query,
                      headers={"Authorization": "Bearer " + tok}, method="GET")
        result = obj.get("result", {}) if isinstance(obj, dict) else {}
        candles = result.get("candles", []) if isinstance(result, dict) else []
        if not isinstance(candles, list):
            print("CANDLE_RESPONSE_UNEXPECTED")
            return 4
        print("CANDLES_HTTP_STATUS=200")
        print(f"CANDLE_SYMBOL={args.symbol}")
        print(f"CANDLES_COUNT={len(candles)}")
        if candles:
            timestamps = [str(c.get("timestamp", "")) for c in candles if isinstance(c, dict)]
            if timestamps:
                print("NEWEST_TIMESTAMP=" + timestamps[0][:40])
        print("COLLECTOR_NOT_STARTED=true")
        return 0
    except urllib.error.HTTPError as e:
        print(f"CANDLES_HTTP_STATUS={e.code}")
        return 4
    except (OSError, ValueError) as e:
        print("CANDLES_ERROR=" + type(e).__name__)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
