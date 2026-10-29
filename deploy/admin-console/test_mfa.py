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
    print("ADMIN_MFA_OFFLINE_TEST=PASS")

if __name__=="__main__":
    run()
