#!/usr/bin/env python3
"""Single-host Toss OAuth broker over an access-controlled Unix socket.

Run as the credential-owning service account. Clients must set TOSS_TOKEN_SOCKET,
and never mint independently. Never expose this socket outside the machine.
"""
import argparse
import fcntl
import signal
import json
import os
import socket
import socketserver
import struct
from pathlib import Path

from toss_auth import get_token


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            # Filesystem permissions plus peer credentials; never trust uid in request.
            raw = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _, uid, _ = struct.unpack("3i", raw)
            if uid not in self.server.allowed_uids:
                return
            request = json.loads(self.rfile.readline(4096))
            force = request.get("force") is True
            rejected = request.get("rejected_token", "")
            if not isinstance(rejected, str) or len(rejected) > 8192:
                raise ValueError("INVALID_REJECTED_TOKEN")
            token = get_token(self.server.credentials, force=force, rejected_token=rejected)
            result = {"ok": bool(token), "token": token} if token else {"ok": False}
        except Exception:
            # Do not print or return credentials, token, HTTP response body.
            result = {"ok": False}
        self.wfile.write((json.dumps(result) + "\n").encode())


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--secrets", required=True)
    parser.add_argument("--group-gid", required=True, type=int)
    parser.add_argument("--allow-uid", action="append", type=int, required=True)
    args = parser.parse_args()
    if os.environ.get("TOSS_TOKEN_SOCKET"):
        raise SystemExit("BROKER_MUST_MINT_LOCALLY")
    secrets = Path(args.secrets)
    if not secrets.is_file() or secrets.is_symlink():
        raise SystemExit("SECRETS_FILE_REQUIRED")
    cfg = json.loads(secrets.read_text(encoding="utf-8"))
    sock_path = Path(args.socket)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    # Lifetime lock prevents a second broker replacing a live socket.
    lock = sock_path.with_suffix(".lock").open("a+")
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit("BROKER_ALREADY_RUNNING") from None
    if sock_path.exists():
        if not sock_path.is_socket():
            raise SystemExit("SOCKET_PATH_NOT_SOCKET")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            try: probe.connect(str(sock_path))
            except ConnectionRefusedError:
                if sock_path.stat().st_uid != os.getuid(): raise SystemExit("FOREIGN_SOCKET_OWNER")
                sock_path.unlink()  # proven stale, owned socket; no credential/cache deletion
            else: raise SystemExit("SOCKET_ALREADY_ACTIVE")
    def terminate(*_): raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    old_umask = os.umask(0o077)
    try:
        with Server(str(sock_path), Handler) as server:
            try:
                os.chown(sock_path, os.getuid(), args.group_gid)
                os.chmod(sock_path, 0o660)
                server.allowed_uids = set(args.allow_uid)
                server.credentials = cfg
                server.serve_forever(poll_interval=0.5)
            finally:
                sock_path.unlink(missing_ok=True)
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
