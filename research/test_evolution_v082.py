"""Deterministic tiny synthetic V082 tests. No real market data or broker access."""
from dataclasses import replace
from datetime import datetime
import copy
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from research.evolution.config import Config
from research.evolution.indicators import compute_indicators,POLICY_INDICATORS
from research.evolution.indicator_engine import Portfolio,allocation,correlation,signal_decision
from research.evolution.features import MinuteFeatureProvider
from research.evolution.genome import Genome,mutate,crossover
from research.evolution.population import initial_population,reproduce,daily_evaluation
from research.evolution.engine import evaluate_day
from research.evolution.run_v082 import main,CONFIG
from research.evolution.calibrate_v082 import verify_capacity
from research.backtest_v4_strategies import MINUTE,exchange_sessions
from research.test_evolution_v0 import fixture,signal
from research.test_evolution_v081 import evaluations
from research.test_evolution_available_data import prepare


def config():return Config(**json.loads(CONFIG.read_text()))


def buy_genome(c):
    from research.evolution.features import POLICY_FEATURES
    w=[0.]*len(POLICY_FEATURES);w[POLICY_FEATURES.index('available_cash')]=2.
    return Genome(tuple(w),.25,-2.,.01,.04,20,.3,
                  indicator_weights=(0.,)*len(POLICY_INDICATORS),context_weights=(0.,)*6).validate(c)


