"""Small deterministic tests. No historical data, network, or order calls."""
import copy
import fcntl
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from research import intraday_v4_engine as e
from research.intraday_v4_candidates import maintenance_context, evaluate_candidate
from research.intraday_v4_metrics import chronological_folds
from research.backtest_v4_strategies import exchange_sessions
from research.compare_v4_research import analyze, comparable, cost_counterfactual, concentration_stress, load
from research.run_v4_research_campaign import plan, run
from backtest_manager import BacktestManager
from research.test_intraday_v4 import clock, session, references, install, ctx, bar


class CandidateTests(unittest.TestCase):
    def test_baseline_gate_unchanged(self):
        T=clock(11,0); refs=references(T)
        for kind in ('ir1','ir2','ir3'):
            for stage in ('IDLE','SETUP','ARMED','TRIGGERED'):
                for direction in ('TREND_UP','TREND_DOWN','RANGE','UNKNOWN'):
                    for vol in ('NORMAL_VOL','HIGH_VOL','LOW_VOL','UNKNOWN'):
                        context=ctx(direction); context['volatility']=vol
                        self.assertEqual(maintenance_context(kind,'AAPL',context,refs,T,stage),
                                         e.allowed_context(kind,'AAPL',context,refs,T,stage))

    def test_mixed_persistence_only_ir1_and_known_not_down(self):
        T=clock(11,0); refs=references(T); context=ctx('MIXED')
        for stage in ('SETUP','ARMED'):
            self.assertTrue(maintenance_context('ir1','AAPL',context,refs,T,stage))
        for stage in ('IDLE','TRIGGERED','MANAGING'):
            self.assertFalse(maintenance_context('ir1','AAPL',context,refs,T,stage))
        for direction in ('DOWN',None):
            other=dict(context,spy=direction)
            self.assertFalse(maintenance_context('ir1','AAPL',other,refs,T,'ARMED'))

    def test_candidate_keeps_sector_confirmation(self):
        T=clock(11,0); refs=references(T); context=ctx('MIXED')
        self.assertTrue(maintenance_context('ir1','NVDA',context,refs,T,'SETUP'))
        refs['SOXX'].snapshots[T]['u20']=-.01
        self.assertFalse(maintenance_context('ir1','NVDA',context,refs,T,'SETUP'))
        del refs['SOXX']
        self.assertFalse(maintenance_context('ir1','NVDA',context,refs,T,'SETUP'))

    def armed(self):
        T=clock(11,0); s=session(); refs=references(T); refs['AAPL']=s
        install(s,T,102.4,low=101,high=102.5)
        s.minutes[T-20*e.MINUTE]=bar(T-20*e.MINUTE,100)
        s.snapshots[T]['u20']=.02
        refs['QQQ'].snapshots[T]['u20']=.001
        event=e.Event('ir1','AAPL','ARMED',dict(A=1,origin=99,impulseEnd=102,advance=3,
                      B=102,L_arm=100.9,stop=100.8,armedTimestamp=clock(10,59).isoformat()))
        return T,s,refs,event

    def test_neutral_pullback_survives_but_does_not_trigger(self):
        T,s,refs,event=self.armed()
        baseline=copy.deepcopy(event)
        e.evaluate(baseline,s,refs,T,ctx('MIXED'),True)
        self.assertEqual(baseline.state,'CANCELLED')
        evaluate_candidate(event,s,refs,T,ctx('MIXED'),True)
        self.assertEqual(event.state,'ARMED')
        self.assertEqual((event.fields['B'],event.fields['L_arm']),(102,100.9))

    def test_trend_restoration_triggers_same_structure(self):
        T,s,refs,event=self.armed()
        a=copy.deepcopy(event); b=copy.deepcopy(event)
        e.evaluate(a,s,refs,T,ctx(),True)
        evaluate_candidate(b,s,refs,T,ctx(),True)
        self.assertEqual(a.state,'TRIGGERED')
        self.assertEqual(a,b)

    def test_wait_timeout_is_not_extended(self):
        T,s,refs,event=self.armed()
        event.fields['armedTimestamp']=clock(10,54).isoformat()
        evaluate_candidate(event,s,refs,T,ctx('MIXED'),True)
        self.assertEqual(event.fields['reason'],'TRIGGER_EXPIRED')

    def test_entry_still_requires_original_context(self):
        T,s,refs,event=self.armed()
        event.fields.update(signalTimestamp=clock(10,59).isoformat(),setupTimestamp=clock(10,50).isoformat(),triggerClose=102.4)
        event.state='TRIGGERED'
        book=e.Book('ir1')
        self.assertFalse(book.enter(event,s.minutes[T],T,ctx('MIXED'),s,refs))
        self.assertIsNone(book.position)

    def test_entry_candle_provenance_is_the_completed_observation(self):
        T,s,refs,event=self.armed()
        event.fields.update(signalTimestamp=clock(10,59).isoformat(),setupTimestamp=clock(10,50).isoformat(),triggerClose=102.4)
        event.state='TRIGGERED'; book=e.Book('ir1')
        self.assertTrue(book.enter(event,s.minutes[T],T,ctx(),s,refs))
        self.assertEqual(book.position['entryCandle']['barObservable'],T.isoformat())
        self.assertEqual(book.position['entryCandle']['close'],book.position['entryMarketPrice'])
        self.assertLess(book.position['signalTimestamp'],book.position['entryTimestamp'])


