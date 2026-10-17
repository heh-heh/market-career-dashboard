"""Isolated V082 research portfolio replay. No broker imports or network calls."""
from collections import Counter
from datetime import datetime
import numpy as np
from .account import Account,ExecutionCostModel
from .features import MinuteFeatureProvider,POLICY_BASE,benchmark_for_symbol
from .indicators import IndicatorCache,POLICY_INDICATORS

LEVERAGE={'TQQQ':3.,'SQQQ':3.,'SOXL':3.,'SOXS':3.}
INVERSE=frozenset(('SQQQ','SOXS'))


class Portfolio:
    """Independent cash ledger; existing Account handles each fractional lot.

    Position cash is reserved once at entry. Missing held prices invalidate the
    evaluation, even if observations later recover; marks never create fills.
    """
    def __init__(self,config):
        self.config=config;self.initial_capital=config.initial_capital;self.reserve=self.initial_capital
        self.lots={};self.pending=[];self.trades=[];self.cancellations={};self.max_drawdown=0.;self.peak_equity=self.initial_capital
        self.research_data_complete=True;self.observations_stale=False;self.research_marks=[];self.data_gap_samples=[];self._cost=0.;self._turnover=0.;self.realized_pnl=0.
        self.last_exit=None;self.decision_samples=[];self.latest_analysis={}
    @property
    def position(self):return {'symbol':','.join(sorted(self.lots)),'positions':[a.position for a in self.lots.values()]} if self.lots else None
    @property
    def cash(self):return self.reserve+sum(a.cash for a in self.lots.values())
    @property
    def equity(self):return self.reserve+sum(a.equity for a in self.lots.values())
    @property
    def transaction_cost(self):return self._cost+sum(a.transaction_cost for a in self.lots.values())
    @property
    def turnover(self):return self._turnover+sum(a.turnover for a in self.lots.values())
    def mark(self,t):
        self.peak_equity=max(self.peak_equity,self.equity)
        self.max_drawdown=max(self.max_drawdown,(self.peak_equity-self.equity)/self.peak_equity)
        self.research_marks.append((t,self.equity))
    def buy(self,s,price,t,budget,signal,genome):
        if s in self.lots or budget>self.reserve+1e-9 or budget<self.config.portfolio_min_order:raise ValueError('PORTFOLIO_CASH_OR_DUPLICATE')
        a=Account(budget,ExecutionCostModel(self.config.slippage_bps,self.config.half_spread_bps));a.research_timing=True
        a.buy(s,price,t,1.,signal,genome)
        a.position.update(indicators=signal.get("indicators"),indicatorContributions=signal.get("indicatorContributions"),decisionReason="V082_LINEAR_INDICATOR_THRESHOLD")
        self.reserve-=budget;self.lots[s]=a
    def sell(self,s,price,t,pending):
        a=self.lots.pop(s);trade=a.sell(price,t,pending['reason'],pending['signalTimestamp'])
        self.reserve+=a.cash;self._cost+=a.transaction_cost;self._turnover+=a.turnover;self.realized_pnl+=a.realized_pnl
        self.trades.append(trade);self.last_exit=t
    def state_features(self,t,duration):
        held=max(((t-datetime.fromisoformat(a.position['entryTimestamp'])).total_seconds()/60 for a in self.lots.values()),default=0.)
        return (float(bool(self.lots)),sum(a.unrealized_pnl for a in self.lots.values())/self.initial_capital,held/duration,self.cash/self.initial_capital)


def correlation(provider,a,b,t):
    """Exact last 31 completed closes. Missing/flat series cannot prove independence."""
    from research.backtest_v4_strategies import MINUTE
    if getattr(provider,'_correlation_time',None)!=t:
        provider._correlation_time=t;provider._correlation_cache={}
    key=tuple(sorted((a,b)))
    if key in provider._correlation_cache:return provider._correlation_cache[key]
    paths=[]
    for s in (a,b):
        bars=[provider.bars.get(s,{}).get(t-i*MINUTE) for i in range(30,-1,-1)]
        if any(x is None for x in bars):return None
        p=np.array([x.c for x in bars]);r=np.diff(p)/p[:-1]
        if r.std()<1e-12:return None
        paths.append(r)
    value=float(np.corrcoef(*paths)[0,1]);provider._correlation_cache[key]=value
    return value


