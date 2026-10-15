"""Tiny deterministic V07-R1 fixtures: no broker, EC2 access, or historical dataset."""
import contextlib
import copy
import io
import json
import random
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
from research.backtest_v4_strategies import MINUTE, exchange_sessions
from research.evolution.account import Account, ExecutionCostModel
from research.evolution.config import Config
from research.evolution.data_access import check_dates, inspect_window, scheduled_days
from research.evolution.engine import evaluate_day
from research.evolution.features import MinuteFeatureProvider
from research.evolution.genome import Genome, random_genome, mutate, crossover
from research.evolution.population import Bot, initial_population, daily_evaluation, aggregate_window_evaluations, reproduce
from research.evolution.regime_timing import CONTEXT_FEATURES, market_context, trade_quality
from research.evolution.run_evolution_v0 import main, parser, run
from research.evolution.evaluate_population_v03 import run as validate_run, parser as validate_parser
from research.test_evolution_v0 import fixture, buy_genome, signal, write_fixtures

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'research/evolution/v07_5d_regime_timing_config.json'


def config(**kw):
    return replace(Config(**json.loads(CONFIG.read_text())),**kw)


def reference(direction=1):
    return dict(return_5m=.002*direction,return_15m=.004*direction,return_30m=.006*direction,
                volatility_30m=.0001,distance_from_vwap=.003*direction,vwap_slope_5m=.001*direction,
                volume_ratio=1.2,efficiency_30m=.8)


def batch(bots,cfg,day,pnl=1., trades=1):
    rows=[]
    for b in bots:
        a=Account(100,ExecutionCostModel());a.cash+=pnl;a.research_timing=True
        a.research_marks=[(None,100.),(None,100.+pnl)]
        e=daily_evaluation(b,a,day,cfg,mark_death=False)
        e['trades']=trades;e['timing']['trades']=trades
        rows.append(e)
    return rows


class RegimeTests(unittest.TestCase):
    def test_bull_bear_sideways_and_bear_rebound(self):
        c=config()
        self.assertEqual(market_context(reference(),reference(),c)['regime'],'BULL')
        self.assertEqual(market_context(reference(-1),reference(-1),c)['regime'],'BEAR')
        self.assertEqual(market_context(reference(),reference(-1),c)['regime'],'SIDEWAYS')
        rebound=reference(-1);rebound['return_5m']=.002
        self.assertEqual(market_context(rebound,rebound,c)['regime'],'SIDEWAYS')
        noise=reference();noise['volatility_30m']=.1
        self.assertEqual(market_context(noise,noise,c)['regime'],'SIDEWAYS')
        self.assertFalse(market_context(None,reference(),c)['valid'])

    def test_future_mutation_does_not_change_regime_or_policy_input(self):
        rows,o,c=fixture(count=100)
        changed=copy.deepcopy(rows)
        for symbol,minutes in changed.items():
            for t,b in list(minutes.items()):
                if t>=o+60*MINUTE:minutes[t]=replace(b,c=b.c*2,h=b.h*2,v=b.v*10)
        a=MinuteFeatureProvider(config()).prepare(rows,o,c,['TQQQ'])
        b=MinuteFeatureProvider(config()).prepare(changed,o,c,['TQQQ'])
        np.testing.assert_array_equal(a.matrix[:60],b.matrix[:60])
        self.assertEqual({t:x for t,x in a.market.items() if t<=o+60*MINUTE},
                         {t:x for t,x in b.market.items() if t<=o+60*MINUTE})

    def test_missing_exact_reference_never_future_filled(self):
        rows,o,c=fixture(count=90);del rows['QQQ'][o+40*MINUTE]
        p=MinuteFeatureProvider(config()).prepare(rows,o,c,['TQQQ'])
        self.assertFalse(p.market[o+41*MINUTE]['valid'])
        self.assertFalse(p.valid[40:,0].any())