class AnalysisTests(unittest.TestCase):
    def trade(self):
        return dict(strategy='ir1',symbol='AAPL',date='2022-04-04',entryTimestamp='2022-04-04T10:00:00-04:00',
                    exitTimestamp='2022-04-04T10:10:00-04:00',session='REGULAR',exitReason='TARGET',
                    returnPct=-1,netR=-1,pnlUsd=-300,holdDurationSeconds=600,quantity=1,
                    entryPrice=100.03,entryMarketPrice=100,exitMarketPrice=99,initialStop=99.03,
                    marketRegime=dict(direction='TREND_UP',volatility='NORMAL_VOL'))

    def test_combined_fold_capital_is_correct(self):
        days=[f'{y}-{m:02d}-01' for y in range(2021,2025) for m in range(1,13)]
        a=chronological_folds([self.trade()],days,30000)['folds'][0]['oos']
        b=chronological_folds([self.trade()],days)['folds'][0]['oos']
        self.assertEqual(a['paperAccountReturnPct'],-1)
        self.assertEqual(a['maxDrawdownPct'],1)
        self.assertEqual(a['expectancyR'],b['expectancyR'])
        self.assertEqual(b['paperAccountReturnPct'],-3)

    def test_exclusive_fold_end_does_not_require_next_month_data(self):
        days=list(exchange_sessions('2021-01-01','2024-06-28'))
        f=chronological_folds([],days)['folds']
        self.assertEqual(f[-1]['oosPeriod'][1],'2024-07-01')
        shorter=chronological_folds([],days[:-1])['folds']
        self.assertEqual(len(shorter),len(f)-1)

    def test_cost_counterfactual_uses_only_observed_marks(self):
        t=self.trade(); original=copy.deepcopy(t)
        gross=cost_counterfactual([t],0,0)[0]
        worse=cost_counterfactual([t],5,1)[0]
        self.assertEqual(gross['pnlUsd'],-1)
        self.assertLess(worse['pnlUsd'],gross['pnlUsd'])
        self.assertEqual(t,original)
        self.assertEqual(worse['exitTimestamp'],t['exitTimestamp'])

    def test_empty_results_and_zero_loss_pf(self):
        r=dict(configuration=dict(strategy=['ir1','ir2','ir3']),trades=[])
        x=analyze(r)
        self.assertEqual(x['equityCurve']['initialCashUsd'],30000)
        self.assertEqual(x['sampleVerdict'],'INSUFFICIENT_SAMPLE')
        self.assertEqual(len(x['byStrategy']),3)
        self.assertIsNone(x['overall']['profitFactorR'])

    def test_analysis_rejects_ambiguous_naive_trade_timestamp(self):
        t=self.trade(); t['exitTimestamp']='2022-04-04T10:10:00'
        with self.assertRaisesRegex(ValueError,'naive'):
            analyze(dict(configuration=dict(strategy=['ir1']),trades=[t]))

    def test_comparison_fails_without_matching_source_hashes(self):
        r=dict(configuration={},execution={})
        self.assertFalse(comparable(r,r,(None,None))['comparable'])
        a=dict(symbols={'AAPL':dict(sha256='first')})
        b=dict(symbols={'AAPL':dict(sha256='different')})
        self.assertFalse(comparable(r,r,(a,b))['comparable'])
        self.assertTrue(comparable(r,r,(a,a))['comparable'])

    def test_concentration_stress_is_descriptive_and_does_not_mutate(self):
        loss=self.trade(); win=dict(loss,symbol='SPY',pnlUsd=200,returnPct=1,netR=1)
        trades=[loss,win]; original=copy.deepcopy(trades)
        x=concentration_stress(trades,30000)['scenarios']
        self.assertEqual(x['removeBestNetPnlSymbol']['removedGroup'],'SPY')
        self.assertEqual(x['removeBestNetPnlSymbol']['metrics']['pnlUsd'],-300)
        self.assertEqual(x['removeUpToThreeLargestWinners']['removedTrades'],1)
        self.assertEqual(trades,original)

    def test_analysis_keeps_each_oos_empty_fold_explicit(self):
        r=dict(configuration=dict(strategy=['ir1']),trades=[],chronologicalEvaluation=dict(folds=[
            dict(trainPeriod=['2021-01-01','2022-01-01'],validationPeriod=['2022-01-01','2022-04-01'],oosPeriod=['2022-04-01','2022-07-01'])]))
        x=analyze(r)['chronologicalEvaluation']
        self.assertEqual(x['totalOOSFolds'],1)
        self.assertEqual(x['nonemptyOOSFolds'],0)