def allocation(portfolio,s,price,indicator,provider,t,genome):
    """Cash, stop-risk, effective gross ETF exposure and past correlation caps.

    A cap reduction never liquidates an existing lot. Unknown dependence blocks
    adding that candidate, rather than assuming zero correlation.
    """
    c=portfolio.config;equity=portfolio.equity
    if portfolio.observations_stale:return 0.,'INVALID_PORTFOLIO_OBSERVATION'
    if max(portfolio.max_drawdown,(portfolio.peak_equity-equity)/portfolio.peak_equity)>=c.portfolio_daily_loss_limit:return 0.,'DAILY_LOSS_LIMIT'
    regime=provider.market[t]
    if not regime['valid']:return 0.,'MARKET_CONTEXT_MISSING'
    if regime['regime']=='BEAR' and s not in INVERSE:return 0.,'BEAR_LONG_RISK'
    if regime['regime']=='BULL' and s in INVERSE:return 0.,'BULL_INVERSE_RISK'
    if len(portfolio.lots)>=c.portfolio_max_positions:return 0.,'ADMIN_POSITION_CAP'
    if s in portfolio.lots:return 0.,'ALREADY_HELD'
    if indicator['dollarVolume']<c.portfolio_min_dollar_volume:return 0.,'LOW_DOLLAR_VOLUME'
    for other in portfolio.lots:
        # Same underlying levered/inverse exposure is never independent.
        if (s in LEVERAGE or other in LEVERAGE) and benchmark_for_symbol(s)[0]==benchmark_for_symbol(other)[0]:
            return 0.,'SHARED_LEVERAGED_UNDERLYING'
        rho=correlation(provider,s,other,t)
        if rho is None:return 0.,'CORRELATION_UNAVAILABLE'
        if abs(rho)>=c.portfolio_correlation_limit:return 0.,'CORRELATED_EXPOSURE'
    used_risk=sum(max(0,a.position['quantity']*(a.last_price-a.position['stopPrice'])) for a in portfolio.lots.values())
    remaining=max(0,equity*c.portfolio_risk_budget-used_risk)
    # ATR and stop distance both matter. Costs included in prospective stop risk.
    risk_pct=max(genome.stop_loss_pct,indicator['atr14']/price)+2*(c.slippage_bps+c.half_spread_bps)/10000
    effective=sum(a.position['quantity']*a.last_price*LEVERAGE.get(x,1.) for x,a in portfolio.lots.items())
    budget=min(portfolio.reserve*genome.position_fraction,remaining/risk_pct,
               max(0,equity-effective)/LEVERAGE.get(s,1.))
    return (budget,'RISK_AND_CASH_APPROVED') if budget>=c.portfolio_min_order else (0.,'INSUFFICIENT_CASH_OR_RISK_BUDGET')


def signal_decision(base_score,indicator_score,threshold,indicator,valid,holding=False):
    if not valid or not indicator or not indicator.get('ready'):return 'BLOCKED','INDICATOR_OR_REFERENCE_WARMUP_GAP'
    if indicator['conflict']:return ('HOLD' if holding else 'WAIT'),'INDICATOR_CONFLICT'
    if base_score+indicator_score>=threshold:return 'BUY','LINEAR_INDICATOR_BUY_THRESHOLD'
    return ('HOLD' if holding else 'WAIT'),'BELOW_BUY_THRESHOLD'


def analysis_record(bot,t,s,decision,reason,score,indicator,contributions,a,budget,config):
    return dict(symbol=s,botId=bot.bot_id,decisionTimestamp=t.isoformat(),dataOrigin='HISTORICAL_COMPLETED_MINUTE',
        decision=decision,reason=reason,score=score,indicators=indicator,contributions=contributions,
        portfolio=dict(held=len(a.lots),adminCap=config.portfolio_max_positions,
            allowedPositions=len(a.lots)+(1 if budget else 0),newEntriesAllowed=1 if budget else 0,
            proposedBudget=budget,cash=a.cash,equity=a.equity,
            stopRisk=sum(max(0,x.position['quantity']*(x.last_price-x.position['stopPrice'])) for x in a.lots.values()),
            effectiveGrossExposure=sum(x.position['quantity']*x.last_price*LEVERAGE.get(sym,1.) for sym,x in a.lots.items())),
        expectedRoundTripCostBps=2*(config.slippage_bps+config.half_spread_bps))


