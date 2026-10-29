#!/usr/bin/env python3
"""Offline security checks; no network, live secrets or credentials required."""
from __future__ import annotations
import base64
import hashlib
import hmac
import time

from mfa_auth import otp_at, otp_match_step, password_valid

def run():
    # RFC 6238 SHA-1 test vector, truncated to six digits.
    secret=base64.b32encode(b"12345678901234567890").decode("ascii")
    assert otp_at(secret,59//30)=="287082", "TOTP RFC reference mismatch"
    assert otp_match_step(secret,"287082",now=59)==1
    assert otp_match_step(secret,"287082",now=89)==1
    assert otp_match_step(secret,"287082",now=119) is None
    assert otp_match_step(secret,"abc123",now=59) is None
    assert otp_match_step(secret,"28708",now=59) is None
    assert otp_match_step(secret,"2870823",now=59) is None
    password="correct-horse-2026"
    salt=b"some-random-salt"
    iterations=400000
    record={
        "salt":base64.b64encode(salt).decode("ascii"),
        "digest":base64.b64encode(
            hashlib.pbkdf2_hmac("sha256",password.encode(),salt,iterations)
        ).decode("ascii"),
        "iterations":iterations
    }
    assert password_valid(record,password)
    assert not password_valid(record,"wrong")
    assert not password_valid({"salt":"foo"},"password")
    # Verify the authentication gate: password-only SSH legacy works before
    # enrollment, but remote access never works without TOTP. Once enrolled,
    # BOTH the local and remote login paths require the same TOTP.
    from pathlib import Path
    import json
    import tempfile
    import console
    with tempfile.TemporaryDirectory(prefix="admin-mfa-test-") as temp:
        original=console.PRIVATE
        console.PRIVATE=Path(temp)
        try:
            p=Path(temp)/"credentials.json"
            p.write_text(json.dumps(record))
            assert console.verify_login(None,password,None,require_mfa=False)
            assert not console.verify_login(None,password,None,require_mfa=True)
            record["username"]="owner_a"
            record["totp_secret"]=secret
            record["remote_host"]=console.REMOTE_HOST
            p.write_text(json.dumps(record))
            from unittest.mock import patch
            with patch("console.otp_match_step",side_effect=lambda key,code: 12 if code=="287082" else None):
                assert not console.verify_login("owner_a",password,None,require_mfa=True)
                assert not console.verify_login("incorrect",password,"287082",require_mfa=True)
                assert console.verify_login("owner_a",password,"287082",require_mfa=True)
                assert not console.verify_login("owner_a",password,"287082",require_mfa=True), "TOTP replay accepted"
                assert not console.verify_login("owner_a","wrong","287082",require_mfa=False)
        finally:
            console.PRIVATE=original
            console.LAST_OTP_STEP.clear()
    print("ADMIN_MFA_OFFLINE_TEST=PASS")

if __name__=="__main__":
    run()