class CampaignTests(unittest.TestCase):
    def args(self,root):
        return Namespace(data_dir=root/'absent',manifest=root/'manifest.json',results_dir=root/'results',
                         manager_dir=root/'manager',timestamp_kind='start',provisional=True,plan=False,
                         from_date='2024-01-02',to_date='2024-01-10',symbols=None,cost_stress=False)

    def test_plan_contains_four_baselines_two_candidates_unique_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); jobs=plan(self.args(root),root/'unique')
            self.assertEqual(len(jobs),6)
            self.assertEqual(len({j['directory'] for j in jobs}),6)
            self.assertEqual([j['name'] for j in jobs[:4]],['baseline-ir1-2bps','baseline-ir2-2bps','baseline-ir3-2bps','baseline-all-2bps'])
            self.assertTrue(all('--timestamp-kind' in j['command'] for j in jobs))
            self.assertFalse((root/'unique').exists())

    def test_missing_data_never_launches_or_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp, patch('research.run_v4_research_campaign.subprocess.Popen') as popen:
            root=Path(tmp); args=self.args(root)
            old=root/'old_result.json'; old.write_text('preserve')
            with self.assertRaisesRegex(ValueError,'DATA_ACCESS_UNAVAILABLE'): run(args)
            popen.assert_not_called()
            self.assertEqual(old.read_text(),'preserve')
            self.assertFalse(args.results_dir.exists())

    def test_unreviewed_not_silently_used(self):
        with tempfile.TemporaryDirectory() as tmp, patch('research.run_v4_research_campaign.subprocess.Popen') as popen:
            args=self.args(Path(tmp)); args.data_dir.mkdir(); args.provisional=False
            with self.assertRaisesRegex(ValueError,'REVIEWED_MANIFEST_REQUIRED'): run(args)
            popen.assert_not_called()

    def test_outputs_cannot_touch_historical_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=self.args(Path(tmp)); args.results_dir=args.data_dir/'results'
            with self.assertRaisesRegex(ValueError,'cannot be inside'): run(args)

    def test_v4_ui_restart_archives_previous_result_even_if_launch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); manager=BacktestManager(root,root/'manager'); manager.directory.mkdir()
            paths=manager.paths('v4-all')
            old={key:'previous-'+key for key in paths}
            for key,path in paths.items(): path.write_text(old[key])
            with patch.object(manager,'active_job',return_value=None), patch.object(manager,'preflight',return_value=dict(runnable=True)), patch('backtest_manager.subprocess.Popen',side_effect=OSError('synthetic failure')):
                with self.assertRaises(OSError): manager.start('v4-all')
            archives=list((manager.directory/'backtest_archive').iterdir())
            self.assertEqual(len(archives),1)
            for key,path in paths.items(): self.assertEqual((archives[0]/path.name).read_text(),old[key])
            self.assertEqual(json.loads(paths['state'].read_text())['phase'],'error')

    def test_existing_campaign_lock_blocks_api_without_wait_or_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); manager=BacktestManager(root,root/'manager'); manager.directory.mkdir()
            with (manager.directory/'historical_backtest.lock').open('a') as lock, patch('backtest_manager.subprocess.Popen') as popen:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError,'execution lock'): manager.start('v4-all')
                popen.assert_not_called()


if __name__ == '__main__': unittest.main()
