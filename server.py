#!/usr/bin/env python3
import asyncio, io, json, os, pwd, re, secrets, shlex, subprocess, sys, threading, time, urllib.parse, urllib.request, uuid, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from trading import paper as paper_broker, strategy as trading_strategy
from live_trader import LiveAutoTrader
from paper_trader import PaperV3Trader
from toss_auth import get_token as shared_toss_token
from toss_rate_limit import wait_for_slot, group_for_path
from simple_momentum_v1 import SimpleMomentumPaper, ReadOnlyTossMarketData, persistent_directory
from backtest_manager import PreflightBlocked, BacktestManager, BACKTEST_ENGINES as RESEARCH_BACKTEST_ENGINES

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
RESEARCH_EXPORTER=None
RESEARCH_EXPORT_LOCK=threading.Lock()

API_FEATURES_FILE=Path(os.getenv("API_FEATURES_FILE","/home/ubuntu/.market-career-dashboard/api_features.json"))
API_FEATURE_LOCK=threading.RLock()
API_FEATURE_DEFS={
    "live_trading":{
        "label":"실거래 API",
        "description":"실주문·ARM·자동매매·실거래 스캔 엔드포인트. OFF 시 즉시 DISARM 및 엔진 OFF.",
        "default":True,
        "risk":"high",
    },
    "paper_trading":{
        "label":"모의매매 API",
        "description":"V3/Simple 모의매매 상태·주문·자동 실행 API. OFF 시 신규 자동 실행을 중지.",
        "default":True,
        "risk":"medium",
    },
    "backtest":{
        "label":"백테스트 API",
        "description":"백테스트 실행·상태·결과 다운로드 API.",
        "default":True,
        "risk":"medium",
    },
    "autonomous_ai":{
        "label":"AI 연구 실행 API",
        "description":"Dry Run·실제 연구·V0.7 기준선 재생성 시작 기능. 상태/로그 조회는 항상 유지.",
        "default":True,
        "risk":"medium",
    },
    "research_export":{
        "label":"연구 Export API",
        "description":"연구 결과·Population·Trades·데이터 Export 생성/다운로드. AI Dry Run이 사용.",
        "default":True,
        "risk":"medium",
    },
    "research_monitors":{
        "label":"연구 모니터 API",
        "description":"V4/Evolution/Tick/Autonomous 읽기 전용 상태 API.",
        "default":True,
        "risk":"low",
    },
    "market_data":{
        "label":"시세·실시간 API",
        "description":"Quotes/Realtime HTTP API와 Toss 실시간 WebSocket 공급. OFF 시 실시간 공급을 휴면.",
        "default":True,
        "risk":"medium",
    },
    "dashboard_data":{
        "label":"메인 대시보드 데이터 API",
        "description":"메인 페이지의 dashboard.json API. OFF 시 메인 화면 일부 데이터가 표시되지 않음.",
        "default":True,
        "risk":"low",
    },
}

def _api_feature_defaults():
    return {name:bool(meta.get("default",True)) for name,meta in API_FEATURE_DEFS.items()}

def _load_api_feature_state():
    state=_api_feature_defaults()
    try:
        if API_FEATURES_FILE.is_symlink():
            raise ValueError("API feature file must not be a symlink")
        if API_FEATURES_FILE.is_file():
            raw=json.loads(API_FEATURES_FILE.read_text(encoding="utf-8"))
            values=raw.get("features",raw) if isinstance(raw,dict) else {}
            if not isinstance(raw,dict) or not isinstance(values,dict) or any(k in state and not isinstance(v,bool) for k,v in values.items()):
                raise ValueError("Invalid API feature settings schema")
            if isinstance(values,dict):
                for name,value in values.items():
                    if name in state and isinstance(value,bool):
                        state[name]=value
    except Exception as e:
        print("WARNING: API feature settings load failed:",e,flush=True)
        state["live_trading"]=False
        state["paper_trading"]=False
    return state

API_FEATURE_STATE=_load_api_feature_state()

def api_feature_snapshot():
    with API_FEATURE_LOCK:
        return {
            name:{
                "key":name,
                "label":meta["label"],
                "description":meta["description"],
                "risk":meta["risk"],
                "enabled":bool(API_FEATURE_STATE.get(name,meta.get("default",True))),
            }
            for name,meta in API_FEATURE_DEFS.items()
        }

def _save_api_feature_state():
    API_FEATURES_FILE.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        os.chmod(API_FEATURES_FILE.parent,0o700)
    except OSError:
        pass
    payload={
        "version":1,
        "updatedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
        "features":{name:bool(API_FEATURE_STATE.get(name,meta.get("default",True)))
                    for name,meta in API_FEATURE_DEFS.items()},
    }
    tmp=API_FEATURES_FILE.parent/("." + API_FEATURES_FILE.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(payload,f,ensure_ascii=False,indent=2,sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,API_FEATURES_FILE)
        os.chmod(API_FEATURES_FILE,0o600)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

def api_feature_enabled(name):
    with API_FEATURE_LOCK:
        meta=API_FEATURE_DEFS.get(name)
        return True if meta is None else bool(API_FEATURE_STATE.get(name,meta.get("default",True)))

def api_feature_requirements(path,method):
    method=str(method or "GET").upper()
    if path.startswith("/api/research/export/"):
        return ["research_export"]
    if path.startswith("/api/backtest/"):
        return ["backtest"]
    if path.startswith("/api/trading/live/"):
        return ["live_trading"]
    if path.startswith("/api/trading/paper/"):
        return ["paper_trading"]
    if path=="/api/trading/engine":
        return ["live_trading"]
    if path in {"/api/admin/ai/start","/api/admin/ai/rebuild-baseline"}:
        return ["autonomous_ai"] + (["research_export"] if path.endswith("/start") else [])
    if path in {"/api/ticks/status","/api/ticks/view","/api/research/v4/audit/status","/api/research/v4/status",
                "/api/research/evolution/status","/api/research/evolution/v08/status","/api/research/autonomous/status"}:
        return ["research_monitors"]
    if path in {"/api/quotes","/api/realtime"}:
        return ["market_data"]
    if path=="/api/dashboard":
        return ["dashboard_data"]
    return []

def disabled_api_feature(path,method):
    for name in api_feature_requirements(path,method):
        if not api_feature_enabled(name):
            return name
    return None

def api_disabled_response(path, feature):
    if path=="/api/admin/ai/start" and feature=="research_export":
        return {"ok":False,"error":"DEPENDENCY_DISABLED","dependency":feature,"feature":feature}
    return {"ok":False,"error":"API_DISABLED","feature":feature,"label":API_FEATURE_DEFS[feature]["label"]}

def _api_feature_side_effects(name,enabled):
    actions=[]
    if enabled:
        return actions
    if name=="live_trading":
        try:
            LIVE_TRADER.disarm()
            actions.append("live trader disarmed")
        except Exception as e:
            actions.append("live trader fail-closed warning: "+str(e))
        try:
            LIVE_TRADER.set_engine(False)
            actions.append("live trader engine disabled")
        except Exception as e:
            actions.append("live trader engine disable warning: "+str(e))
        with TRADING_LOCK:
            TRADING_STATE["live_auto_enabled"]=False
            TRADING_STATE["live_armed"]=False
            TRADING_STATE["engine_enabled"]=False
    elif name=="paper_trading":
        try:
            PAPER_V3.set_enabled(False)
            actions.append("paper V3 auto disabled")
        except Exception as e:
            actions.append("paper V3 disable warning: "+str(e))
        try:
            if SIMPLE_PAPER is not None:
                SIMPLE_PAPER.set_enabled(False)
                actions.append("Simple paper auto disabled")
        except Exception as e:
            actions.append("Simple paper disable warning: "+str(e))
    return actions

def set_api_feature(name,enabled):
    if name not in API_FEATURE_DEFS:
        raise ValueError("Unknown API feature")
    if not isinstance(enabled,bool):
        raise ValueError("enabled must be a JSON boolean")
    with API_FEATURE_LOCK:
        previous=bool(API_FEATURE_STATE.get(name,API_FEATURE_DEFS[name].get("default",True)))
        API_FEATURE_STATE[name]=enabled
        try:
            _save_api_feature_state()
        except Exception:
            API_FEATURE_STATE[name]=previous
            raise
    actions=_api_feature_side_effects(name,enabled) if previous!=enabled else []
    return {
        "feature":name,
        "enabled":enabled,
        "changed":previous!=enabled,
        "actions":actions,
        "features":api_feature_snapshot(),
    }

def research_exporter():
    global RESEARCH_EXPORTER
    with RESEARCH_EXPORT_LOCK:
        if RESEARCH_EXPORTER is None:
            from research_export import ResearchExporter
            RESEARCH_EXPORTER=ResearchExporter(ROOT, export_dir=os.getenv("RESEARCH_EXPORT_DIR","/home/ubuntu/research-exports"))
        return RESEARCH_EXPORTER

def shutil_copy_export(source,target):
    while True:
        chunk=source.read(1024*1024)
        if not chunk: break
        target.write(chunk)

AUTONOMOUS_ROOT=Path(os.getenv("EVOLUTION_AUTONOMOUS_DIR","/home/ubuntu/evolution-autonomous"))
AUTONOMOUS_SCRIPT=ROOT/"research"/"autonomous"/"run_research_loop.py"
AUTONOMOUS_MANIFEST=ROOT/"research"/"v4_data_manifest.json"
AUTONOMOUS_WEB_LOG=AUTONOMOUS_ROOT/"web-launch.log"
AUTONOMOUS_LAUNCH_LOCK=threading.Lock()
V07_REBUILD_SCRIPT=ROOT/"research"/"evolution"/"run_v07_campaign.py"
V07_RESULTS_ROOT=Path(os.getenv("EVOLUTION_RESULTS_DIR","/home/ubuntu/evolution-results"))
V07_REBUILD_LOG=AUTONOMOUS_ROOT/"v07-rebuild.log"
V07_REBUILD_LOCK=threading.Lock()


def _autonomous_processes():
    rows=[]
    proc=Path("/proc")
    try:
        entries=list(proc.iterdir())
        host_uptime=float((proc/"uptime").read_text().split()[0])
        hz=os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        boot_epoch=time.time()-host_uptime
    except (OSError,ValueError,KeyError):
        entries=[]; hz=100; boot_epoch=time.time()
    for item in entries:
        if not item.name.isdigit():
            continue
        try:
            raw=(item/"cmdline").read_bytes().replace(b"\x00",b" ").decode("utf-8","ignore")
        except OSError:
            continue
        if "research/autonomous/run_research_loop.py" not in raw:
            continue
        row={
            "pid":int(item.name),
            "worker":"--worker" in raw,
            "dryRun":"--dry-run" in raw,
            "resume":"--resume" in raw,
        }
        try:
            stat_fields=(item/"stat").read_text().rsplit(") ",1)[1].split()
            start_ticks=int(stat_fields[19])
            started=boot_epoch+(start_ticks/hz)
            row["startedAt"]=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(started))
            row["uptimeSeconds"]=max(0,int(time.time()-started))
            row["processState"]=stat_fields[0]
        except (OSError,ValueError,IndexError):
            pass
        try:
            status_text=(item/"status").read_text()
            match=re.search(r"^VmRSS:\s+(\d+)\s+kB$",status_text,re.MULTILINE)
            if match:
                row["rssMb"]=round(int(match.group(1))/1024,1)
        except OSError:
            pass
        rows.append(row)
    return sorted(rows,key=lambda x:x["pid"])

