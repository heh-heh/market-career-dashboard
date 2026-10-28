#!/usr/bin/env python3
"""SSH-only authenticated operator portal. No arbitrary shell or broker execution."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

HERE = Path(__file__).resolve().parent
REPO = Path("/srv/apps/market-career-dashboard")
DATA = Path("/srv/ai-data/market-dashboard")
PRIVATE = Path("/srv/apps/_deploy/dashboard/admin-console/private")
BIND = "127.0.0.1"
PORT = 18085
MAX_OUTPUT = 24000
MAX_BODY = 8192
MAX_AGE = 8 * 3600
SESSION_COOKIE = "market_home_admin"
SESSIONS = {}
FAILED_LOGINS = {}
LOCK = threading.Lock()
VALID_KEYS = ("OPENAI_API_KEY", "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET",
              "TOSS_ACCOUNT_SEQ", "NEWS_API_KEY", "ALPHA_VANTAGE_API_KEY")
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


def _json_file(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_private(path: Path, obj: dict) -> None:
    PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = PRIVATE / (path.name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def verify_password(password: str) -> bool:
    record = _json_file(PRIVATE / "credentials.json")
    try:
        salt = base64.b64decode(record["salt"], validate=True)
        expected = base64.b64decode(record["digest"], validate=True)
        rounds = int(record["iterations"])
        if not 250000 <= rounds <= 1000000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
        return hmac.compare_digest(actual, expected)
    except (ValueError, KeyError, TypeError):
        return False


def active_session(request: BaseHTTPRequestHandler) -> tuple[str, str] | None:
    jar = cookies.SimpleCookie()
    try:
        jar.load(request.headers.get("Cookie", ""))
        token = jar[SESSION_COOKIE].value
    except (KeyError, cookies.CookieError, ValueError):
        return None
    with LOCK:
        state = SESSIONS.get(token)
        if state is not None and state["expires"] > time.time():
            return token, state["csrf"]
        SESSIONS.pop(token, None)
        return None


def run_known(action: str) -> dict:
    if action == "server.collector-status":
        status = _json_file(DATA / "collector_status.json")
        safe = {k: status[k] for k in
                ("phase", "startedAt", "finishedAt", "lastSuccessAt", "counts", "errorCode", "lastError")
                if k in status}
        return {"ok": bool(safe), "text": json.dumps(safe, ensure_ascii=False, indent=2) if safe else "수집 상태가 없습니다."}
    spec = COMMANDS.get(action)
    if spec is None:
        return {"ok": False, "text": "허용되지 않은 명령입니다."}
    command, cwd = spec
    try:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_OPTIONAL_LOCKS"] = "0"
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=12, check=False)
        return {"ok": result.returncode == 0,
                "text": (result.stdout + result.stderr)[:MAX_OUTPUT] or "(출력 없음)",
                "exitCode": result.returncode}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "text": "실행 실패: " + type(exc).__name__}


def service_state(service: str) -> str:
    try:
        r = subprocess.run(["systemctl", "is-active", service], capture_output=True,
                           text=True, timeout=4, check=False)
        value = r.stdout.strip()
        return "running" if value == "active" else "not-running"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def overview() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:18084/api/health", timeout=3) as response:
            health = json.load(response)
        # Explicit allowlist so future API fields or secrets are not relayed.
        api = {key: health.get(key) for key in
               ("ok", "service", "mode", "dataReady", "dataUpdatedAt", "tradingEnabled", "researchEnabled")}
    except (OSError, ValueError):
        api = {"ok": False, "error": "API 연결 실패"}
    collect = _json_file(DATA / "collector_status.json")
    collection = {k: collect.get(k) for k in ("phase", "startedAt", "finishedAt", "lastSuccessAt", "counts")}
    history_dir = Path("/srv/ai-data/stock-collection/historical-smoke")
    history_progress = _json_file(history_dir / "_collection_progress.json")
    history_summary = {}
    for symbol in ("NVDA", "SOXL", "SOXS", "SPY"):
        item = history_progress.get(symbol)
        if isinstance(item, dict):
            history_summary[symbol] = {
                "status": str(item.get("status", "unknown"))[:70],
                "storedRows": item.get("stored"),
                "oldestTimestamp": item.get("oldestTimestamp"),
                "latestTimestamp": item.get("latestTimestamp"),
                "completedPages": item.get("page"),
            }
    prod_data = Path("/srv/ai-data/stock-collection")
    runtime = prod_data / "runtime/collection"
    scheduler = _json_file(runtime / "status.json")
    control = _json_file(runtime / "control.json")
    live_progress = _json_file(prod_data / "historical/_collection_progress.json")
    tick_progress = _json_file(prod_data / "ticks/status.json")
    scheduler_service = service_state("market-stock-collection.service")
    collection_paused = control.get("paused") is not False
    live_summary = {}
    for symbol, state in sorted(live_progress.items()):
        if not isinstance(symbol, str) or not isinstance(state, dict):
            continue
        if not re.fullmatch(r"[A-Z0-9]{1,8}", symbol):
            continue
        live_summary[symbol] = {
            "status": str(state.get("status", "unknown"))[:70],
            "storedRows": state.get("stored"),
            "verifiedTradingDays": state.get("verifiedTradingDays"),
            "missingTradingDays": state.get("missingTradingDays"),
            "latestTimestamp": state.get("latestTimestamp"),
        }
        if len(live_summary) >= 50:
            break
    ai_dir = Path("/srv/ai-data/ai-research")
    # AI engine has not been migrated. Do not treat archived files as running bots.
    return {
        "api": api,
        "collection": collection,
        "research": {"status": "NOT_DEPLOYED", "running": False, "note": "AI V0.8.3 학습 엔진은 별도 이전 예정입니다."},
        "bots": {"status": "NOT_DEPLOYED", "count": None, "note": "현재 운영 중인 학습 봇이 없습니다."},
        "historical": {"storageExists": history_dir.is_dir(),
                       "storagePath": str(prod_data / "historical"),
                       "collectorStatus": scheduler.get("mode") or ("SMOKE_DATA_ONLY" if history_summary else "NOT_DEPLOYED"),
                       "serviceState": scheduler_service,
                       "paused": collection_paused,
                       "activeWorker": scheduler.get("collector"),
                       "calendarConfirmed": scheduler.get("calendarConfirmed"),
                       "sessionDate": scheduler.get("sessionDate"),
                       "reason": str(scheduler.get("reason") or "")[:80],
                       "verification": "UNVERIFIED",
                       "automaticCollection": scheduler_service == "running" and not collection_paused,
                       "smokeSymbols": history_summary,
                       "symbols": live_summary},
        "ticks": {"service": "market-stock-collection.service",
                  "state": scheduler_service,
                  "mode": scheduler.get("mode") or "NOT_DEPLOYED",
                  "paused": collection_paused,
                  "activeWorker": scheduler.get("collector"),
                  "lastUpdate": tick_progress.get("updatedAt"),
                  "feedState": str(tick_progress.get("state") or "NO_TICK_STATUS")[:80],
                  "sessionDate": tick_progress.get("sessionDate"),
                  "symbolCount": len(tick_progress.get("symbols") or []) if isinstance(tick_progress.get("symbols"), list) else None,
                  "note": "정규장에는 Tick, 장외에는 과거 1분봉 보충을 단일 스케줄러가 배타적으로 실행합니다."},
        "keys": {name: bool(_json_file(PRIVATE / "api_keys.json").get(name)) for name in VALID_KEYS},
        "meta": {"adminAccess": "SSH tunnel only", "serverPath": str(REPO),
                 "aiStorageReady": ai_dir.is_dir(), "publicAdmin": False}
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "PrivateDashboardAdmin/2.0"

    def send_bytes(self, status: int, raw: bytes, mime: str, extra: dict | None = None):
        self.send_response(status)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def reply(self, status: int, obj: dict, extra: dict | None = None):
        self.send_bytes(status, json.dumps(obj, ensure_ascii=False).encode(),
                        "application/json; charset=utf-8", extra)

    def allowed_host(self) -> bool:
        return self.headers.get("Host", "") in ALLOWED_HOSTS and self.client_address[0] in ("127.0.0.1", "::1")

    def origin_valid(self) -> bool:
        return self.headers.get("Origin", "") == "http://" + self.headers.get("Host", "")

    def json_body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= MAX_BODY or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return None
            data = json.loads(self.rfile.read(n))
            return data if isinstance(data, dict) else None
        except (ValueError, OSError):
            return None

    def do_GET(self):
        if not self.allowed_host():
            self.reply(403, {"error": "Forbidden"})
            return
        path = urlsplit(self.path)
        if path.path == "/api/health":
            self.reply(200, {"ok": True, "scope": "localhost-only", "mode": "authenticated-admin"})
            return
        if path.path in ("/", "/app.js"):
            if path.path == "/app.js":
                filename, mime = "app.js", "text/javascript; charset=utf-8"
            else:
                filename, mime = "index.html", "text/html; charset=utf-8"
            try:
                self.send_bytes(200, (HERE / filename).read_bytes(), mime)
            except OSError:
                self.send_bytes(500, b"Asset unavailable", "text/plain")
            return
        session = active_session(self)
        if not session:
            self.reply(401, {"error": "LOGIN_REQUIRED"})
            return
        if path.path == "/api/me":
            self.reply(200, {"authenticated": True, "csrf": session[1]})
        elif path.path == "/api/overview":
            self.reply(200, overview())
        elif path.path == "/api/run":
            names = parse_qs(path.query).get("action", [])
            action = names[0] if len(names) == 1 else ""
            self.reply(200, run_known(action))
        else:
            self.reply(404, {"error": "Not found"})

    def do_POST(self):
        if not self.allowed_host():
            self.reply(403, {"error": "Forbidden"})
            return
        if not self.origin_valid():
            self.reply(403, {"error": "INVALID_ORIGIN"})
            return
        path = urlsplit(self.path).path
        body = self.json_body()
        if body is None:
            self.reply(400, {"error": "INVALID_JSON"})
            return
        if path == "/api/login":
            with LOCK:
                count, reset = FAILED_LOGINS.get("local", (0, 0))
                if time.time() > reset:
                    count, reset = 0, time.time() + 600
                if count >= 8:
                    self.reply(429, {"error": "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요."})
                    return
            password = body.get("password", "")
            good = isinstance(password, str) and 0 < len(password) <= 256 and verify_password(password)
            if not good:
                with LOCK:
                    FAILED_LOGINS["local"] = (count + 1, reset)
                self.reply(401, {"error": "비밀번호가 올바르지 않습니다."})
                return
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            with LOCK:
                FAILED_LOGINS.pop("local", None)
                SESSIONS[token] = {"expires": time.time() + MAX_AGE, "csrf": csrf}
            self.reply(200, {"ok": True, "csrf": csrf},
                       {"Set-Cookie": f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={MAX_AGE}"})
            return
        session = active_session(self)
        if not session or not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session[1]):
            self.reply(403, {"error": "AUTH_OR_CSRF_REQUIRED"})
            return
        if path == "/api/logout":
            with LOCK:
                SESSIONS.pop(session[0], None)
            self.reply(200, {"ok": True},
                       {"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
            return
        if path == "/api/keys":
            name = body.get("name", "")
            value = body.get("value", "")
            if name not in VALID_KEYS or not isinstance(value, str) or len(value) > 2048:
                self.reply(400, {"error": "INVALID_KEY"})
                return
            stored = _json_file(PRIVATE / "api_keys.json")
            if value:
                stored[name] = value
            else:
                stored.pop(name, None)
            try:
                _atomic_private(PRIVATE / "api_keys.json", stored)
            except OSError:
                self.reply(500, {"error": "CREDENTIAL_WRITE_FAILED"})
                return
            self.reply(200, {"ok": True, "name": name, "configured": bool(value)})
            return
        self.reply(404, {"error": "Not found"})

    def log_message(self, format, *args):
        # Never emit URL query strings, passwords, or credentials into logs.
        pass


if __name__ == "__main__":
    if not (PRIVATE / "credentials.json").is_file():
        raise SystemExit("ADMIN_CREDENTIALS_NOT_CONFIGURED")
    print(f"Private authenticated portal http://{BIND}:{PORT}", flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
