#!/usr/bin/env python3
"""Collect historical 1-minute OHLCV candles from Toss Securities Open API."""
import argparse, csv, gzip, json, time, base64, sys, os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
SECRETS=ROOT/"server_secrets.json"
sys.path.insert(0,str(ROOT))
from toss_auth import get_token as shared_toss_token
BASE="https://openapi.tossinvest.com"
TOKEN_URL=BASE+"/oauth2/token"
DEFAULT_SYMBOLS=["NVDA","AMD","INTC","SOXL","SOXS","TQQQ"]
PROGRESS=ROOT/"data"/"toss_1m_progress.json"

def token(force=False):
    cfg=json.loads(SECRETS.read_text(encoding="utf-8"))
    tok=shared_toss_token(cfg, force=force)
    if not tok:
        raise RuntimeError("Toss OAuth token을 발급받지 못했습니다.")
    return tok

def fetch_page(tok,symbol,before=None,count=200,refresh_token=None):
    q={"symbol":symbol,"interval":"1m","count":str(count),"adjusted":"true"}
    if before: q["before"]=before
    url=BASE+"/api/v1/candles?"+urlencode(q)
    for attempt in range(8):
        try:
            req=Request(url,headers={"Authorization":"Bearer "+tok,
                                     "User-Agent":"market-career-dashboard/history-collector"})
            with urlopen(req,timeout=20) as r:
                return json.loads(r.read()).get("result",{})
        except HTTPError as e:
            body=e.read().decode("utf-8","ignore")
            if e.code==429 and attempt<7:
                time.sleep(min(30,2**attempt))
                continue
            if e.code==401 and attempt<2 and refresh_token is not None:
                msg=body.lower()
                if "token-revoked" in msg or "unauthorized" in msg:
                    tok=refresh_token()
                    time.sleep(0.5)
                    continue
            raise RuntimeError(f"Toss candles HTTP {e.code}: {body[:300]}")


def write_progress(symbol, payload):
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    if PROGRESS.exists():
        try:
            current = json.loads(PROGRESS.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current[symbol] = payload
    tmp = PROGRESS.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, PROGRESS)

def collect(symbol,since,pages,sleep_s):
    tok=token()
    rows={}
    before=None
    out=ROOT/"data"/"toss_1m"
    out.mkdir(parents=True,exist_ok=True)
    path=out/(symbol+".csv.gz")
    if path.exists():
        try:
            with gzip.open(path,"rt",newline="",encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r.get("timestamp"):
                        rows[r["timestamp"]]=r
            existing_oldest=min(rows) if rows else None
            if existing_oldest and (not since or existing_oldest > since):
                before=existing_oldest
        except Exception:
            rows={}
    refresh=lambda: token(force=True)
    started=time.time()
    latest_ts=max(rows) if rows else None
    existing_oldest=min(rows) if rows else None
    initial_coverage=0.0
    if latest_ts and existing_oldest and since:
        try:
            import datetime as _dt
            a=_dt.datetime.fromisoformat(since.replace("Z","+00:00")).timestamp()
            b=_dt.datetime.fromisoformat(latest_ts.replace("Z","+00:00")).timestamp()
            o=_dt.datetime.fromisoformat(existing_oldest.replace("Z","+00:00")).timestamp()
            if b>a: initial_coverage=max(0.0,min(100.0,((b-o)/(b-a))*100.0))
        except Exception: pass
    write_progress(symbol,{"symbol":symbol,"status":"starting","page":0,"pages":pages,"stored":len(rows),"fetched":0,"coveragePct":round(initial_coverage,2),"latestTimestamp":latest_ts,"oldestTimestamp":existing_oldest,"since":since,"startedAt":started,"updatedAt":started})
    for n in range(pages):
        obj=fetch_page(tok,symbol,before,refresh_token=refresh)
        candles=obj.get("candles") or []
        if not candles: break
        page_ts=[str(x.get("timestamp") or "") for x in candles if x.get("timestamp")]
        if page_ts and latest_ts is None:
            latest_ts=max(page_ts)
        oldest_ts=min(page_ts) if page_ts else None
        for c in candles:
            ts=str(c.get("timestamp") or "")
            if not ts: continue
            if since and ts < since: continue
            rows[ts]={"timestamp":ts,"open":c.get("openPrice"),"high":c.get("highPrice"),
                      "low":c.get("lowPrice"),"close":c.get("closePrice"),
                      "volume":c.get("volume"),"currency":c.get("currency")}
        next_before=obj.get("nextBefore")
        coverage=0.0
        if latest_ts and oldest_ts and since:
            try:
                import datetime as _dt
                a=_dt.datetime.fromisoformat(since.replace("Z","+00:00")).timestamp()
                b=_dt.datetime.fromisoformat(latest_ts.replace("Z","+00:00")).timestamp()
                o=_dt.datetime.fromisoformat(oldest_ts.replace("Z","+00:00")).timestamp()
                if b>a:
                    coverage=max(0.0,min(100.0,((b-o)/(b-a))*100.0))
            except Exception:
                pass
        write_progress(symbol,{"symbol":symbol,"status":"collecting","page":n+1,"pages":pages,"stored":len(rows),"fetched":len(candles),"coveragePct":round(coverage,2),"latestTimestamp":latest_ts,"oldestTimestamp":oldest_ts,"since":since,"startedAt":started,"updatedAt":time.time()})
        print(f"{symbol}: page {n+1}/{pages}, fetched={len(candles)}, stored={len(rows)}",flush=True)
        if since and next_before and next_before < since: break
        if not next_before or next_before==before: break
        before=next_before
        time.sleep(sleep_s)
    ordered=[rows[k] for k in sorted(rows)]
    with gzip.open(path,"wt",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["timestamp","open","high","low","close","volume","currency"])
        w.writeheader(); w.writerows(ordered)
    write_progress(symbol,{"symbol":symbol,"status":"completed","page":n+1 if 'n' in locals() else 0,"pages":pages,"stored":len(ordered),"fetched":len(ordered),"coveragePct":100,"latestTimestamp":latest_ts,"oldestTimestamp":ordered[0]["timestamp"] if ordered else None,"since":since,"startedAt":started,"updatedAt":time.time()})
    print(f"{symbol}: total saved {len(ordered)} -> {path}",flush=True)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--symbols",default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--since",default="2021-01-01T00:00:00+00:00")
    ap.add_argument("--pages",type=int,default=3000)
    ap.add_argument("--sleep",type=float,default=0.25)
    a=ap.parse_args()
    for s in [x.strip().upper() for x in a.symbols.split(",") if x.strip()]:
        collect(s,a.since,a.pages,a.sleep)

if __name__=="__main__": main()
