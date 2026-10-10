#!/usr/bin/env python3
import asyncio, io, json, os, pwd, re, secrets, shlex, subprocess, sys, threading, time, urllib.parse, urllib.request, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from trading import paper as paper_broker, strategy as trading_strategy
from live_trader import LiveAutoTrader
from paper_trader import PaperV3Trader
from toss_auth import get_token as shared_toss_token
from toss_rate_limit import wait_for_slot, group_for_path
from simple_momentum_v1 import SimpleMomentumPaper, ReadOnlyTossMarketData, persistent_directory
from backtest_manager import BacktestManager, BACKTEST_ENGINES as RESEARCH_BACKTEST_ENGINES

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"/"dashboard.json"
TICK_DATA_DIR=ROOT/"data"/"toss_ticks"
TICK_STATUS=TICK_DATA_DIR/"status.json"
SECRETS=ROOT/"server_secrets.json"
HOST=os.getenv("HOST","0.0.0.0")
PORT=int(os.getenv("PORT","8080"))
INTERVAL=int(os.getenv("UPDATE_INTERVAL","300"))
ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD","")
SESSIONS={}
SESSION_TTL=int(os.getenv("SESSION_TTL","1800"))
SESSION_LOCK=threading.Lock()
TRADING_MODE=os.getenv("TRADING_MODE","paper").lower()
LIVE_TRADING_ENABLED=os.getenv("LIVE_TRADING_ENABLED","false").lower()=="true"
MAX_ORDER_KRW=int(os.getenv("MAX_ORDER_KRW","100000"))
MAX_DAILY_LOSS_KRW=int(os.getenv("MAX_DAILY_LOSS_KRW","50000"))
TRADING_STATE={
    "engine_enabled":False,
    "live_armed":False,
    "live_auto_enabled":False,
    "live_halted":False,
    "last_order_id":None,
    "last_error":None,
}
TRADING_LOCK=threading.Lock()
LIVE_TRADER=LiveAutoTrader(ROOT)
SIMPLE_PAPER=None
SIMPLE_PAPER_LOCK=threading.Lock()
STORAGE_CACHE={"at":0.0,"value":{}}
STORAGE_CACHE_LOCK=threading.Lock()

SIMPLE_BT_DIR=Path(os.getenv("BACKTEST_DATA_DIR","/var/lib/market-career-dashboard"))
SIMPLE_BT_STATE=SIMPLE_BT_DIR/"backtest_simple_v1_state.json"
SIMPLE_BT_RESULT=SIMPLE_BT_DIR/"backtest_simple_v1_result.json"
SIMPLE_BT_TRADES=SIMPLE_BT_DIR/"backtest_simple_v1_trades.csv"
SIMPLE_BT_LOG=SIMPLE_BT_DIR/"backtest_simple_v1.log"
SIMPLE_BT_LOCK=threading.Lock()

V3_BT_DIR=Path(os.getenv("BACKTEST_DATA_DIR","/var/lib/market-career-dashboard"))
V3_BT_STATE=V3_BT_DIR/"backtest_multistrategy_v1_state.json"
V3_BT_RESULT=V3_BT_DIR/"backtest_multistrategy_v1_result.json"
V3_BT_LOG=V3_BT_DIR/"backtest_multistrategy_v1.log"
V3_BT_LOCK=threading.Lock()

BACKTEST_ENGINES={
    "simple-v1":{
        "id":"simple-v1",
        "name":"Simple Momentum V1",
        "description":"급등 → 눌림 → 재상승 확인 기반 Simple V1",
    },
    "v3":{
        "id":"v3",
        "name":"Strategy Engine V3",
        "description":"기존 ORB/VWAP/Close Momentum multi-strategy V3",
    },
}
BACKTEST_ENGINES.update({k:{"id":k,"name":v["label"],"description":"IR_SPEC_V1 · 미검증 연구 가설 · 완료봉 다음 관측값 체결"}
                        for k,v in RESEARCH_BACKTEST_ENGINES.items() if k.startswith("v4-")})
BACKTEST_MANAGER=BacktestManager(ROOT)

def _read_json_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    except Exception:
        return {}

def _backtest_pid_running(pid, script_name):
    try:
        pid=int(pid or 0)
        if pid<=0:
            return False
        cmd=Path(f"/proc/{pid}/cmdline")
        if not cmd.exists():
            return False
        raw=cmd.read_bytes().replace(b"\\x00",b" ").decode("utf-8","ignore")
        return script_name in raw
    except Exception:
        return False

def _simple_bt_pid_running(pid):
    return _backtest_pid_running(pid,"backtest_simple_v1.py")

def simple_backtest_status():
    state=_read_json_file(SIMPLE_BT_STATE)
    result=_read_json_file(SIMPLE_BT_RESULT)
    pid=state.get("pid")
    running=_simple_bt_pid_running(pid)
    phase=str(state.get("phase") or ("completed" if result else "waiting"))
    if state.get("running") and not running and phase not in {"completed","error"}:
        phase="interrupted"
    log=""
    if SIMPLE_BT_LOG.exists():
        try:
            log="\n".join(SIMPLE_BT_LOG.read_text(encoding="utf-8",errors="ignore").splitlines()[-80:])
        except Exception:
            log=""
    return {
        "running":running,
        "phase":phase,
        "progress":float(state.get("progress") or (100 if result else 0)),
        "pid":pid if running else None,
        "updatedAt":state.get("updatedAt"),
        "currentTimestamp":state.get("currentTimestamp"),
        "processedRows":state.get("processedRows",0),
        "symbols":state.get("symbols",0),
        "trades":state.get("trades",0),
        "signals":state.get("signals",0),
        "entries":state.get("entries",0),
        "paperCash":state.get("paperCash"),
        "error":state.get("error"),
        "summary":result.get("overall") or state.get("summary") or {},
        "funnel":result.get("funnel") or {},
        "bySession":result.get("bySession") or {},
        "byExitReason":result.get("byExitReason") or {},
        "byYear":result.get("byYear") or {},
        "bySymbol":result.get("bySymbol") or {},
        "configuration":result.get("configuration") or {},
        "execution":result.get("execution") or {},
        "data":result.get("data") or {},
        "resultAvailable":bool(result),
        "downloadAvailable":SIMPLE_BT_RESULT.exists(),
        "log":log,
    }

def _start_simple_backtest():
    with SIMPLE_BT_LOCK:
        current=simple_backtest_status()
        if current.get("running"):
            raise ValueError("Simple V1 backtest is already running")
        SIMPLE_BT_DIR.mkdir(parents=True,exist_ok=True)
        for p in (SIMPLE_BT_STATE,SIMPLE_BT_RESULT,SIMPLE_BT_TRADES,SIMPLE_BT_LOG):
            try: p.unlink(missing_ok=True)
            except Exception: pass
        script=ROOT/"research"/"backtest_simple_v1.py"
        if not script.exists():
            raise RuntimeError("research/backtest_simple_v1.py is missing")
        data_dir=ROOT/"data"/"toss_1m"
        if not data_dir.exists() or not any(data_dir.glob("*.csv.gz")):
            raise RuntimeError("historical Toss 1m data is unavailable")
        cmd=[
            sys.executable,str(script),
            "--data",str(data_dir),
            "--out",str(SIMPLE_BT_RESULT),
            "--trades-csv",str(SIMPLE_BT_TRADES),
            "--state",str(SIMPLE_BT_STATE),
            "--log",str(SIMPLE_BT_LOG),
        ]
        subprocess.Popen(
            cmd,cwd=str(ROOT),stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            start_new_session=True,close_fds=True,
        )
        deadline=time.time()+2.0
        while time.time()<deadline:
            time.sleep(0.05)
            status=simple_backtest_status()
            if status.get("running") or status.get("phase") in {"starting","running","error"}:
                return status
        return simple_backtest_status()