class CompatibilityTests(unittest.TestCase):
    def test_legacy_config_and_genome_json_unchanged(self):
        legacy=Config(**json.loads((ROOT/'research/evolution/v07_holdout_config.json').read_text()))
        self.assertEqual((legacy.evaluation_window_sessions,legacy.min_profitable_sessions_for_parent),(7,4))
        g=random_genome(random.Random(42),legacy)
        old=g.to_dict()
        self.assertNotIn('context_weights',old)
        self.assertEqual(Genome.from_dict(old).to_dict(),old)
        self.assertEqual(Genome.from_dict(old).digest,g.digest)
        Genome.from_dict(old).validate(config())

    def test_ab_initial_population_legacy_genes_match_with_same_seed(self):
        c=config();old=replace(c,fitness_mode='v05_structural')
        baseline=initial_population(old,random.Random(42),'2023-06-01')
        candidate=initial_population(c,random.Random(42),'2023-06-01')
        for a,b in zip(baseline,candidate):
            fields=b.genome.to_dict();fields.pop('context_weights')
            self.assertEqual(a.genome.to_dict(),fields)

    def test_context_genes_mutate_without_parent_change_and_crossover_bounded(self):
        c=config(mutation_probability=1,mutation_strength=2)
        parent=random_genome(random.Random(1),c);before=parent.to_dict()
        self.assertEqual(len(parent.context_weights),len(CONTEXT_FEATURES))
        self.assertEqual(Genome.from_dict(json.loads(json.dumps(before))),parent)
        child,_=mutate(parent,random.Random(2),c)
        child.validate(c);self.assertEqual(parent.to_dict(),before)
        crossed=crossover(parent,child,random.Random(4)).validate(c)
        self.assertEqual(crossed,crossover(parent,child,random.Random(4)))
        legacy=buy_genome(c)
        crossover(parent,legacy,random.Random(3)).validate(c)

    def test_zero_context_weights_preserve_trade_execution(self):
        rows,o,close=fixture(); c=config()
        bot=Bot('b',buy_genome(c),0,[],'2024-06-03')
        old=replace(c,fitness_mode='v05_structural')
        a,_=evaluate_day([bot],rows,o,close,['TQQQ'],old)
        b,_=evaluate_day([bot],rows,o,close,['TQQQ'],c)
        fields=('entryTimestamp','exitTimestamp','entryFillPrice','exitFillPrice','pnlUsd','costUsd')
        self.assertTrue(a[0].trades)
        self.assertEqual([[t[k] for k in fields] for t in a[0].trades],[[t[k] for k in fields] for t in b[0].trades])
        for t in b[0].trades:
            self.assertGreater(t['entryTimestamp'],t['signalTimestamp'])
            self.assertGreater(t['exitTimestamp'],t['exitSignalTimestamp'])
            self.assertEqual(t['entryLatencySeconds'],60)
            self.assertIn(t['entryRegime'],('BULL','BEAR','SIDEWAYS'))


