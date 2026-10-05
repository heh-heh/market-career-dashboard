#!/usr/bin/env python3
import json, os, secrets, subprocess, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"/"dashboard.json"
SECRETS=ROOT/"server_secrets.json"
HOST=os.getenv("HOST","0.0.0.0")
PORT=int(os.getenv("PORT","8080"))
INTERVAL=int(os.getenv("UPDATE_INTERVAL","300"))
ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD","")
SESSIONS=set()
SESSION_LOCK=threading.Lock()

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
    with SESSION_LOCK:
        return bool(token and token in SESSIONS)

def update_data():
    while True:
        try:
            subprocess.run(["python3",str(ROOT/"scripts"/"update_data.py")],cwd=ROOT,timeout=240,check=False)
        except Exception as e:
            print("update error:",e,flush=True)
        time.sleep(INTERVAL)

def live_quotes():
    try:
        data=json.loads(DATA.read_text(encoding="utf-8"))
        items=data.get("stocks",[])+data.get("indices",[])
        out={}
        for item in items:
            symbol=item.get("symbol")
            if not symbol: continue
            url="https://query1.finance.yahoo.com/v8/finance/chart/"+urllib.parse.quote(symbol,safe="")+"?range=1d&interval=1m&includePrePost=false"
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 market-career-dashboard/1.2"})
            try:
                with urllib.request.urlopen(req,timeout=5) as r:
                    obj=json.loads(r.read())
                meta=obj["chart"]["result"][0]["meta"]
                price=meta.get("regularMarketPrice")
                prev=meta.get("previousClose")
                change=((price/prev)-1)*100 if price is not None and prev else None
                out[symbol]={"price":price,"change":change,"live":True}
            except Exception:
                continue
        return {"updatedAt":time.time(),"quotes":out}
    except Exception as e:
        return {"updatedAt":time.time(),"quotes":{},"error":str(e)}

class Handler(BaseHTTPRequestHandler):
    def send_json(self,obj,status=200):
        raw=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin","*")
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
            with SESSION_LOCK: SESSIONS.add(token)
            self.send_json({"ok":True,"token":token},200)
            return
        if path=="/api/logout":
            token=auth_token(self)
            with SESSION_LOCK: SESSIONS.discard(token)
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
    print(f"API listening on {HOST}:{PORT}; update interval={INTERVAL}s",flush=True)
    if not ADMIN_PASSWORD:
        print("WARNING: ADMIN_PASSWORD is not set; /api/login is disabled.",flush=True)
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