def v3_backtest_status():
    state=_read_json_file(V3_BT_STATE)
    result=_read_json_file(V3_BT_RESULT)
    launch=_read_json_file(BACKTEST_MANAGER.paths("v3")["launch"])
    pid=state.get("pid") or launch.get("pid")
    running=_backtest_pid_running(pid,"backtest_multistrategy_v1.py")
    phase=str(state.get("phase") or ("starting" if running else "completed" if result else "waiting"))
    if state.get("running") and not running and phase not in {"completed","error"}:
        phase="interrupted"
    log=""
    if V3_BT_LOG.exists():
        try:
            log="\n".join(V3_BT_LOG.read_text(encoding="utf-8",errors="ignore").splitlines()[-80:])
        except Exception:
            log=""
    overall=result.get("overall") or state.get("summary") or {}
    summary={
        "trades":overall.get("trades",state.get("trades",0)),
        "wins":None,
        "losses":None,
        "winRatePct":overall.get("winRatePct",0),
        "sumTradeReturnPct":overall.get("totalReturnPct",0),
        "expectancyPct":overall.get("expectancyPct",0),
        "medianReturnPct":None,
        "avgWinPct":overall.get("avgWinPct",0),
        "avgLossPct":overall.get("avgLossPct",0),
        "payoffRatio":(
            float(overall.get("avgWinPct") or 0)/float(overall.get("avgLossPct") or 1)
            if float(overall.get("avgLossPct") or 0)>0 else 0
        ),
        "profitFactor":overall.get("profitFactor",0),
        "pnlUsd":None,
        "paperAccountReturnPct":None,
        "maxDrawdownPct":overall.get("maxDrawdownPct",0),
    }
    return {
        "engineId":"v3",
        "engineName":BACKTEST_ENGINES["v3"]["name"],
        "running":running,
        "phase":phase,
        "progress":float(state.get("progress") or (100 if result else 0)),
        "pid":pid if running else None,
        "updatedAt":state.get("updatedAt"),
        "currentTimestamp":state.get("currentDay"),
        "processedRows":None,
        "symbols":len(result.get("symbols") or state.get("symbols") or []),
        "trades":summary.get("trades",0),
        "signals":(result.get("funnel") or {}).get("buySignals",0),
        "entries":(result.get("funnel") or {}).get("trades",summary.get("trades",0)),
        "error":state.get("error"),
        "summary":summary,
        "funnel":result.get("funnel") or {},
        "breakdownLabel":"전략별 결과",
        "breakdown":result.get("byStrategy") or {},
        "secondaryBreakdownLabel":"종목·전략별 결과",
        "secondaryBreakdown":result.get("byTickerStrategy") or {},
        "byYear":{},
        "configuration":{"minFinalScore":result.get("minFinalScore")},
        "execution":result.get("execution") or {},
        "data":{"symbols":result.get("symbols") or []},
        "resultAvailable":bool(result),
        "downloadAvailable":V3_BT_RESULT.exists(),
        "log":log,
    }

def _start_v3_backtest():
    with V3_BT_LOCK:
        if simple_backtest_status().get("running"):
            raise ValueError("Simple V1 backtest is already running")
        current=v3_backtest_status()
        if current.get("running"):
            raise ValueError("V3 backtest is already running")
        V3_BT_DIR.mkdir(parents=True,exist_ok=True)
        for p in (V3_BT_STATE,V3_BT_RESULT,V3_BT_LOG):
            try: p.unlink(missing_ok=True)
            except Exception: pass
        script=ROOT/"research"/"backtest_multistrategy_v1.py"
        if not script.exists():
            raise RuntimeError("research/backtest_multistrategy_v1.py is missing")
        data_dir=ROOT/"data"/"toss_1m"
        if not data_dir.exists() or not any(data_dir.glob("*.csv.gz")):
            raise RuntimeError("historical Toss 1m data is unavailable")
        cmd=[
            sys.executable,str(script),
            "--data",str(data_dir),
            "--symbols","auto",
            "--out",str(V3_BT_RESULT),
            "--state",str(V3_BT_STATE),
            "--log",str(V3_BT_LOG),
        ]
        subprocess.Popen(
            cmd,cwd=str(ROOT),stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            start_new_session=True,close_fds=True,
        )
        deadline=time.time()+2.0
        while time.time()<deadline:
            time.sleep(0.05)
            status=v3_backtest_status()
            if status.get("running") or status.get("phase") in {"starting","backtest","error"}:
                return status
        return v3_backtest_status()

def start_simple_backtest():
    return BACKTEST_MANAGER.start("simple-v1",legacy_start=_start_simple_backtest)

def start_v3_backtest():
    BACKTEST_MANAGER.start("v3")
    return v3_backtest_status()

def backtest_engine_status(engine_id):
    engine_id=str(engine_id or "").strip().lower()
    if engine_id.startswith("v4-") and engine_id in BACKTEST_ENGINES:
        status=BACKTEST_MANAGER.status(engine_id)
        status.update(engineId=engine_id,engineName=BACKTEST_ENGINES[engine_id]["name"],
            breakdownLabel="전략별 결과" if engine_id=="v4-all" else "시장 regime별 결과" if engine_id=="v4-ir3" else "종목별 결과",
            breakdown=status.get("byStrategy",{}) if engine_id=="v4-all" else status.get("byRegime",{}) if engine_id=="v4-ir3" else status.get("bySymbol",{}),
            secondaryBreakdownLabel="청산 사유별 결과",secondaryBreakdown=status.get("byExitReason",{}))
        return status
    if engine_id=="simple-v1":
        status=simple_backtest_status()
        status.update({
            "engineId":"simple-v1",
            "engineName":BACKTEST_ENGINES["simple-v1"]["name"],
            "breakdownLabel":"세션별 결과",
            "breakdown":status.get("bySession") or {},
            "secondaryBreakdownLabel":"청산 사유별 결과",
            "secondaryBreakdown":status.get("byExitReason") or {},
        })
        return status
    if engine_id=="v3":
        return v3_backtest_status()
    raise ValueError("unsupported backtest engine")

def start_backtest_engine(engine_id):
    engine_id=str(engine_id or "").strip().lower()
    if engine_id.startswith("v4-") and engine_id in BACKTEST_ENGINES:
        BACKTEST_MANAGER.start(engine_id)
        return backtest_engine_status(engine_id)
    if engine_id=="simple-v1":
        if v3_backtest_status().get("running"):
            raise ValueError("V3 backtest is already running")
        return backtest_engine_status(engine_id) if simple_backtest_status().get("running") else {
            **start_simple_backtest(),
            "engineId":"simple-v1",
            "engineName":BACKTEST_ENGINES["simple-v1"]["name"],
        }
    if engine_id=="v3":
        return start_v3_backtest()
    raise ValueError("unsupported backtest engine")

def backtest_download_files(engine_id):
    engine_id=str(engine_id or "").strip().lower()
    if engine_id.startswith("v4-") and engine_id in BACKTEST_ENGINES:
        status=BACKTEST_MANAGER.status(engine_id)
        if status["running"]: raise ValueError("Backtest is still running")
        if not status["downloadAvailable"]: raise FileNotFoundError("Current run has no completed result")
        paths=BACKTEST_MANAGER.paths(engine_id)
        return [(paths[k],paths[k].name) for k in ("result","trades","state","log","audit","decisions")],engine_id
    if engine_id=="simple-v1":
        if not SIMPLE_BT_RESULT.exists():
            raise FileNotFoundError("backtest result is not available yet")
        return [
            (SIMPLE_BT_RESULT,"backtest_simple_v1_result.json"),
            (SIMPLE_BT_TRADES,"backtest_simple_v1_trades.csv"),
            (SIMPLE_BT_STATE,"backtest_simple_v1_state.json"),
            (SIMPLE_BT_LOG,"backtest_simple_v1.log"),
        ],"simple-v1"
    if engine_id=="v3":
        if not V3_BT_RESULT.exists():
            raise FileNotFoundError("backtest result is not available yet")
        return [
            (V3_BT_RESULT,"backtest_v3_result.json"),
            (V3_BT_STATE,"backtest_v3_state.json"),
            (V3_BT_LOG,"backtest_v3.log"),
        ],"v3"
    raise ValueError("unsupported backtest engine")

def storage_status():
    now=time.time()
    with STORAGE_CACHE_LOCK:
        if STORAGE_CACHE["value"] and now-STORAGE_CACHE["at"]<15:
            return dict(STORAGE_CACHE["value"])
    try:
        st=os.statvfs(str(ROOT))
        total=int(st.f_blocks*st.f_frsize)
        free=int(st.f_bavail*st.f_frsize)
        used=max(0,total-free)
        data_dir=ROOT/"data"/"toss_1m"
        data_bytes=0
        file_count=0
        if data_dir.exists():
            for p in data_dir.rglob("*"):
                try:
                    if p.is_file():
                        data_bytes+=p.stat().st_size
                        file_count+=1
                except OSError:
                    pass
        free_pct=(free/total*100.0) if total else 0.0
        level="critical" if free_pct<10 else "warning" if free_pct<20 else "ok"
        value={
            "totalBytes":total,
            "usedBytes":used,
            "freeBytes":free,
            "usedPct":round((used/total*100.0) if total else 0.0,2),
            "freePct":round(free_pct,2),
            "dataBytes":data_bytes,
            "dataFileCount":file_count,
            "level":level,
            "checkedAt":now,
        }
    except Exception as e:
        value={"error":str(e),"level":"unknown","checkedAt":now}
    with STORAGE_CACHE_LOCK:
        STORAGE_CACHE["at"]=now
        STORAGE_CACHE["value"]=dict(value)
    return value

