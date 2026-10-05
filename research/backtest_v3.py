#!/usr/bin/env python3
import io, json, zipfile, urllib.request
from pathlib import Path
import pandas as pd
import numpy as np

SYMBOLS = ["NVDA","AMD","INTC","SOXL","SOXS","TQQQ"]
URL = "https://marketparquet.com/api/data/sample/intraday_sample.zip"
OUT = Path("research")
OUT.mkdir(exist_ok=True)

def load():
    req = urllib.request.Request(URL, headers={"User-Agent":"Mozilla/5.0 market-career-dashboard-backtest"})
    raw = urllib.request.urlopen(req, timeout=60).read()
    z = zipfile.ZipFile(io.BytesIO(raw))
    frames=[]
    for n in z.namelist():
        if not n.lower().endswith((".parquet",".csv")): continue
        try:
            b=z.read(n)
            if n.lower().endswith(".parquet"): d=pd.read_parquet(io.BytesIO(b))
            else: d=pd.read_csv(io.BytesIO(b))
        except Exception: continue
        cols={c.lower():c for c in d.columns}
        if not all(k in cols for k in ["timestamp","symbol","open","high","low","close","volume"]): continue
        d=d.rename(columns={cols[k]:k for k in cols})
        d=d[d.symbol.isin(SYMBOLS)].copy()
        if not d.empty: frames.append(d[["timestamp","symbol","open","high","low","close","volume"]])
    if not frames: raise RuntimeError("No target symbols found in sample pack")
    x=pd.concat(frames,ignore_index=True)
    x.timestamp=pd.to_datetime(x.timestamp)
    return x.sort_values(["symbol","timestamp"])

def bars(x):
    x=x.set_index("timestamp").sort_index()
    x=x.between_time("09:30","15:59")
    o=x.open.resample("3min",origin="start_day",offset="30min").first()
    h=x.high.resample("3min",origin="start_day",offset="30min").max()
    l=x.low.resample("3min",origin="start_day",offset="30min").min()
    c=x.close.resample("3min",origin="start_day",offset="30min").last()
    v=x.volume.resample("3min",origin="start_day",offset="30min").sum()
    d=pd.concat([o,h,l,c,v],axis=1).dropna()
    d.columns=["open","high","low","close","volume"]
    return d

def indicators(d):
    d=d.copy()
    d["ema9"]=d.close.ewm(span=9,adjust=False).mean()
    d["ema21"]=d.close.ewm(span=21,adjust=False).mean()
    tr=pd.concat([(d.high-d.low),(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1)
    d["atr"]=tr.rolling(14).mean()
    delta=d.close.diff()
    up=delta.clip(lower=0).rolling(14).mean()
    dn=(-delta.clip(upper=0)).rolling(14).mean()
    rs=up/dn.replace(0,np.nan)
    d["rsi"]=100-(100/(1+rs))
    d["vmed"]=d.volume.rolling(20).median()
    d["rvol"]=d.volume/d.vmed
    d["vwap"]=(d.close*d.volume).groupby(d.index.date).cumsum()/d.volume.groupby(d.index.date).cumsum()
    return d.dropna()

def trades(d, params):
    out=[]; i=30
    while i<len(d)-1:
        r=d.iloc[i]
        if not (r.ema9>r.ema21 and r.rvol>=params["rvol"] and r.close>r.high*0.999 and r.close>r.vwap and 50<=r.rsi<=params["rsi_hi"] and (r.close-r.ema9)<=params["atr_gap"]*r.atr):
            i+=1; continue
        # breakout condition is evaluated on completed bar; entry is next bar open
        if i<1 or r.close<=d.iloc[i-1].high:
            i+=1; continue
        entry=d.iloc[i+1].open
        stop=min(r.low, entry-params["stop_atr"]*r.atr)
        risk=entry-stop
        if risk<=0 or risk/entry>params["max_risk_pct"]: i+=1; continue
        target=entry+params["rr"]*risk
        exitp=entry; reason="TIME"
        end=min(len(d)-1,i+10)
        for j in range(i+1,end+1):
            q=d.iloc[j]
            if q.low<=stop:
                exitp=stop; reason="STOP"; break
            if q.high>=target:
                exitp=target; reason="TARGET"; break
            if j==end:
                exitp=q.close; reason="TIME"
        ret=(exitp/entry-1)*100
        out.append({"entry_time":str(d.index[i+1]),"entry":float(entry),"exit":float(exitp),"ret_pct":ret,"reason":reason})
        i=max(i+1,j+1)
    return pd.DataFrame(out)

def score(t):
    if t.empty: return {"trades":0}
    wins=(t.ret_pct>0).sum()
    gross_win=t.loc[t.ret_pct>0,"ret_pct"].sum()
    gross_loss=-t.loc[t.ret_pct<0,"ret_pct"].sum()
    return {
        "trades":int(len(t)),
        "win_rate_pct":round(100*wins/len(t),2),
        "avg_ret_pct":round(float(t.ret_pct.mean()),4),
        "median_ret_pct":round(float(t.ret_pct.median()),4),
        "profit_factor":round(float(gross_win/gross_loss),3) if gross_loss else None,
        "total_ret_pct":round(float(t.ret_pct.sum()),3),
        "max_drawdown_pct":round(float((t.ret_pct.cumsum()-t.ret_pct.cumsum().cummax()).min()),3),
        "stop_rate_pct":round(float((t.reason=="STOP").mean()*100),2),
    }

def main():
    x=load(); result={"symbols":{}, "source":"MarketParquet free intraday sample pack","formula":"V3 next-open execution, 3-minute bars"}
    grid=[]
    for rv in [1.2,1.4,1.5,1.7,2.0]:
      for hi in [65,70,75]:
       for gap in [0.6,0.8,1.0,1.2]:
        for rr in [1.0,1.2,1.5,2.0]:
         grid.append({"rvol":rv,"rsi_hi":hi,"atr_gap":gap,"stop_atr":1.2,"rr":rr,"max_risk_pct":0.012})
    for sym in SYMBOLS:
        q=x[x.symbol==sym]
        if q.empty: result["symbols"][sym]={"available":False}; continue
        d=indicators(bars(q))
        days=sorted(pd.Series(d.index.date).unique())
        if len(days)<5: result["symbols"][sym]={"available":True,"bars":len(d),"days":len(days),"note":"too few days"}; continue
        cut=days[max(1,int(len(days)*0.7)-1)]
        train=d[d.index.date<=cut]; test=d[d.index.date>cut]
        best=None
        for p in grid:
            s=score(trades(train,p))
            if s.get("trades",0)>=5:
                key=(s.get("win_rate_pct",0),s.get("profit_factor") or 0,s.get("avg_ret_pct",0))
                if best is None or key>best[0]: best=(key,p,s)
        if best is None:
            p=grid[0]
        else: p=best[1]
        result["symbols"][sym]={
            "available":True,"days":len(days),"bars_3m":len(d),
            "train_days":len(sorted(pd.Series(train.index.date).unique())),
            "test_days":len(sorted(pd.Series(test.index.date).unique())),
            "optimized_params":p,
            "train":score(trades(train,p)),
            "test":score(trades(test,p))
        }
    (OUT/"backtest_v3_result.json").write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=="__main__": main()
