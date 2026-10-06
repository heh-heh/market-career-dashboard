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
DEFAULT_UNTIL="2026-10-01T23:59:59+00:00"
PROGRESS=ROOT/"data"/"toss_1m_progress.json"
ERROR_LOG=ROOT/"data"/"toss_errors.jsonl"

def token(force=False):
    cfg=json.loads(SECRETS.read_text(encoding="utf-8"))
    tok=shared_toss_token(cfg, force=force)
    if not tok:
        raise RuntimeError("Toss OAuth token을 발급받지 못했습니다.")
    return tok

def record_error(symbol, before, attempt, error, http_code=None, body=None):
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry={"timestamp":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
           "symbol":symbol,"before":before,"attempt":attempt,
           "httpCode":http_code,"error":str(error),
           "response":(body or "")[:1000]}
    with ERROR_LOG.open("a",encoding="utf-8") as f:
        f.write(json.dumps(entry,ensure_ascii=False)+"\n")

def fetch_page(tok,symbol,before=None,count=100,refresh_token=None):
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
            if e.code==429:
                record_error(symbol,before,attempt,"HTTPError",e.code,body)
                if attempt<7:
                    time.sleep(min(30,2**attempt))
                    continue
            if e.code==401 and attempt<2 and refresh_token is not None:
                record_error(symbol,before,attempt,"HTTPError",e.code,body)
                msg=body.lower()
                if "token-revoked" in msg or "unauthorized" in msg or "invalid-token" in msg:
                    tok=refresh_token()
                    time.sleep(0.5)
                    continue
            record_error(symbol,before,attempt,"HTTPError",e.code,body)
            raise RuntimeError(f"Toss candles HTTP {e.code}: {body[:300]}")
        except Exception as e:
            record_error(symbol,before,attempt,type(e).__name__,None,str(e))
            raise


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

def save_rows(path, rows):
    ordered=[rows[k] for k in sorted(rows)]
    tmp=path.with_suffix(".tmp.gz")
    with gzip.open(tmp,"wt",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["timestamp","open","high","low","close","volume","currency"])
        w.writeheader()
        w.writerows(ordered)
    os.replace(tmp,path)
    return len(ordered)

def collect(symbol,since,until,pages,sleep_s,batch_pages,batch_sleep):
    tok=token()
    rows={}
    before=until
    out=ROOT/"data"/"toss_1m"
    out.mkdir(parents=True,exist_ok=True)
    path=out/(symbol+".csv.gz")
    if path.exists():
        try:
            with gzip.open(path,"rt",newline="",encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    ts=r.get("timestamp")
                    if ts and (not since or ts >= since) and (not until or ts <= until):
                        rows[ts]=r
            existing_oldest=min(rows) if rows else None
            if existing_oldest and (not since or existing_oldest > since):
                before=existing_oldest
            elif until:
                before=until
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
        try:
            obj=fetch_page(tok,symbol,before,refresh_token=refresh)
            candles=obj.get("candles") or []
        except Exception as e:
            saved=save_rows(path,rows)
            write_progress(symbol,{"symbol":symbol,"status":"error","page":n,"pages":pages,"stored":saved,
                                  "fetched":0,"coveragePct":round(initial_coverage,2),"latestTimestamp":latest_ts,
                                  "oldestTimestamp":min(rows) if rows else existing_oldest,"since":since,
                                  "startedAt":started,"updatedAt":time.time(),"error":str(e)[:500]})
            print(f"{symbol}: ERROR after page {n}; checkpoint saved={saved}",flush=True)
            raise
        if not candles: break
        page_ts=[str(x.get("timestamp") or "") for x in candles if x.get("timestamp")]
        if page_ts and latest_ts is None:
            latest_ts=max(page_ts)
        oldest_ts=min(page_ts) if page_ts else None
        for c in candles:
            ts=str(c.get("timestamp") or "")
            if not ts: continue
            if since and ts < since: continue
            if until and ts > until: continue
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
        if (n + 1) % batch_pages == 0:
            saved=save_rows(path,rows)
            write_progress(symbol,{"symbol":symbol,"status":"checkpoint","page":n+1,"pages":pages,"stored":saved,
                                  "fetched":len(candles),"coveragePct":round(coverage,2),"latestTimestamp":latest_ts,
                                  "oldestTimestamp":min(rows) if rows else oldest_ts,"since":since,
                                  "startedAt":started,"updatedAt":time.time()})
            print(f"{symbol}: checkpoint saved={saved} at page {n+1}; pausing {batch_sleep:.1f}s",flush=True)
            if n + 1 < pages:
                time.sleep(batch_sleep)
    saved=save_rows(path,rows)
    write_progress(symbol,{"symbol":symbol,"status":"completed","page":n+1 if 'n' in locals() else 0,"pages":pages,"stored":saved,"fetched":saved,
                        "coveragePct":100,"latestTimestamp":latest_ts,"oldestTimestamp":min(rows) if rows else None,
                        "since":since,"startedAt":started,"updatedAt":time.time()})
    print(f"{symbol}: total saved {saved} -> {path}",flush=True)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--symbols",default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--since",default="2021-01-01T00:00:00+00:00")
    ap.add_argument("--until",default=DEFAULT_UNTIL)
    ap.add_argument("--pages",type=int,default=10000)
    ap.add_argument("--sleep",type=float,default=0.5)
    ap.add_argument("--batch-pages",type=int,default=150)
    ap.add_argument("--batch-sleep",type=float,default=10.0)
    a=ap.parse_args()
    for s in [x.strip().upper() for x in a.symbols.split(",") if x.strip()]:
        collect(s,a.since,a.until,a.pages,a.sleep,a.batch_pages,a.batch_sleep)

if __name__=="__main__": main()