def tick_collector_status():
    status=_read_json_file(TICK_STATUS)
    session_date=str(status.get("sessionDate") or "")
    session_dir=TICK_DATA_DIR/session_date if session_date else None
    meta=_read_json_file(session_dir/"session_meta.json") if session_dir else {}

    symbols=status.get("symbols") or meta.get("symbols") or []
    if not isinstance(symbols,list):
        symbols=[]
    counts=meta.get("ticksBySymbol") or {}
    if not isinstance(counts,dict):
        counts={}
    clean_counts={}
    for symbol,value in counts.items():
        try:
            clean_counts[str(symbol)]=int(value or 0)
        except Exception:
            clean_counts[str(symbol)]=0

    data_bytes=0
    data_files=0
    if session_dir and session_dir.exists():
        for p in session_dir.iterdir():
            try:
                if p.is_file() and (p.name.endswith(".csv") or p.name.endswith(".csv.gz")):
                    data_bytes+=p.stat().st_size
                    data_files+=1
            except OSError:
                pass

    updated_at=status.get("updatedAt") or meta.get("updatedAt")
    stale_seconds=None
    if updated_at:
        try:
            import datetime as _dt
            ts=_dt.datetime.fromisoformat(str(updated_at).replace("Z","+00:00"))
            if ts.tzinfo is None:
                ts=ts.replace(tzinfo=_dt.timezone.utc)
            stale_seconds=max(0.0,time.time()-ts.timestamp())
        except Exception:
            stale_seconds=None

    state=str(status.get("state") or "unavailable")
    healthy=bool(status) and state not in {"stopped","paused_low_disk"}
    if stale_seconds is not None and stale_seconds>900:
        healthy=False

    return {
        "available":bool(status),
        "healthy":healthy,
        "state":state,
        "provider":status.get("provider") or meta.get("provider"),
        "calendarSource":status.get("calendarSource") or meta.get("calendarSource"),
        "calendarError":status.get("calendarError"),
        "sessionDate":session_date or None,
        "marketSession":status.get("marketSession") or meta.get("currentSession"),
        "openAt":status.get("openAt") or meta.get("openAt"),
        "closeAt":status.get("closeAt") or meta.get("closeAt"),
        "nextOpenAt":status.get("nextOpenAt"),
        "nextCloseAt":status.get("nextCloseAt"),
        "connectionId":status.get("connectionId"),
        "subscribed":int(status.get("subscribed") or len(meta.get("subscribed") or [])),
        "symbolCount":len(symbols),
        "symbols":symbols,
        "ticksTotal":int(meta.get("ticksTotal") or sum(clean_counts.values())),
        "ticksBySymbol":clean_counts,
        "reconnects":int(meta.get("reconnects") or 0),
        "localQueueDrops":int(meta.get("localQueueDrops") or 0),
        "invalidMessages":int(meta.get("invalidMessages") or 0),
        "queueDepth":int(meta.get("queueDepth") or 0),
        "connectionAttempts":int(meta.get("connectionAttempts") or 0),
        "dataBytes":data_bytes,
        "dataFileCount":data_files,
        "updatedAt":updated_at,
        "staleSeconds":round(stale_seconds,1) if stale_seconds is not None else None,
        "lastError":status.get("error") or meta.get("lastError"),
        "lossySource":True,
        "sourceSequenceAvailable":False,
        "completenessClaim":"NONE",
    }


def load_secrets():
    if not SECRETS.exists():
        return {}
    try:
        return json.loads(SECRETS.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_secrets(obj):
    tmp=SECRETS.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(tmp,SECRETS)
    try:
        # The tick collector runs as ubuntu and needs read-only access to the
        # same API credentials. Keep owner=root and grant only the ubuntu group.
        ubuntu=pwd.getpwnam("ubuntu")
        os.chown(SECRETS,-1,ubuntu.pw_gid)
        os.chmod(SECRETS,0o640)
    except OSError:
        pass

def auth_token(handler):
    value=handler.headers.get("Authorization","")
    return value[7:].strip() if value.startswith("Bearer ") else ""

def authorized(handler):
    token=auth_token(handler)
    now=time.time()
    with SESSION_LOCK:
        expires=SESSIONS.get(token,0)
        if token and expires>now:
            return True
        if token:
            SESSIONS.pop(token,None)
        return False

def trading_authorized(handler):
    return authorized(handler)

ADMIN_CONSOLE_SERVICES={
    "market-career-dashboard.service",
    "collect-expanded-universe.service",
    "collect-toss-ticks.service",
    "backtest-v2-tqqq.service",
    "backtest-multistrategy.service",
    "backtest-hybrid-watchdog.service",
}
ADMIN_CONSOLE_LOGS={
    "collector": Path("/var/lib/market-career-dashboard/expanded_universe.log"),
    "v2": Path("/var/lib/market-career-dashboard/backtest_v2_tqqq_mr.log"),
    "multi": Path("/var/lib/market-career-dashboard/backtest_multistrategy_v1.log"),
}
ADMIN_CONSOLE_STATES={
    "collector": ROOT/"data"/"expanded_universe_progress.json",
    "v2": Path("/var/lib/market-career-dashboard/backtest_v2_tqqq_mr_state.json"),
    "multi": Path("/var/lib/market-career-dashboard/backtest_multistrategy_v1_state.json"),
}
ADMIN_CONSOLE_LOCK=threading.Lock()
GIT_CREDENTIAL_FILE=Path("/home/ubuntu/.config/market-dashboard/git-credentials")
GIT_LOCK=threading.Lock()

def _git_run(args,timeout=60):
    p=subprocess.run(
        ["git","-c",f"safe.directory={ROOT}","-C",str(ROOT),*args],
        cwd=str(ROOT),capture_output=True,text=True,
        timeout=timeout,check=False,
        env={**os.environ,"GIT_TERMINAL_PROMPT":"0"},
    )
    out=(p.stdout or "")+(p.stderr or "")
    if len(out)>30000:
        out=out[-30000:]+"\n[output truncated]"
    return p.returncode,out.rstrip()

def _install_runtime_requirements(timeout=300):
    python=ROOT/".venv"/"bin"/"python"
    requirements=ROOT/"requirements.txt"
    if not python.is_file():
        return 2,f"virtualenv python not found: {python}"
    if not requirements.is_file():
        return 2,f"requirements.txt not found: {requirements}"
    p=subprocess.run(
        [str(python),"-m","pip","install","-r",str(requirements)],
        cwd=str(ROOT),capture_output=True,text=True,
        timeout=timeout,check=False,
    )
    out=(p.stdout or "")+(p.stderr or "")
    if len(out)>30000:
        out=out[-30000:]+"\n[output truncated]"
    return p.returncode,out.rstrip()

def _git_branch():
    code,out=_git_run(["rev-parse","--abbrev-ref","HEAD"])
    branch=out.strip() if code==0 else ""
    if not branch or branch=="HEAD":
        raise RuntimeError("detached HEAD에서는 pull/push를 실행하지 않습니다.")
    return branch

def github_auth_status():
    code,remote=_git_run(["remote","get-url","origin"])
    remote=remote.strip() if code==0 else ""
    code,branch_out=_git_run(["rev-parse","--abbrev-ref","HEAD"])
    branch=branch_out.strip() if code==0 else ""
    code,status=_git_run(["status","--short","--branch"])
    return {
        "configured":GIT_CREDENTIAL_FILE.exists() and GIT_CREDENTIAL_FILE.stat().st_size>0,
        "remote":remote,
        "branch":branch,
        "status":status,
    }

def save_github_token(token):
    token=str(token or "").strip()
    if len(token)<20 or any(c.isspace() for c in token):
        raise ValueError("유효한 GitHub fine-grained PAT를 입력하세요.")
    req=urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization":"Bearer "+token,
            "Accept":"application/vnd.github+json",
            "User-Agent":"market-career-dashboard-admin",
        },
    )
    try:
        with urllib.request.urlopen(req,timeout=15) as r:
            user=json.loads(r.read() or b"{}")
    except Exception as e:
        raise ValueError("GitHub 토큰 확인에 실패했습니다. 권한과 토큰 상태를 확인하세요.") from e
    login=str(user.get("login") or "").strip()
    if not login:
        raise ValueError("GitHub 사용자 정보를 확인하지 못했습니다.")

    GIT_CREDENTIAL_FILE.parent.mkdir(parents=True,exist_ok=True)
    encoded_user=urllib.parse.quote("x-access-token",safe="")
    encoded_token=urllib.parse.quote(token,safe="")
    GIT_CREDENTIAL_FILE.write_text(
        f"https://{encoded_user}:{encoded_token}@github.com\n",
        encoding="utf-8",
    )
    os.chmod(GIT_CREDENTIAL_FILE,0o600)
    try:
        u=pwd.getpwnam("ubuntu")
        os.chown(GIT_CREDENTIAL_FILE,u.pw_uid,u.pw_gid)
        os.chown(GIT_CREDENTIAL_FILE.parent,u.pw_uid,u.pw_gid)
    except Exception:
        pass

    helper=f"store --file {GIT_CREDENTIAL_FILE}"
    code,out=_git_run(["config","--local","credential.helper",helper])
    if code!=0:
        raise RuntimeError(out or "credential.helper 설정 실패")
    _git_run(["config","--local","user.name",login])
    _git_run(["config","--local","user.email",f"{login}@users.noreply.github.com"])
    return {"login":login,**github_auth_status()}

def clear_github_token():
    try:
        GIT_CREDENTIAL_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    _git_run(["config","--local","--unset-all","credential.helper"])
    return github_auth_status()