def _v07_rebuild_processes():
    rows=[]
    try:
        entries=list(Path("/proc").iterdir())
        host_uptime=float(Path("/proc/uptime").read_text().split()[0])
        hz=os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        boot_epoch=time.time()-host_uptime
    except (OSError,ValueError,KeyError):
        entries=[]; hz=100; boot_epoch=time.time()
    for item in entries:
        if not item.name.isdigit():
            continue
        try:
            raw=(item/"cmdline").read_bytes().replace(b"\x00",b" ").decode("utf-8","ignore")
        except OSError:
            continue
        if "research/evolution/run_v07_campaign.py" not in raw:
            continue
        row={"pid":int(item.name)}
        try:
            stat_fields=(item/"stat").read_text().rsplit(") ",1)[1].split()
            start_ticks=int(stat_fields[19])
            started=boot_epoch+(start_ticks/hz)
            row["startedAt"]=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(started))
            row["uptimeSeconds"]=max(0,int(time.time()-started))
            row["processState"]=stat_fields[0]
        except (OSError,ValueError,IndexError):
            pass
        try:
            status_text=(item/"status").read_text()
            match=re.search(r"^VmRSS:\s+(\d+)\s+kB$",status_text,re.MULTILINE)
            if match:
                row["rssMb"]=round(int(match.group(1))/1024,1)
        except OSError:
            pass
        rows.append(row)
    return sorted(rows,key=lambda x:x["pid"])

def _latest_v07_campaign():
    try:
        folders=sorted(
            (p for p in V07_RESULTS_ROOT.glob("campaign-*-v07") if p.is_dir() and not p.is_symlink()),
            key=lambda p:p.name,
            reverse=True,
        )
    except OSError:
        return {}
    for folder in folders:
        info=_safe_ai_json(folder/"campaign.json")
        if info:
            phase=str(info.get("phase") or "waiting")
            progress={
                "training":10,
                "internal_holdout":45,
                "shortlist":65,
                "development_validation":75,
                "diagnostics":90,
                "completed":100,
                "error":100,
            }.get(phase,0)
            return {
                **info,
                "progressPct":progress,
                "campaignDir":folder.name,
            }
    return {}

def v07_rebuild_status():
    processes=_v07_rebuild_processes()
    campaign=_latest_v07_campaign()
    return {
        "running":bool(processes),
        "processes":processes,
        "campaign":campaign,
        "logAvailable":V07_REBUILD_LOG.is_file(),
        "researchOnly":True,
        "finalOosGate":"CLOSED",
    }

def start_v07_baseline_rebuild():
    if not api_feature_enabled("autonomous_ai"):
        raise ValueError("API_DEPENDENCY_DISABLED: dependency=autonomous_ai")
    with V07_REBUILD_LOCK:
        if _autonomous_processes():
            raise ValueError("AUTONOMOUS_RESEARCH_RUNNING")
        if _v07_rebuild_processes():
            raise ValueError("V07_BASELINE_REBUILD_ALREADY_RUNNING")
        if not V07_REBUILD_SCRIPT.is_file():
            raise ValueError("V07_REBUILD_SCRIPT_MISSING")
        V07_RESULTS_ROOT.mkdir(parents=True,exist_ok=True)
        AUTONOMOUS_ROOT.mkdir(parents=True,exist_ok=True)
        python=ROOT/".venv"/"bin"/"python"
        if not python.is_file():
            raise ValueError("virtualenv python is missing")
        logs_dir=AUTONOMOUS_ROOT/"logs"
        logs_dir.mkdir(parents=True,exist_ok=True)
        if V07_REBUILD_LOG.exists() and V07_REBUILD_LOG.stat().st_size:
            stamp=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())
            V07_REBUILD_LOG.replace(logs_dir/f"v07-rebuild-{stamp}-{uuid.uuid4().hex[:8]}.log")
        cmd=[
            str(python),str(V07_REBUILD_SCRIPT),
            "--data-dir",str(ROOT/"data"/"toss_1m"),
            "--results-dir",str(V07_RESULTS_ROOT),
            "--symbols","TQQQ,AAPL",
            "--population","20",
            "--max-population","300",
            "--shortlist-max","40",
            "--seed","42",
        ]
        log=V07_REBUILD_LOG.open("wb",buffering=0)
        log.write((
            f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}] "
            "V0.7 baseline rebuild started; train=2022-2023 holdout=2024 dev=2025 finalOos=CLOSED\n"
        ).encode("utf-8"))
        try:
            child=subprocess.Popen(
                cmd,cwd=str(ROOT),stdin=subprocess.DEVNULL,
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True,
                env=dict(os.environ,EVOLUTION_RESULTS_DIR=str(V07_RESULTS_ROOT)),
            )
        finally:
            log.close()
        return {
            "started":True,
            "pid":child.pid,
            "researchOnly":True,
            "finalOosGate":"CLOSED",
            "train":"2022-01-03..2023-12-29",
            "internalHoldout":"2024-01-02..2024-12-31",
            "developmentValidation":"2025-01-02..2025-12-31",
        }


_AUTONOMOUS_PIPELINE=[
    ("downloading","데이터 다운로드"),
    ("analyzing","Baseline 분석"),
    ("planning","가설·실험 계획"),
    ("safety","안전 검사"),
    ("implementing","후보 구현"),
    ("testing","테스트"),
    ("training","학습"),
    ("internal_holdout","2024 Holdout"),
    ("shortlisting","후보 선별"),
    ("development_validation","2025 Validation"),
    ("diagnostics","일반화 진단"),
    ("comparing","Baseline 비교"),
    ("completed","완료"),
]

def _autonomous_iteration_folder(current):
    try:
        number=int(current.get("iteration"))
    except (TypeError,ValueError):
        return None
    folder=AUTONOMOUS_ROOT/f"iteration-{number:04d}"
    try:
        if not folder.is_dir() or folder.is_symlink() or folder.resolve().parent!=AUTONOMOUS_ROOT.resolve():
            return None
    except OSError:
        return None
    return folder

