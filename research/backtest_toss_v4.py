#!/usr/bin/env python3
"""Backtest the 3-minute V3 strategy using locally collected Toss 1-minute candles."""
import argparse, gzip, json
from pathlib import Path
import pandas as pd
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"/"toss_1m"
OUT=ROOT/"research"/"backtest_toss_v4_result.json"
SYMBOLS=["NVDA","AMD","INTC","SOXL","SOXS","TQQQ"]

def load_symbol(sym):
    p=DATA/(sym+".csv.gz")
    if not p.exists(): return pd.DataFrame()
    with gzip.open(p,"rt",encoding="utf-8") as f:
        d=pd.read_csv(f)
    d["timestamp"]=pd.to_datetime(d["timestamp"],utc=True)
    for c in ["open","high","low","close","volume"]: d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.dropna(subset=["timestamp","open","high","low","close","volume"])
    return d.sort_values("timestamp")

def bars(x):
    x=x.set_index("timestamp").sort_index()
    # US equities regular session in New York time.
    x=x.tz_convert("America/New_York")
    x=x.between_time("09:30","15:59")
    frames=[]
    for day, g in x.groupby(x.index.date):
        g=g.sort_index()
        o=g.open.resample("3min",origin="start_day",offset="30min").first()
        h=g.high.resample("3min",origin="start_day",offset="30min").max()
        l=g.low.resample("3min",origin="start_day",offset="30min").min()
        c=g.close.resample("3min",origin="start_day",offset="30min").last()
        v=g.volume.resample("3min",origin="start_day",offset="30min").sum()
        d=pd.concat([o,h,l,c,v],axis=1).dropna()
        d.columns=["open","high","low","close","volume"]
        frames.append(d)
    return pd.concat(frames).sort_index() if frames else pd.DataFrame()

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
    pv=d.close*d.volume
    d["vwap"]=pv.groupby(d.index.date).cumsum()/d.volume.groupby(d.index.date).cumsum()
    return d.replace([np.inf,-np.inf],np.nan).dropna()

def trades(d,p,slip=0.0002):
    out=[]; i=30
    while i<len(d)-1:
        r=d.iloc[i]
        if not (r.ema9>r.ema21 and r.rvol>=p["rvol"] and r.close>d.iloc[i-1].high and
                r.close>r.vwap and 50<=r.rsi<=p["rsi_hi"] and
                (r.close-r.ema9)<=p["atr_gap"]*r.atr):
            i+=1; continue
        entry=d.iloc[i+1].open
        # Practical stop: use the tighter of the signal-bar low and ATR stop.
        stop=max(r.low,entry-p["stop_atr"]*r.atr)
        risk=entry-stop
        if risk<=0 or risk/entry>p["max_risk_pct"]:
            i+=1; continue
        target=entry+p["rr"]*risk
        exitp=entry; reason="TIME"; j=i+1
        end=min(len(d)-1,i+10)
        for j in range(i+1,end+1):
            q=d.iloc[j]
            if q.low<=stop:
                exitp=stop; reason="STOP"; break
            if q.high>=target:
                exitp=target; reason="TARGET"; break
            if j==end: exitp=q.close
        gross=(exitp/entry-1)*100
        net=gross-(slip*2*100)
        out.append({"entry_time":str(d.index[i+1]),"entry":float(entry),"exit":float(exitp),
                    "gross_ret_pct":gross,"ret_pct":net,"reason":reason})
        i=max(i+1,j+1)
    return pd.DataFrame(out)

def score(t):
    if t.empty:return {"trades":0}
    wins=(t.ret_pct>0).sum()
    gw=t.loc[t.ret_pct>0,"ret_pct"].sum()
    gl=-t.loc[t.ret_pct<0,"ret_pct"].sum()
    eq=t.ret_pct.cumsum(); dd=eq-eq.cummax()
    return {"trades":int(len(t)),"win_rate_pct":round(100*wins/len(t),2),
            "avg_ret_pct":round(float(t.ret_pct.mean()),4),
            "median_ret_pct":round(float(t.ret_pct.median()),4),
            "profit_factor":round(float(gw/gl),3) if gl else None,
            "total_ret_pct":round(float(t.ret_pct.sum()),3),
            "max_drawdown_pct":round(float(dd.min()),3),
            "stop_rate_pct":round(float((t.reason=="STOP").mean()*100),2)}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--min-days",type=int,default=100)
    ap.add_argument("--slippage",type=float,default=0.0002)
    a=ap.parse_args()
    grid=[{"rvol":rv,"rsi_hi":hi,"atr_gap":gap,"stop_atr":1.2,"rr":rr,"max_risk_pct":.012}
          for rv in [1.2,1.4,1.5,1.7,2.0]
          for hi in [65,70,75]
          for gap in [.6,.8,1.0,1.2]
          for rr in [1.0,1.2,1.5,2.0]]
    result={"source":"Toss Securities Open API historical 1m candles",
            "formula":"V4 3-minute bars, next-open execution, 0.02% slippage per side",
            "symbols":{}}
    for sym in SYMBOLS:
        raw=load_symbol(sym)
        if raw.empty: result["symbols"][sym]={"available":False}; continue
        d=indicators(bars(raw))
        days=sorted(pd.Series(d.index.date).unique())
        if len(days)<a.min_days:
            result["symbols"][sym]={"available":True,"days":len(days),"bars_3m":len(d),
                                     "note":f"need at least {a.min_days} days"}
            continue
        cut=days[int(len(days)*.7)-1]
        train=d[d.index.date<=cut]; test=d[d.index.date>cut]
        best=None
        for p in grid:
            s=score(trades(train,p,a.slippage))
            if s.get("trades",0)>=20:
                key=(s.get("profit_factor") or 0,s.get("win_rate_pct",0),s.get("avg_ret_pct",0))
                if best is None or key>best[0]: best=(key,p,s)
        p=best[1] if best else grid[0]
        result["symbols"][sym]={
            "available":True,"days":len(days),"bars_3m":len(d),
            "train_days":len(sorted(pd.Series(train.index.date).unique())),
            "test_days":len(sorted(pd.Series(test.index.date).unique())),
            "optimized_params":p,
            "train":score(trades(train,p,a.slippage)),
            "test":score(trades(test,p,a.slippage))}
    OUT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=="__main__": main()
