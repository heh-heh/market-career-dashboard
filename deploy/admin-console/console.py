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
from mfa_auth import password_valid, otp_match_step
from history_progress import progress_snapshot
from data_inventory import inventory

HERE = Path(__file__).resolve().parent
REPO = Path("/srv/apps/market-career-dashboard")
DATA = Path("/srv/ai-data/market-dashboard")
PRIVATE = Path("/srv/apps/_deploy/dashboard/admin-console/private")
BIND = "127.0.0.1"
PORT = 18085
MAX_OUTPUT = 24000
MAX_BODY = 8192
MAX_AGE = 60 * 60
SESSION_COOKIE = "market_home_admin"
SESSIONS = {}
FAILED_LOGINS = {}
TICKETS = {}
LAST_OTP_STEP = {}
PAGES_ORIGIN = "https://heh-heh.github.io"
REMOTE_HOST = "heh-heh.tail39e546.ts.net:8444"
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


def _atomic_control(path: Path, value: dict) -> None:
    """Write a collector-owned control atomically, without following symlinks."""
    if path.is_symlink() or not path.is_file() or path.parent.is_symlink():
        raise OSError("INVALID_COLLECTOR_CONTROL")
    temp=path.with_name(path.name+"."+secrets.token_hex(12)+".tmp")
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as output:
            json.dump(value,output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


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


def otp_enrolled() -> bool:
    data = _json_file(PRIVATE / "credentials.json")
    return bool(data.get("username") and data.get("totp_secret")
                and data.get("remote_host") == REMOTE_HOST)


def verify_login(username: str, password: str, otp: str, *, require_mfa: bool) -> bool:
    record = _json_file(PRIVATE / "credentials.json")
    if not isinstance(password, str) or not password_valid(record, password):
        return False
    if otp_enrolled():
        if not isinstance(username, str) or not hmac.compare_digest(
                username.encode("utf-8"), str(record["username"]).encode("utf-8")):
            return False
        step = otp_match_step(record["totp_secret"], otp)
        if step is None:
            return False
        with LOCK:
            previous = LAST_OTP_STEP.get(username, -1)
            if step <= previous:
                return False
            LAST_OTP_STEP[username] = step
        return True
    # Backwards-compatible localhost login only until enrollment. Remote login
    # must NEVER use the legacy password-only path.
    return not require_mfa


def login_limited() -> bool:
    with LOCK:
        count, reset = FAILED_LOGINS.get("local", (0, 0))
        if time.time() > reset:
            FAILED_LOGINS["local"] = (0, time.time() + 600)
            return False
        return count >= 8


def login_failure() -> None:
    with LOCK:
        count, reset = FAILED_LOGINS.get("local", (0, time.time() + 600))
        FAILED_LOGINS["local"] = (count + 1, reset)


def login_success() -> None:
    with LOCK:
        FAILED_LOGINS.pop("local", None)


def create_session() -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with LOCK:
        SESSIONS[token] = {"expires": time.time() + MAX_AGE, "csrf": csrf}
    return token, csrf


def new_ticket() -> str:
    value=secrets.token_urlsafe(36)
    with LOCK:
        for key, expiration in list(TICKETS.items()):
            if expiration < time.time():
                TICKETS.pop(key, None)
        TICKETS[hashlib.sha256(value.encode("ascii")).digest()] = time.time()+45
    return value


def consume_ticket(value: str) -> bool:
    if not isinstance(value,str) or len(value)>160:
        return False
    digest=hashlib.sha256(value.encode("utf-8")).digest()
    with LOCK:
        deadline=TICKETS.pop(digest,0)
    return deadline>=time.time()



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


def telemetry_snapshot() -> dict:
    """Private authenticated view of the already sanitized, read-only host metrics.

    No control endpoints, secret files, shell commands or credentials are involved.
    """
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:18084/api/server-telemetry", timeout=3
        ) as response:
            telemetry = json.load(response)
        if (not isinstance(telemetry, dict) or
                telemetry.get("mode") != "PUBLIC_READ_ONLY_AGGREGATED" or
                telemetry.get("ok") is not True):
            raise ValueError("UNVERIFIED_TELEMETRY")
        return telemetry
    except (OSError, ValueError, TypeError):
        return {"ok": False, "error": "TELEMETRY_API_NOT_READY"}