def run_git_action(action):
    action=str(action or "").strip().lower()
    if action=="status":
        return _git_run(["status","--short","--branch"])
    if action=="log":
        return _git_run(["log","--oneline","--decorate","-n","30"])
    if action=="branches":
        return _git_run(["branch","-vv"])
    if action=="diff":
        return _git_run(["diff","--stat"])
    if action=="fetch":
        return _git_run(["fetch","--prune","origin"],timeout=120)
    if action=="pull":
        branch=_git_branch()
        if branch=="main":
            code,remote_head=_git_run(["rev-parse","origin/strategy-engine-v3"])
            if code==0:
                return 2,(
                    "현재 EC2 로컬 브랜치가 main이지만 배포 코드는 strategy-engine-v3입니다.\n"
                    "GitHub · Git 탭의 '배포 브랜치 맞추기'를 먼저 실행하세요."
                )
        return _git_run(["pull","--ff-only","origin",branch],timeout=120)
    if action=="sync-v3":
        code,out=_git_run(["fetch","--prune","origin"],timeout=120)
        if code!=0:
            return code,out
        code2,out2=_git_run(["switch","-C","strategy-engine-v3","origin/strategy-engine-v3"],timeout=120)
        if code2!=0:
            return code2,(out+"\n"+out2).strip()
        code3,out3=_git_run(["branch","--set-upstream-to=origin/strategy-engine-v3","strategy-engine-v3"])
        if code3!=0:
            return code3,(out+"\n"+out2+"\n"+out3).strip()
        code4,out4=_install_runtime_requirements()
        return code4,(out+"\n"+out2+"\n"+out3+"\n[dependencies]\n"+out4).strip()
    if action=="push":
        branch=_git_branch()
        return _git_run(["push","origin",f"HEAD:{branch}"],timeout=120)
    raise ValueError("unsupported git action")

def _console_run(args,timeout=15):
    p=subprocess.run(
        args,cwd=str(ROOT),capture_output=True,text=True,
        timeout=timeout,check=False,
    )
    out=(p.stdout or "")+(p.stderr or "")
    if len(out)>30000:
        out=out[-30000:]+"\n[output truncated]"
    return p.returncode,out.rstrip()

def run_admin_console(command):
    cmd=str(command or "").strip()
    if not cmd:
        return 0,""
    if len(cmd)>500:
        raise ValueError("command too long")
    try:
        parts=shlex.split(cmd)
    except Exception as e:
        raise ValueError("invalid command") from e
    if not parts:
        return 0,""

    if any(x in cmd for x in ("&&","||",";","\x60","$(" ,">","<","\n","\r")):
        raise ValueError("shell operators are not allowed")

    if parts[0] in {"help","?"}:
        return 0,(
            "허용 명령\n"
            "  pwd\n"
            "  df -h /\n"
            "  du -sh data/toss_1m\n"
            "  git status|branch|diff|fetch|pull|push\n"
            "  (배포 브랜치 정리는 관리자 Git 탭의 전용 버튼 사용)\n"
            "  git log [N]\n"
            "  systemctl status|is-active|restart <service>\n"
            "  api status|restart\n"
            "  deps status|install\n"
            "  journalctl <service> [N]\n"
            "  tail collector|v2|multi [N]\n"
            "  state collector|v2|multi\n"
            "서비스: "+", ".join(sorted(ADMIN_CONSOLE_SERVICES))
        )

    if parts==["pwd"]:
        return 0,str(ROOT)

    if parts in (["df","-h"],["df","-h","/"]):
        return _console_run(["df","-h","/"])

    if parts in (["du","-sh","data/toss_1m"],["du","-sh",str(ROOT/"data"/"toss_1m")]):
        return _console_run(["du","-sh",str(ROOT/"data"/"toss_1m")],timeout=30)

    if parts[:2]==["git","status"] and len(parts)==2:
        return _git_run(["status","--short","--branch"])

    if parts[:2]==["git","branch"] and len(parts)==2:
        return _git_run(["branch","-vv"])

    if parts[:2]==["git","diff"] and len(parts)==2:
        return _git_run(["diff","--stat"])

    if parts[:2]==["git","fetch"] and len(parts)==2:
        return _git_run(["fetch","--prune","origin"],timeout=120)

    if parts[:2]==["git","pull"] and len(parts)==2:
        branch=_git_branch()
        return _git_run(["pull","--ff-only","origin",branch],timeout=120)

    if parts[:2]==["git","push"] and len(parts)==2:
        branch=_git_branch()
        return _git_run(["push","origin",f"HEAD:{branch}"],timeout=120)

    if parts[:2]==["git","log"]:
        n=10
        if len(parts)==3:
            n=max(1,min(50,int(parts[2])))
        elif len(parts)>3:
            raise ValueError("usage: git log [N]")
        return _git_run(["log","--oneline","-n",str(n)])

    if parts and parts[0]=="api":
        if parts==["api","status"]:
            return _console_run(
                ["systemctl","status","market-career-dashboard.service","--no-pager","--full"],
                timeout=20,
            )
        if parts==["api","restart"]:
            code,out=_console_run(
                ["systemctl","--no-block","restart","market-career-dashboard.service"],
                timeout=5,
            )
            if code==0:
                return 0,"API 재시작을 예약했습니다. 약 5~10초 뒤 자동으로 다시 연결됩니다."
            return code,out or "API 재시작 요청에 실패했습니다."
        raise ValueError("usage: api status|restart")

    if parts and parts[0]=="deps":
        if parts==["deps","status"]:
            py=ROOT/".venv"/"bin"/"python"
            if not py.is_file():
                return 2,f"virtualenv python not found: {py}"
            return _console_run(
                [str(py),"-c","import exchange_calendars, websockets; print('exchange_calendars='+exchange_calendars.__version__); print('websockets='+websockets.__version__)"],
                timeout=20,
            )
        if parts==["deps","install"]:
            return _install_runtime_requirements()
        raise ValueError("usage: deps status|install")

    if parts and parts[0]=="systemctl":
        if len(parts)!=3 or parts[1] not in {"status","is-active","restart"}:
            raise ValueError("usage: systemctl status|is-active|restart <service>")
        service=parts[2]
        if service not in ADMIN_CONSOLE_SERVICES:
            raise ValueError("service not allowed")
        if parts[1]=="restart" and service=="market-career-dashboard.service":
            return _console_run(["systemctl","--no-block","restart",service],timeout=5)
        args=["systemctl",parts[1],service]
        if parts[1]=="status":
            args+=["--no-pager","--full"]
        return _console_run(args,timeout=20)

    if parts and parts[0]=="journalctl":
        if len(parts) not in {2,3}:
            raise ValueError("usage: journalctl <service> [N]")
        service=parts[1]
        if service not in ADMIN_CONSOLE_SERVICES:
            raise ValueError("service not allowed")
        n=max(1,min(200,int(parts[2]))) if len(parts)==3 else 50
        return _console_run(["journalctl","-u",service,"-n",str(n),"--no-pager"],timeout=20)

    if parts and parts[0]=="tail":
        if len(parts) not in {2,3} or parts[1] not in ADMIN_CONSOLE_LOGS:
            raise ValueError("usage: tail collector|v2|multi [N]")
        n=max(1,min(300,int(parts[2]))) if len(parts)==3 else 50
        p=ADMIN_CONSOLE_LOGS[parts[1]]
        if not p.exists():
            return 1,f"log not found: {p}"
        return _console_run(["tail","-n",str(n),str(p)])

    if parts and parts[0]=="state":
        if len(parts)!=2 or parts[1] not in ADMIN_CONSOLE_STATES:
            raise ValueError("usage: state collector|v2|multi")
        p=ADMIN_CONSOLE_STATES[parts[1]]
        if not p.exists():
            return 1,f"state not found: {p}"
        raw=p.read_text(encoding="utf-8",errors="ignore")
        try:
            raw=json.dumps(json.loads(raw),ensure_ascii=False,indent=2)
        except Exception:
            pass
        return 0,raw[-30000:]

    raise ValueError("허용되지 않은 명령입니다. help 를 입력하세요.")

def trading_status():
    with TRADING_LOCK:
        return {
            **TRADING_STATE,
            "mode":TRADING_MODE,
            "liveTradingEnabled":LIVE_TRADING_ENABLED,
            "maxOrderKrw":MAX_ORDER_KRW,
            "dailyLossLimitKrw":MAX_DAILY_LOSS_KRW,
            "live":LIVE_TRADER.status(),
        }

def backtest_ready():
    return (ROOT/"research"/"backtest_toss_v5_result.json").exists()

def update_data():
    while True:
        if not backtest_ready():
            time.sleep(30)
            continue
        try:
            subprocess.run([sys.executable,str(ROOT/"scripts"/"update_data.py")],cwd=ROOT,timeout=240,check=False)
        except Exception as e:
            print("update error:",e,flush=True)
        time.sleep(INTERVAL)

TOSS_TOKEN_URL="https://openapi.tossinvest.com/oauth2/token"
TOSS_API_BASE="https://openapi.tossinvest.com"
TOSS_TOKEN_LOCK=threading.Lock()
TOSS_TOKEN={"access_token":"","expires_at":0.0}

def toss_symbol(symbol):
    # Toss Securities uses six-digit KRX symbols for Korean equities.
    if len(symbol)==9 and symbol[6:] in (".KS",".KQ") and symbol[:6].isdigit():
        return symbol[:6]
    return symbol

def toss_access_token(cfg, force=False):
    return shared_toss_token(cfg, force=force)

