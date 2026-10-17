"""Synthetic-only selection and paired-window regressions; no market API calls."""
from dataclasses import replace
import contextlib
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from research.evolution.config import Config
from research.evolution.population import initial_population, reproduce, aggregate_window_evaluations
from research.evolution.run_v07_available import parser, run
from research.evolution.compare_v08_profit_only import compare
from research.test_evolution_available_data import prepare
from research.backtest_v4_strategies import exchange_sessions

ROOT=Path(__file__).resolve().parents[1]
CFG=ROOT/'research/evolution/v08_profit_only_config.json'


def config():return Config(**json.loads(CFG.read_text()))


def records(bots, profitable):
    return [dict(botId=b.bot_id,evaluationValid=True,windowComplete=True,windowSessions=5,validDays=5,
        reproductionEligible=False,windowPnl=1. if i<profitable else 0.,dailyPnl=1. if i<profitable else 0.,
        windowReturn=.01 if i<profitable else 0.,fitness=-.1-i*.001,maxDrawdown=.9,
        profitableSessions=1, parentSelectionReason='CONSISTENCY_OR_RISK_GATE',fitnessComponents={}) for i,b in enumerate(bots)]


class ProfitOnlyTests(unittest.TestCase):
    def setUp(self):
        self.cfg=config();self.bots=initial_population(self.cfg,random.Random(42),'2022-01-03')

    def reproduce(self, rows, seed=43, cfg=None):
        return reproduce(self.bots,rows,cfg or self.cfg,random.Random(seed),1,'2022-01-10')

    def test_single_profit_parent_exactly_100_no_self_crossover(self):
        before=[b.record() for b in self.bots]
        children,m=self.reproduce(records(self.bots,1))
        self.assertEqual(len(children),100);self.assertEqual(m['parentPoolSize'],1)
        self.assertEqual(m['crossoverCount'],0);self.assertFalse(m['randomRecovery'])
        self.assertEqual(m['eliteCount'],1);self.assertEqual(m['maxParentContribution'],1)
        self.assertTrue(all(c.parent_ids==[self.bots[0].bot_id] for c in children))
        self.assertEqual(m['parentSelection'][0]['children'],100)
        self.assertEqual(before,[b.record() for b in self.bots])
        self.assertEqual(children[0].genome.digest,self.bots[0].genome.digest)

    def test_every_profitable_bot_is_candidate_even_bad_fitness_mdd_consistency(self):
        for n in (5,30,100):
            with self.subTest(n=n):
                children,m=self.reproduce(records(self.bots,n))
                ids={b.bot_id for b in self.bots[:n]}
                self.assertEqual(set(m['parentIds']),ids)
                self.assertEqual(len(children),100)
                self.assertTrue(all(set(c.parent_ids)<=ids for c in children))
                self.assertTrue(all(len(c.parent_ids)==len(set(c.parent_ids)) for c in children))
                self.assertGreater(m['crossoverCount'],0)

    def test_only_zero_profitable_causes_recovery(self):
        rows=records(self.bots,0)
        rows[0]['windowPnl']=-1
        rows[1]['windowPnl']=1e-8
        children,m=self.reproduce(rows)
        self.assertTrue(m['randomRecovery']);self.assertEqual(m['parentPoolSize'],0)
        self.assertTrue(all(not c.parent_ids for c in children))
        for key,value in [('evaluationValid',False),('windowComplete',False),('windowSessions',4),('validDays',4)]:
            rows=records(self.bots,1);rows[0][key]=value
            self.assertTrue(self.reproduce(rows)[1]['randomRecovery'])

    def test_deterministic_seed_and_legacy_gate_unchanged(self):
        rows=records(self.bots,5)
        a,ma=self.reproduce(rows);b,mb=self.reproduce(rows)
        self.assertEqual([c.record() for c in a],[c.record() for c in b]);self.assertEqual(ma,mb)
        legacy=replace(self.cfg,selection_policy='legacy')
        self.assertTrue(self.reproduce(rows,cfg=legacy)[1]['randomRecovery'])
        self.assertNotIn('selection_policy',legacy.snapshot())
        with self.assertRaises(ValueError):replace(self.cfg,selection_policy='unknown')

    def test_aggregate_positive_window_not_old_gates(self):
        from research.test_evolution_v07_5d import batch
        batches=[batch(self.bots,self.cfg,f'2023-06-0{i+1}',p) for i,p in enumerate((10,-9,0,0,0))]
        new=aggregate_window_evaluations(self.bots,batches,self.cfg,complete=True)
        old=aggregate_window_evaluations(self.bots,batches,replace(self.cfg,selection_policy='legacy'),complete=True)
        self.assertTrue(all(e['reproductionEligible'] for e in new))
        self.assertFalse(any(e['reproductionEligible'] for e in old))
        self.assertEqual([e['fitness'] for e in new],[e['fitness'] for e in old])
        self.assertEqual([e['windowPnl'] for e in new],[e['windowPnl'] for e in old])
        for complete in (True,False):
            early=aggregate_window_evaluations(self.bots,batches[:4],self.cfg,complete=complete)
            self.assertFalse(any(e['reproductionEligible'] for e in early))

    def test_synthetic_100_bots_ten_sessions_two_generations_and_pair(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp);dates=list(exchange_sessions('2023-06-01','2023-06-14'))
            plan=prepare(root,dates)
            args=parser().parse_args(['--selection-plan',str(plan),'--config',str(CFG),
                '--population','100','--smoke','--results-dir',str(root/'runs'),'--data-origin','synthetic'])
            pressure=dict(cpuPct=1,memoryPct=1,swapPct=0,processRssMb=1)
            with patch('research.evolution.run_v07_available.sample_resources',return_value=pressure),patch('psutil.cpu_percent',return_value=1):
                out=run(args)
            info=json.loads((out/'run.json').read_text())
            self.assertEqual(info['completedTrainGenerations'],2)
            self.assertEqual(info['population'],100);self.assertEqual(info['finalOosGate'],'CLOSED')
            self.assertEqual(info['version'],'V08_PROFIT_ONLY_1')
            self.assertEqual(len(json.loads((out/'final_population.json').read_text())),100)
            for path in out.glob('window_*.json'):
                window=json.loads(path.read_text());self.assertEqual(len(window['dates']),5)
                selection=window['selection'];self.assertEqual(selection['offspringCount'],100)
                ids={e['botId'] for e in window['bots'] if e['windowPnl']>self.cfg.epsilon}
                self.assertEqual(set(selection['parentIds']),ids)
                self.assertEqual(selection['randomRecovery'],not ids)
            # Same-sized legacy control, same synthetic data/seed/dates; no threshold fitting.
            control=root/'control.json';control.write_text(json.dumps(replace(self.cfg,selection_policy='legacy').snapshot()))
            args.config=control;args.smoke=False;args.from_date=dates[0];args.to_date=dates[-1]
            with patch('research.evolution.run_v07_available.sample_resources',return_value=pressure):
                old=run(args)
            comparison=compare(old,out)
            self.assertTrue(comparison['comparable'],comparison['mismatches'])
            self.assertEqual(len(comparison['v07']['windows']),2)

    def test_monitor_is_read_only_and_oos_stays_blocked(self):
        from research.evolution.monitor import status
        from research.evolution.data_access import check_dates
        from research.evolution.run_v08_profit_only import main
        with self.assertRaisesRegex(ValueError,'2026|OOS'):
            check_dates('2026-01-01','2026-01-02')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'run';folder.mkdir()
            raw=dict(phase='completed',version='V08_PROFIT_ONLY_1',selectionPolicy='v08_profit_only',
                population=100,completedSessions=5,requestedSessions=10,reproduction={'parentPoolSize':1,'meanMdd':.02},secret='excluded')
            path=folder/'status.json';path.write_text(json.dumps(raw));before=path.read_bytes()
            (folder/'window_0000.json').write_text(json.dumps({'dates':['2023-06-01']*5,'selection':{'selectionPolicy':'v08_profit_only','meanWindowReturn':.01}}))
            view=status(root)
            self.assertEqual(view['selectionPolicy'],'v08_profit_only')
            self.assertEqual(view['reproduction']['parentPoolSize'],1)
            self.assertEqual(view['reproductionHistory'][0]['meanWindowReturn'],.01)
            self.assertNotIn('secret',view);self.assertEqual(path.read_bytes(),before)

    def test_cpu_pressure_blocks_before_new_run(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp);dates=list(exchange_sessions('2023-06-01','2023-06-14'));plan=prepare(root,dates)
            args=parser().parse_args(['--selection-plan',str(plan),'--config',str(CFG),'--population','100','--smoke','--results-dir',str(root/'out')])
            with patch('psutil.cpu_percent',return_value=99):
                with self.assertRaisesRegex(ValueError,'RESOURCE_PREFLIGHT_BLOCKED'):run(args)
            self.assertFalse((root/'out').exists())


if __name__=='__main__':unittest.main()
