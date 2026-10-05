#!/usr/bin/env python3
import asyncio, json, os, re, secrets, subprocess, sys, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from trading import paper as paper_broker, strategy as trading_strategy
from live_trader import LiveAutoTrader
from toss_auth import get_token as shared_toss_token

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"/"dashboard.json"
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
    try: os.chmod(SECRETS,0o600)
    except OSError: pass

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

def update_data():
    while True:
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
        if path=="/api/trading/status":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            self.send_json({"ok":True,**trading_status()}); return
        if path=="/api/trading/live/status":
            if not trading_authorized(self):
                self.send_json({"ok":False,"error":"unauthorized"},401); return
            self.send_json({"ok":True,**LIVE_TRADER.status()}); return
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
                log_text=log_path.read_text(encoding="utf-8",errors="ignore") if log_path.exists() else ""
                lines=log_text.splitlines()
                tail="\n".join(lines[-100:])
                symbols=["NVDA","AMD","INTC","SOXL","SOXS","TQQQ"]
                progress_path=ROOT/"data"/"toss_1m_progress.json"
                live_progress={}
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
                self.send_json({"ok":True,"phase":phase,"progress":progress,"running":running,
                    "symbols":list(stats.values()),"completedSymbols":completed,
                    "currentSymbol":current,"currentPage":current_stats["page"] if current_stats else None,
                    "totalPages":current_stats["pages"] if current_stats else None,
                    "currentStored":current_stats["stored"] if current_stats else None,
                    "resultReady":result_path.exists(),"updatedAt":time.time(),"log":tail})
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},500)
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
    print(f"API listening on {HOST}:{PORT}; update interval={INTERVAL}s",flush=True)
    if not ADMIN_PASSWORD:
        print("WARNING: ADMIN_PASSWORD is not set; /api/login is disabled.",flush=True)
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