def toss_quotes(cfg, items):
    token=toss_access_token(cfg)
    if not token or not items:
        return {}
    # The Open API supports up to 200 symbols per prices request.
    symbol_map={toss_symbol(x.get("symbol","")):x.get("symbol","") for x in items if x.get("symbol")}
    requested=list(symbol_map)
    out={}
    for start in range(0,len(requested),200):
        chunk=requested[start:start+200]
        qs=urllib.parse.urlencode({"symbols":",".join(chunk)})
        url=TOSS_API_BASE+"/api/v1/prices?"+qs
        wait_for_slot("MARKET_DATA")
        req=urllib.request.Request(
            url,
            headers={
                "Authorization":"Bearer "+token,
                "User-Agent":"market-career-dashboard/1.3"
            }
        )
        with urllib.request.urlopen(req,timeout=8) as r:
            obj=json.loads(r.read())
        for row in obj.get("result",[]) or []:
            toss_sym=str(row.get("symbol") or "")
            original=symbol_map.get(toss_sym,toss_sym)
            price=float(row["lastPrice"]) if row.get("lastPrice") is not None else None
            out[original]={
                "price":price,
                "change":None,
                "live":True,
                "provider":"toss",
                "timestamp":row.get("timestamp")
            }
    return out

LIVE_TRADER.set_token_provider(lambda force=False: toss_access_token(load_secrets(), force=force))

def simple_paper():
    """Lazy initialization keeps paper storage errors out of live startup."""
    global SIMPLE_PAPER
    with SIMPLE_PAPER_LOCK:
        if SIMPLE_PAPER is None:
            directory=persistent_directory()
            if directory.resolve()==PAPER_V3.data_dir.resolve():
                raise ValueError("V3 and Simple paper storage directories must be different")
            client=ReadOnlyTossMarketData(lambda force=False: toss_access_token(load_secrets(), force=force))
            SIMPLE_PAPER=SimpleMomentumPaper(directory,client)
            SIMPLE_PAPER.start()
        return SIMPLE_PAPER

def restore_simple_paper():
    try:
        simple_paper()
    except Exception as e:
        print("Simple paper startup unavailable:",e,flush=True)

def paper_v3_market_snapshot(managed_symbol=None):
    """Read real Toss market data through the V3 engine without placing orders."""
    with LIVE_TRADER.lock:
        session,session_message=LIVE_TRADER._calendar()
        candidates=LIVE_TRADER._scan_candidates()
        managed=None
        if managed_symbol:
            managed=next(
                (x for x in candidates if str(x.get("symbol") or "").upper()==str(managed_symbol).upper()),
                None,
            )
            if managed is None:
                rows=LIVE_TRADER._candles(str(managed_symbol).upper())
                analyzed=LIVE_TRADER._analyze(rows)
                managed={
                    "symbol":str(managed_symbol).upper(),
                    "name":str(managed_symbol).upper(),
                    **analyzed,
                }
        return {
            "session":session,
            "sessionMessage":session_message,
            "candidates":candidates,
            "managed":managed,
            "timestamp":time.time(),
        }

PAPER_V3=PaperV3Trader(ROOT,paper_v3_market_snapshot)

def yahoo_quote(symbol):
    url="https://query1.finance.yahoo.com/v8/finance/chart/"+urllib.parse.quote(symbol,safe="")+"?range=1d&interval=1m&includePrePost=false"
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 market-career-dashboard/1.3"})
    with urllib.request.urlopen(req,timeout=5) as r:
        obj=json.loads(r.read())
    meta=obj["chart"]["result"][0]["meta"]
    price=meta.get("regularMarketPrice")
    prev=meta.get("previousClose")
    change=((price/prev)-1)*100 if price is not None and prev else None
    return {"price":price,"change":change,"live":True,"provider":"yahoo"}

def live_quotes():
    try:
        data=json.loads(DATA.read_text(encoding="utf-8"))
        stock_items=data.get("stocks",[])
        index_items=data.get("indices",[])
        out={}
        cfg=load_secrets()
        # Use the user's Toss Open API for equities/ETFs when configured.
        try:
            toss_items=[x for x in stock_items if x.get("symbol")]
            out.update(toss_quotes(cfg,toss_items))
        except Exception as e:
            print("toss quote error:",e,flush=True)

        # Keep Yahoo as fallback for any stock Toss could not return, and for indices
        # because the dashboard also tracks KOSPI/KOSDAQ/NASDAQ/S&P/SOX symbols.
        fallback_items=index_items+[x for x in stock_items if x.get("symbol") not in out]
        for item in fallback_items:
            symbol=item.get("symbol")
            if not symbol: continue
            try:
                out[symbol]=yahoo_quote(symbol)
            except Exception:
                continue
        return {
            "updatedAt":time.time(),
            "quotes":out,
            "provider":"toss+yahoo" if out else "none"
        }
    except Exception as e:
        return {"updatedAt":time.time(),"quotes":{},"provider":"none","error":str(e)}


REALTIME_WS_HOST=os.getenv("REALTIME_WS_HOST","127.0.0.1")
REALTIME_WS_PORT=int(os.getenv("REALTIME_WS_PORT","8081"))
TOSS_WS_URL="wss://openapi-ws.tossinvest.com/ws/v1"
REALTIME_CLIENTS=set()
REALTIME_QUOTES={}
REALTIME_STATUS={
    "connected":False,
    "lastMessageAt":None,
    "lastError":None,
    "subscribed":[],
}
REALTIME_STATE_LOCK=threading.Lock()

def realtime_snapshot():
    with REALTIME_STATE_LOCK:
        return {
            "connected":bool(REALTIME_STATUS["connected"]),
            "lastMessageAt":REALTIME_STATUS["lastMessageAt"],
            "lastError":REALTIME_STATUS["lastError"],
            "subscribed":list(REALTIME_STATUS["subscribed"]),
            "clientCount":len(REALTIME_CLIENTS),
            "quotes":dict(REALTIME_QUOTES),
        }

def build_toss_trade_subscriptions():
    try:
        data=json.loads(DATA.read_text(encoding="utf-8"))
    except Exception:
        return [], {}
    stocks=data.get("stocks",[])
    kr=[]
    us=[]
    reverse={}
    for item in stocks:
        original=str(item.get("symbol") or "").strip()
        if not original:
            continue
        toss=toss_symbol(original)
        if original.endswith(".KS") or original.endswith(".KQ"):
            if toss and toss not in kr:
                kr.append(toss)
                reverse[toss]=original
        else:
            if toss and toss not in us:
                us.append(toss)
                reverse[toss]=original
    declarations=[]
    if kr:
        declarations.append({"type":"trade:kr","codes":kr})
    if us:
        declarations.append({"type":"trade:us","codes":us})
    return declarations, reverse

async def broadcast_realtime(message):
    if not REALTIME_CLIENTS:
        return
    raw=json.dumps(message,ensure_ascii=False,separators=(",",":"))
    clients=list(REALTIME_CLIENTS)
    results=await asyncio.gather(
        *(client.send(raw) for client in clients),
        return_exceptions=True
    )
    for client,result in zip(clients,results):
        if isinstance(result,Exception):
            REALTIME_CLIENTS.discard(client)

async def browser_realtime_handler(websocket,*_args):
    REALTIME_CLIENTS.add(websocket)
    try:
        snap=realtime_snapshot()
        initial={"type":"snapshot","connected":snap["connected"],"quotes":snap["quotes"],"ts":time.time()}
        await websocket.send(json.dumps(initial,ensure_ascii=False,separators=(",",":")))
        async for _message in websocket:
            # Browser is read-only; ignore client messages.
            pass
    except Exception:
        pass
    finally:
        REALTIME_CLIENTS.discard(websocket)

async def toss_realtime_once():
    import websockets
    cfg=load_secrets()
    token=toss_access_token(cfg)
    if not token:
        raise RuntimeError("Toss Open API credentials are not configured")
    declarations, reverse=build_toss_trade_subscriptions()
    if not declarations:
        raise RuntimeError("No stock symbols available for Toss WebSocket subscription")
    headers={"Authorization":"Bearer "+token}
    try:
        major=int(str(getattr(websockets,"__version__","15")).split(".",1)[0])
    except Exception:
        major=15
    kwargs={"additional_headers":headers} if major>=14 else {"extra_headers":headers}
    async with websockets.connect(
        TOSS_WS_URL,
        ping_interval=None,
        close_timeout=5,
        **kwargs
    ) as ws:
        declaration=[{"id":"market-dashboard"}]+declarations
        await ws.send(json.dumps(declaration,separators=(",",":")))
        with REALTIME_STATE_LOCK:
            REALTIME_STATUS["connected"]=True
            REALTIME_STATUS["lastError"]=None
            REALTIME_STATUS["subscribed"]=[
                f"trade:kr:{x}" for x in next((d["codes"] for d in declarations if d["type"]=="trade:kr"),[])
            ]+[
                f"trade:us:{x}" for x in next((d["codes"] for d in declarations if d["type"]=="trade:us"),[])
            ]
        await broadcast_realtime({"type":"status","connected":True,"provider":"toss_ws","ts":time.time()})

        async def keepalive():
            while True:
                await asyncio.sleep(60)
                await ws.send("PING")

        keepalive_task=asyncio.create_task(keepalive())
        try:
            async for raw in ws:
                try:
                    payload=json.loads(raw)
                except Exception:
                    continue
                if payload.get("type") in ("subscriptions","error","pong"):
                    if payload.get("type")=="error":
                        with REALTIME_STATE_LOCK:
                            REALTIME_STATUS["lastError"]=payload.get("error")
                    continue
                if payload.get("type")!="message":
                    continue
                topic=str(payload.get("topic") or "")
                data=payload.get("data") or {}
                if not topic.startswith("trade:"):
                    continue
                toss_sym=topic.rsplit(":",1)[-1]
                original=reverse.get(toss_sym)
                if not original:
                    continue
                try:
                    price=float(data.get("price"))
                except (TypeError,ValueError):
                    continue
                with REALTIME_STATE_LOCK:
                    old=REALTIME_QUOTES.get(original,{})
                    quote={
                        "price":price,
                        "change":old.get("change"),
                        "live":True,
                        "provider":"toss_ws",
                        "timestamp":data.get("timestamp")
                    }
                    REALTIME_QUOTES[original]=quote
                    REALTIME_STATUS["lastMessageAt"]=time.time()
                await broadcast_realtime({"type":"quote","symbol":original,**quote})
        finally:
            keepalive_task.cancel()
            with REALTIME_STATE_LOCK:
                REALTIME_STATUS["connected"]=False