def overview() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:18084/api/health", timeout=3) as response:
            health = json.load(response)
        # Explicit allowlist so future API fields or secrets are not relayed.
        api = {key: health.get(key) for key in
               ("ok", "service", "mode", "dataReady", "dataUpdatedAt", "tradingEnabled", "researchEnabled")}
    except (OSError, ValueError):
        api = {"ok": False, "error": "API 연결 실패"}
    telemetry = telemetry_snapshot()
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
    tick_control = _json_file(runtime / "tick-control.json")
    tick_enabled = tick_control.get("enabled") is True
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
    paper_dir = Path("/srv/ai-data/paper-arena")
    paper_raw = _json_file(paper_dir / "status.json")
    paper_ctl = _json_file(paper_dir / "control.json")
    paper_keys = (
        "mode", "version", "researchOnly", "realOrdersEnabled",
        "executionAssumption", "updatedAt", "state", "feedReason", "sessionDate",
        "bots", "botsWithPositions", "initialCapitalUsd", "cashUsd", "investedUsd",
        "equityUsd", "pnlUsd", "returnPct", "realizedPnlUsd",
        "positionsCount", "uniqueHoldings", "tradeCount", "ticksProcessedThisPoll",
    )
    paper = {key: paper_raw.get(key) for key in paper_keys}
    paper["service"] = service_state("market-paper-arena.service")
    paper["paused"] = paper_ctl.get("paused") is not False
    # Never propagate untrusted/huge data to the authenticated browser.
    paper["holdings"] = [
        {k: item.get(k) for k in ("symbol", "shares", "accounts", "marketValueUsd", "unrealizedPnlUsd", "quoteTime")}
        for item in paper_raw.get("holdings", [])[:6] if isinstance(item, dict)
    ] if isinstance(paper_raw.get("holdings"), list) else []
    paper["quotes"] = [
        {k: item.get(k) for k in ("symbol", "priceUsd", "receivedAt")}
        for item in paper_raw.get("quotes", [])[:6] if isinstance(item, dict)
    ] if isinstance(paper_raw.get("quotes"), list) else []
    paper["recentTrades"] = [
        {k: item.get(k) for k in ("at", "bot", "side", "symbol", "qty", "fillUsd", "feeUsd")}
        for item in paper_raw.get("recentTrades", [])[:10] if isinstance(item, dict)
    ] if isinstance(paper_raw.get("recentTrades"), list) else []
    # Twenty isolated *paper* account status cards. No broker keys or raw files.
    paper["agents"] = []
    raw_agents = paper_raw.get("agents")
    if isinstance(raw_agents, list):
        for entry in raw_agents[:20]:
            if not isinstance(entry, dict):
                continue
            agent_id = entry.get("id")
            if not isinstance(agent_id, str) or not re.fullmatch(r"A(?:0[1-9]|1[0-9]|20)", agent_id):
                continue
            safe = {key: entry.get(key) for key in (
                "id", "strategy", "state", "initialCapitalUsd", "cashUsd",
                "investedUsd", "equityUsd", "pnlUsd", "returnPct",
                "realizedPnlUsd", "unrealizedPnlUsd", "positionCount",
                "tradeCount", "markStale"
            )}
            watchlist = entry.get("watchlist")
            safe["watchlist"] = [
                sym for sym in watchlist[:6]
                if isinstance(sym, str) and re.fullmatch(r"[A-Z0-9]{1,8}", sym)
            ] if isinstance(watchlist, list) else []
            holdings = entry.get("holdings")
            safe["holdings"] = [
                {key: item.get(key) for key in (
                    "symbol", "qty", "entryUsd", "markUsd", "marketValueUsd",
                    "unrealizedPnlUsd", "openedAt", "quoteAt", "markStale"
                )}
                for item in holdings[:3] if isinstance(item, dict)
            ] if isinstance(holdings, list) else []
            last = entry.get("lastTrade")
            safe["lastTrade"] = {key: last.get(key) for key in (
                "at", "side", "symbol", "qty", "fillUsd"
            )} if isinstance(last, dict) else None
            paper["agents"].append(safe)
    ai_dir = Path("/srv/ai-data/ai-research")
    # AI engine has not been migrated. Do not treat archived files as running bots.
    return {
        "api": api,
        "telemetry": telemetry,
        "collection": collection,
        "paper": paper,
        "research": {"status": "NOT_DEPLOYED", "running": False, "note": "AI V0.8.3 학습 엔진은 별도 이전 예정입니다."},
        "bots": {"status": "NOT_DEPLOYED", "count": None, "note": "현재 운영 중인 학습 봇이 없습니다."},
        "historical50": progress_snapshot(),
        "historical": {"storageExists": history_dir.is_dir(),
                       "storagePath": str(prod_data / "historical"),
                       "collectorStatus": scheduler.get("mode") or ("SMOKE_DATA_ONLY" if history_summary else "NOT_DEPLOYED"),
                       "serviceState": scheduler_service,
                       "paused": collection_paused,
                       "activeWorker": scheduler.get("collector"),
                       "calendarConfirmed": scheduler.get("calendarConfirmed"),
                       "sessionDate": scheduler.get("sessionDate"),
                       "reason": str(scheduler.get("reason") or "")[:80],
                       "lastExitCode": scheduler.get("lastExitCode") if isinstance(scheduler.get("lastExitCode"), int) and not isinstance(scheduler.get("lastExitCode"), bool) else None,
                       "workerFailures": scheduler.get("workerFailures") if isinstance(scheduler.get("workerFailures"), int) and not isinstance(scheduler.get("workerFailures"), bool) else None,
                       "lastSchedulerUpdate": str(scheduler.get("updatedAt") or "")[:40],
                       "verification": "UNVERIFIED",
                       "automaticCollection": scheduler_service == "running" and not collection_paused,
                       "smokeSymbols": history_summary,
                       "symbols": live_summary},
        "ticks": {"service": "market-stock-collection.service",
                  "enabled": tick_enabled,
                  "active": scheduler.get("collector") == "tick",
                  "controlReason": str(tick_control.get("reason") or "MISSING_DISABLED")[:80],
                  "state": scheduler_service,
                  "serviceState": scheduler_service,
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
                         "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def reply(self, status: int, obj: dict, extra: dict | None = None):
        self.send_bytes(status, json.dumps(obj, ensure_ascii=False).encode(),
                        "application/json; charset=utf-8", extra)

    def is_remote(self) -> bool:
        return self.headers.get("Host", "") == REMOTE_HOST

    def allowed_host(self) -> bool:
        host=self.headers.get("Host", "")
        return (self.client_address[0] in ("127.0.0.1", "::1")
                and (host in ALLOWED_HOSTS or (host == REMOTE_HOST and otp_enrolled())))

    def origin_valid(self) -> bool:
        scheme = "https" if self.is_remote() else "http"
        return self.headers.get("Origin", "") == scheme + "://" + self.headers.get("Host", "")

    def pages_origin(self) -> bool:
        return (self.is_remote() and otp_enrolled()
                and self.headers.get("Origin", "") == PAGES_ORIGIN)

    def send_cors(self, status: int, data: dict) -> None:
        self.reply(status,data,{"Access-Control-Allow-Origin":PAGES_ORIGIN,
                                "Vary":"Origin", "Access-Control-Allow-Private-Network":"true"})

    def session_cookie(self, token: str, max_age: int = MAX_AGE) -> str:
        # Lax is needed for a top-level cross-site post/redirect from GitHub
        # Pages; all subsequent write requests still require strict Origin + CSRF.
        flags = "; Secure" if self.is_remote() else ""
        return (f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; "
                f"SameSite=Lax; Max-Age={max_age}{flags}")

    def do_OPTIONS(self):
        if not self.allowed_host() or not self.pages_origin():
            self.reply(403,{"error":"Forbidden"})
            return
        if urlsplit(self.path).path != "/api/remote/login":
            self.reply(404,{"error":"Not found"})
            return
        if self.headers.get("Access-Control-Request-Method") != "POST":
            self.reply(403,{"error":"METHOD_DENIED"})
            return
        self.send_bytes(204,b"","text/plain",{
            "Access-Control-Allow-Origin":PAGES_ORIGIN,
            "Access-Control-Allow-Methods":"POST",
            "Access-Control-Allow-Headers":"Content-Type",
            "Access-Control-Allow-Private-Network":"true",
            "Access-Control-Max-Age":"600",
            "Vary":"Origin",
        })

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
        if path.path in ("/", "/app.js", "/server-metrics.js", "/server-metrics.css"):
            if path.path == "/app.js":
                filename, mime = "app.js", "text/javascript; charset=utf-8"
            elif path.path == "/server-metrics.js":
                filename, mime = "server-metrics.js", "text/javascript; charset=utf-8"
            elif path.path == "/server-metrics.css":
                filename, mime = "server-metrics.css", "text/css; charset=utf-8"
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
        elif path.path == "/api/history/status":
            self.reply(200, progress_snapshot())
        elif path.path == "/api/data/inventory":
            self.reply(200, inventory())
        elif path.path == "/api/telemetry":
            self.reply(200, telemetry_snapshot())
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
        path = urlsplit(self.path).path
        if path == "/auth/claim":
            # One-time handoff from static Pages to the private, same-origin
            # admin UI; never put credentials or bearer tokens into a URL.
            if not self.pages_origin():
                self.reply(403, {"error": "INVALID_ORIGIN"})
                return
            try:
                size = int(self.headers.get("Content-Length","0"))
                if not 0 < size <= 512 or self.headers.get("Content-Type","").split(";")[0] != "application/x-www-form-urlencoded":
                    raise ValueError()
                data = parse_qs(self.rfile.read(size).decode("ascii"),strict_parsing=True)
                ticket = data.get("ticket", [])
                if len(ticket)!=1 or not consume_ticket(ticket[0]):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError, OSError):
                self.reply(403, {"error": "INVALID_OR_EXPIRED_TICKET"})
                return
            token, csrf = create_session()
            self.send_bytes(303,b"","text/plain",{"Set-Cookie":self.session_cookie(token),
                                                    "Location":"/"})
            return
        if path == "/api/remote/login":
            if not self.pages_origin():
                self.reply(403, {"error":"INVALID_ORIGIN"})
                return
            body = self.json_body()
            if body is None:
                self.send_cors(400,{"error":"INVALID_JSON"})
                return
            if login_limited():
                self.send_cors(429,{"error":"TOO_MANY_ATTEMPTS"})
                return
            username, password, otp = (body.get(k) for k in ("username","password","otp"))
            if not (isinstance(username,str) and len(username)<=64 and
                    isinstance(password,str) and len(password)<=256 and
                    isinstance(otp,str) and verify_login(username,password,otp,require_mfa=True)):
                login_failure()
                self.send_cors(401,{"error":"INVALID_CREDENTIALS"})
                return
            login_success()
            self.send_cors(200,{"ok":True,"ticket":new_ticket()})
            return
        if not self.origin_valid():
            self.reply(403, {"error": "INVALID_ORIGIN"})
            return
        body = self.json_body()
        if body is None:
            self.reply(400, {"error": "INVALID_JSON"})
            return
        if path == "/api/login":
            if login_limited():
                self.reply(429, {"error": "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요."})
                return
            username, password, otp = (body.get(k) for k in ("username","password","otp"))
            # Remotely accessible HTTPS admin login always requires 3 factors.
            # Local SSH can use the original password until enrollment.
            good = verify_login(username,password,otp,require_mfa=self.is_remote())
            if not good:
                login_failure()
                self.reply(401, {"error": "아이디, 비밀번호 또는 OTP가 올바르지 않습니다."})
                return
            login_success()
            token, csrf = create_session()
            self.reply(200, {"ok": True, "csrf": csrf},
                       {"Set-Cookie": self.session_cookie(token)})
            return
        session = active_session(self)
        if not session or not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session[1]):
            self.reply(403, {"error": "AUTH_OR_CSRF_REQUIRED"})
            return
        if path == "/api/logout":
            with LOCK:
                SESSIONS.pop(session[0], None)
            self.reply(200, {"ok": True},
                       {"Set-Cookie": self.session_cookie("", 0)})
            return
        if path == "/api/paper/control":
            paused = body.get("paused")
            if type(paused) is not bool:
                self.reply(400, {"error": "INVALID_CONTROL_VALUE"})
                return
            if paused is False and service_state("market-paper-arena.service") != "running":
                self.reply(409, {"error": "PAPER_SERVICE_NOT_RUNNING"})
                return
            control_dir = Path("/srv/ai-data/paper-arena")
            control_file = control_dir / "control.json"
            if not control_dir.is_dir() or control_dir.is_symlink() or control_file.is_symlink():
                self.reply(409, {"error": "PAPER_NOT_INSTALLED"})
                return
            command = {
                "paused": paused,
                "reason": "ADMIN_UI_PAUSE" if paused else "ADMIN_UI_RESUME",
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }
            temp = control_dir / ("control." + secrets.token_hex(12) + ".tmp")
            try:
                fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump(command, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temp, control_file)
            except OSError:
                self.reply(500, {"error": "PAPER_CONTROL_WRITE_FAILED"})
                return
            finally:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
            self.reply(200, {"ok": True, "paused": paused, "status": "REQUESTED"})
            return
        if path == "/api/ticks/control":
            enabled = body.get("enabled")
            if type(enabled) is not bool:
                self.reply(400, {"error":"INVALID_TICK_CONTROL"})
                return
            config=Path("/srv/ai-data/stock-collection/runtime/collection/tick-control.json")
            if not config.is_file() or config.is_symlink() or config.parent.is_symlink():
                self.reply(409, {"error":"TICK_PAUSE_GATE_NOT_INSTALLED"})
                return
            if enabled and (service_state("market-stock-collection.service")!="running" or
                            service_state("market-stock-token-broker.service")!="running"):
                self.reply(409, {"error":"COLLECTOR_SERVICE_NOT_RUNNING"})
                return
            saved=_json_file(config)
            if type(saved.get("enabled")) is not bool:
                self.reply(409, {"error":"TICK_CONFIG_INVALID"})
                return
            new={"enabled":enabled,
                 "reason":"ADMIN_TICK_RESUME" if enabled else "ADMIN_TICK_PAUSE",
                 "updatedAt":datetime.now(timezone.utc).isoformat()}
            temp=config.with_name("tick-control."+secrets.token_hex(12)+".tmp")
            try:
                fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,"w",encoding="utf-8") as output:
                    json.dump(new,output)
                    output.flush();os.fsync(output.fileno())
                os.replace(temp,config)
            except OSError:
                self.reply(500,{"error":"TICK_CONTROL_WRITE_FAILED"})
                return
            finally:
                temp.unlink(missing_ok=True)
            self.reply(200,{"ok":True,"enabled":enabled,"status":"REQUESTED"})
            return
        if path == "/api/history/start":
            # One explicit operator action starts/queues historical collection,
            # without modifying the independently disabled 1-tick gate.
            if service_state("market-stock-token-broker.service") != "running" or service_state("market-stock-collection.service") != "running":
                self.reply(409,{"error":"STOCK_COLLECTOR_SERVICES_NOT_RUNNING"})
                return
            runtime=Path("/srv/ai-data/stock-collection/runtime/collection")
            historical=runtime/"historical-control.json"
            master=runtime/"control.json"
            if runtime.is_symlink() or not runtime.is_dir() or any(
                    p.is_symlink() or not p.is_file() for p in (historical,master)):
                self.reply(409,{"error":"HISTORICAL_OR_SCHEDULER_CONTROL_NOT_INSTALLED"})
                return
            if not progress_snapshot().get("configured"):
                self.reply(409,{"error":"HISTORICAL_50_SYMBOLS_NOT_READY"})
                return
            h_old=_json_file(historical)
            m_old=_json_file(master)
            if type(h_old.get("enabled")) is not bool or type(m_old.get("paused")) is not bool:
                self.reply(409,{"error":"CONTROL_FILE_UNEXPECTED_STATE"})
                return
            # Write both permits in the same authenticated request. The shared
            # scheduler resume alone cannot re-enable the separately paused tick.
            updated=datetime.now(timezone.utc).isoformat()
            try:
                for target,payload in (
                    (master,{"paused":False,"reason":"ADMIN_HISTORY_START_MASTER","updatedAt":updated}),
                    (historical,{"enabled":True,"reason":"ADMIN_HISTORY_START","updatedAt":updated})
                ):
                    _atomic_control(target,payload)
            except OSError:
                self.reply(500,{"error":"HISTORICAL_START_WRITE_FAILED"})
                return
            verified_h=_json_file(historical)
            verified_m=_json_file(master)
            if verified_h.get("enabled") is not True or verified_m.get("paused") is not False:
                self.reply(500,{"error":"HISTORICAL_START_VERIFICATION_FAILED"})
                return
            scheduler=_json_file(runtime/"status.json")
            mode=scheduler.get("mode") if isinstance(scheduler.get("mode"),str) else "UNKNOWN"
            worker=scheduler.get("collector") if scheduler.get("collector") in ("tick","backfill") else None
            self.reply(200,{"ok":True,"enabled":True,"masterPaused":False,
                            "mode":mode,"collector":worker,
                            "queued":worker!="backfill",
                            "lastAction":"ADMIN_HISTORY_START","updatedAt":updated})
            return
        if path == "/api/history/control":
            enabled=body.get("enabled")
            if type(enabled) is not bool:
                self.reply(400,{"error":"INVALID_HISTORICAL_CONTROL"})
                return
            control_file=Path("/srv/ai-data/stock-collection/runtime/collection/historical-control.json")
            if control_file.is_symlink() or not control_file.parent.is_dir() or not control_file.is_file():
                self.reply(409,{"error":"HISTORICAL_50_NOT_INSTALLED"})
                return
            current=progress_snapshot()
            if not current.get("configured"):
                self.reply(409,{"error":"HISTORICAL_50_SYMBOLS_NOT_READY"})
                return
            updated=datetime.now(timezone.utc).isoformat()
            data={"enabled":enabled,"reason":"ADMIN_HISTORY_RESUME" if enabled else "ADMIN_HISTORY_PAUSE",
                  "updatedAt":updated}
            try:
                _atomic_control(control_file,data)
            except OSError:
                self.reply(500,{"error":"HISTORICAL_CONTROL_WRITE_FAILED"})
                return
            verified=_json_file(control_file)
            if verified.get("enabled") is not enabled:
                self.reply(500,{"error":"HISTORICAL_CONTROL_VERIFY_FAILED"})
                return
            self.reply(200,{"ok":True,"enabled":enabled,"lastAction":data["reason"],
                            "updatedAt":updated,
                            "note":"SCHEDULER_REQUIRES_MASTER_RESUME_AND_OFFHOURS"})
            return
        if path == "/api/collection/control":
            desired = body.get("paused")
            if type(desired) is not bool:
                self.reply(400, {"error": "INVALID_CONTROL_VALUE"})
                return
            if desired is False and (
                service_state("market-stock-token-broker.service") != "running" or
                service_state("market-stock-collection.service") != "running"
            ):
                self.reply(409, {"error": "COLLECTION_SERVICE_NOT_RUNNING"})
                return
            control_dir = Path("/srv/ai-data/stock-collection/runtime/collection")
            control_file = control_dir / "control.json"
            if not control_dir.is_dir() or control_dir.is_symlink() or control_file.is_symlink():
                self.reply(409, {"error": "COLLECTION_NOT_INSTALLED"})
                return
            control_data = {
                "paused": desired,
                "reason": "ADMIN_UI_PAUSE" if desired else "ADMIN_UI_RESUME",
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }
            temp = control_dir / ("control." + secrets.token_hex(12) + ".tmp")
            try:
                fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump(control_data, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temp, control_file)
            except OSError:
                self.reply(500, {"error": "CONTROL_WRITE_FAILED"})
                return
            finally:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
            self.reply(200, {"ok": True, "paused": desired, "status": "REQUESTED"})
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
