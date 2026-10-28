#!/usr/bin/env python3
"""Private, read-only home-server and Git console. Never expose using Funnel."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

HERE = Path(__file__).resolve().parent
REPO = Path("/srv/apps/market-career-dashboard")
DATA = Path("/srv/ai-data/market-dashboard")
BIND = "127.0.0.1"
PORT = 18085
MAX_OUTPUT = 24_000
COMMANDS = {
    "server.containers": (["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"], None),
    "server.resources": (["free", "-h"], None),
    "server.disks": (["df", "-h", "/", "/srv/ai-data"], None),
    "server.uptime": (["uptime"], None),
    "server.docker-stats": (["docker", "stats", "--no-stream", "--format", "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"], None),
    "server.dashboard-api": (["docker", "logs", "--tail", "60", "market-dashboard-api"], None),
    "server.collector-logs": (["docker", "logs", "--tail", "60", "market-dashboard-collector"], None),
    "server.funnel": (["tailscale", "funnel", "status"], None),
    "git.status": (["git", "status", "--short", "--branch"], REPO),
    "git.log": (["git", "log", "-n", "12", "--date=short", "--pretty=format:%h %ad %s"], REPO),
    "git.branches": (["git", "branch", "-avv"], REPO),
    "git.diff": (["git", "diff", "--stat"], REPO),
    "git.staged": (["git", "diff", "--cached", "--stat"], REPO),
    "git.worktrees": (["git", "worktree", "list"], REPO),
}
ALLOWED_HOSTS = {"127.0.0.1:18085", "localhost:18085"}


def run_known(action: str) -> dict:
    if action == "server.collector-status":
        path = DATA / "collector_status.json"
        if not path.exists():
            return {"ok": False, "text": "수집 상태 파일이 없습니다."}
        try:
            obj = json.loads(path.read_text(encoding="utf-8")[:65536])
            safe = {k: obj.get(k) for k in ("phase", "startedAt", "finishedAt", "lastSuccessAt", "counts", "errorCode", "lastError") if k in obj}
            return {"ok": True, "text": json.dumps(safe, indent=2, ensure_ascii=False)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "text": "수집 상태 읽기 실패: " + type(exc).__name__}
    spec = COMMANDS.get(action)
    if spec is None:
        return {"ok": False, "text": "허용되지 않은 조회 항목입니다."}
    command, cwd = spec
    try:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        result = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=12, check=False,
        )
        output = (result.stdout + result.stderr)[:MAX_OUTPUT]
        return {"ok": result.returncode == 0, "text": output or "(출력 없음)", "exitCode": result.returncode}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "text": "실행 실패: " + type(exc).__name__}


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "LocalAdminConsole/1.0"

    def _send(self, status: int, data: bytes, mime: str):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("Host", "") not in ALLOWED_HOSTS:
            self._send(403, b"Forbidden host", "text/plain; charset=utf-8")
            return
        request = urlsplit(self.path)
        if request.path == "/api/run":
            qs = parse_qs(request.query)
            actions = qs.get("action", [])
            action = actions[0] if len(actions) == 1 else ""
            obj = run_known(action)
            obj["action"] = action
            obj["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._send(200, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            return
        if request.path in ("/", "/app.js"):
            filename = "index.html" if request.path == "/" else "app.js"
            mime = "text/html; charset=utf-8" if filename.endswith(".html") else "text/javascript; charset=utf-8"
            try:
                self._send(200, (HERE / filename).read_bytes(), mime)
            except OSError:
                self._send(500, b"Asset not available", "text/plain")
            return
        if request.path == "/api/health":
            self._send(200, b'{"ok":true,"scope":"localhost-only","mode":"read-only"}', "application/json")
            return
        self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        self._send(405, b"Read-only admin console", "text/plain")

    def log_message(self, format, *args):
        # Do not log query strings or user-provided values.
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer((BIND, PORT), ConsoleHandler)
    print(f"Private dashboard console listening on http://{BIND}:{PORT}", flush=True)
    server.serve_forever()