class FitnessTests(unittest.TestCase):
    def test_five_complete_days_three_positive_and_risk_limit(self):
        c=config();bots=initial_population(c,random.Random(1),'2023-06-01')
        batches=[batch(bots,c,f'2023-06-0{i+1}',p) for i,p in enumerate((1,1,1,-.1,-.1))]
        early=aggregate_window_evaluations(bots,batches[:4],c,complete=False)
        self.assertFalse(any(e['reproductionEligible'] for e in early))
        evaluated=aggregate_window_evaluations(bots,batches,c,complete=True)
        self.assertTrue(all(e['reproductionEligible'] for e in evaluated))
        self.assertTrue(all(e['profitableSessions']==3 for e in evaluated))
        self.assertEqual(evaluated[0]['fitnessComponents']['costPenalty'],0)
        evaluated[0]['regimePerformance']['BULL']['sampleAdequate']=False
        self.assertEqual(evaluated[0]['regimeEvidence'],'INSUFFICIENT_REGIME_SAMPLES')
        excessive=[batch(bots,c,'2023-06-01',p) for p in (10,-9,1,1,1)]
        result=aggregate_window_evaluations(bots,excessive,c,complete=True)
        self.assertTrue(all(e['windowMaxDrawdown']>.05 and not e['reproductionEligible'] for e in result))

    def test_inactive_losing_and_invalid_never_become_parents(self):
        c=config();bots=initial_population(c,random.Random(1),'2023-06-01')
        for pnl in (0,-1):
            evaluated=aggregate_window_evaluations(bots,[batch(bots,c,'2023-06-01',pnl,trades=0)]*5,c,complete=True)
            self.assertFalse(any(e['reproductionEligible'] for e in evaluated))
            children,selection=reproduce(bots,evaluated,c,random.Random(1),1,'2023-06-07')
            self.assertEqual(len(children),20);self.assertTrue(selection['randomRecovery'])
        bad=[batch(bots,c,'2023-06-01') for _ in range(5)];bad[1][0]['evaluationValid']=False
        self.assertFalse(aggregate_window_evaluations(bots,bad,c,complete=True)[0]['evaluationValid'])

    def test_no_reward_for_trading_more_no_double_cost_penalty(self):
        c=config();bots=initial_population(c,random.Random(1),'2023-06-01')
        few=aggregate_window_evaluations(bots,[batch(bots,c,'2023-06-01',trades=1)]*5,c,complete=True)[0]
        many=aggregate_window_evaluations(bots,[batch(bots,c,'2023-06-01',trades=10)]*5,c,complete=True)[0]
        self.assertGreater(few['fitness'],many['fitness'])
        with self.assertRaisesRegex(ValueError,'duplicate'):replace(c,transaction_cost_penalty=1)

    def test_tiny_positive_days_do_not_meet_parent_consistency_gate(self):
        c=config();bots=initial_population(c,random.Random(1),'2023-06-01')
        rows=[batch(bots,c,'2023-06-01',pnl=.0000005) for _ in range(5)]
        evaluated=aggregate_window_evaluations(bots,rows,c,complete=True)
        self.assertTrue(all(e['profitableSessions']==0 and not e['reproductionEligible'] for e in evaluated))

    def test_net_costs_and_timing_only_after_exit(self):
        c=config();a=Account(100,ExecutionCostModel(),research_timing=True)
        t=datetime.fromisoformat('2023-06-01T10:00:00-04:00')
        sig=signal(t-MINUTE)|dict(entryQualityMinutes=5,entryRegime='BEAR',entryVolatility15m=.0001)
        a.buy('TQQQ',100,t,1,sig,buy_genome(c))
        self.assertNotIn('timingQuality',a.position)
        a.mark(99,t+MINUTE);a.mark(103,t+2*MINUTE)
        trade=a.sell(101,t+3*MINUTE,'TEST',(t+2*MINUTE).isoformat())
        q=trade_quality(trade,c)
        self.assertGreater(q['recoveredAdverseExcess'],0)
        self.assertGreater(q['profitableGivebackExcess'],0)
        self.assertAlmostEqual(trade['grossPnlUsd']-trade['costUsd'],trade['pnlUsd'])
        self.assertAlmostEqual(a.cash-100,trade['pnlUsd'])
        losing=dict(trade,pnlUsd=-1,returnPct=-1)
        self.assertEqual(trade_quality(losing,c)['recoveredAdverseExcess'],0)
        self.assertEqual(trade_quality(losing,c)['profitableGivebackExcess'],0)
        b=Bot('b',buy_genome(c),0,[],'2023-06-01')
        e=daily_evaluation(b,a,'2023-06-01',c)
        self.assertEqual(e['regimePerformance']['BEAR']['trades'],1)
        self.assertIn('timingQuality',a.trades[0])


