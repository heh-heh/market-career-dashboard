"""V083 independent two-parent reproduction tests; no network or real market data."""
import json
import random
import unittest
from dataclasses import replace
from unittest.mock import patch

from research.evolution.config import Config
from research.evolution.population import initial_population
from research.evolution.v083_top_two import select_top_two,reproduce_top_two
from research.evolution.v083_pacing import PacedMonitor
from research.evolution.run_v083 import CONFIG,parser


def evaluations(bots, values):
    return [dict(
        botId=b.bot_id,windowPnl=float(values[i]),windowReturn=values[i]/100,
        windowMaxDrawdown=.005+i*.0001,fitness=.1-i*.0001,
        windowComplete=True,evaluationValid=True,windowSessions=5,validDays=5,
        trades=2) for i,b in enumerate(bots)]


class TopTwoV083(unittest.TestCase):
    def setUp(self):
        self.cfg=Config(**json.loads(CONFIG.read_text()))
        self.bots=initial_population(self.cfg,random.Random(11),'2023-01-03')

    def test_strict_50_two_genomes_all_new_mutated_and_no_parent_state(self):
        rows=evaluations(self.bots,[3,2]+[-1]*48)
        self.bots[0].lifetime_pnl=100
        before=[b.record() for b in self.bots]
        children,meta=reproduce_top_two(self.bots,rows,self.cfg,random.Random(4),1,'2023-01-10')
        self.assertEqual(meta['parentPoolSize'],2)
        self.assertEqual(meta['parentIds'],[self.bots[0].bot_id,self.bots[1].bot_id])
        self.assertEqual(len(children),50)
        self.assertEqual(meta['eliteCount'],0)
        self.assertFalse(meta['restartRequested'])
        self.assertTrue(all(c.generation==1 and c.parent_ids==meta['parentIds'] for c in children))
        self.assertTrue(all(c.lifetime_days==0 and c.lifetime_equity==100 for c in children))
        self.assertTrue(all(c.mutation_count>=1 for c in children))
        self.assertEqual(before,[b.record() for b in self.bots])
        others=set(b.bot_id for b in self.bots[2:])
        self.assertTrue(others.isdisjoint({p for c in children for p in c.parent_ids}))
        children2,meta2=reproduce_top_two(self.bots,rows,self.cfg,random.Random(4),1,'2023-01-10')
        self.assertEqual([x.record() for x in children],[x.record() for x in children2])
        self.assertEqual(meta,meta2)

    def test_zero_profit_rewinds_and_selects_best_preservation(self):
        rows=evaluations(self.bots,[-5]*50)
        rows[12]['windowPnl']=0
        rows[13]['windowPnl']=-.01
        chosen,profitable=select_top_two(rows,self.cfg.epsilon)
        self.assertFalse(profitable)
        self.assertEqual([r['botId'] for r in chosen],[self.bots[12].bot_id,self.bots[13].bot_id])
        _,meta=reproduce_top_two(self.bots,rows,self.cfg,random.Random(7),1,'2023-01-10')
        self.assertTrue(meta['restartRequested'])
        self.assertEqual(meta['profitableBotCount'],0)
        self.assertEqual(meta['parentIds'],[self.bots[12].bot_id,self.bots[13].bot_id])

    def test_one_profit_still_uses_two_best_and_no_unrelated_bots(self):
        rows=evaluations(self.bots,[-1]*50)
        rows[1]['windowPnl']=.5
        rows[3]['windowPnl']=0
        _,meta=reproduce_top_two(self.bots,rows,self.cfg,random.Random(1),1,'2023-01-10')
        self.assertEqual(meta['profitableBotCount'],1)
        self.assertFalse(meta['restartRequested'])
        self.assertEqual(meta['parentIds'],[self.bots[1].bot_id,self.bots[3].bot_id])

    def test_all_invalid_or_missing_bot_blocks_without_recovery(self):
        rows=evaluations(self.bots,[0]*50)
        rows[0]['evaluationValid']=False
        with self.assertRaisesRegex(ValueError,'V083_REQUIRES_50_VALID'):
            reproduce_top_two(self.bots,rows,self.cfg,random.Random(1),1,'2023-01-10')
        rows=evaluations(self.bots,[0]*50)
        rows[1]['botId']=rows[0]['botId']
        with self.assertRaisesRegex(ValueError,'V083_REQUIRES_50_VALID'):
            reproduce_top_two(self.bots,rows,self.cfg,random.Random(1),1,'2023-01-10')

    def test_pacing_never_disables_resource_guard(self):
        monitor=PacedMonitor(pacing_seconds=.005)
        monitor.failure='RESOURCE_TARGET_EXCEEDED:cpuPct:80>75.0'
        with patch('research.evolution.v083_pacing.time.sleep') as sleeper:
            with self.assertRaisesRegex(ValueError,'RESOURCE_PAUSED'):
                monitor.check()
            sleeper.assert_not_called()

    def test_runner_explicit_opt_in_and_bounded_restarts(self):
        p=parser()
        a=p.parse_args(['--selection-plan','dummy.json'])
        self.assertEqual(a.max_restarts,2)
        self.assertEqual(a.population,50)
        self.assertEqual(a.cpu_pacing_ms,25)
        self.assertEqual(a.results_dir.name,'evolution-v083')
        self.assertEqual(self.cfg.parent_pool_size,2)
        self.assertEqual(self.cfg.elite_count,0)
        self.assertEqual(self.cfg.selection_policy,'v081_survival')
        self.assertTrue(self.cfg.indicator_aware)


if __name__ == '__main__':
    unittest.main()