class IndicatorsTests(unittest.TestCase):
    def test_sma_ema_vwap_bollinger_rsi_atr_adx(self):
        rows,o,c=fixture(day='2023-06-01');bars=list(rows['AAPL'].values());d=compute_indicators(bars)
        last=bars[-1];f=d[last.end];prices=np.array([b.c for b in bars])
        self.assertTrue(f['ready']);self.assertEqual(len(f['policyValues']),15)
        self.assertAlmostEqual(f['sma200'],prices[-200:].mean())
        self.assertAlmostEqual(f['bollingerUpper'],prices[-20:].mean()+2*prices[-20:].std())
        self.assertAlmostEqual(f['vwap'],sum((b.h+b.l+b.c)/3*b.v for b in bars)/sum(b.v for b in bars))
        ema=bars[0].c
        for b in bars[1:]:ema+=.2*(b.c-ema)
        self.assertAlmostEqual(f['ema9'],ema)
        self.assertTrue(0<=f['rsi14']<=100);self.assertTrue(0<=f['stochRsi14']<=1);self.assertTrue(0<=f['adx14']<=100)
        self.assertGreater(f['atr14'],0);self.assertIsNone(f['spread'])
        self.assertFalse(d[bars[198].end]['ready'])
    def test_wilder_reference_monotonic_path(self):
        rows,o,c=fixture(day='2023-06-01');bars=[]
        for i,b in enumerate(rows['AAPL'].values()):
            price=100+i;bars.append(replace(b,o=price,c=price,h=price+.5,l=price-.5))
        f=compute_indicators(bars)[bars[-1].end]
        self.assertEqual(f['rsi14'],100);self.assertEqual(f['adx14'],100);self.assertAlmostEqual(f['atr14'],1.5,places=6)
    def test_causal_future_mutation_and_previous_volume(self):
        rows,o,c=fixture(day='2023-06-01');bars=list(rows['AAPL'].values());a=compute_indicators(bars)
        mutated=[replace(b,o=b.o*2,h=b.h*2,l=b.l*2,c=b.c*2,v=b.v*7) if i>=250 else b for i,b in enumerate(bars)]
        b=compute_indicators(mutated)
        for bar in bars[:250]:self.assertEqual(a[bar.end],b[bar.end])
        self.assertEqual(a[bars[230].end]['volumeMean20'],np.mean([x.v for x in bars[210:230]]))
    def test_gap_sticky_no_future_fill_naive_and_oos_block(self):
        rows,o,c=fixture(day='2023-06-01');bars=list(rows['AAPL'].values());d=compute_indicators(bars[:210]+bars[211:])
        self.assertNotIn(bars[211].end,d)
        with self.assertRaisesRegex(ValueError,'TIMEZONE'):compute_indicators([replace(bars[0],end=bars[0].end.replace(tzinfo=None))])
        with self.assertRaisesRegex(ValueError,'FINAL_OOS'):compute_indicators([replace(bars[0],end=bars[0].end.replace(year=2026))])
    def test_signal_conflict_is_wait_missing_is_blocked(self):
        self.assertEqual(signal_decision(9,0,1,{'ready':True,'conflict':True},True)[0],'WAIT')
        self.assertEqual(signal_decision(9,0,1,None,False)[0],'BLOCKED')


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.cfg=config();self.rows,self.o,self.c=fixture(day='2023-06-01');self.t=self.o+220*MINUTE
        self.provider=MinuteFeatureProvider(self.cfg).prepare(self.rows,self.o,self.c,['AAPL','TQQQ'])
        self.f=compute_indicators(self.rows['AAPL'].values())[self.t];self.g=buy_genome(self.cfg)
    def test_cash_fractional_cost_and_isolation(self):
        p=Portfolio(self.cfg);q=Portfolio(self.cfg);s=signal(self.t);s['entryQualityMinutes']=5
        p.buy('AAPL',180,self.t,30,s,self.g)
        self.assertAlmostEqual(p.cash,70);self.assertLess(p.lots['AAPL'].position['quantity'],1)
        self.assertGreater(p.transaction_cost,0);self.assertEqual(q.cash,100);self.assertIsNone(q.position)
        p.sell('AAPL',181,self.t+MINUTE,dict(reason='TEST',signalTimestamp=self.t.isoformat()))
        self.assertAlmostEqual(p.cash,100+p.realized_pnl);self.assertEqual(len(p.trades),1)
    def test_risk_budget_cash_and_daily_loss(self):
        p=Portfolio(self.cfg);budget,reason=allocation(p,'AAPL',180,self.f,self.provider,self.t,self.g)
        self.assertGreater(budget,0);self.assertLessEqual(budget,p.cash);self.assertLessEqual(budget,100*.02/.0106)
        p.max_drawdown=.04;self.assertEqual(allocation(p,'AAPL',180,self.f,self.provider,self.t,self.g)[1],'DAILY_LOSS_LIMIT')
    def test_high_corr_and_leveraged_underlying_block(self):
        p=Portfolio(self.cfg);s=signal(self.t);s['entryQualityMinutes']=5;p.buy('AAPL',180,self.t,25,s,self.g)
        with patch('research.evolution.indicator_engine.correlation',return_value=.95):
            self.assertEqual(allocation(p,'NVDA',60,self.f,self.provider,self.t,self.g)[1],'CORRELATED_EXPOSURE')
        p=Portfolio(self.cfg);p.buy('TQQQ',60,self.t,25,s,self.g)
        self.provider.market[self.t]['regime']='BULL'
        self.assertEqual(allocation(p,'SQQQ',20,self.f,self.provider,self.t,self.g)[1],'BULL_INVERSE_RISK')
        self.provider.market[self.t]['regime']='SIDEWAYS'
        self.assertEqual(allocation(p,'SQQQ',20,self.f,self.provider,self.t,self.g)[1],'SHARED_LEVERAGED_UNDERLYING')
    def test_cap_reduction_no_forced_sale_and_zero_allowance(self):
        p=Portfolio(replace(self.cfg,portfolio_max_positions=1));s=signal(self.t);s['entryQualityMinutes']=5;p.buy('AAPL',180,self.t,20,s,self.g)
        self.assertEqual(allocation(p,'TQQQ',60,self.f,self.provider,self.t,self.g)[1],'ADMIN_POSITION_CAP');self.assertIn('AAPL',p.lots)
        p=Portfolio(self.cfg);self.provider.market[self.t]['regime']='BEAR'
        self.assertEqual(allocation(p,'AAPL',180,self.f,self.provider,self.t,self.g)[0],0)
    def test_correlation_uses_past_only_missing_blocks(self):
        before=correlation(self.provider,'AAPL','TQQQ',self.t)
        for t in list(self.provider.bars['TQQQ']):
            if t>self.t:del self.provider.bars['TQQQ'][t]
        self.assertEqual(before,correlation(self.provider,'AAPL','TQQQ',self.t))
        del self.provider.bars['TQQQ'][self.t-MINUTE];self.provider._correlation_cache.clear();self.assertIsNone(correlation(self.provider,'AAPL','TQQQ',self.t))
    def test_three_independent_lots_cash_and_exposure_cap(self):
        p=Portfolio(self.cfg);s=signal(self.t);s['entryQualityMinutes']=5
        with patch('research.evolution.indicator_engine.correlation',return_value=.1):
            for symbol in ('AAPL','MSFT','META'):
                budget,reason=allocation(p,symbol,180,self.f,self.provider,self.t,self.g)
                self.assertGreater(budget,0);p.buy(symbol,180,self.t,budget,s,self.g)
        self.assertEqual(len(p.lots),3);self.assertGreaterEqual(p.cash,0)
        self.assertEqual(allocation(p,'TQQQ',60,self.f,self.provider,self.t,self.g)[1],'ADMIN_POSITION_CAP')