def _safe_ai_json(path):
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size>2*1024*1024:
            return {}
        value=json.loads(path.read_text(encoding="utf-8"))
        from research_export import sanitize
        return sanitize(value) if isinstance(value,dict) else {}
    except (OSError,ValueError,TypeError,json.JSONDecodeError):
        return {}

def _autonomous_pipeline(current):
    state=str(current.get("state") or "waiting")
    dry=bool(current.get("dryRun"))
    states=[x[0] for x in _AUTONOMOUS_PIPELINE]
    normalized=state if state in states else ("completed" if state=="completed" else None)
    current_index=states.index(normalized) if normalized in states else -1
    rows=[]
    for i,(key,label) in enumerate(_AUTONOMOUS_PIPELINE):
        if state=="failed":
            status="failed" if key==str(current.get("lastSuccessfulState") or "") else "pending"
        elif dry and state=="completed" and key in {"implementing","testing","training","internal_holdout","shortlisting","development_validation","diagnostics","comparing"}:
            status="skipped"
        elif state=="completed":
            status="complete"
        elif key=="safety":
            plan_done=current_index>=states.index("implementing") if current_index>=0 else False
            status="complete" if plan_done else ("active" if state=="planning" else "pending")
        elif current_index>=0:
            status="complete" if i<current_index else ("active" if i==current_index else "pending")
        else:
            status="active" if state=="queued" and i==0 else "pending"
        rows.append({"key":key,"label":label,"status":status})
    return rows

def _autonomous_checkpoints(folder):
    if folder is None:
        return []
    checks=[
        ("experiment","experiment.json"),
        ("source manifest","source_manifest.json"),
        ("baseline selection","baseline_selection.json"),
        ("baseline metrics","baseline_metrics.json"),
        ("plan","plan.json"),
        ("quarterly diagnostics","quarterly_diagnostics.json"),
        ("code diff","code_diff.patch"),
        ("tests","test_results.json"),
        ("training checkpoint","training_run.json"),
        ("holdout checkpoint","holdout_run.json"),
        ("stage process","stage_process.json"),
        ("validation checkpoint","validation_run.json"),
        ("diagnostics","diagnostics.json"),
        ("comparison","comparison.json"),
        ("report","report.json"),
        ("error traceback","error_traceback.log"),
        ("resource guard","resource_guard.json"),
    ]
    rows=[]
    for label,name in checks:
        p=folder/name
        try:
            rows.append({"label":label,"present":p.is_file() and not p.is_symlink(),
                         "bytes":p.stat().st_size if p.is_file() and not p.is_symlink() else 0})
        except OSError:
            rows.append({"label":label,"present":False,"bytes":0})
    shortlist=folder/"shortlist-v08"/"run.json"
    try:
        rows.append({"label":"shortlist","present":shortlist.is_file() and not shortlist.is_symlink(),
                     "bytes":shortlist.stat().st_size if shortlist.is_file() and not shortlist.is_symlink() else 0})
    except OSError:
        rows.append({"label":"shortlist","present":False,"bytes":0})
    return rows

def _safe_log_tail(path,lines=160,max_bytes=128*1024):
    from research_export import sanitize
    try:
        if not path.is_file() or path.is_symlink():
            return {"text":"","updatedAt":None,"bytes":0}
        size=path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0,size-max_bytes))
            raw=f.read(max_bytes)
        text_value=raw.decode("utf-8","replace")
        if size>max_bytes:
            text_value=text_value.split("\n",1)[-1]
        safe=[]
        for line in text_value.splitlines()[-max(1,min(int(lines),300)):]:
            clean=sanitize(line)
            safe.append(clean if isinstance(clean,str) else "[REDACTED]")
        return {
            "text":"\n".join(safe),
            "updatedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(path.stat().st_mtime)),
            "bytes":size,
        }
    except (OSError,ValueError):
        return {"text":"","updatedAt":None,"bytes":0}

def _latest_completed_dry_run():
    try:
        folders=sorted(
            (p for p in AUTONOMOUS_ROOT.glob("iteration-*") if p.is_dir() and not p.is_symlink()),
            key=lambda p:p.name,
            reverse=True,
        )
    except OSError:
        return None
    for folder in folders:
        info=_safe_ai_json(folder/"experiment.json")
        if info.get("state")=="completed" and info.get("dryRun") is True:
            return {
                "iteration":info.get("iteration"),
                "candidateVersion":info.get("candidateVersion"),
                "finishedAt":info.get("finishedAt"),
            }
    return None

def autonomous_admin_status():
    from research.autonomous.monitor import status as autonomous_status
    AUTONOMOUS_ROOT.mkdir(parents=True,exist_ok=True)
    result=autonomous_status(AUTONOMOUS_ROOT)
    processes=_autonomous_processes()
    current=dict(result.get("current") or {})
    folder=_autonomous_iteration_folder(current)
    detail={}
    if folder is not None:
        experiment=_safe_ai_json(folder/"experiment.json")
        for key in ("error","failureCode","startedAt","updatedAt","finishedAt","decision","dryRun",
                    "dryRunReviewed","selectedCandidates","trainingProgress","holdoutProgress",
                    "validationProgress","positiveBots","validationMean","validationMedian","avgTrades",
                    "baselineRunId","baselineCandidates","baselineRejected","resourceGuard","failureFacts","lastSuccessfulState"):
            if key in experiment:
                detail[key]=experiment.get(key)
        guard=_safe_ai_json(folder/"resource_guard.json")
        if guard:
            detail["resourceGuard"]=guard
        stage=_safe_ai_json(folder/"stage_process.json")
        if stage:
            detail["validationStage"]={
                "attempt":stage.get("attempt"),
                "startedAt":stage.get("startedAt"),
                "pid":(stage.get("identity") or {}).get("pid"),
            }
        selection=_safe_ai_json(folder/"baseline_selection.json")
        if selection:
            detail["baselineSelection"]={
                "policy":selection.get("policy"),
                "candidateCount":selection.get("candidateCount"),
                "selectedRunId":selection.get("selectedRunId"),
                "selectedCatalogId":selection.get("selectedCatalogId"),
                "criteriaRelaxed":selection.get("criteriaRelaxed"),
                "rejected":selection.get("rejected") or [],
            }
    dry_run=_latest_completed_dry_run()
    from research.autonomous.storage import research_storage
    return {
        **result,
        "current":{**current,**detail},
        "running":bool(processes),
        "processes":processes,
        "pipeline":_autonomous_pipeline({**current,**detail}),
        "checkpoints":_autonomous_checkpoints(folder),
        "dryRunReady":bool(dry_run),
        "reviewedDryRun":dry_run,
        "manifestAvailable":AUTONOMOUS_MANIFEST.is_file(),
        "manifestPath":"research/v4_data_manifest.json" if AUTONOMOUS_MANIFEST.is_file() else None,
        "logAvailable":AUTONOMOUS_WEB_LOG.is_file(),
        "finalOosGate":"CLOSED",
        "researchOnly":True,
        "storage":research_storage(AUTONOMOUS_ROOT,ROOT),
        "apiFeatures":api_feature_snapshot(),
        "baselineRebuild":v07_rebuild_status(),
        "serverTime":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
    }

def _issue_autonomous_session(ttl=43200):
    value=secrets.token_urlsafe(32)
    with SESSION_LOCK:
        SESSIONS[value]=time.time()+ttl
    return value