async def realtime_loop():
    while not backtest_ready():
        await asyncio.sleep(30)
    while True:
        try:
            await toss_realtime_once()
        except Exception as e:
            msg=str(e)
            print("realtime websocket error:",msg,flush=True)
            with REALTIME_STATE_LOCK:
                REALTIME_STATUS["connected"]=False
                REALTIME_STATUS["lastError"]=msg
            await broadcast_realtime({"type":"status","connected":False,"provider":"toss_ws","error":msg,"ts":time.time()})
            await asyncio.sleep(5)

async def realtime_server():
    import websockets
    async with websockets.serve(
        browser_realtime_handler,
        REALTIME_WS_HOST,
        REALTIME_WS_PORT,
        ping_interval=30,
        ping_timeout=20,
        max_size=1024*1024,
    ):
        print(f"Realtime browser WebSocket listening on {REALTIME_WS_HOST}:{REALTIME_WS_PORT}",flush=True)
        await realtime_loop()

def start_realtime():
    try:
        import websockets  # noqa: F401
    except Exception as e:
        print("WARNING: realtime websocket disabled; install websockets package:",e,flush=True)
        return
    try:
        asyncio.run(realtime_server())
    except Exception as e:
        print("realtime server stopped:",e,flush=True)

class Handler(BaseHTTPRequestHandler):
    def send_json(self,obj,status=200):
        raw=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin","https://heh-heh.github.io")
        self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        length=int(self.headers.get("Content-Length","0") or 0)
        if length>65536: raise ValueError("request too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_OPTIONS(self):
        self.send_json({"ok":True})

    def do_GET(self):
        path=self.path.split("?")[0]
        if path in {"/ticks","/tick-status","/api/ticks/view"}:
            try:
                raw=(ROOT/"tick-status.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Cache-Control","no-store")
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503)
            return
        if path=="/api/backtest/engines":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            self.send_json({"ok":True,"engines":list(BACKTEST_ENGINES.values())}); return
        if path=="/api/backtest/status":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                qs=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                engine=(qs.get("engine") or ["simple-v1"])[0]
                self.send_json({"ok":True,**backtest_engine_status(engine)}); return
            except ValueError as e:
                self.send_json({"ok":False,"error":str(e)},400); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/backtest/download":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                qs=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                engine=(qs.get("engine") or ["simple-v1"])[0]
                files,label=backtest_download_files(engine)
                present=[(src,name) for src,name in files if Path(src).exists()]
                buf=io.BytesIO()
                with zipfile.ZipFile(buf,"w",compression=zipfile.ZIP_DEFLATED) as archive:
                    for src,name in present:
                        archive.write(src,name)
                raw=buf.getvalue()
                filename=label+"-backtest-"+time.strftime("%Y%m%d-%H%M%S",time.gmtime())+".zip"
                self.send_response(200)
                self.send_header("Content-Type","application/zip")
                self.send_header("Content-Disposition",'attachment; filename="'+filename+'"')
                self.send_header("Access-Control-Allow-Origin","https://heh-heh.github.io")
                self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization")
                self.send_header("Access-Control-Expose-Headers","Content-Disposition")
                self.send_header("Cache-Control","no-store")
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            except FileNotFoundError as e:
                self.send_json({"ok":False,"error":str(e)},404); return
            except ValueError as e:
                self.send_json({"ok":False,"error":str(e)},400); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/backtest/simple-v1/status":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                self.send_json({"ok":True,**simple_backtest_status()}); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/backtest/simple-v1/download":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                files=[
                    (SIMPLE_BT_RESULT,"backtest_simple_v1_result.json"),
                    (SIMPLE_BT_TRADES,"backtest_simple_v1_trades.csv"),
                    (SIMPLE_BT_STATE,"backtest_simple_v1_state.json"),
                    (SIMPLE_BT_LOG,"backtest_simple_v1.log"),
                ]
                present=[(src,name) for src,name in files if src.exists()]
                if not SIMPLE_BT_RESULT.exists():
                    self.send_json({"ok":False,"error":"backtest result is not available yet"},404); return
                buf=io.BytesIO()
                with zipfile.ZipFile(buf,"w",compression=zipfile.ZIP_DEFLATED) as archive:
                    for src,name in present:
                        archive.write(src,name)
                raw=buf.getvalue()
                filename="simple-v1-backtest-"+time.strftime("%Y%m%d-%H%M%S",time.gmtime())+".zip"
                self.send_response(200)
                self.send_header("Content-Type","application/zip")
                self.send_header("Content-Disposition",'attachment; filename="'+filename+'"')
                self.send_header("Access-Control-Allow-Origin","https://heh-heh.github.io")
                self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization")
                self.send_header("Access-Control-Expose-Headers","Content-Disposition")
                self.send_header("Cache-Control","no-store")
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/trading/paper/logs/download":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                simple=simple_paper()
                sources=[
                    (PAPER_V3.state_path,"v3/state.json"),
                    (PAPER_V3.trades_path,"v3/trades.jsonl"),
                    (PAPER_V3.decisions_path,"v3/decisions.jsonl"),
                    (simple.state_path,"simple_v1/state.json"),
                    (simple.directory/"trades.jsonl","simple_v1/trades.jsonl"),
                    (simple.directory/"decisions.jsonl","simple_v1/decisions.jsonl"),
                    (simple.directory/"signals.jsonl","simple_v1/signals.jsonl"),
                ]
                present=[(Path(src),arc) for src,arc in sources if Path(src).is_file()]
                if not present:
                    self.send_json({"ok":False,"error":"paper logs unavailable"},404); return
                buf=io.BytesIO()
                manifest={"generatedAt":time.time(),"files":[]}
                with zipfile.ZipFile(buf,"w",compression=zipfile.ZIP_DEFLATED) as archive:
                    for src,arc in present:
                        stat=src.stat()
                        archive.write(src,arc)
                        manifest["files"].append({"path":arc,"bytes":stat.st_size,"mtime":stat.st_mtime})
                    archive.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
                raw=buf.getvalue()
                filename="paper-trading-logs-"+time.strftime("%Y%m%d-%H%M%S",time.gmtime())+".zip"
                self.send_response(200)
                self.send_header("Content-Type","application/zip")
                self.send_header("Content-Disposition",'attachment; filename="'+filename+'"')
                self.send_header("Access-Control-Allow-Origin","https://heh-heh.github.io")
                self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization")
                self.send_header("Access-Control-Expose-Headers","Content-Disposition")
                self.send_header("Cache-Control","no-store")
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/trading/paper/simple-v1/status":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                self.send_json({"ok":True,**simple_paper().status()}); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/trading/status":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            self.send_json({"ok":True,**trading_status()}); return
        if path=="/api/trading/live/status":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            self.send_json({"ok":True,**LIVE_TRADER.status()}); return
        if path=="/api/trading/paper/v3/status":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            self.send_json({"ok":True,**PAPER_V3.status()}); return
        if path=="/api/trading/live/account":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                self.send_json({"ok":True,**LIVE_TRADER.account_snapshot()}); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/trading/paper/portfolio":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                prices={}
                data=json.loads(DATA.read_text(encoding="utf-8"))
                for item in data.get("stocks",[])+data.get("indices",[]):
                    if item.get("symbol") and item.get("price") is not None:
                        prices[item["symbol"]]=float(item["price"])
                self.send_json({"ok":True,"portfolio":paper_broker.snapshot(prices)}); return
            except Exception:
                self.send_json({"ok":False,"error":"paper portfolio unavailable"},503); return
        if path=="/api/backtest/monitor":
            try:
                log_path=Path("/tmp/toss_hybrid.log")
                result_path=ROOT/"research"/"backtest_toss_v5_result.json"
                ms_root=Path("/var/lib/market-career-dashboard")
                ms_state_path=ms_root/"backtest_multistrategy_v1_state.json"
                ms_result_path=ms_root/"backtest_multistrategy_v1_result.json"
                ms_log_path=ms_root/"backtest_multistrategy_v1.log"
                ms_analysis_path=ms_root/"backtest_multistrategy_v1_analysis.json"
                v2_state_path=ms_root/"backtest_v2_tqqq_mr_state.json"
                v2_result_path=ms_root/"backtest_v2_tqqq_mr.json"
                expanded_progress_path=ROOT/"data"/"expanded_universe_progress.json"
                expanded_log_path=ms_root/"expanded_universe.log"
                log_text=log_path.read_text(encoding="utf-8",errors="ignore") if log_path.exists() else ""
                lines=log_text.splitlines()
                tail="\n".join(lines[-100:])
                symbols=["NVDA","AMD","INTC","SOXL","SOXS","TQQQ"]
                progress_path=ROOT/"data"/"toss_1m_progress.json"
                watchdog_path=ROOT/"data"/"toss_hybrid_watchdog.json"
                live_progress={}
                watchdog={}
                if watchdog_path.exists():
                    try: watchdog=json.loads(watchdog_path.read_text(encoding="utf-8"))
                    except Exception: watchdog={}
                if progress_path.exists():
                    try: live_progress=json.loads(progress_path.read_text(encoding="utf-8"))
                    except Exception: live_progress={}
                stats={s:{"symbol":s,"page":0,"pages":10000,"fetched":0,"stored":0,"status":"waiting","coveragePct":0,"latestTimestamp":None,"oldestTimestamp":None,"since":None} for s in symbols}
                page_re=re.compile(r"^(\w+): page (\d+)/(\d+), fetched=(\d+), stored=(\d+)")
                saved_re=re.compile(r"^(\w+): total saved (\d+)")
                for line in lines:
                    m=page_re.search(line)
                    if m and m.group(1) in stats:
                        s=m.group(1); stats[s].update(page=int(m.group(2)),pages=int(m.group(3)),fetched=int(m.group(4)),stored=int(m.group(5)),status="collecting")
                    m=saved_re.search(line)
                    if m and m.group(1) in stats:
                        s=m.group(1); stats[s].update(stored=int(m.group(2)),status="completed")
                for s in symbols:
                    if isinstance(live_progress.get(s),dict):
                        stats[s].update(live_progress[s])
                completed=[s for s in symbols if stats[s]["status"]=="completed"]
                current=next((s for s in reversed(symbols) if stats[s]["status"]=="collecting"),None)
                current_stats=stats.get(current) if current else None
                if result_path.exists():
                    phase="completed"; progress=100
                else:
                    running_out=subprocess.run(["pgrep","-af","[c]ollect_toss_1m.py"],capture_output=True,text=True).stdout.strip()
                    backtest_out=subprocess.run(["pgrep","-af","[b]in/backtest_toss_v5"],capture_output=True,text=True).stdout.strip()
                    build_out=subprocess.run(["pgrep","-af","[b]uild_backtest_cpp.sh"],capture_output=True,text=True).stdout.strip()
                    running=bool(running_out or backtest_out or build_out)
                    if build_out and not current:
                        phase="building"; progress=5
                    elif running_out or completed or current:
                        phase="collecting"
                        cur_frac=(float(current_stats.get("coveragePct",0))/100.0 if current_stats else 0)
                        progress=min(88,round(((len(completed)+cur_frac)/len(symbols))*85))
                    elif backtest_out:
                        phase="backtest"; progress=90
                    elif "Built:" in log_text:
                        phase="backtest"; progress=90
                    elif log_text:
                        phase="starting"; progress=2
                    else:
                        phase="waiting"; progress=0
                running=bool(subprocess.run(["pgrep","-af","[c]ollect_toss_1m.py|[b]in/backtest_toss_v5|[b]uild_backtest_cpp.sh"],capture_output=True,text=True).stdout.strip())
                if watchdog.get("phase"):
                    phase=watchdog.get("phase",phase)
                    if phase=="completed":
                        progress=100
                    elif phase=="backtest":
                        progress=max(progress,90)
                    elif phase in ("collecting","retrying"):
                        progress=max(progress, min(88, progress))
                    elif phase=="building":
                        progress=max(progress,5)
                ms_state={}
                ms_result={}
                ms_analysis={}
                v2_state={}
                v2_result={}
                expanded_progress={}
                if ms_state_path.exists():
                    try: ms_state=json.loads(ms_state_path.read_text(encoding="utf-8"))
                    except Exception: ms_state={}
                if ms_result_path.exists():
                    try: ms_result=json.loads(ms_result_path.read_text(encoding="utf-8"))
                    except Exception: ms_result={}
                if ms_analysis_path.exists():
                    try: ms_analysis=json.loads(ms_analysis_path.read_text(encoding="utf-8"))
                    except Exception: ms_analysis={}
                if v2_state_path.exists():
                    try: v2_state=json.loads(v2_state_path.read_text(encoding="utf-8"))
                    except Exception: v2_state={}
                if v2_result_path.exists():
                    try: v2_result=json.loads(v2_result_path.read_text(encoding="utf-8"))
                    except Exception: v2_result={}
                if expanded_progress_path.exists():
                    try: expanded_progress=json.loads(expanded_progress_path.read_text(encoding="utf-8"))
                    except Exception: expanded_progress={}
                # Attach detailed per-symbol collector state so the web UI can
                # show collection progress, row counts, date bounds and errors
                # for the full expanded research universe.
                eu_symbols=expanded_progress.get("symbols") or []
                eu_completed=set(expanded_progress.get("completedSymbols") or [])
                eu_failed=set(expanded_progress.get("failedSymbols") or [])
                eu_current=expanded_progress.get("currentSymbol")
                eu_symbol_progress=[]
                for sym in eu_symbols:
                    detail=dict(live_progress.get(sym) or {})
                    detail["symbol"]=sym
                    if sym in eu_failed:
                        detail["status"]="error"
                    elif sym == eu_current and expanded_progress.get("running"):
                        detail["status"]=detail.get("status") or "collecting"
                    elif sym in eu_completed:
                        detail["status"]="completed"
                        if detail.get("coveragePct") is None:
                            detail["coveragePct"]=100
                    else:
                        detail["status"]=detail.get("status") or "waiting"
                    eu_symbol_progress.append(detail)
                expanded_progress["symbolProgress"]=eu_symbol_progress
                if expanded_log_path.exists():
                    try:
                        expanded_lines=expanded_log_path.read_text(encoding="utf-8",errors="ignore").splitlines()
                        expanded_progress["log"]="\n".join(expanded_lines[-120:])
                    except Exception:
                        expanded_progress["log"]=""
                ms_running=bool(subprocess.run(
                    ["pgrep","-af","[b]acktest_multistrategy_v1.py"],
                    capture_output=True,text=True
                ).stdout.strip())
                if ms_state or ms_result or ms_running:
                    phase=str(ms_state.get("phase") or ("completed" if ms_result else "backtest"))
                    progress=int(ms_state.get("progress") or (100 if ms_result else 1))
                    running=ms_running or bool(ms_state.get("running"))
                    if ms_log_path.exists():
                        ms_lines=ms_log_path.read_text(encoding="utf-8",errors="ignore").splitlines()
                        tail="\n".join(ms_lines[-100:])
                    overall=ms_result.get("overall") or ms_state.get("summary") or {}
                    by_strategy=ms_result.get("byStrategy") or ms_state.get("byStrategy") or {}
                    funnel=ms_result.get("funnel") or {}
                    self.send_json({
                        "ok":True,
                        "engine":"multi-strategy-v3",
                        "phase":phase,
                        "progress":progress,
                        "running":running,
                        "resultReady":ms_result_path.exists(),
                        "updatedAt":time.time(),
                        "currentDay":ms_state.get("currentDay"),
                        "completedDays":ms_state.get("completedDays"),
                        "totalDays":ms_state.get("totalDays"),
                        "currentStored":ms_state.get("trades") or overall.get("trades"),
                        "symbols":[{"symbol":x,"status":"ready","coveragePct":100} for x in (ms_state.get("symbols") or ms_result.get("symbols") or [])],
                        "completedSymbols":ms_state.get("symbols") or ms_result.get("symbols") or [],
                        "watchdog":{
                            "phase":phase,
                            "message":ms_state.get("message") or ("백테스트 완료" if ms_result else "멀티전략 백테스트"),
                            "updatedAt":ms_state.get("updatedAt"),
                            "error":ms_state.get("error"),
                        },
                        "multiStrategy":{
                            "overall":overall,
                            "byStrategy":by_strategy,
                            "funnel":funnel,
                            "execution":ms_result.get("execution") or {},
                            "minFinalScore":ms_result.get("minFinalScore"),
                            "days":ms_result.get("days") or ms_state.get("totalDays"),
                            "symbols":ms_result.get("symbols") or ms_state.get("symbols") or [],
                            "analysis":ms_analysis,
                            "expandedUniverse":expanded_progress,
                            "storage":storage_status(),
                            "v2Research":{
                                "state":v2_state,
                                "resultReady":v2_result_path.exists(),
                                "result":{
                                    "engine":v2_result.get("engine"),
                                    "symbol":v2_result.get("symbol"),
                                    "days":v2_result.get("days"),
                                    "variantsTested":v2_result.get("variantsTested"),
                                    "top":v2_result.get("top") or [],
                                } if v2_result else {},
                            },
                        },
                        "log":tail,
                    })
                    return

                self.send_json({"ok":True,"engine":"legacy-hybrid","phase":phase,"progress":progress,"running":running or bool(watchdog.get("phase") in ("starting","building","collecting","retrying","backtest","retrying_backtest")),
                    "watchdog":watchdog,
                    "symbols":list(stats.values()),"completedSymbols":completed,
                    "currentSymbol":current,"currentPage":current_stats["page"] if current_stats else None,
                    "totalPages":current_stats["pages"] if current_stats else None,
                    "currentStored":current_stats["stored"] if current_stats else None,
                    "resultReady":result_path.exists(),"updatedAt":time.time(),"log":tail})
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},500)
            return
        if path=="/api/ticks/status":
            try:
                self.send_json({"ok":True,**tick_collector_status()})
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503)
            return
        if path=="/api/health":
            self.send_json({"ok":True,"service":"market-career-dashboard"})
            return
        if path=="/api/quotes":
            self.send_json(live_quotes())
            return
        if path=="/api/dashboard":
            try:
                self.send_json(json.loads(DATA.read_text(encoding="utf-8")))
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503)
            return
        if path=="/api/realtime":
            snap=realtime_snapshot()
            self.send_json({"ok":True,**snap})
            return
        if path=="/api/admin/github":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                self.send_json({"ok":True,**github_auth_status()})
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},500)
            return
        if path=="/api/settings":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            cfg=load_secrets()
            self.send_json({"ok":True,"providers":{
                "toss":{"configured":bool(cfg.get("toss",{}).get("app_key"))},
                "generic":{"configured":bool(cfg.get("generic",{}).get("api_key"))}
            }})
            return
        self.send_json({"error":"not found"},404)

    def do_POST(self):
        path=self.path.split("?")[0]
        if path=="/api/backtest/start":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                if not isinstance(body,dict) or set(body)-{"engine"} or not isinstance(body.get("engine","simple-v1"),str):
                    self.send_json({"ok":False,"error":"Only an allowlisted engine is permitted"},400); return
                engine=str(body.get("engine") or "simple-v1").strip().lower()
                if engine not in BACKTEST_ENGINES:
                    self.send_json({"ok":False,"error":"Unknown backtest engine"},400); return
                self.send_json({"ok":True,**start_backtest_engine(engine)}); return
            except ValueError as e:
                self.send_json({"ok":False,"error":str(e)},409); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/backtest/simple-v1/start":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                self.send_json({"ok":True,**start_simple_backtest()}); return
            except ValueError as e:
                self.send_json({"ok":False,"error":str(e)},409); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path in {"/api/trading/paper/simple-v1/auto","/api/trading/paper/simple-v1/scan"}:
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                engine=simple_paper()
                if path.endswith("/auto"):
                    body=self.read_json()
                    if not isinstance(body,dict) or not isinstance(body.get("enabled"),bool):
                        raise ValueError("enabled must be a JSON boolean")
                    result=engine.set_enabled(body["enabled"])
                else:
                    result=engine.scan()
                self.send_json({"ok":True,**result}); return
            except ValueError as e:
                self.send_json({"ok":False,"error":str(e)},400); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/trading/engine":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            body=self.read_json()
            with TRADING_LOCK:
                TRADING_STATE["engine_enabled"]=bool(body.get("enabled"))
                if not TRADING_STATE["engine_enabled"]:
                    TRADING_STATE["live_auto_enabled"]=False
            self.send_json({"ok":True,**trading_status()}); return
        if path=="/api/trading/paper/order":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                side=str(body.get("side","")).upper()
                symbol=str(body.get("symbol","")).upper().strip()
                quantity=int(body.get("quantity",0))
                price=float(body.get("price",0))
                if side not in {"BUY","SELL"} or not symbol or quantity<=0 or price<=0:
                    raise ValueError("invalid paper order")
                if quantity*price>MAX_ORDER_KRW:
                    raise ValueError("MAX_ORDER_KRW exceeded")
                trade=paper_broker.trade(symbol,side,quantity,price)
                self.send_json({"ok":True,"mode":"paper","trade":trade,"portfolio":paper_broker.snapshot({symbol:price})}); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},400); return
        if path=="/api/trading/paper/reset":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            paper_broker.reset()
            self.send_json({"ok":True,"portfolio":paper_broker.snapshot()}); return
        if path=="/api/trading/paper/v3/auto":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                self.send_json({"ok":True,**PAPER_V3.set_enabled(bool(body.get("enabled")))}); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},400); return
        if path=="/api/trading/paper/v3/scan":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                self.send_json({"ok":True,**PAPER_V3.scan(force=True)}); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/trading/live/arm":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                self.send_json({"ok":True,**LIVE_TRADER.arm(body.get("phrase"))}); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},403); return
        if path=="/api/trading/live/disarm":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            self.send_json({"ok":True,**LIVE_TRADER.disarm()}); return
        if path=="/api/trading/live/auto":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                self.send_json({"ok":True,**LIVE_TRADER.set_auto(bool(body.get("enabled")))}); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},400); return
        if path=="/api/admin/github":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                if body.get("disconnect"):
                    with GIT_LOCK:
                        info=clear_github_token()
                    self.send_json({"ok":True,**info}); return
                token=str(body.get("token") or "")
                with GIT_LOCK:
                    info=save_github_token(token)
                self.send_json({"ok":True,**info}); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},400); return
        if path=="/api/admin/git":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                action=str(body.get("action") or "")
                with GIT_LOCK:
                    code,output=run_git_action(action)
                self.send_json({"ok":True,"action":action,"exitCode":code,"output":output})
            except subprocess.TimeoutExpired:
                self.send_json({"ok":False,"error":"git command timed out"},408)
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},400)
            return
        if path=="/api/admin/console":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                command=str(body.get("command") or "")
                with ADMIN_CONSOLE_LOCK:
                    code,output=run_admin_console(command)
                self.send_json({"ok":True,"command":command,"exitCode":code,"output":output})
            except subprocess.TimeoutExpired:
                self.send_json({"ok":False,"error":"command timed out"},408)
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},400)
            return
        if path=="/api/trading/live/engine":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                self.send_json({"ok":True,**LIVE_TRADER.set_engine(bool(body.get("enabled")))}); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},400); return
        if path=="/api/trading/live/scan":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                self.send_json({"ok":True,**LIVE_TRADER.scan(False)}); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/login":
            try:
                body=self.read_json()
                password=str(body.get("password",""))
            except Exception:
                self.send_json({"ok":False,"error":"invalid request"},400); return
            if not ADMIN_PASSWORD:
                self.send_json({"ok":False,"error":"ADMIN_PASSWORD is not configured on server"},503); return
            if not secrets.compare_digest(password,ADMIN_PASSWORD):
                self.send_json({"ok":False,"error":"invalid credentials"},401); return
            token=secrets.token_urlsafe(32)
            with SESSION_LOCK: SESSIONS[token]=time.time()+SESSION_TTL
            self.send_json({"ok":True,"token":token,"expiresIn":SESSION_TTL},200)
            return
        if path=="/api/logout":
            token=auth_token(self)
            with SESSION_LOCK: SESSIONS.pop(token,None)
            self.send_json({"ok":True}); return
        if path=="/api/settings":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
            except Exception:
                self.send_json({"ok":False,"error":"invalid request"},400); return
            cfg=load_secrets()
            toss=cfg.setdefault("toss",{})
            generic=cfg.setdefault("generic",{})
            for key in ("app_key","app_secret"):
                if key in body.get("toss",{}):
                    value=str(body["toss"].get(key) or "").strip()
                    if value: toss[key]=value
                    elif key in toss: toss.pop(key)
            if "api_key" in body.get("generic",{}):
                value=str(body["generic"].get("api_key") or "").strip()
                if value: generic["api_key"]=value
                elif "api_key" in generic: generic.pop("api_key")
            save_secrets(cfg)
            self.send_json({"ok":True,"message":"서버에 안전하게 저장했습니다.","providers":{
                "toss":{"configured":bool(toss.get("app_key"))},
                "generic":{"configured":bool(generic.get("api_key"))}
            }})
            return
        self.send_json({"error":"not found"},404)

    def log_message(self,fmt,*args):
        print("%s - %s"%(self.address_string(),fmt%args),flush=True)

if __name__=="__main__":
    DATA.parent.mkdir(parents=True,exist_ok=True)
    threading.Thread(target=update_data,daemon=True).start()
    threading.Thread(target=start_realtime,daemon=True).start()
    LIVE_TRADER.start()
    threading.Thread(target=restore_simple_paper,daemon=True,name="simple-paper-restore").start()
    PAPER_V3.start()
    print(f"API listening on {HOST}:{PORT}; update interval={INTERVAL}s",flush=True)
    if not ADMIN_PASSWORD:
        print("WARNING: ADMIN_PASSWORD is not set; /api/login is disabled.",flush=True)
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