class EvolutionTests(unittest.TestCase):
    def test_fifty_breeding_mutation_serialization_legacy_unchanged(self):
        cfg=config();bots=initial_population(cfg,random.Random(7),'2022-01-03')
        self.assertEqual(len(bots),50);self.assertEqual(Genome.from_dict(bots[0].genome.to_dict()),bots[0].genome)
        for profit in (0,1,5,30):
            children,m=reproduce(bots,evaluations(bots,profit),cfg,random.Random(1),1,'2022-01-10')
            self.assertEqual(len(children),50);self.assertTrue(all(len(b.genome.indicator_weights)==15 for b in children))
            self.assertEqual(m['parentPoolSize'],profit or 2)
            if not profit:self.assertTrue(all(b.mutation_count>=1 for b in children))
        parent=bots[0].genome;before=parent.digest
        child,n=mutate(parent,random.Random(4),cfg);child.validate(cfg);self.assertEqual(parent.digest,before)
        self.assertEqual(crossover(parent,bots[1].genome,random.Random(1)),crossover(parent,bots[1].genome,random.Random(1)))
        legacy=replace(cfg,indicator_aware=False);self.assertFalse(initial_population(legacy,random.Random(7),'2022-01-03')[0].genome.indicator_weights)
        self.assertNotIn('indicator_aware',legacy.snapshot())
    def test_50_bots_shared_indicators_next_observation_and_no_same_bar(self):
        cfg=config();rows,o,c=fixture(day='2023-06-01');bots=initial_population(cfg,random.Random(2),'2023-06-01')
        for b in bots:b.genome=buy_genome(cfg)
        ac,d=evaluate_day(bots,rows,o,c,['AAPL','TQQQ'],cfg)
        self.assertEqual(len(ac),50);self.assertEqual(d['indicatorCalculations'],5);self.assertLessEqual(len(d['decisionSamples']),30)
        self.assertGreater(sum(len(a.trades) for a in ac),0)
        for a in ac:
            self.assertTrue(daily_evaluation(bots[0],a,'2023-06-01',cfg)['evaluationValid'])
            self.assertGreaterEqual(a.cash,0)
            for tr in a.trades:
                self.assertGreater(tr['entryTimestamp'],tr['signalTimestamp']);self.assertEqual(tr['entryLatencySeconds'],60)
                self.assertIn('indicators',tr);self.assertEqual(tr['executionModel'],'NEXT_COMPLETED_MINUTE_CLOSE_PROXY')
    def test_missing_reference_prevents_valid_evaluation(self):
        cfg=config();rows,o,c=fixture(day='2023-06-01');del rows['QQQ'][o+220*MINUTE]
        bots=initial_population(cfg,random.Random(1),'2023-06-01')[:1]
        ac,d=evaluate_day(bots,rows,o,c,['AAPL'],cfg,data_policy='causal_available')
        self.assertFalse(daily_evaluation(bots[0],ac[0],'2023-06-01',cfg)['evaluationValid'])
    def test_future_reference_gap_cannot_change_earlier_trades(self):
        cfg=config();rows,o,c=fixture(day='2023-06-01');other=copy.deepcopy(rows)
        del other['QQQ'][o+300*MINUTE]
        bots=initial_population(cfg,random.Random(1),'2023-06-01')[:1];bots[0].genome=buy_genome(cfg)
        a,_=evaluate_day(bots,rows,o,c,['AAPL'],cfg,data_policy='causal_available')
        b,_=evaluate_day(bots,other,o,c,['AAPL'],cfg,data_policy='causal_available')
        cutoff=(o+300*MINUTE).isoformat()
        earlier=lambda x:[tr for tr in x[0].trades if tr['exitTimestamp']<cutoff]
        self.assertTrue(earlier(a));self.assertEqual(earlier(a),earlier(b))
        self.assertFalse(b[0].research_data_complete)
    def test_fresh_v082_certificate_unique_runs_and_tamper(self):
        import time
        from research.evolution.calibrate_v082 import code_hashes,host_fingerprint
        from research.backtest_v4_strategies import file_sha256
        from research.test_evolution_v081 import measurement
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan=root/'plan.json';plan.write_text('{}');cert=root/'capacity.json'
            d=dict(version='V082_CAPACITY_1',population=50,dataOrigin='synthetic',status='MEASURED_SAFE',host=host_fingerprint(),createdEpoch=time.time(),
                planSha256=file_sha256(plan),configSha256=file_sha256(CONFIG),codeHashes=code_hashes(),seed=42,maxSessionSeconds=900,trials=[])
            for i in range(3):
                run=root/str(i)/'run.json';run.parent.mkdir()
                run.write_text(json.dumps(dict(version='V082_INDICATOR_1',phase='completed',population=50,completedTrainGenerations=1,
                    codeHashes=d['codeHashes'],sourcePlanSha256=d['planSha256'],seed=42,dataOrigin='synthetic',config=config().snapshot(),resourceReport=measurement())))
                d['trials'].append(dict(path=str(run),sha256=file_sha256(run)))
            cert.write_text(json.dumps(d));self.assertEqual(verify_capacity(cert,plan,CONFIG,42)['population'],50)
            d['dataOrigin']='toss';cert.write_text(json.dumps(d))
            with self.assertRaisesRegex(ValueError,'UNSAFE_OR_INVALID'):verify_capacity(cert,plan,CONFIG,42)
            d['dataOrigin']='synthetic'
            original=d['trials'][2]
            d['trials'][2]=d['trials'][1];cert.write_text(json.dumps(d))
            with self.assertRaisesRegex(ValueError,'RUN_CHANGED'):verify_capacity(cert,plan,CONFIG,42)
            d['trials'][2]=original;cert.write_text(json.dumps(d));run.write_text('{}')
            with self.assertRaisesRegex(ValueError,'RUN_CHANGED'):verify_capacity(cert,plan,CONFIG,42)
    def test_no_broker_imports_or_network_in_research_evaluator(self):
        import ast
        for name in ('indicator_engine.py','indicators.py'):
            tree=ast.parse((CONFIG.parent/name).read_text())
            imports=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
            imports += [a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
            self.assertFalse(any(any(x in module for x in ('live_trader','toss','requests','urllib','socket')) for module in imports))
    def test_old_certificate_and_population_raise_before_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'old.json';path.write_text('{"version":"V081_CAPACITY_1"}')
            with self.assertRaisesRegex(ValueError,'V082_CAPACITY'):verify_capacity(path,Path('absent'),CONFIG,42)
        with self.assertRaises(SystemExit):main(['--selection-plan','absent','--population','100','--pilot'])
        with self.assertRaisesRegex(ValueError,'FINAL_OOS'):main(['--selection-plan','absent','--from-date','2026-01-01','--to-date','2026-01-10'])
    def test_synthetic_five_day_generation_artifacts(self):
        import contextlib,io
        from research.evolution.run_v07_available import parser,run
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);days=list(exchange_sessions('2023-06-01','2023-06-07'));plan=prepare(root,days)
            args=parser().parse_args(['--selection-plan',str(plan),'--config',str(CONFIG),'--population','50','--from-date',days[0],'--to-date',days[-1],
                '--results-dir',str(root/'results'),'--data-origin','synthetic'])
            with patch('research.evolution.run_v07_available.resource_preflight',return_value={}),contextlib.redirect_stdout(io.StringIO()):out=run(args)
            info=json.loads((out/'run.json').read_text());self.assertEqual(info['completedTrainGenerations'],1);self.assertEqual(info['version'],'V082_INDICATOR_1')
            self.assertEqual(info['population'],50);self.assertTrue((out/'window_0000.json').exists());self.assertEqual(info['finalOosGate'],'CLOSED')
            from research.evolution.compare_v082 import summarize,compare
            baseline=summarize(out);candidate=copy.deepcopy(baseline);candidate['config']['indicator_policy_enabled']=False
            comparison=compare(baseline,candidate)
            self.assertTrue(comparison['pairedMarketConditions']);self.assertTrue(comparison['indicatorScoreEffectIsolated'])
            candidate['seed']=99;self.assertFalse(compare(baseline,candidate)['pairedMarketConditions'])

if __name__=='__main__':unittest.main()