def start_autonomous_research(mode, provisional=False):
    for feature in ("autonomous_ai","research_export"):
        if not api_feature_enabled(feature):
            raise ValueError("API_DEPENDENCY_DISABLED: dependency="+feature)
    mode=str(mode or "").strip().lower()
    if mode not in {"dry-run","run"}:
        raise ValueError("mode must be dry-run or run")
    if not AUTONOMOUS_SCRIPT.is_file():
        raise ValueError("Autonomous research runner is not deployed")
    with AUTONOMOUS_LAUNCH_LOCK:
        if _autonomous_processes():
            raise ValueError("AUTONOMOUS_ALREADY_RUNNING")
        if mode=="run" and _latest_completed_dry_run() is None:
            raise ValueError("DRY_RUN_REVIEW_REQUIRED")
        AUTONOMOUS_ROOT.mkdir(parents=True,exist_ok=True)
        try:
            os.chmod(AUTONOMOUS_ROOT,0o700)
        except OSError:
            pass
        python=ROOT/".venv"/"bin"/"python"
        if not python.is_file():
            raise ValueError("virtualenv python is missing")
        cmd=[str(python),str(AUTONOMOUS_SCRIPT),"--max-iterations","1"]
        if mode=="dry-run":
            cmd.insert(2,"--dry-run")
        else:
            cmd.insert(2,"--resume")
            if AUTONOMOUS_MANIFEST.is_file() and not provisional:
                cmd.extend(["--data-manifest",str(AUTONOMOUS_MANIFEST)])
            elif provisional:
                cmd.append("--provisional")
            else:
                raise ValueError("REVIEWED_MANIFEST_REQUIRED_OR_EXPLICIT_PROVISIONAL")
        token=_issue_autonomous_session()
        env=dict(os.environ)
        env.update({
            "RESEARCH_EXPORT_BASE_URL":"http://127.0.0.1:8080",
            "RESEARCH_EXPORT_TOKEN":token,
            "EVOLUTION_AUTONOMOUS_DIR":str(AUTONOMOUS_ROOT),
        })
        logs_dir=AUTONOMOUS_ROOT/"logs"
        logs_dir.mkdir(parents=True,exist_ok=True)
        try:
            os.chmod(logs_dir,0o700)
        except OSError:
            pass
        if AUTONOMOUS_WEB_LOG.exists() and AUTONOMOUS_WEB_LOG.stat().st_size:
            stamp=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())
            archived=logs_dir/f"web-launch-{stamp}-{uuid.uuid4().hex[:8]}.log"
            AUTONOMOUS_WEB_LOG.replace(archived)
            try:
                os.chmod(archived,0o600)
            except OSError:
                pass
        log=AUTONOMOUS_WEB_LOG.open("wb",buffering=0)
        header=(
            f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}] "
            f"Autonomous Research launch mode={mode} provisional={bool(provisional)} "
            f"maxIterations=1 finalOos=CLOSED\n"
        ).encode("utf-8")
        log.write(header)
        try:
            child=subprocess.Popen(
                cmd,cwd=str(ROOT),env=env,stdin=subprocess.DEVNULL,
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True,
            )
        finally:
            log.close()
        return {
            "started":True,
            "mode":mode,
            "pid":child.pid,
            "provisional":bool(provisional),
            "manifestAvailable":AUTONOMOUS_MANIFEST.is_file(),
            "researchOnly":True,
            "finalOosGate":"CLOSED",
        }
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
        # Historical data may be bind-mounted onto a different EBS filesystem.
        data_dir=ROOT/"data"/"toss_1m"
        st=os.statvfs(str(data_dir if data_dir.exists() else ROOT))
        total=int(st.f_blocks*st.f_frsize)
        free=int(st.f_bavail*st.f_frsize)
        used=max(0,total-free)
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


def _tail_json_line(path,max_bytes=131072):
    path=Path(path)
    if not path.exists() or not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            size=f.seek(0,os.SEEK_END)
            f.seek(max(0,size-max_bytes))
            raw=f.read().decode("utf-8","ignore")
        lines=[line.strip() for line in raw.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                obj=json.loads(line)
                if isinstance(obj,dict):
                    return obj
            except Exception:
                continue
    except Exception:
        pass
    return {}


def _v4_audit_process():
    matches=[]
    proc=Path("/proc")
    try:
        entries=list(proc.iterdir())
    except Exception:
        return None
    for p in entries:
        if not p.name.isdigit():
            continue
        try:
            parts=[x.decode("utf-8","ignore") for x in (p/"cmdline").read_bytes().split(b"\0") if x]
        except Exception:
            continue
        if not any("audit_v4_clock_context.py" in x for x in parts):
            continue
        info={"pid":int(p.name),"args":parts}
        for flag,key in (("--out","out"),("--report","report"),("--sessions-out","sessions"),("--timestamp-kind","timestampKind")):
            try:
                i=parts.index(flag)
                if i+1<len(parts):
                    info[key]=parts[i+1]
            except ValueError:
                pass
        matches.append(info)
    return max(matches,key=lambda x:x["pid"]) if matches else None


def v4_audit_status():
    root=Path(os.getenv("V4_RESEARCH_ROOT","/home/ubuntu/v4-research"))
    proc=_v4_audit_process()
    run_dir=None
    out_path=None
    sessions_path=None
    report_path=None
    timestamp_kind=None

    if proc:
        out_path=Path(proc["out"]) if proc.get("out") else None
        sessions_path=Path(proc["sessions"]) if proc.get("sessions") else None
        report_path=Path(proc["report"]) if proc.get("report") else None
        timestamp_kind=proc.get("timestampKind")
        run_dir=(out_path or sessions_path or report_path).parent if (out_path or sessions_path or report_path) else None

    if run_dir is None and root.exists():
        try:
            dirs=[p for p in root.iterdir() if p.is_dir()]
            if dirs:
                run_dir=max(dirs,key=lambda p:p.stat().st_mtime)
        except Exception:
            run_dir=None

    if run_dir is not None:
        out_path=out_path or run_dir/"v4_data_audit.json"
        sessions_path=sessions_path or run_dir/"v4_data_audit_sessions.jsonl"
        report_path=report_path or run_dir/"v4_data_audit.md"

    result=_read_json_file(out_path) if out_path else {}
    last_session=_tail_json_line(sessions_path) if sessions_path else {}
    sessions_bytes=0
    updated_at=None
    if sessions_path and sessions_path.exists():
        try:
            st=sessions_path.stat()
            sessions_bytes=st.st_size
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(st.st_mtime))
        except Exception:
            pass

    if proc:
        phase="running"
    elif result:
        phase="completed" if str(result.get("status") or "").startswith("PASS") else "failed"
    elif run_dir:
        phase="interrupted"
    else:
        phase="idle"

    current_date=(last_session.get("sessionDate") or last_session.get("date") or
                  last_session.get("tradingDate") or last_session.get("day"))
    current_symbol=last_session.get("symbol")
    return {
        "available":bool(proc or run_dir or result),
        "running":bool(proc),
        "phase":phase,
        "pid":proc.get("pid") if proc else None,
        "timestampKind":timestamp_kind or result.get("timestampKind"),
        "runDir":str(run_dir) if run_dir else None,
        "sessionsBytes":sessions_bytes,
        "sessionsUpdatedAt":updated_at,
        "currentSessionDate":current_date,
        "currentSymbol":current_symbol,
        "lastSession":last_session,
        "resultReady":bool(result),
        "resultStatus":result.get("status"),
        "aggregate":result.get("aggregate") or {},
        "reportReady":bool(report_path and report_path.exists()),
    }



def _v4_read_dict(path):
    value=_read_json_file(path)
    return value if isinstance(value,dict) else {}


def _v4_processes(script_name):
    matches=[]
    try:
        entries=list(Path("/proc").iterdir())
    except Exception:
        return matches
    for p in entries:
        if not p.name.isdigit():
            continue
        try:
            parts=[x.decode("utf-8","ignore") for x in (p/"cmdline").read_bytes().split(b"\0") if x]
        except Exception:
            continue
        if not any(script_name in x for x in parts):
            continue
        item={"pid":int(p.name),"args":parts}
        for flag,key in (
            ("--results-dir","resultsDir"),("--from-date","fromDate"),("--to-date","toDate"),
            ("--strategy","strategy"),("--research-variant","researchVariant"),
            ("--state","state"),("--out","out")
        ):
            try:
                i=parts.index(flag)
                if i+1<len(parts):
                    item[key]=parts[i+1]
            except ValueError:
                pass
        matches.append(item)
    return matches


def _v4_latest_campaign(stage_dir):
    stage_dir=Path(stage_dir)
    if not stage_dir.exists():
        return None,None
    try:
        paths=list(stage_dir.glob("*/campaign.json"))
    except Exception:
        return None,None
    if not paths:
        return None,None
    path=max(paths,key=lambda p:p.stat().st_mtime)
    return path.parent,_v4_read_dict(path)


def _v4_file_time(path):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(Path(path).stat().st_mtime))
    except Exception:
        return None


