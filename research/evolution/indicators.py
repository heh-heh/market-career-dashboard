"""V082 shared causal END-bar indicators. No quotes or synthetic orderbook inputs."""
from collections import deque
import math
import numpy as np

POLICY_INDICATORS=('sma20_distance','sma50_distance','sma200_distance',
    'ema9_distance','ema21_distance','ema50_distance','macd_hist_atr',
    'rsi_centered','stoch_rsi_centered','adx_direction','atr_pct',
    'bollinger_position','volatility_expansion','volume_expansion','price_volume')


def compute_indicators(bars):
    """One contiguous session prefix. Reset on gaps; no overnight averaging.

    Wilder RSI/ATR seed with 14 differences/TRs (first TR=high-low); ADX
    seeds from the first 14 available DX values. EMA seeds at first close.
    StochRSI is unsmoothed 14-RSI min/max; flat range returns 0.5.
    SMA200 warmup is required; missing measurements are None, never zero.
    """
    out={};history=deque(maxlen=201);rsi_history=deque(maxlen=14)
    trs=deque(maxlen=14);gains=deque(maxlen=14);losses=deque(maxlen=14)
    plus=deque(maxlen=14);minus=deque(maxlen=14);dxs=deque(maxlen=14)
    ema={};signal=None;atr=ag=al=pdm=mdm=adx=None;pv=vol=0.;previous=None
    for b in sorted(bars,key=lambda x:x.end):
        if b.end.year>=2026:raise ValueError('FINAL_OOS_ACCESS_BLOCKED')
        if b.end.tzinfo is None or b.end.utcoffset() is None:raise ValueError('TIMEZONE_REQUIRED')
        if previous and (b.end-previous.end).total_seconds()!=60:
            break  # prefix invalid forever after gap, matching original provider
        if not (all(math.isfinite(x) for x in (b.o,b.h,b.l,b.c,b.v)) and b.l>0 and b.v>=0 and b.h>=max(b.o,b.c) and b.l<=min(b.o,b.c)):
            break
        old=list(history);history.append(b);p=np.array([x.c for x in history])
        prior_vol=np.array([x.v for x in old[-20:]])
        vm=float(prior_vol.mean()) if len(prior_vol)==20 else None
        tr=max(b.h-b.l,abs(b.h-previous.c),abs(b.l-previous.c)) if previous else b.h-b.l
        trs.append(tr)
        atr=(sum(trs)/14 if len(trs)==14 else None) if atr is None else (13*atr+tr)/14
        if previous:
            diff=b.c-previous.c;gains.append(max(diff,0));losses.append(max(-diff,0))
            up=b.h-previous.h;down=previous.l-b.l
            pu=up if up>down and up>0 else 0.;md=down if down>up and down>0 else 0.
            plus.append(pu);minus.append(md)
            if ag is None and len(gains)==14:ag=sum(gains)/14;al=sum(losses)/14
            elif ag is not None:ag=(13*ag+max(diff,0))/14;al=(13*al+max(-diff,0))/14
            if pdm is None and len(plus)==14:pdm=sum(plus)/14;mdm=sum(minus)/14
            elif pdm is not None:pdm=(13*pdm+pu)/14;mdm=(13*mdm+md)/14
        rsi=(50. if ag==al==0 else 100. if al==0 else 100-100/(1+ag/al)) if ag is not None else None
        if rsi is not None:rsi_history.append(rsi)
        stoch=(.5 if max(rsi_history)==min(rsi_history) else (rsi-min(rsi_history))/(max(rsi_history)-min(rsi_history))) if len(rsi_history)==14 else None
        di_plus=100*pdm/atr if pdm is not None and atr else None
        di_minus=100*mdm/atr if mdm is not None and atr else None
        if di_plus is not None:
            dx=100*abs(di_plus-di_minus)/(di_plus+di_minus) if di_plus+di_minus else 0.
            dxs.append(dx)
            adx=(sum(dxs)/14 if len(dxs)==14 else None) if adx is None else (13*adx+dx)/14
        for n in (9,12,21,26,50):ema[n]=b.c if n not in ema else ema[n]+2/(n+1)*(b.c-ema[n])
        macd=ema[12]-ema[26];signal=macd if signal is None else signal+.2*(macd-signal)
        pv+=(b.h+b.l+b.c)/3*b.v;vol+=b.v;vwap=pv/vol if vol else None
        sma={n:float(p[-n:].mean()) if len(p)>=n else None for n in (20,50,200)}
        std=float(p[-20:].std()) if len(p)>=20 else None
        returns=np.diff(p)/p[:-1] if len(p)>1 else np.array([])
        # Both windows fully prior/current; disjoint short vs baseline volatility.
        vr=float(returns[-5:].std()/returns[-35:-5].std()) if len(returns)>=35 and returns[-35:-5].std()>0 else None
        vratio=b.v/vm if vm and vm>0 else None
        f=dict(sma20=sma[20],sma50=sma[50],sma200=sma[200],ema9=ema[9],ema21=ema[21],ema50=ema[50],
               vwap=vwap,macd=macd,macdSignal=signal,macdHistogram=macd-signal,rsi14=rsi,stochRsi14=stoch,
               adx14=adx,diPlus=di_plus,diMinus=di_minus,atr14=atr,
               bollingerMiddle=sma[20],bollingerUpper=sma[20]+2*std if std is not None else None,
               bollingerLower=sma[20]-2*std if std is not None else None,
               volatilityRatio=vr,volumeMean20=vm,relativeVolume=vratio,
               priceVolumeChange=(b.c/previous.c-1)*(vratio-1) if previous and vratio is not None else None,
               dollarVolume=b.c*b.v,spread=None,spreadSource='UNAVAILABLE_OHLCV',
               sourceTimestamp=b.end.isoformat(),timestampKind='end',session='REGULAR',
               ready=len(history)>=200 and all(x is not None for x in (atr,adx,rsi,stoch,vr,vratio,vwap)))
        if f['ready']:
            values=[(b.c/sma[n]-1)/.01 for n in (20,50,200)]
            values += [(b.c/ema[n]-1)/.01 for n in (9,21,50)]
            values += [(macd-signal)/atr if atr else 0.,(rsi-50)/50,2*stoch-1,
                       adx/100*(1 if di_plus>di_minus else -1 if di_plus<di_minus else 0),
                       (atr/b.c)/.01,(b.c-sma[20])/(2*std) if std else 0.,vr-1,vratio-1,
                       f['priceVolumeChange']/.01]
            f['policyValues']=tuple(float(np.clip(x,-5,5)) for x in values)
            trend=1 if ema[9]>ema[21]>ema[50] and b.c>vwap else -1 if ema[9]<ema[21]<ema[50] and b.c<vwap else 0
            momentum=1 if rsi>50 and macd>signal else -1 if rsi<50 and macd<signal else 0
            f.update(trend='BULL' if trend>0 else 'BEAR' if trend<0 else 'SIDEWAYS',
                     momentum=momentum,conflict=bool(trend*momentum<0),
                     trendPersistenceProxy=adx/100,entryRiskAtrPct=atr/b.c,
                     interpretation='Indicator strength is not a probability or demonstrated edge')
        out[b.end]=f;previous=b
    return out


class IndicatorCache:
    """One symbol/session calculation, shared by all genomes; no cross-run cache."""
    def __init__(self,provider):
        self.data={s:compute_indicators(bars.values()) for s,bars in provider.bars.items()}
        self.calculations=len(self.data)
    def get(self,symbol,t):return self.data.get(symbol,{}).get(t)