class DataAndPipelineTests(unittest.TestCase):
    def test_oos_block_before_data_access_in_both_clis(self):
        for first,last in (('2026-01-01','2026-01-02'),('2025-12-31','2026-01-01')):
            with self.assertRaisesRegex(ValueError,'FINAL_OOS'):check_dates(first,last)
        args=parser().parse_args(['--config',str(CONFIG),'--from-date','2026-01-02','--to-date','2026-01-05'])
        with patch('research.evolution.run_evolution_v0.prepare_inputs',side_effect=AssertionError('data touched')):
            with self.assertRaisesRegex(ValueError,'FINAL_OOS'):run(args)
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);c=config();b=initial_population(c,random.Random(1),'2023-01-03')
            (d/'run.json').write_text(json.dumps(dict(config=c.snapshot(),symbols=['TQQQ'])))
            (d/'final_population.json').write_text(json.dumps([x.record() for x in b]))
            args=validate_parser().parse_args(['--source-run',str(d),'--from-date','2026-01-02','--to-date','2026-01-05'])
            with patch('research.evolution.evaluate_population_v03.prepare_inputs',side_effect=AssertionError('data touched')):
                with self.assertRaisesRegex(ValueError,'FINAL_OOS'):validate_run(args)

    def test_end_labels_missing_day_no_fill_and_future_prices_not_decoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_fixtures(root,['2024-06-03','2024-06-05'])
            p=root/'TQQQ.csv'
            with p.open('a') as f:f.write('2026-01-02T10:00:00-05:00,TQQQ,NEVER_PARSE,invalid,invalid,invalid,invalid\n')
            meta=inspect_window(p,'TQQQ','2024-06-03','2024-06-05')
            self.assertEqual(meta['rows'],780)
            days=list(scheduled_days({'TQQQ':p},{'symbols':{'TQQQ':meta}},exchange_sessions('2024-06-03','2024-06-05')))
            self.assertEqual(days[1],('2024-06-04',{}))
            b=next(iter(days[0][1]['TQQQ'].values()))
            self.assertEqual(b.start.hour,9);self.assertEqual(b.end.minute,31)
            with p.open('a') as f:f.write('2024-06-03T09:31:00-04:00,TQQQ,1,1,1,1,1\n')
            with self.assertRaisesRegex(ValueError,'out-of-order'):inspect_window(p,'TQQQ','2024-06-03','2024-06-05')

    def test_naive_timestamp_rejected_without_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'TQQQ.csv';p.write_text('timestamp,open,high,low,close,volume\n2024-06-03T09:31:00,1,1,1,1,1\n')
            with self.assertRaises(ValueError):inspect_window(p,'TQQQ','2024-06-03','2024-06-03')
            self.assertEqual(inspect_window(p,'TQQQ','2024-06-03','2024-06-03','America/New_York')['rows'],1)

    def test_reproducible_ten_sessions_two_generations_and_old_genome_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';dates=list(exchange_sessions('2023-06-01','2023-06-14'))
            self.assertEqual(len(dates),10);write_fixtures(data,dates)
            c=config();cfg=root/'cfg.json';cfg.write_text(json.dumps(c.snapshot()))
            argv=['--data-dir',str(data),'--results-dir',str(root/'results'),'--symbols','TQQQ,AAPL',
                  '--from-date',dates[0],'--to-date',dates[-1],'--population','20','--config',str(cfg),
                  '--provisional','--data-origin','synthetic','--seed','42']
            before={p.name:p.read_bytes() for p in data.iterdir()}
            with contextlib.redirect_stdout(io.StringIO()):a=main(argv);b=main(argv)
            ai=json.loads((a/'run.json').read_text());self.assertEqual(ai['completedTrainGenerations'],2)
            self.assertTrue(ai['resultsValid']);self.assertEqual(ai['finalOosGate'],'CLOSED')
            generations=[json.loads(x) for x in (a/'generations.jsonl').read_text().splitlines()]
            self.assertEqual([i+1 for i,g in enumerate(generations) if g['windowComplete']],[5,10])
            self.assertEqual([g['generation'] for g in generations],[0]*5+[1]*5)
            self.assertTrue(all('researchDiagnostics' in g for g in generations))
            for f in ('final_population.json','generations.jsonl','lineage.jsonl'):
                self.assertEqual((a/f).read_bytes(),(b/f).read_bytes())
            self.assertEqual(before,{p.name:p.read_bytes() for p in data.iterdir()})
            from research.evolution.compare_v07_experiments import compare
            self.assertTrue(compare([a,b])['comparable'])
            e=json.loads((a/'generation_0000_session_05/window_evaluations.json').read_text())
            self.assertTrue(all('fitnessComponents' in r and 'parentSelectionReason' in r for r in e))

    def test_read_only_audit_reports_known_missing_2024_days(self):
        from research.evolution.audit_v07_data import audit,KNOWN_DATES
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_fixtures(root,['2024-07-25','2024-08-05'])
            before={p.name:p.read_bytes() for p in root.iterdir()}
            result=audit(root,['TQQQ','AAPL'],'2024-07-25','2024-08-05')
            self.assertFalse(result['mechanicalPass'])
            self.assertEqual(set(result['known2024RepairCheck']['AAPL']),set(KNOWN_DATES))
            self.assertTrue(all(v['observedMinutes']==0 for v in result['known2024RepairCheck']['TQQQ'].values()))
            self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir()})
            self.assertFalse(result['priceAdjustmentConfirmed'])

    def test_disk_and_research_mount_fail_closed(self):
        from research.evolution.audit_v07_data import resource_preflight
        from collections import namedtuple
        usage=namedtuple('Usage','total used free')
        with patch('research.evolution.audit_v07_data.shutil.disk_usage',return_value=usage(100*1024**3,95*1024**3,5*1024**3)):
            with self.assertRaisesRegex(ValueError,'RESOURCE_GUARD_PAUSED'):resource_preflight('/tmp')
        with patch('research.evolution.audit_v07_data.storage_info',return_value=dict(mount=dict(target='/',source='/dev/root'))):
            with self.assertRaisesRegex(ValueError,'RESEARCH_MOUNT_REQUIRED'):resource_preflight('/tmp',require_research_mount=True)

    def test_future_features_cannot_affect_already_executed_trades(self):
        rows,o,close=fixture();cfg=config()
        b=Bot('b',buy_genome(cfg),0,[],'2023-06-01')
        a,_=evaluate_day([b],rows,o,close,['TQQQ'],cfg)
        changed=copy.deepcopy(rows)
        for symbol,minutes in changed.items():
            for t,bar in list(minutes.items()):
                if t>=o+150*MINUTE:minutes[t]=replace(bar,c=bar.c*2,h=bar.h*2)
        other,_=evaluate_day([b],changed,o,close,['TQQQ'],cfg)
        past=lambda account:[t for t in account.trades if datetime.fromisoformat(t['exitTimestamp'])<=o+150*MINUTE]
        self.assertTrue(past(a[0]));self.assertEqual(past(a[0]),past(other[0]))

    def test_candidate_frozen_validation_does_not_evolve_or_change_source(self):
        from research.evolution.evaluate_population_v03 import main as validation
        from research.evolution.population import load_population_records
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_fixtures(root/'data',['2024-06-03','2024-06-04'])
            c=config();bots=[Bot('b',buy_genome(c),0,[],'2023-01-03')]
            source=root/'source';source.mkdir()
            (source/'run.json').write_text(json.dumps(dict(config=c.snapshot(),symbols=['TQQQ'],runId='train',fitnessMode=c.fitness_mode)))
            (source/'final_population.json').write_text(json.dumps([b.record() for b in bots]))
            original=(source/'final_population.json').read_bytes()
            args=['--source-run',str(source),'--data-dir',str(root/'data'),'--results-dir',str(root/'results'),
                  '--from-date','2024-06-03','--to-date','2024-06-04','--expected-split','validation','--provisional','--data-origin','synthetic']
            with contextlib.redirect_stdout(io.StringIO()):directory=validation(args)
            info=json.loads((directory/'run.json').read_text())
            summary=json.loads((directory/'validation_summary.json').read_text())
            self.assertTrue(info['sourcePopulationVerified']);self.assertTrue(summary['resultsValid'])
            self.assertEqual(info['finalOosGate'],'CLOSED');self.assertIn('regimePerformance',summary['bots'][0])
            self.assertEqual(original,(source/'final_population.json').read_bytes())
            self.assertEqual(bots[0].genome,load_population_records([bots[0].record()],c)[0].genome)

    def test_incomplete_source_invalidates_account_not_profitable_parent(self):
        rows,o,c=fixture();del rows['AAPL'][o+100*MINUTE]
        cfg=config();bots=[Bot('b',buy_genome(cfg),0,[],'2023-06-01')]
        a,_=evaluate_day(bots,rows,o,c,['TQQQ','AAPL'],cfg)
        e=daily_evaluation(bots[0],a[0],'2023-06-01',cfg)
        self.assertFalse(e['evaluationValid']);self.assertEqual(e['failureReason'],'INCOMPLETE_REQUIRED_SESSION')


if __name__=='__main__':unittest.main()