def _v4_stage_status(run_root,stage,processes):
    stage_root=Path(run_root)/stage
    campaign_dir,state=_v4_latest_campaign(stage_root)
    if not isinstance(state,dict):
        state={}
    relevant=[]
    stage_resolved=str(stage_root.resolve())
    for proc in processes:
        value=proc.get("resultsDir")
        if not value:
            continue
        try:
            if str(Path(value).resolve())==stage_resolved:
                relevant.append(proc)
        except Exception:
            continue
    running=bool(relevant)
    total=len(state.get("jobs") or []) if state else 0
    completed=len(state.get("completed") or []) if state else 0
    current_job=state.get("currentJob") if state else None
    phase=str(state.get("phase") or ("running" if running else "waiting"))
    if running and phase in {"starting","running"}:
        phase="running"
    elif not running and phase in {"starting","running"}:
        phase="interrupted"
    elif not state:
        phase="waiting"

    current_state={}
    current_log=""
    current_dir=None
    if state and current_job:
        for job in state.get("jobs") or []:
            if job.get("name")==current_job:
                current_dir=Path(job.get("directory",""))
                break
    if current_dir:
        current_state=_v4_read_dict(current_dir/"state.json")
        for candidate in (current_dir/"run.log",current_dir/"terminal.log"):
            if candidate.exists():
                try:
                    current_log="\n".join(candidate.read_text(encoding="utf-8",errors="ignore").splitlines()[-30:])
                except Exception:
                    current_log=""
                if current_log:
                    break

    progress=0.0
    if total:
        progress=100.0*completed/total
        if current_job and completed<total:
            try:
                progress+=float(current_state.get("progress") or 0)/total
            except Exception:
                pass
    progress=max(0.0,min(100.0,progress))

    return {
        "stage":stage,
        "available":bool(state or campaign_dir),
        "running":running,
        "phase":phase,
        "campaignDir":str(campaign_dir) if campaign_dir else None,
        "updatedAt":_v4_file_time(campaign_dir/"campaign.json") if campaign_dir else None,
        "totalJobs":total or 5,
        "completedJobs":completed,
        "progress":round(progress,2),
        "currentJob":current_job,
        "error":state.get("error") if state else None,
        "dataMode":state.get("dataMode") if state else None,
        "current":{
            "phase":current_state.get("phase"),
            "progress":current_state.get("progress"),
            "processedRows":current_state.get("processedRows",0),
            "completedDays":current_state.get("completedDays",0),
            "currentDay":current_state.get("currentDay"),
            "currentTimestamp":current_state.get("currentTimestamp"),
            "trades":current_state.get("trades",0),
            "signals":current_state.get("signals",0),
            "entries":current_state.get("entries",0),
            "updatedAt":current_state.get("updatedAt"),
            "pid":current_state.get("pid"),
            "researchVariant":current_state.get("researchVariant"),
            "topRejectionReason":current_state.get("topRejectionReason"),
            "funnelDiagnosticsEnabled":current_state.get("funnelDiagnosticsEnabled"),
            "error":current_state.get("error"),
        },
        "log":current_log,
    }



def _v4_frequency_status(run_root):
    """Read only the latest analysis state; never launch jobs or inspect raw data."""
    folder=run_root/"funnel" if run_root else None
    files=list(folder.glob("*/state.json")) if folder and folder.is_dir() else []
    state_path=max(files,key=lambda p:p.stat().st_mtime) if files else None
    state=_v4_read_dict(state_path) if state_path else {}
    phase=state.get("phase","waiting")
    running=phase=="running" and any(p.get("pid")==state.get("pid") for p in _v4_processes("analyze_v4_frequency.py"))
    if phase=="running" and not running: phase="interrupted"
    return dict(stage="funnel",available=bool(state),running=running,phase=phase,
        progress=state.get("progress",0),completedJobs=state.get("completedJobs",0),totalJobs=state.get("totalJobs",5),
        currentJob=state.get("currentJob"),current=state,verdict=state.get("verdict"),
        candidateSelection=state.get("candidateSelection"),error=state.get("error"),
        resultPath=state.get("resultPath"),log=state.get("error") or state.get("candidateSelection") or "Read-only artifact analysis")


def v4_research_status():
    root=Path(os.getenv("V4_RESEARCH_ROOT","/home/ubuntu/v4-research"))
    run_root=None
    if root.exists():
        try:
            dirs=[p for p in root.iterdir() if p.is_dir()]
            if dirs:
                run_root=max(dirs,key=lambda p:p.stat().st_mtime)
        except Exception:
            run_root=None

    audit=v4_audit_status()
    if run_root is None and audit.get("runDir"):
        run_root=Path(audit["runDir"])

    campaigns=_v4_processes("run_v4_research_campaign.py")
    if run_root:
        smoke=_v4_stage_status(run_root,"smoke",campaigns)
        full=_v4_stage_status(run_root,"full",campaigns)
    else:
        smoke={"stage":"smoke","available":False,"running":False,"phase":"waiting","totalJobs":5,"completedJobs":0,"progress":0}
        full={"stage":"full","available":False,"running":False,"phase":"waiting","totalJobs":5,"completedJobs":0,"progress":0}

    comparison={}
    comparison_path=None
    all_analysis_path=None
    full_dir=Path(full["campaignDir"]) if full.get("campaignDir") else None
    if full_dir:
        comparison_path=full_dir/"v4_comparison.json"
        all_analysis_path=full_dir/"v4_all_analysis.json"
        comparison=_v4_read_dict(comparison_path)

    compare_phase="completed" if comparison else "waiting"
    if full.get("phase")=="completed" and not comparison:
        compare_phase="ready"
    if full.get("phase") in {"error","interrupted","failed"}:
        compare_phase="blocked"

    audit_status=str(audit.get("resultStatus") or "")
    audit_phase=("completed" if audit_status.startswith("PASS") else
                 "failed" if audit.get("resultReady") else audit.get("phase","waiting"))

    frequency=_v4_frequency_status(run_root)
    candidate_validation=dict(phase="blocked",running=False,reason="정량 funnel 검토 전 신규 후보 미선정; 기존 IR1-R1 보존")
    roadmap=[
        {"id":"audit","label":"데이터 감사","phase":audit_phase,
         "detail":audit_status or ("실행 중" if audit.get("running") else "대기")},
        {"id":"smoke","label":"Smoke 5개","phase":smoke.get("phase","waiting"),
         "detail":f'{smoke.get("completedJobs",0)}/{smoke.get("totalJobs",5)} 완료'},
        {"id":"full","label":"전체 백테스트","phase":full.get("phase","waiting"),
         "detail":f'{full.get("completedJobs",0)}/{full.get("totalJobs",5)} 완료'},
        {"id":"compare","label":"비교/최종 판정","phase":compare_phase,
         "detail":str(comparison.get("verdict") or ("실행 가능" if compare_phase=="ready" else "대기"))},
        {"id":"funnel","label":"Funnel diagnostics","phase":frequency["phase"],
         "detail":frequency.get("candidateSelection") or "기존 artifact 읽기 / 추가 gate 계측"},
        {"id":"candidate","label":"Candidate validation","phase":candidate_validation["phase"],
         "detail":candidate_validation["reason"]},
    ]

    active_stage=None
    if frequency.get("running"):
        active_stage="funnel"
    elif smoke.get("running"):
        active_stage="smoke"
    elif full.get("running"):
        active_stage="full"
    elif audit.get("running"):
        active_stage="audit"

    return {
        "runRoot":str(run_root) if run_root else None,
        "activeStage":active_stage,
        "audit":audit,
        "smoke":smoke,
        "full":full,
        "comparison":{
            "phase":compare_phase,
            "resultReady":bool(comparison),
            "verdict":comparison.get("verdict"),
            "comparison":comparison.get("comparison"),
            "updatedAt":_v4_file_time(comparison_path) if comparison_path and comparison_path.exists() else None,
            "allAnalysisReady":bool(all_analysis_path and all_analysis_path.exists()),
        },
        "funnel":frequency,
        "candidateValidation":candidate_validation,
        "roadmap":roadmap,
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
ADMIN_COLLECTORS={
    "oneMinute":{
        "service":"collect-expanded-universe.service",
        "label":"Toss 1분봉 확장 수집",
        "mode":"oneshot",
    },
    "rawTick":{
        "service":"collect-toss-ticks.service",
        "label":"Toss Raw Tick 수집",
        "mode":"continuous",
    },
}

def _systemctl_value(*args,timeout=15):
    code,out=_console_run(["systemctl",*args],timeout=timeout)
    return code,(out or "").strip()

def collector_admin_status():
    rows={}
    for key,item in ADMIN_COLLECTORS.items():
        service=item["service"]
        acode,active=_systemctl_value("is-active",service)
        ecode,enabled=_systemctl_value("is-enabled",service)
        rows[key]={
            **item,
            "active":active=="active",
            "activeState":active or ("unknown" if acode else "inactive"),
            "enabled":enabled in {"enabled","enabled-runtime"},
            "enabledState":enabled or ("unknown" if ecode else "disabled"),
        }
    return {"collectors":rows}

def control_collector(key,action):
    key=str(key or "")
    action=str(action or "").lower()
    if key not in ADMIN_COLLECTORS:
        raise ValueError("unknown collector")
    if action not in {"start","stop","restart","enable","disable"}:
        raise ValueError("unsupported collector action")
    if action in {"start", "restart", "enable"}:
        _, active = _systemctl_value("is-active", "research-collection-scheduler.service")
        _, enabled = _systemctl_value("is-enabled", "research-collection-scheduler.service")
        if active == "active" or enabled in {"enabled", "enabled-runtime"}:
            raise ValueError("CENTRAL_COLLECTION_SCHEDULER_OWNS_WORKERS: use pause/resume")
    service=ADMIN_COLLECTORS[key]["service"]
    args=["systemctl"]
    if action=="restart":
        args+=["restart",service]
    elif action in {"start","stop","enable","disable"}:
        args+=[action,service]
    code,out=_console_run(args,timeout=30)
    if code!=0:
        raise RuntimeError(out or f"{action} failed")
    return collector_admin_status()

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

def _validated_git_commit(ref):
    ref=str(ref or "").strip()
    if not ref or len(ref)>180 or ref.startswith("-") or any(x in ref for x in ("..","@{","\\","\n","\r"," ","\t")):
        raise ValueError("invalid git ref")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/\-]*|[0-9a-fA-F]{7,40}",ref):
        raise ValueError("invalid git ref")
    code,out=_git_run(["rev-parse","--verify",ref+"^{commit}"])
    if code!=0 or not re.fullmatch(r"[0-9a-f]{40}",out.strip()):
        raise ValueError("git ref not found")
    return out.strip()

