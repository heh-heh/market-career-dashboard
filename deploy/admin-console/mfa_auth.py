"""Local-only authentication helpers, zero third-party dependencies."""
from __future__ import annotations
import base64
import hashlib
import hmac
import re
import struct
import time

def password_valid(record: dict, password: str) -> bool:
    try:
        salt=base64.b64decode(record["salt"],validate=True)
        digest=base64.b64decode(record["digest"],validate=True)
        n=int(record["iterations"])
        if not 250000 <= n <= 1000000 or not 0 < len(password) <= 256:
            return False
        actual=hashlib.pbkdf2_hmac("sha256",password.encode("utf-8"),salt,n)
        return hmac.compare_digest(actual,digest)
    except (KeyError,ValueError,TypeError):
        return False

def otp_at(secret: str, step: int) -> str:
    key=base64.b32decode(secret.upper().strip(),casefold=True)
    digest=hmac.new(key,struct.pack(">Q",step),hashlib.sha1).digest()
    pos=digest[-1]&15
    value=struct.unpack(">I",digest[pos:pos+4])[0]&0x7fffffff
    return str(value%1000000).zfill(6)

def otp_match_step(secret: str, submitted: str, now: float | None=None) -> int | None:
    if not isinstance(submitted,str) or re.fullmatch(r"[0-9]{6}",submitted) is None:
        return None
    clock=int(time.time() if now is None else now)//30
    try:
        for step in (clock-1,clock,clock+1):
            if step >= 0 and hmac.compare_digest(otp_at(secret,step),submitted):
                return step
    except (ValueError,TypeError,base64.binascii.Error):
        return None
    return None
