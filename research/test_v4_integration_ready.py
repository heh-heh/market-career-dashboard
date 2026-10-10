"""Small offline regressions for rebased research tools. No historical replay."""
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.audit_v4_clock_context import scan_file, audit_data, write_report
from research.compare_v4_research import analyze
from research.intraday_v4_metrics import streaks
from research import test_v4_revalidation as fixtures
from research.backtest_v4_strategies import MINUTE,NY
from datetime import datetime

class IntegrationTests(unittest.TestCase):
    def write(self,path,rows):
        with path.open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['timestamp','open','high','low','close','volume'])
            w.writerows(rows)

    def test_mechanical_quality_exact_conflicting_duplicate_and_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'SPY.csv'
            t='2024-06-03T09:30:00-04:00'
            self.write(p,[[t,100,101,99,100,0],[t,100,101,99,100,0],[t,100,101,99,100,-1]])
            m=scan_file(p,'SPY','start')
            self.assertEqual((m['duplicates'],m['exactDuplicates'],m['conflictingDuplicates']),(2,1,1))
            self.assertEqual((m['negativeVolume'],m['zeroVolume'],m['invalidOHLCV']),(1,2,1))
            self.assertEqual(m['sha256'],hashlib.sha256(p.read_bytes()).hexdigest())
            self.assertEqual(m['currencyEvidence'],'UNAVAILABLE_NOT_ASSUMED_USD')

    def test_timezone_dst_conversion_samples_and_prefix_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'SPY.csv'
            self.write(p,[[t,100,101,99,100,1] for t in ('2024-03-08T14:30:00Z','2024-03-11T13:30:00Z','2024-03-12T13:30:00Z')])
            m=scan_file(p,'SPY','start',to_date='2024-03-11')
            self.assertEqual(m['rows'],2);self.assertEqual(m['lastTimestamp'],'2024-03-11T13:30:00Z')
            self.assertEqual(len(m['timestampExamples']),2)
            for x in m['timestampExamples'].values():
                self.assertIn('T09:30:00',x['normalizedStartET'])
                self.assertIn('T09:31:00',x['observableET'])
                self.assertTrue(x['kst'].endswith('+09:00'))

    def test_naive_input_and_order_errors_are_not_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'SPY.csv'
            self.write(p,[[t,100,101,99,100,1] for t in ('2024-06-03T09:32:00-04:00','2024-06-03T09:31:00-04:00','2024-06-03T09:33:00')])
            original=p.read_bytes();m=scan_file(p,'SPY','start')
            self.assertEqual(m['outOfOrder'],1);self.assertEqual(m['badTimezone'],1)
            self.assertTrue(m['errors']);self.assertEqual(p.read_bytes(),original)

    def test_source_hash_mutation_fails_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'SPY.csv';self.write(p,[['2024-06-03T09:30:00-04:00',100,101,99,100,1]])
            with patch('research.audit_v4_clock_context.file_sha256',side_effect=['original','changed']):
                m=scan_file(p,'SPY','start')
            self.assertIn('Source changed during audit',m['errors'])

    def test_tiny_audit_reports_regular_pre_after_and_history_without_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);paths={}
            opening=datetime(2024,6,3,9,30,tzinfo=NY);closing=opening.replace(hour=16,minute=0)
            for symbol in ('SPY','QQQ'):
                p=root/(symbol+'.csv');paths[symbol]=p
                self.write(p,[[t.isoformat(),100,101,99,100,1] for t in (opening-MINUTE,opening,closing-MINUTE,closing)])
            out=root/'audit.json';r=audit_data(paths,'start',out,root/'sessions.jsonl',calendar_provider=lambda a,b:{'2024-06-03':(opening,closing)})
            self.assertEqual(r['reviewStatus'],'PROVISIONAL_UNREVIEWED_DATA')
            self.assertEqual(r['aggregate']['missing_regular_minutes'],776)
            self.assertEqual(r['aggregate']['PRE_rows_clock_proxy'],2)
            self.assertEqual(r['aggregate']['AFTER_rows_clock_proxy'],2)
            self.assertEqual(r['aggregate']['history_61_available_sessions'],0)
            rows=[json.loads(x) for x in (root/'sessions.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['historyCoverage']['requiredDailySessions'],61)
            write_report(root/'audit.md',r)
            self.assertIn('PROVISIONAL_UNREVIEWED_DATA',(root/'audit.md').read_text())

    def test_streaks_use_chronological_exits_and_reset_at_breakeven(self):
        values=[1,1,-1,-1,-1,0,1]
        trades=[dict(returnPct=r,exitTimestamp=f'2024-06-03T10:{i:02}:00-04:00') for i,r in enumerate(values)]
        self.assertEqual(streaks(list(reversed(trades))),dict(maxConsecutiveWins=2,maxConsecutiveLosses=3))

    def test_four_cost_scenarios_stability_and_inconclusive(self):
        t=fixtures.AnalysisTests().trade();r=analyze(dict(configuration=dict(strategy=['ir1']),trades=[t]))
        scenarios=r['robustnessCounterfactuals']['scenarios']
        self.assertEqual(len(scenarios),4)
        values=[m['pnlUsd'] for m in scenarios.values()]
        self.assertEqual(values,sorted(values,reverse=True))
        self.assertEqual(r['verdict'],'inconclusive')
        self.assertEqual(r['stability']['yearly']['negativePeriods'],1)
        self.assertEqual(r['stability']['topFiveContribution']['trades'],1)
        self.assertIsNone(r['finalCompleteOOSFold'])

    def test_archived_cost_algebra_matches_observed_mark_counterfactual(self):
        from research.compare_v4_research import archived_cost_sensitivity,cost_counterfactual
        t=fixtures.AnalysisTests().trade()
        t.update(entryPrice=100.03,entryMarketPrice=100,exitMarketPrice=99,initialStop=99.03,quantity=100/100.03)
        x=99*(1-.0003)
        t.update(returnPct=100*(x/100.03-1),pnlUsd=t["quantity"]*(x-100.03),netR=x-100.03)
        for slip in (0,2,5,10):
            spread=0 if slip==0 else 1
            a=archived_cost_sensitivity([t],3,slip,spread,100)[0]
            b=cost_counterfactual([t],slip,spread)[0]
            for key in ("returnPct","netR","pnlUsd"):self.assertAlmostEqual(a[key],b[key],places=10)

    def test_final_fold_metrics_recomputed_with_combined_capital(self):
        t=fixtures.AnalysisTests().trade()
        periods=dict(trainPeriod=['2020-01-01','2021-01-01'],validationPeriod=['2021-01-01','2022-01-01'],oosPeriod=['2022-01-01','2023-01-01'],oos={'paperAccountReturnPct':999})
        r=analyze(dict(configuration=dict(strategy=['ir1','ir2','ir3']),trades=[t],chronologicalEvaluation=dict(folds=[periods])))
        self.assertEqual(r['finalCompleteOOSFold']['oos']['paperAccountReturnPct'],-1)

if __name__=='__main__':unittest.main()
