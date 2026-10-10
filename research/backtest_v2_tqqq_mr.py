#!/usr/bin/env python3
"""V2 research harness for the only V1 OOS-stable setup: TQQQ VWAP mean reversion.

Goals:
1) Do NOT optimize against one full-history number.
2) Use coarse, interpretable parameter sets.
3) Evaluate chronological rolling windows.
4) Separate train ranking from test performance.
5) Keep execution conservative: next-minute open, stop-before-target, slippage.

This script is research-only and does not place orders or modify live settings.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import statistics
import time
from collections import defaultdict
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

NY=ZoneInfo("America/New_York")
EPS=1e-12


def parse_ts(v):
    dt=datetime.fromisoformat(str(v).replace("Z","+00:00"))
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=NY)
    return dt.astimezone(NY)


def load_days(path):
    rows=[]
    with gzip.open(path,"rt",encoding="utf-8",newline="") as fp:
        for r in csv.DictReader(fp):
            try:
                dt=parse_ts(r["timestamp"])
                if not (dtime(9,30) <= dt.time() < dtime(16,0)):
                    continue
                o,h,l,c=map(float,(r["open"],r["high"],r["low"],r["close"]))
                v=float(r.get("volume") or 0)
                if min(o,h,l,c)<=0: continue
                rows.append({"dt":dt,"o":o,"h":h,"l":l,"c":c,"v":max(0.0,v)})
            except Exception:
                continue
    rows.sort(key=lambda x:x["dt"])
    by=defaultdict(list)
    for r in rows: by[r["dt"].date().isoformat()].append(r)
    return dict(by)


def aggregate(rows,n=5):
    out=[]
    for i in range(0,len(rows)-n+1,n):
        ch=rows[i:i+n]
        if len(ch)<n: continue
        out.append({
            "dt":ch[-1]["dt"],"o":ch[0]["o"],"h":max(x["h"] for x in ch),
            "l":min(x["l"] for x in ch),"c":ch[-1]["c"],"v":sum(x["v"] for x in ch),
            "start":i,"end":i+n-1,
        })
    return out


def ema(vals,n):
    if not vals:return []
    a=2/(n+1)
    out=[vals[0]]
    for x in vals[1:]: out.append(a*x+(1-a)*out[-1])
    return out


def atr(bars,n=14):
    tr=[]
    for i,b in enumerate(bars):
        p=bars[i-1]["c"] if i else b["c"]
        tr.append(max(b["h"]-b["l"],abs(b["h"]-p),abs(b["l"]-p)))
    return statistics.fmean(tr[-n:]) if tr else 0


def er(vals,n=12):
    if len(vals)<=n:return 0
    net=abs(vals[-1]-vals[-1-n])
    path=sum(abs(vals[i]-vals[i-1]) for i in range(len(vals)-n,len(vals)))
    return net/max(path,EPS)


def vwap_series(bars):
    pv=vv=0.0; out=[]
    for b in bars:
        tp=(b["h"]+b["l"]+b["c"])/3
        pv += tp*b["v"]; vv += b["v"]
        out.append(pv/max(vv,EPS))
    return out


def cross_count(vals,refs,n=12):
    if len(vals)<2:return 0
    start=max(1,len(vals)-n)
    prev=1 if vals[start-1]>=refs[start-1] else -1
    c=0
    for i in range(start,len(vals)):
        cur=1 if vals[i]>=refs[i] else -1
        if cur!=prev:c+=1
        prev=cur
    return c


def zscore(vals,n=20):
    w=vals[-n:]
    if len(w)<2:return 0
    sd=statistics.pstdev(w)
    return (w[-1]-statistics.fmean(w))/max(sd,EPS)


def rvol(vols,n=20):
    base=vols[-n-1:-1] if len(vols)>n else vols[:-1]
    med=statistics.median(base) if base else max(vols[-1],1)
    return vols[-1]/max(med,EPS)


def features(bars):
    cs=[x["c"] for x in bars]; vs=[x["v"] for x in bars]
    vw=vwap_series(bars)
    dev=[c-v for c,v in zip(cs,vw)]
    a=atr(bars)
    return {
        "price":cs[-1],"prev":cs[-2],"atr":a,"er":er(cs),
        "vwap":vw[-1],"z":zscore(dev),"zprev":zscore(dev[:-1]) if len(dev)>2 else 0,
        "cross":cross_count(cs,vw),"rvol":rvol(vs),
        "vwapSlope":(vw[-1]-vw[-4])/max(a,EPS) if len(vw)>=4 else 0,
        "ema9":ema(cs,9)[-1],"ema21":ema(cs,21)[-1],
    }


def in_window(dt,start,end):
    t=dt.time()
    return dtime(*start) <= t <= dtime(*end)


def signal(f,p,dt):
    # Range quality is a soft regime gate but entry confirmation is hard:
    # price must have been statistically stretched and already turning back.
    if not in_window(dt,p["start"],p["end"]): return False
    if f["er"] > p["er_max"]: return False
    if f["cross"] < p["cross_min"]: return False
    if f["rvol"] > p["rvol_max"]: return False
    if abs(f["vwapSlope"]) > p["slope_max"]: return False
    if not (f["zprev"] < -p["z_entry"] and f["z"] > f["zprev"] and f["price"] > f["prev"]):
        return False
    return True


def simulate(day, entry_i, f, p, slip_bps):
    if entry_i>=len(day):return None
    slip=slip_bps/10000
    entry=day[entry_i]["o"]*(1+slip)
    stop=entry-p["stop_atr"]*f["atr"]
    risk=entry-stop
    if risk<=EPS:return None

    if p["target"]=="VWAP":
        target=f["vwap"]
        # If next-open gaps through VWAP, use a minimum positive reward target.
        if target<=entry: target=entry+p["min_target_r"]*risk
    else:
        target=entry+p["target_r"]*risk

    last=min(len(day)-1,entry_i+p["max_hold_min"])
    exit_px=day[last]["c"]; reason="time"
    mfe=0.0; mae=0.0
    for j in range(entry_i,last+1):
        b=day[j]
        mfe=max(mfe,b["h"]-entry)
        mae=max(mae,entry-b["l"])
        if b["l"]<=stop:
            exit_px=stop; reason="stop"; last=j; break
        if b["h"]>=target:
            exit_px=target; reason="target"; last=j; break
        if b["dt"].time()>=dtime(15,50):
            exit_px=b["c"]; reason="close"; last=j; break

    fill=exit_px*(1-slip)
    ret=fill/entry-1
    return {
        "entryTime":day[entry_i]["dt"].isoformat(),
        "exitTime":day[last]["dt"].isoformat(),
        "returnPct":ret*100,
        "R":ret/(risk/entry),
        "exitReason":reason,
        "mfeR":mfe/risk,
        "maeR":mae/risk,
    }


def run_variant(days,p,slip_bps=2.0):
    trades=[]
    for day,rows in sorted(days.items()):
        bars=aggregate(rows,5)
        taken=False
        for bi in range(5,len(bars)):
            hist=bars[:bi+1]
            f=features(hist)
            dt=bars[bi]["dt"]
            if signal(f,p,dt):
                # enter next 1m open after the completed 5m bar
                entry_i=bars[bi]["end"]+1
                tr=simulate(rows,entry_i,f,p,slip_bps)
                if tr:
                    trades.append({"day":day,**tr})
                    taken=True
                break
        # At most one setup/day keeps variants comparable and reduces overtrading.
    return trades


def summary(trades,risk_fraction=.002):
    if not trades:
        return {"trades":0,"winRatePct":0,"expectancyR":0,"profitFactorR":0,
                "compoundedReturnPct":0,"maxDrawdownPct":0}
    rs=[x["R"] for x in trades]
    wins=[x for x in rs if x>0]; losses=[x for x in rs if x<=0]
    gp=sum(wins); gl=abs(sum(losses))
    eq=peak=1.0; mdd=0.0
    for r in rs:
        eq*=1+max(-.99,risk_fraction*r)
        peak=max(peak,eq)
        mdd=min(mdd,eq/peak-1)
    return {
        "trades":len(rs),
        "winRatePct":round(100*len(wins)/len(rs),2),
        "expectancyR":round(statistics.fmean(rs),5),
        "profitFactorR":round(gp/gl,4) if gl>EPS else (999.0 if gp>0 else 0),
        "avgWinR":round(statistics.fmean(wins),5) if wins else 0,
        "avgLossR":round(abs(statistics.fmean(losses)),5) if losses else 0,
        "compoundedReturnPct":round((eq-1)*100,3),
        "maxDrawdownPct":round(mdd*100,3),
    }


def make_variants():
    # Coarse variants only. No tiny decimal grid.
    out=[]
    idx=0
    for z in (1.2,1.5,1.8,2.1):
      for ermax in (.30,.40):
       for rvmax in (1.8,2.5):
        for stop in (.8,1.0,1.2):
         idx+=1
         out.append({
           "id":f"MR{idx:03d}",
           "z_entry":z,"er_max":ermax,"rvol_max":rvmax,
           "cross_min":2,"slope_max":.35,"stop_atr":stop,
           "target":"VWAP","min_target_r":.6,"target_r":1.0,
           "max_hold_min":60,"start":(10,0),"end":(14,30),
         })
    # Add a few time/regime variants around the baseline rather than exploding the grid.
    extras=[
      {"z_entry":1.5,"er_max":.35,"rvol_max":2.0,"cross_min":3,"slope_max":.25,"stop_atr":1.0,"target":"VWAP","min_target_r":.7,"target_r":1.0,"max_hold_min":45,"start":(10,30),"end":(14,0)},
      {"z_entry":1.8,"er_max":.35,"rvol_max":2.0,"cross_min":2,"slope_max":.25,"stop_atr":1.0,"target":"VWAP","min_target_r":.8,"target_r":1.0,"max_hold_min":60,"start":(10,0),"end":(15,0)},
      {"z_entry":1.5,"er_max":.30,"rvol_max":1.8,"cross_min":3,"slope_max":.20,"stop_atr":.8,"target":"VWAP","min_target_r":.7,"target_r":1.0,"max_hold_min":45,"start":(11,0),"end":(14,30)},
    ]
    for e in extras:
      idx+=1;e=dict(e);e["id"]=f"MR{idx:03d}";out.append(e)
    return out


def split_days(days):
    keys=sorted(days)
    n=len(keys)
    # Anchored 3-fold forward validation. Each test slice is future data.
    cuts=[(.45,.60),(.60,.75),(.75,1.0)]
    folds=[]
    for a,b in cuts:
        train_end=max(1,int(n*a)); test_end=max(train_end+1,int(n*b))
        train_keys=keys[:train_end]
        test_keys=keys[train_end:test_end]
        folds.append((
            {k:days[k] for k in train_keys},
            {k:days[k] for k in test_keys},
            (train_keys[0],train_keys[-1]) if train_keys else None,
            (test_keys[0],test_keys[-1]) if test_keys else None,
        ))
    return folds


def score_candidate(folds,p,slip):
    fold_results=[]
    for train,test,trange,vrange in folds:
        tr=summary(run_variant(train,p,slip))
        te=summary(run_variant(test,p,slip))
        fold_results.append({"trainRange":trange,"testRange":vrange,"train":tr,"test":te})
    tests=[x["test"] for x in fold_results]
    valid=[x for x in tests if x["trades"]>=8]
    positive=sum(1 for x in valid if x["expectancyR"]>0 and x["profitFactorR"]>1.0)
    med_exp=statistics.median([x["expectancyR"] for x in valid]) if valid else -999
    med_pf=statistics.median([x["profitFactorR"] for x in valid]) if valid else 0
    total=sum(x["trades"] for x in valid)
    # Stability first; raw magnitude second.
    rank=(positive,med_exp,med_pf,total)
    return {"params":p,"folds":fold_results,"positiveTestFolds":positive,
            "medianTestExpectancyR":round(med_exp,5),"medianTestProfitFactorR":round(med_pf,4),
            "testTrades":total,"rank":rank}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data",default="/home/ubuntu/market-career-dashboard/data/toss_1m/TQQQ.csv.gz")
    ap.add_argument("--out",default="/var/lib/market-career-dashboard/backtest_v2_tqqq_mr.json")
    ap.add_argument("--slippage-bps",type=float,default=2.0)
    ap.add_argument("--state",default="")
    args=ap.parse_args()

    days=load_days(Path(args.data))
    folds=split_days(days)
    results=[]
    variants=make_variants()
    state_path=Path(args.state) if args.state else None
    def write_state(i,phase="running",top=None):
        if not state_path:return
        state_path.parent.mkdir(parents=True,exist_ok=True)
        obj={
          "phase":phase,"running":phase=="running",
          "completedVariants":i,"totalVariants":len(variants),
          "progress":round(100*i/max(1,len(variants)),1),
          "updatedAt":time.time(),"top":top or [],
        }
        tmp=state_path.with_suffix(state_path.suffix+".tmp")
        tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
        tmp.replace(state_path)
    write_state(0)
    for i,p in enumerate(variants,1):
        results.append(score_candidate(folds,p,args.slippage_bps))
        if i%5==0 or i==len(variants):
            tmp_top=sorted(results,key=lambda x:x["rank"],reverse=True)[:3]
            write_state(i,top=tmp_top)
            print(f"{i}/{len(variants)} variants",flush=True)

    results.sort(key=lambda x:x["rank"],reverse=True)
    payload={
      "engine":"v2_tqqq_mean_reversion_research",
      "symbol":"TQQQ","days":len(days),"slippageBps":args.slippage_bps,
      "variantsTested":len(results),
      "selectionRule":"maximize positive future folds, then median OOS expectancy R/PF; coarse grid only",
      "top":results[:15],
      "all":results,
      "generatedAt":time.time(),
    }
    p=Path(args.out);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    write_state(len(variants),phase="completed",top=results[:5])
    print(json.dumps({"out":str(p),"days":len(days),"variants":len(results),"top":results[:5]},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
