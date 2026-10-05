#!/usr/bin/env python3
import json, os, subprocess, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"/"dashboard.json"
HOST=os.getenv("HOST","0.0.0.0")
PORT=int(os.getenv("PORT","8080"))
INTERVAL=int(os.getenv("UPDATE_INTERVAL","300"))

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
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 market-career-dashboard/1.1"})
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
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        if self.path.split("?")[0]=="/api/health":
            self.send_json({"ok":True,"service":"market-career-dashboard"})
            return
        if self.path.split("?")[0]=="/api/quotes":
            self.send_json(live_quotes())
            return
        if self.path.split("?")[0]=="/api/dashboard":
            try:
                self.send_json(json.loads(DATA.read_text(encoding="utf-8")))
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},503)
            return
        self.send_json({"error":"not found"},404)
    def log_message(self,fmt,*args):
        print("%s - %s"%(self.address_string(),fmt%args),flush=True)

if __name__=="__main__":
    DATA.parent.mkdir(parents=True,exist_ok=True)
    threading.Thread(target=update_data,daemon=True).start()
    print(f"API listening on {HOST}:{PORT}; update interval={INTERVAL}s",flush=True)
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