def merge_git_ref(ref):
    sha=_validated_git_commit(ref)
    code,out=_git_run(["merge","--no-ff","--no-edit",sha],timeout=180)
    return code,(f"merge {ref} -> {sha[:12]}\n"+out).strip()

def deploy_status():
    parts=[]
    for args in (["status","--short","--branch"],["rev-list","--left-right","--count","HEAD...origin/strategy-engine-v3"]):
        code,out=_git_run(args)
        parts.append(out if code==0 else f"[git error] {out}")
    code,out=_console_run(["systemctl","is-active","market-career-dashboard.service"],timeout=10)
    parts.append("API service: "+(out or f"exit {code}"))
    return 0,"\n".join(parts)

def _evolution_workers_active():
    # Read-only flock probes survive API PID turnover; no research data writes.
    import fcntl
    for root in (Path("/mnt/research"),Path("/mnt/research-extra")):
        for name in ("v07-web-jobs","v08-web-jobs","v081-web-jobs","v082-web-jobs"):
            lock=root/name/"research.lock"
            if lock.is_symlink(): return True
            if not lock.is_file(): continue
            try:
                with lock.open("rb") as f:
                    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except (BlockingIOError,OSError):return True
    for item in Path("/proc").iterdir():
        if not item.name.isdecimal():continue
        try:tokens=(item/"cmdline").read_bytes().split(b"\0")
        except OSError:continue
        if any(Path(t.decode("utf-8","ignore")).name in {"run_v081.py","calibrate_v081.py","run_v082.py","calibrate_v082.py"} for t in tokens):return True
    return False


def deploy_apply():
    if _autonomous_processes() or _v07_rebuild_processes() or _evolution_workers_active():
        return 2,"AI_RESEARCH_RUNNING: AI 연구/기준선 재생성 완료 후 배포 적용/API 재시작을 실행하세요."
    branch=_git_branch()
    if branch!="strategy-engine-v3":
        return 2,"배포는 strategy-engine-v3 브랜치에서만 허용합니다."
    code,conflicts=_git_run(["diff","--name-only","--diff-filter=U"])
    if code!=0 or conflicts.strip():
        return 2,"병합 충돌을 먼저 해결하거나 'git merge --abort'를 실행하세요.\n"+conflicts
    checks=[
        [str(ROOT/".venv"/"bin"/"python"),"-m","py_compile",
         "server.py","research_export.py","research_export_metadata.py"],
    ]
    dep_code,dep_out=_install_runtime_requirements()
    if dep_code!=0:
        return dep_code,"[dependencies]\n"+dep_out
    outputs=["[dependencies]\n"+dep_out]
    for cmd in checks:
        code,out=_console_run(cmd,timeout=60)
        outputs.append("[check]\n"+(out or "OK"))
        if code!=0:
            return code,"\n".join(outputs)
    # Refresh the already-installed scheduler too; preserve its pause control.
    if _systemctl_value("is-active", "research-collection-scheduler.service")[1] == "active":
        code,out=_console_run(["systemctl", "--no-block", "restart", "research-collection-scheduler.service"],timeout=5)
        outputs.append("[collection scheduler]\n"+(out or "restart scheduled"))
        if code != 0: return code,"\n".join(outputs)
    code,out=_console_run(["systemctl","--no-block","restart","market-career-dashboard.service"],timeout=5)
    outputs.append("[restart]\n"+(out or "API restart scheduled"))
    return code,"\n".join(outputs)

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
        branch=_git_branch()
        parts=[out]
        if branch!="strategy-engine-v3":
            code2,out2=_git_run(["switch","strategy-engine-v3"],timeout=120)
            parts.append(out2)
            if code2!=0:
                return code2,"\n".join(x for x in parts if x).strip()
        code3,out3=_git_run(["merge","--no-ff","--no-edit","origin/strategy-engine-v3"],timeout=120)
        parts.append(out3)
        return code3,"\n".join(x for x in parts if x).strip()
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
            "  df -h (all mounted disks) | df -h / (root only) | lsblk\n"
            "  du -sh data/toss_1m\n"
            "  git status|branch|diff|fetch|pull|push\n"
            "  git merge-v3 | git merge <ref> | git merge --abort\n"
            "  git log [N]\n"
            "  deploy status|patch|apply\n"
            "  systemctl status|is-active|start|stop|restart|enable|disable <service>\n"
            "  api status|restart\n"
            "  deps status|install\n"
            "  journalctl <service> [N]\n"
            "  tail collector|v2|multi [N]\n"
            "  state collector|v2|multi\n"
            "  v07 prepare|audit|select|smoke|status|log (legacy research compatibility)\n"
            "  v08 smoke|train|holdout|validation|export|status|log|report|retry (detached V0.8 research)\n"
            "서비스: "+", ".join(sorted(ADMIN_CONSOLE_SERVICES))
        )

    if parts and parts[0]=="v07":
        from v07_web_jobs import console as v07_console
        return v07_console(parts)

    if parts and parts[0]=="v082":
        from v082_web_jobs import console as v082_console
        return v082_console(parts)
    if parts and parts[0]=="v081":
        from v081_web_jobs import console as v081_console
        return v081_console(parts)

    if parts and parts[0]=="v08":
        from v08_web_jobs import console as v08_console
        return v08_console(parts)

    if parts==["pwd"]:
        return 0,str(ROOT)

    if parts==["df","-h"]:
        return _console_run(["df","-h","-l"],timeout=10)
    if parts==["df","-h","/"]:
        return _console_run(["df","-h","/"],timeout=10)
    if parts==["lsblk"]:
        return _console_run(["lsblk","-o","NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT"],timeout=10)

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

    if parts==["git","merge-v3"]:
        code,out=_git_run(["fetch","--prune","origin"],timeout=120)
        if code!=0:
            return code,out
        code2,out2=merge_git_ref("origin/strategy-engine-v3")
        return code2,(out+"\n"+out2).strip()

    if parts==["git","merge","--abort"]:
        return _git_run(["merge","--abort"],timeout=60)

    if len(parts)==3 and parts[:2]==["git","merge"]:
        return merge_git_ref(parts[2])

    if parts and parts[0]=="deploy":
        if parts==["deploy","status"]:
            return deploy_status()
        if parts==["deploy","patch"]:
            code,out=_git_run(["fetch","--prune","origin"],timeout=120)
            if code!=0:
                return code,out
            code2,out2=merge_git_ref("origin/strategy-engine-v3")
            return code2,(out+"\n"+out2).strip()
        if parts==["deploy","apply"]:
            return deploy_apply()
        raise ValueError("usage: deploy status|patch|apply")

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
        if len(parts)!=3 or parts[1] not in {"status","is-active","start","stop","restart","enable","disable"}:
            raise ValueError("usage: systemctl status|is-active|start|stop|restart|enable|disable <service>")
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
        if not api_feature_enabled("dashboard_data"):
            time.sleep(min(30,INTERVAL))
            continue
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
                if not api_feature_enabled("market_data"):
                    with REALTIME_STATE_LOCK:
                        REALTIME_STATUS["lastError"]="market_data API feature disabled"
                    break
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
        if not api_feature_enabled("market_data"):
            with REALTIME_STATE_LOCK:
                REALTIME_STATUS["connected"]=False
                REALTIME_STATUS["lastError"]="market_data API feature disabled"
                REALTIME_STATUS["subscribed"]=[]
            await asyncio.sleep(5)
            continue
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


def collection_scheduler_status():
    """Public whitelist, local disk only; no market/calendar/token requests."""
    from research_collection_scheduler import STATE_DIR
    try:
        state = json.loads((STATE_DIR/"status.json").read_text())
        fields = ("mode", "reason", "isOpen", "etTime", "kstTime", "openAt", "closeAt",
                  "collector", "automatic", "updatedAt", "calendarConfirmed", "workerFailures", "lastExitCode")
        result = {k: state.get(k) for k in fields}
        from datetime import datetime, timezone
        from research_collection_scheduler import aware
        result["stale"] = (datetime.now(timezone.utc)-aware(state["updatedAt"])).total_seconds() > 15
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {"mode": "UNAVAILABLE", "automatic": False, "stale": True}