def evaluate_day(bots,rows,opening,closing,symbols,config,*,data_policy='strict',resource_guard=None):
    from research.backtest_v4_strategies import MINUTE
    if opening.year>=2026 or closing.year>=2026:raise ValueError('FINAL_OOS_ACCESS_BLOCKED')
    if data_policy not in ('strict','causal_available'):raise ValueError('Unknown data policy')
    if resource_guard:resource_guard()
    provider=MinuteFeatureProvider(config).prepare(rows,opening,closing,symbols)
    cache=IndicatorCache(provider)
    if resource_guard:resource_guard()
    accounts=[Portfolio(config) for _ in bots];counts=Counter();samples=[];latest={}
    complete=all(len(provider.bars.get(s,{}))==provider.duration for s in ('SPY','QQQ','SOXX'))
    if data_policy=='strict':complete=complete and all(len(provider.bars.get(s,{}))==provider.duration for s in symbols)
    for a in accounts:a.research_data_complete=complete
    weights=np.asarray([b.genome.weights for b in bots]);iw=np.asarray([b.genome.indicator_weights for b in bots])
    cw=np.asarray([b.genome.context_weights for b in bots]);cutoff=closing-config.cutoff_minutes*MINUTE
    for ti,t in enumerate(provider.times):
        if resource_guard:resource_guard()
        base=provider.matrix[ti]@weights[:,:len(POLICY_BASE)].T
        if provider.market[t]['valid']:base+=np.asarray(provider.market[t]['policyValues'])@cw.T
        indicators=[cache.get(s,t) for s in provider.symbols]
        vals=np.asarray([f['policyValues'] if f and f['ready'] else (0.,)*len(POLICY_INDICATORS) for f in indicators])
        extra=vals@iw.T if config.indicator_policy_enabled else np.zeros_like(base)
        for bi,(bot,a) in enumerate(zip(bots,accounts)):
            filled=False
            # Revalue existing exposure at the execution observation before sizing.
            for held,lot in a.lots.items():
                observed=provider.bars.get(held,{}).get(t)
                if observed:lot.mark(observed.c,t)
                else:a.research_data_complete=False;a.observations_stale=True
            for pending in list(a.pending):
                sig=datetime.fromisoformat(pending['signalTimestamp']);s=pending['symbol'];bar=provider.bars.get(s,{}).get(t)
                if t<=sig:continue
                if pending['side']=='BUY':
                    a.pending.remove(pending)
                    si=provider.symbols.index(s);f=indicators[si]
                    if t!=sig+MINUTE or bar is None or t>=cutoff or not provider.valid[ti,si] or not f or not f['ready']:
                        counts['MISSING_OR_LATE_ENTRY_OBSERVATION']+=1;continue
                    budget,reason=allocation(a,s,bar.c,f,provider,t,bot.genome)
                    if not budget:counts[reason]+=1;continue
                    pending.update(entryRegime=provider.market[t]['regime'],entryContextTimestamp=t.isoformat(),
                        entryVolatility15m=provider.raw[s,t]['volatility_15m'],entryVwapDistance=provider.raw[s,t]['distance_from_vwap'])
                    a.buy(s,bar.c,t,budget,pending,bot.genome);filled=True;counts['ENTRY']+=1
                elif bar:
                    a.pending.remove(pending);a.sell(s,bar.c,t,pending);filled=True;counts['EXIT']+=1
                else:counts['STALE_EXIT_OBSERVATION']+=1
            for s,lot in list(a.lots.items()):
                bar=provider.bars.get(s,{}).get(t)
                if bar is None:
                    a.research_data_complete=False;a.cancellations['HELD_SYMBOL_OBSERVATION_GAP']=a.cancellations.get('HELD_SYMBOL_OBSERVATION_GAP',0)+1
                    if len(a.data_gap_samples)<5:a.data_gap_samples.append(dict(symbol=s,missingObservableTime=t.isoformat()))
                    if bi==0:latest[s]=analysis_record(bot,t,s,'BLOCKED','HELD_SYMBOL_OBSERVATION_GAP',None,None,{},a,0.,config)
                    continue
                lot.mark(bar.c,t)
                if any(p['symbol']==s for p in a.pending):continue
                p=lot.position;si=provider.symbols.index(s);f=indicators[si]
                score=float(base[si,bi]+extra[si,bi]+np.dot(a.state_features(t,provider.duration),weights[bi,len(POLICY_BASE):]))
                held=(t-datetime.fromisoformat(p['entryTimestamp'])).total_seconds()/60
                reason=('STOP_LOSS' if bar.c<=p['stopPrice'] else 'TAKE_PROFIT' if bar.c>=p['targetPrice'] else
                    'MAX_HOLD' if held>=bot.genome.max_hold_minutes else
                    'SELL_THRESHOLD' if provider.valid[ti,si] and f and f['ready'] and not f['conflict'] and score<=bot.genome.sell_threshold else
                    'SESSION_EXIT' if t>=cutoff-MINUTE else None)
                if bi==0:
                    contribution=dict(zip(POLICY_INDICATORS,(vals[si]*iw[bi] if config.indicator_policy_enabled else np.zeros(len(POLICY_INDICATORS))).tolist()))
                    latest[s]=analysis_record(bot,t,s,'SELL' if reason else 'HOLD',reason or ('INDICATOR_CONFLICT' if f and f.get('conflict') else 'POSITION_MAINTAINED'),score,f,contribution,a,0.,config)
                if not reason:counts['HOLD']+=1
                if reason:
                    p['exitIndicators']=f;p['exitDecisionScore']=score
                    a.pending.append(dict(side='SELL',symbol=s,signalTimestamp=t.isoformat(),reason=reason))
                    counts['SELL']+=1
            a.mark(t)
            if filled or a.pending or t>=cutoff-MINUTE:continue
            if config.max_trades_per_session and len(a.trades)+len(a.lots)>=config.max_trades_per_session:continue
            if a.last_exit and (t-a.last_exit).total_seconds()<config.entry_cooldown_minutes*60:continue
            scores=base[:,bi]+extra[:,bi]+np.dot(a.state_features(t,provider.duration),weights[bi,len(POLICY_BASE):])
            ranked=sorted(range(len(provider.symbols)),key=lambda i:(-scores[i],provider.symbols[i]))
            for si in ranked:
                s=provider.symbols[si];f=indicators[si]
                if s in a.lots:continue
                decision,reason=signal_decision(float(scores[si]),0.,bot.genome.buy_threshold,f,provider.valid[ti,si])
                budget=0.
                if decision=='BUY':
                    budget,reason=allocation(a,s,provider.bars[s][t].c,f,provider,t,bot.genome)
                    if not budget:decision='BLOCKED'
                counts[decision+':'+reason]+=1
                if bi==0:
                    contribution=dict(zip(POLICY_INDICATORS,(vals[si]*iw[bi] if config.indicator_policy_enabled else np.zeros(len(POLICY_INDICATORS))).tolist()))
                    detail=analysis_record(bot,t,s,decision,reason,float(scores[si]),f,contribution,a,budget,config)
                    latest[s]=detail
                    if decision=='BUY' and len(samples)<30:samples.append(detail)
                if decision=='BUY':
                    bar=provider.bars[s][t]
                    a.pending.append(dict(side='BUY',symbol=s,signalTimestamp=t.isoformat(),score=float(scores[si]),
                        sourceCandle=dict(start=bar.start.isoformat(),end=bar.end.isoformat(),open=bar.o,high=bar.h,low=bar.l,close=bar.c,volume=bar.v),
                        features=dict(provider.raw[s,t]),benchmark=benchmark_for_symbol(s)[0],signalRegime=provider.market[t]['regime'],
                        entryQualityMinutes=config.entry_quality_minutes,indicators=f,indicatorContributions=dict(zip(POLICY_INDICATORS,(vals[si]*iw[bi] if config.indicator_policy_enabled else np.zeros(len(POLICY_INDICATORS))).tolist()))))
                    # At most one pending buy, re-evaluate all budgets at next observation.
                    break
    for a in accounts:a.trades.sort(key=lambda x:(x['exitTimestamp'],x['symbol']))
    return accounts,dict(provider.diagnostics,version='V082_INDICATOR_1',researchOnly=True,dataPolicy=data_policy,
        executionModel='NEXT_COMPLETED_MINUTE_CLOSE_PROXY',indicatorCalculations=cache.calculations,
        decisions=dict(counts),decisionSamples=samples,latestAnalysis=latest,
        observationExtrema='Completed closes only; no high/low fills; missing held observations invalidate full evaluation',
        portfolioSemantics='Allowed count is a candidate-specific conservative cap; one new candidate reserved at a time, rechecked at fill',
        finalOosGate='CLOSED')
