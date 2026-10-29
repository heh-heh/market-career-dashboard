#!/usr/bin/env python3
"""Enroll owner login + Google Authenticator compatible TOTP on home server.

Run interactively under heh-heh after installing admin console. The secret only
prints once on YOUR SSH screen and stays in mode-0600 server credentials.json.
"""
from __future__ import annotations
import base64
import getpass
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from mfa_auth import password_valid, otp_match_step

PRIVATE=Path("/srv/apps/_deploy/dashboard/admin-console/private")
CREDS=PRIVATE/"credentials.json"
HOST="heh-heh.tail39e546.ts.net:8444"

def main():
    if not sys.stdin.isatty():
        raise SystemExit("ERROR=INTERACTIVE_SSH_REQUIRED")
    if CREDS.is_symlink() or not CREDS.is_file():
        raise SystemExit("ERROR=CREDENTIAL_FILE_NOT_FOUND")
    if os.stat(CREDS).st_mode & 0o077:
        raise SystemExit("ERROR=CREDENTIAL_FILE_PERMISSIONS_UNSAFE")
    record=json.loads(CREDS.read_text(encoding="utf-8"))
    previous=getpass.getpass("기존 관리자 비밀번호 확인: ")
    if not password_valid(record,previous):
        raise SystemExit("ERROR=EXISTING_PASSWORD_INCORRECT")
    user=input("새 관리자 ID (영문·숫자·_·- / 3~32자): ").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{2,31}",user):
        raise SystemExit("ERROR=INVALID_USERNAME")
    if record.get("totp_secret") and record.get("username"):
        answer=input("기존 OTP를 새로 등록하시겠습니까? 기존 앱 코드는 무효화됩니다. [yes 입력]: ").strip()
        if answer!="yes":
            raise SystemExit("CANCELLED")

    expected_name=HOST.split(":",1)[0]
    try:
        result=subprocess.run(["tailscale","status","--json"],check=True,
            capture_output=True,text=True,timeout=7)
        name=json.loads(result.stdout).get("Self",{}).get("DNSName","").rstrip(".")
        if name and name!=expected_name:
            raise SystemExit("ERROR=TAILSCALE_DNS_MISMATCH detected="+name+
                             " expected="+expected_name)
    except (OSError,subprocess.TimeoutExpired,subprocess.CalledProcessError,
            ValueError) as exc:
        print("WARNING: Tailnet DNS 확인 불가. 실제 호스트와 일치하는지 확인하세요.")

    key=base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    print("\nGoogle Authenticator → + → 설정 키 입력")
    print("계정 이름: Market Dashboard ("+user+")")
    print("키 유형: 시간 기반")
    print("등록 키 (이번 터미널에서만 표시됨): "+key)
    print("설정 후 6자리 코드를 입력하고 이 정보가 보이는 화면은 닫으세요.\n")
    otp=getpass.getpass("Google Authenticator 6자리 확인 코드: ")
    if otp_match_step(key,otp) is None:
        raise SystemExit("ERROR=OTP_NOT_VERIFIED_NO_CHANGES")
    record.update({"username":user,"totp_secret":key,"remote_host":HOST,
                   "mfa_version":1})
    temporary=PRIVATE/("credentials."+secrets.token_hex(8)+".tmp")
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(record,stream,ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,CREDS)
    finally:
        temporary.unlink(missing_ok=True)
    print("MFA_ENROLLED=YES")
    print("REMOTE_ADMIN_URL=https://"+HOST+"/")
    print("아직 Tailscale Serve를 설정하지 않았다면 SSH에서 별도 설정해야 합니다.")
    print("등록 키는 복구용으로 별도 안전한 위치에 보관하세요.")

if __name__=="__main__":
    main()