class Handler(BaseHTTPRequestHandler):
    def send_export_file(self,path,filename,content_type="application/zip"):
        # Incremental transfer; no multi-GB BytesIO archive in the HTTP process.
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,"rb") as stream:
            self.send_response(200)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Disposition",'attachment; filename="'+filename+'"')
            self.send_header("Access-Control-Allow-Origin","https://heh-heh.github.io")
            self.send_header("Access-Control-Expose-Headers","Content-Disposition")
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Length",str(os.fstat(fd).st_size))
            self.end_headers()
            try: shutil_copy_export(stream,self.wfile)
            except (BrokenPipeError,ConnectionResetError): pass

    def research_export_get(self,path):
        match=re.fullmatch(r"/api/research/export/jobs/([0-9a-f]{32})(/download)?",path)
        qs=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.requestline=re.sub(r"([?&]ticket=)[^&\s]+",r"\1[REDACTED]",self.requestline)
        if not authorized(self):
            ticket=(qs.get("ticket") or [""])[0]
            if not (match and match.group(2) and ticket and RESEARCH_EXPORTER is not None and RESEARCH_EXPORTER.consume_ticket(ticket,match.group(1))):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
        # A ticket is a 45-second single-archive capability, never an admin token.
        try:
            manager=research_exporter()
            if path=="/api/research/export/catalog":
                public=dict(manager.catalog()["public"])
                if (qs.get("researchOnly") or [""])[0]=="true":
                    from research_export import pre_final_oos_rows
                    public["runs"]=pre_final_oos_rows(public["runs"])
                    public["jobs"]=[]
                    public["researchBoundary"]="FINAL_OOS_EXCLUDED"
                self.send_json({"ok":True,**public}); return
            if path=="/api/research/export/summary":
                fmt=(qs.get("format") or ["json"])[0]
                if fmt not in ("json","csv"): raise ValueError("format must be json or csv")
                raw=manager.summary(fmt,research_only=(qs.get("researchOnly") or [""])[0]=="true")
                self.send_response(200)
                self.send_header("Content-Type","text/csv; charset=utf-8" if fmt=="csv" else "application/json; charset=utf-8")
                self.send_header("Content-Disposition",'attachment; filename="evolution_research_summary.'+fmt+'"')
                self.send_header("Access-Control-Allow-Origin","https://heh-heh.github.io")
                self.send_header("Cache-Control","no-store")
                self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            if match:
                if match.group(2):
                    source,name=manager.download(match.group(1)); self.send_export_file(source,name)
                else: self.send_json({"ok":True,**manager.get(match.group(1))})
                return
            self.send_json({"ok":False,"error":"Unknown export endpoint"},404)
        except FileNotFoundError as e: self.send_json({"ok":False,"error":str(e)},404)
        except ValueError as e: self.send_json({"ok":False,"error":str(e)},409)
        except Exception as e: self.send_json({"ok":False,"error":str(e)},503)

    def research_export_post(self,path):
        if not authorized(self):
            self.send_json({"ok":False,"error":"unauthorized"},401); return
        try:
            manager=research_exporter()
            if path=="/api/research/export/jobs":
                self.send_json({"ok":True,**manager.create(self.read_json())},202); return
            match=re.fullmatch(r"/api/research/export/jobs/([0-9a-f]{32})/ticket",path)
            if match:
                self.send_json({"ok":True,"downloadUrl":"/api/research/export/jobs/"+match.group(1)+"/download?ticket="+manager.ticket(match.group(1)),"expiresIn":45}); return
            self.send_json({"ok":False,"error":"Unknown export endpoint"},404)
        except FileNotFoundError as e: self.send_json({"ok":False,"error":str(e)},404)
        except (ValueError,TypeError) as e: self.send_json({"ok":False,"error":str(e)},400)
        except Exception as e: self.send_json({"ok":False,"error":str(e)},503)

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

    def collection50_get(self, path):
        if path == "/api/research/collection50/page":
            # One fixed file, never a user-supplied filesystem path. Fresh UI is
            # deployed with the API, independently of the static Pages cache.
            body = (ROOT/"collection.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers(); self.wfile.write(body); return
        # Read-only metadata is public; CSV downloads require admin session.
        import csv as _csv
        import gzip as _gzip
        base = Path(os.getenv("COLLECTION_50_DIR", "/mnt/research/collection-50-v1")).resolve()
        progress_file = base / "_collection_progress.json"
        if path == "/api/research/collection50/logs":
            from research.collection_activity import recent
            self.send_json({"ok": True, "events": recent(base, limit=120),
                            "retention": "rolling_last_two_segments", "sanitized": True})
            return
        if path == "/api/research/collection50/status":
            progress = {}
            if progress_file.is_file():
                try:
                    progress = json.loads(progress_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    progress = {}
            # Whitelist outward fields: never disclose auth, paths, API errors or request headers.
            fields = ("status", "stored", "page", "pages", "oldestTimestamp",
                      "latestTimestamp", "updatedAt", "coveragePct", "verifiedTradingDays",
                      "missingTradingDays", "currentGap", "terminationReason", "http429Count", "http401Count", "errorCode", "knownGapOutcomes", "restRequests", "lastHttpCode", "collectionFrom", "collectionTo", "direction", "historyCursor")
            rows = []
            for symbol, record in sorted(progress.items()):
                if not re.fullmatch(r"[A-Z0-9]{1,8}", symbol) or not isinstance(record, dict):
                    continue
                rows.append({"symbol": symbol, **{k:record.get(k) for k in fields},
                    "downloadAvailable": any((base/(symbol+suffix)).is_file() and not (base/(symbol+suffix)).is_symlink() for suffix in (".sqlite3", ".csv.gz"))})
            try:
                worker = json.loads((base/"_worker_status.json").read_text())
                active_symbol = worker.get("currentSymbol")
                if active_symbol is not None and not re.fullmatch(r"[A-Z0-9]{1,8}", str(active_symbol)): active_symbol = None
            except (OSError, ValueError, TypeError): active_symbol = None
            self.send_json({"ok": True, "symbols": rows, "count": len(rows), "currentSymbol": active_symbol,
                "scope": "collection_2000_2026_october", "researchDownloadScope": "pre_2026", "coverageVerified": False,
                "collectionFrom": "2000-01-01", "collectionTo": "2026-10-31", "direction": "newest_to_oldest",
                "finalOosGate": "CLOSED", "source": "isolated_50_symbol_collector",
                "scheduler": collection_scheduler_status()}); return
        match = re.fullmatch(r"/api/research/collection50/download/([A-Z0-9]{1,8})", path)
        if not match:
            self.send_json({"ok": False, "error": "not_found"}, 404); return
        if not authorized(self):
            self.send_json({"ok": False, "error": "unauthorized"}, 401); return
        symbol = match.group(1)
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        year_text = (qs.get("year") or [""])[0]
        if not re.fullmatch(r"202[2-5]", year_text):
            self.send_json({"ok": False, "error": "year_required_2022_2025"}, 400); return
        year = int(year_text)
        from research.collection_download import year_download
        export_dir = Path(os.getenv("COLLECTION_DOWNLOAD_DIR", "/home/ubuntu/research-exports/collection50"))
        try:
            with year_download(base, symbol, year, export_dir) as (file, metadata):
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Disposition", f'attachment; filename="{symbol}-{year}-research.csv.gz"')
                self.send_header("Content-Length", str(metadata["bytes"]))
                self.send_header("Access-Control-Allow-Origin", "https://heh-heh.github.io")
                self.send_header("Access-Control-Expose-Headers", "Content-Disposition, X-Content-SHA256, X-Export-Rows, X-Data-Validation")
                self.send_header("X-Content-SHA256", metadata["sha256"])
                self.send_header("X-Export-Rows", str(metadata["rows"]))
                self.send_header("X-Data-Validation", "PROVISIONAL_UNREVIEWED_DATA")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with file.open("rb") as stream:
                    while chunk := stream.read(65536): self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (ValueError, RuntimeError, OSError) as exc:
            code = 404 if isinstance(exc, FileNotFoundError) else 409
            self.send_json({"ok": False, "error": "COLLECTION_EXPORT_BLOCKED", "reason": str(exc) if re.fullmatch(r"[A-Z_0-9]+", str(exc)) else type(exc).__name__}, code)

    def do_GET(self):
        path=self.path.split("?")[0]
        if path.startswith("/api/research/collection50/"):
            self.collection50_get(path); return
        if path=="/api/research/v082/download":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                from v082_web_jobs import download as v082_download
                self.send_export_file(v082_download(), "v082-research-results.zip")
            except (ValueError,OSError) as exc:
                self.send_json({"ok":False,"error":str(exc)},409)
            return
        if path=="/api/research/v081/download":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                from v081_web_jobs import download as v081_download
                self.send_export_file(v081_download(), "v081-research-results.zip")
            except (ValueError,OSError) as exc:
                self.send_json({"ok":False,"error":str(exc)},409)
            return
        if path=="/api/research/v08/download":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                from v08_web_jobs import download as v08_download
                self.send_export_file(v08_download(), "v08-research-results.zip")
            except (ValueError,OSError) as exc:
                self.send_json({"ok":False,"error":str(exc)},409)
            return
        if path=="/api/research/v07/download":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                from v07_web_jobs import download
                self.send_export_file(download(), "v07-research-results.zip")
            except (ValueError,OSError) as exc:
                self.send_json({"ok":False,"error":str(exc)},409)
            return
        if path=="/api/admin/api-features":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            self.send_json({
                "ok":True,
                "features":api_feature_snapshot(),
                "settingsFile":str(API_FEATURES_FILE),
                "controlPlaneAlwaysOn":True,
            }); return
        disabled=disabled_api_feature(path,"GET")
        if disabled:
            self.send_json(api_disabled_response(path,disabled),503); return
        if path.startswith("/api/research/export/"):
            self.research_export_get(path); return
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
        if path=="/api/research/v4/audit/status":
            try:
                self.send_json({"ok":True,**v4_audit_status()})
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503)
            return
        if path=="/api/research/v4/status":
            try:
                self.send_json({"ok":True,**v4_research_status()})
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503)
            return
        if path=="/api/research/autonomous/status":
            try:
                from research.autonomous.monitor import status as autonomous_status
                root=Path(os.getenv("EVOLUTION_AUTONOMOUS_DIR","/home/ubuntu/evolution-autonomous"))
                self.send_json({"ok":True,**autonomous_status(root)})
            except (ValueError,OSError):
                self.send_json({"ok":False,"error":"Autonomous research status unavailable"},503)
            return
        if path=="/api/admin/ai/status":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                self.send_json({"ok":True,**autonomous_admin_status()})
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503)
            return
        if path=="/api/admin/ai/log":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                query=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                lines=max(20,min(300,int((query.get("lines") or ["160"])[0])))
                current=(autonomous_admin_status().get("current") or {})
                folder=_autonomous_iteration_folder(current)
                launch=_safe_log_tail(AUTONOMOUS_WEB_LOG,lines=lines)
                stage={"text":"","updatedAt":None,"bytes":0}
                stageName=None
                if folder is not None:
                    logs=sorted((p for p in folder.glob("stage-*.log") if p.is_file() and not p.is_symlink()),
                                key=lambda p:p.name)
                    if logs:
                        stageName=logs[-1].name
                        stage=_safe_log_tail(logs[-1],lines=lines)
                error_detail={"text":"","updatedAt":None,"bytes":0}
                if folder is not None:
                    error_detail=_safe_log_tail(folder/"error_traceback.log",lines=lines)
                rebuild_log=_safe_log_tail(V07_REBUILD_LOG,lines=lines)
                self.send_json({"ok":True,"launch":launch,"stage":stage,"stageName":stageName,
                                "errorDetail":error_detail,
                                "currentError":current.get("error"),
                                "failureCode":current.get("failureCode"),
                                "lastSuccessfulState":current.get("lastSuccessfulState"),
                                "baselineRebuildLog":rebuild_log,
                                "researchOnly":True,"finalOosGate":"CLOSED"})
            except (ValueError,OSError) as e:
                self.send_json({"ok":False,"error":str(e)},400)
            return
        if path=="/api/research/evolution/v08/status":
            try:
                from research.evolution.monitor import status as evolution_status
                root=Path(os.getenv("EVOLUTION_V08_RESULTS_DIR","/mnt/research/evolution-v08-profit-only"))
                self.send_json({"ok":True,**evolution_status(root),"finalOosGate":"CLOSED"})
            except (ValueError,OSError) as e:
                self.send_json({"ok":False,"error":str(e)},503)
            return
        if path=="/api/research/evolution/status":
            try:
                from research.evolution.monitor import status as evolution_status
                configured=os.getenv("EVOLUTION_RESULTS_DIR")
                if configured:
                    root=Path(configured).expanduser()
                else:
                    external=Path("/home/ubuntu/evolution-results")
                    root=external if external.exists() else ROOT/"research/results/evolution"
                self.send_json({"ok":True,**evolution_status(root)})
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
        if path=="/api/admin/server-health":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                from server_health import snapshot
                self.send_json(snapshot())
            except Exception:
                self.send_json({"ok":False,"error":"SERVER_HEALTH_UNAVAILABLE"},503)
            return
        if path=="/api/admin/github":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                self.send_json({"ok":True,**github_auth_status()})
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},500)
            return
        if path=="/api/admin/collectors":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                self.send_json({"ok":True,**collector_admin_status()})
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503)
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
        if path == "/api/research/collection50/control":
            if not authorized(self):
                self.send_json({"ok": False, "error": "unauthorized"}, 401); return
            try:
                body = self.read_json()
                if not isinstance(body, dict) or not isinstance(body.get("paused"), bool): raise ValueError("paused_boolean_required")
                scheduler_state = collection_scheduler_status()
                if not body['paused'] and (scheduler_state.get('stale', True) or not scheduler_state.get('automatic')):
                    self.send_json({"ok": False, "error": "COLLECTION_SCHEDULER_NOT_READY", "reason": "중앙 수집 서비스를 먼저 배포·적용하세요. 상태가 최신일 때만 시작할 수 있습니다."}, 409); return
                from research_collection_scheduler import save, STATE_DIR
                save(STATE_DIR/"control.json", {"paused": body["paused"]}, share_group=True)
                self.send_json({"ok": True, "paused": body["paused"]}); return
            except (ValueError, OSError):
                self.send_json({"ok": False, "error": "collection_control_rejected"}, 400); return

        if path=="/api/admin/api-features":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                if not isinstance(body,dict) or set(body)!={"feature","enabled"}:
                    raise ValueError("Expected exactly feature and enabled")
                result=set_api_feature(str(body["feature"]),body["enabled"])
                self.send_json({"ok":True,**result}); return
            except ValueError as e:
                self.send_json({"ok":False,"error":str(e)},400); return
            except Exception as e:
                self.send_json({"ok":False,"error":"API feature settings write failed: "+str(e)},503); return
        disabled=disabled_api_feature(path,"POST")
        if disabled:
            self.send_json(api_disabled_response(path,disabled),503); return
        if path.startswith("/api/research/export/"):
            self.research_export_post(path); return
        if path=="/api/admin/ai/rebuild-baseline":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                self.send_json({"ok":True,**start_v07_baseline_rebuild()},202); return
            except ValueError as e:
                self.send_json({"ok":False,"error":str(e)},409); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/admin/ai/start":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                if not isinstance(body,dict) or set(body)-{"mode","provisional"}:
                    raise ValueError("Invalid AI research request")
                mode=str(body.get("mode") or "")
                provisional=bool(body.get("provisional",False))
                self.send_json({"ok":True,**start_autonomous_research(mode,provisional)},202); return
            except ValueError as e:
                self.send_json({"ok":False,"error":str(e)},409); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
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
            except PreflightBlocked as e:
                self.send_json({"ok":False,"error":str(e),"phase":"blocked","runnable":False,
                                "blockedReason":str(e),"manifestAvailable":BACKTEST_MANAGER.preflight(engine)["manifestAvailable"]},412); return
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
        if path=="/api/admin/collectors/control":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                if not isinstance(body,dict) or set(body)-{"collector","action"}:
                    raise ValueError("invalid collector request")
                with ADMIN_CONSOLE_LOCK:
                    result=control_collector(body.get("collector"),body.get("action"))
                self.send_json({"ok":True,**result}); return
            except ValueError as e:
                self.send_json({"ok":False,"error":str(e)},400); return
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503); return
        if path=="/api/admin/git":
            if not authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            try:
                body=self.read_json()
                if not isinstance(body,dict) or set(body)-{"action","ref"}:
                    raise ValueError("invalid git request")
                action=str(body.get("action") or "")
                with GIT_LOCK:
                    if action=="merge-ref":
                        code,output=merge_git_ref(body.get("ref"))
                    elif action=="merge-abort":
                        code,output=_git_run(["merge","--abort"],timeout=60)
                    elif action=="deploy-status":
                        code,output=deploy_status()
                    elif action=="deploy-apply":
                        code,output=deploy_apply()
                    else:
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
    if not api_feature_enabled("live_trading"):
        _api_feature_side_effects("live_trading",False)
    LIVE_TRADER.start()
    if api_feature_enabled("paper_trading"):
        threading.Thread(target=restore_simple_paper,daemon=True,name="simple-paper-restore").start()
    else:
        _api_feature_side_effects("paper_trading",False)
    PAPER_V3.start()
    print(f"API listening on {HOST}:{PORT}; update interval={INTERVAL}s",flush=True)
    if not ADMIN_PASSWORD:
        print("WARNING: ADMIN_PASSWORD is not set; /api/login is disabled.",flush=True)
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
