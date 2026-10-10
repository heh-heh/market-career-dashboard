"""Tiny deterministic instrumentation/artifact tests; no historical simulations."""
import ast
import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from research.intraday_v4_funnel import GateTrace
from research import intraday_v4_engine as e
from research.intraday_v4_candidates import evaluate_candidate
from research.analyze_v4_frequency import (decision_breakdown,frequency_metrics,pnl_semantics,
    compare_admissions,analyze_campaign,main)
from research.run_v4_research_campaign import plan
from research.test_intraday_v4 import clock,session,references,install,ctx,setup_ir2
from research.test_v4_clock_context import market,seeded_history
from research import test_v4_revalidation as fixtures


class GateTests(unittest.TestCase):
    def test_conditional_inputs_and_units(self):
        trace=GateTrace();event=e.Event('ir1','AAPL')
        for passed in (True,False,True):
            gate=trace.probe(event,clock(11,0),ctx())
            if gate('A',passed): gate('B',False)
        c=trace.as_dict()['strategies']['ir1']['IDLE']
        self.assertEqual(c['inputs'],3)
        a,b=c['gates']
        self.assertEqual((a['input'],a['passed'],a['rejected']),(3,2,1))
        self.assertEqual((b['input'],b['passed'],b['rejected']),(2,0,2))
        self.assertAlmostEqual(a['survivalFromChainInput'],2/3)
        self.assertEqual(c['unit'],'decision_observation')

    def test_no_gate_evaluated_after_first_failure(self):
        gate=GateTrace().probe(e.Event('ir1','AAPL'),clock(11,0),ctx())
        gate('A',False)
        with self.assertRaises(AssertionError): gate('B',True)

    def test_repeated_observations_do_not_grow_samples_or_keys(self):
        trace=GateTrace();event=e.Event('ir1','AAPL')
        for _ in range(1000): trace.probe(event,clock(11,0),ctx())('CONTEXT',False,'MARKET_DIRECTION_FILTER')
        chain=trace.as_dict()['strategies']['ir1']['IDLE']
        self.assertEqual(chain['rejectReasons']['MARKET_DIRECTION_FILTER'],1000)
        self.assertEqual(len(trace.chains),1)
        self.assertEqual(len(chain['byFacet']['symbol']),1)

    def test_same_state_symbol_strategy_counts_isolated(self):
        trace=GateTrace()
        for k,s in (('ir1','AAPL'),('ir1','AMD'),('ir2','AMD')):
            trace.probe(e.Event(k,s),clock(11,0),ctx())('AVAILABLE',True)
        x=trace.as_dict()['strategies']
        self.assertEqual(x['ir1']['IDLE']['inputs'],2)
        self.assertEqual(x['ir2']['IDLE']['inputs'],1)
        self.assertEqual(set(x['ir1']['IDLE']['byFacet']['symbol']),{'AAPL','AMD'})

    def test_optin_does_not_change_baseline_event_or_fill(self):
        for direction in ('TREND_UP','MIXED','TREND_DOWN','RANGE','UNKNOWN'):
            T,s,refs,event=fixtures.CandidateTests().armed()
            event.fields['setupTimestamp']=clock(10,50).isoformat()
            a,b=copy.deepcopy(event),copy.deepcopy(event);trace=GateTrace()
            e.evaluate(a,s,refs,T,ctx(direction),True)
            e.evaluate(b,s,refs,T,ctx(direction),True,gate_trace=trace)
            self.assertEqual(a,b)
            if a.state=='TRIGGERED':
                nextT=T+e.MINUTE;install(s,nextT,102.45,low=101.1);refs.update(references(nextT))
                s.minutes[nextT-20*e.MINUTE]=s.minutes[T-20*e.MINUTE]
                s.snapshots[nextT]['u20']=.02;refs['QQQ'].snapshots[nextT]['u20']=.001
                ba,bb=e.Book('ir1'),e.Book('ir1')
                self.assertEqual(ba.enter(a,s.minutes[nextT],nextT,ctx(),s,refs),bb.enter(b,s.minutes[nextT],nextT,ctx(),s,refs,trace))
                self.assertEqual(ba.position,bb.position)

    def test_r1_maintenance_has_bounded_causal_evidence(self):
        T,s,refs,event=fixtures.CandidateTests().armed();event.state='SETUP'
        event.fields.update(pullbacks=[],impulseVolume=5000)
        a=copy.deepcopy(event);evaluate_candidate(a,s,refs,T,ctx('MIXED'),True)
        trace=GateTrace()
        for _ in range(8): evaluate_candidate(event,s,refs,T,ctx('MIXED'),True,trace)
        self.assertEqual(event.state,a.state)
        self.assertEqual(len(event.fields['maintenanceAdmissions']),5)
        self.assertEqual(event.fields['maintenanceAdmissionCount'],8)
        self.assertTrue(all(x['timestamp']==T.isoformat() for x in event.fields['maintenanceAdmissions']))

    def test_full_end_clock_context_and_est_edt_causal_instrumentation(self):
        for date in ('2024-03-08','2024-03-11','2024-06-03','2024-11-04'):
            refs=market(date);s=refs['QQQ'];T=s.opening.replace(hour=15,minute=30)
            a,b=e.Event('ir3','QQQ'),e.Event('ir3','QQQ');trace=GateTrace()
            e.evaluate(a,s,refs,T,e.context(refs,T),True)
            e.evaluate(b,s,refs,T,e.context(refs,T),True,gate_trace=trace)
            self.assertEqual(a,b);self.assertEqual(b.state,'TRIGGERED')
            self.assertEqual(s.minutes[T].start,T-e.MINUTE)
            self.assertNotIn(T+e.MINUTE,s.snapshots)
            gates=trace.as_dict()['strategies']['ir3']['IDLE']['gates']
            self.assertEqual(gates[-1]['gate'],'OPENING_RVOL');self.assertEqual(gates[-1]['passed'],1)

    def test_clock_separates_opening_z_from_volume_without_new_rule(self):
        refs=market();s=refs['QQQ'];T=s.opening.replace(hour=15,minute=30)
        for z,volume,reason in ((.4,2,'OPENING_Z'),(.6,1,'OPENING_RVOL')):
            with patch.object(s,'opening_stats',return_value=dict(openingZ=z,openingRVOL=volume)):
                event=e.Event('ir3','QQQ');trace=GateTrace()
                e.evaluate(event,s,refs,T,e.context(refs,T),True,gate_trace=trace)
                self.assertEqual(event.fields['reason'],'OPENING_CONDITION_FAILED')
                self.assertEqual(trace.as_dict()['strategies']['ir3']['IDLE']['gates'][-1]['gate'],reason)

    def test_missing_benchmark_not_replaced_by_future(self):
        refs=market();s=refs['QQQ'];T=s.opening.replace(hour=15,minute=30)
        refs['SPY'].snapshots[T+e.MINUTE]=refs['SPY'].snapshots.pop(T)
        trace=GateTrace();ev=e.Event('ir3','QQQ')
        e.evaluate(ev,s,refs,T,e.context(refs,T),True,gate_trace=trace)
        self.assertEqual(ev.state,'IDLE')
        self.assertEqual(trace.as_dict()['strategies']['ir3']['IDLE']['rejectReasons']['BENCHMARK_BAR_MISSING'],1)

    def test_daily_history_reason_not_liquidity_or_pattern(self):
        T,s,refs,event=fixtures.CandidateTests().armed();event=e.Event('ir1','AAPL')
        s.eligibility=None;trace=GateTrace()
        e.evaluate(event,s,refs,T,ctx(),True,gate_trace=trace)
        c=trace.as_dict()['strategies']['ir1']['IDLE']
        self.assertEqual(c['rejectReasons']['DAILY_HISTORY_INSUFFICIENT'],1)
        self.assertNotIn('CONTEXT_ALLOWED',{g['gate'] for g in c['gates']})

    def test_ir2_reclaim_first_second_and_third_bar_matches_uninstrumented(self):
        for reclaim in (1,2,3):
            s,event,refs=setup_ir2();a,b=copy.deepcopy(event),copy.deepcopy(event);trace=GateTrace()
            for i in range(1,reclaim+1):
                T=clock(10,5)+5*i*e.MINUTE;price=100.2 if i==reclaim else 100
                install(s,T,price,low=99.6,high=100.3)
                s.fives.append(dict(bar=e.Bar(T-5*e.MINUTE,T,100,100.3,99.6,price,5000),atr=1))
                e.evaluate(a,s,refs,T,ctx('MIXED'),True)
                e.evaluate(b,s,refs,T,ctx('MIXED'),True,gate_trace=trace)
                self.assertEqual(a,b)
            self.assertEqual(b.state,'TRIGGERED' if reclaim<=2 else 'CANCELLED')

    def test_entry_independent_components_do_not_override_primary_reason(self):
        for direction,price in (('TREND_UP',103.5),('MIXED',103.5)):
            T,s,refs,event=fixtures.CandidateTests().armed()
            install(s,T,price,low=101,high=104)
            event.state='TRIGGERED';event.fields.update(signalTimestamp=(T-e.MINUTE).isoformat(),setupTimestamp=clock(10,50).isoformat(),triggerClose=102.4)
            a,b=copy.deepcopy(event),copy.deepcopy(event);ba,bb=e.Book('ir1'),e.Book('ir1');trace=GateTrace()
            self.assertEqual(ba.enter(a,s.minutes[T],T,ctx(direction),s,refs),bb.enter(b,s.minutes[T],T,ctx(direction),s,refs,trace))
            self.assertEqual(a,b)
            c=trace.as_dict()['strategies']['ir1']
            self.assertEqual(c['ENTRY_COMPONENT_CHASE']['gates'][0]['rejected'],1)
            self.assertEqual(c['ENTRY_RESOLUTION']['gates'][0]['rejected'],1)

    def test_campaign_forwards_flag_without_extra_jobs(self):
        from argparse import Namespace
        a=Namespace(data_dir=Path('/tmp/input'),manifest=Path('/tmp/manifest'),timestamp_kind='end',provisional=True,
                    from_date=None,to_date=None,symbols=None,funnel_diagnostics=True)
        jobs=plan(a,Path('/tmp/new'))
        self.assertEqual(len(jobs),5)
        self.assertTrue(all('--funnel-diagnostics' in j['command'] for j in jobs))

    def test_complete_tiny_clock_replay_identical_trades_with_optin(self):
        from research import backtest_intraday_v4 as runner
        from research.backtest_v4_strategies import exchange_sessions
        date='2024-06-03';schedule=exchange_sessions(date,date);opening,closing=schedule[date]
        rows={}
        for symbol in ('SPY','QQQ'):
            rows[symbol]={}
            for n in range(390):
                start=opening+n*e.MINUTE;c=100+(n+1)*.025
                rows[symbol][start]=e.Bar(start,start+e.MINUTE,c-.02,c+.25,c-.25,c,2200)
        outputs=[];trace=GateTrace()
        for enabled in (False,True):
            with patch.object(runner,'History',side_effect=seeded_history):
                outputs.append(runner.run_sessions(iter([(date,rows)]),schedule,{'SPY','QQQ'},['ir3'],
                    gate_trace=trace if enabled else None))
        self.assertEqual(outputs[0],outputs[1]);self.assertEqual(len(outputs[1][0]),1)
        c=trace.as_dict()['strategies']['ir3']['CLOCK_CONJUNCTION']
        self.assertEqual(c['inputs'],2);self.assertEqual(c['gates'][0]['passed'],2)


class ArtifactTests(unittest.TestCase):
    def trade(self,symbol='AAPL',date='2024-06-03',trigger='10:59:00'):
        t=fixtures.AnalysisTests().trade()
        t.update(symbol=symbol,date=date,triggerTimestamp=date+'T'+trigger+'-04:00',
                 entryTimestamp=date+'T11:00:00-04:00',exitTimestamp=date+'T11:10:00-04:00',
                 entryPrice=100,entryMarketPrice=99.97,initialStop=99,quantity=1,pnlUsd=-1,returnPct=-1,
                 setupTimestamp=date+'T10:50:00-04:00')
        return t

    def result(self,trades=()):
        return dict(configuration=dict(contract='IR_SPEC_V1',strategy=['ir1'],researchVariant='baseline',notionalUSD=100,
            fromDate='2021-12-01',toDate='2026-10-01',dataMode='PROVISIONAL_UNREVIEWED_DATA'),trades=list(trades),
            execution=dict(timestampKind='end'),diagnostics={},funnel={})

    def test_pnl_identity_and_real_mismatch(self):
        r=self.result([self.trade()]);self.assertEqual(pnl_semantics(r)['status'],'PASS_FIXED_NOTIONAL_IDENTITY')
        r['trades'][0]['pnlUsd']=-100
        self.assertEqual(pnl_semantics(r)['status'],'MISMATCH')

    def test_empty_years_and_oos_folds_are_explicit(self):
        r=self.result();r['chronologicalEvaluation']=dict(folds=[dict(trainPeriod=['2024-01-01','2025-01-01'],
            validationPeriod=['2025-01-01','2025-04-01'],oosPeriod=['2025-04-01','2025-07-01'])])
        m=frequency_metrics(r)
        self.assertEqual(m['tradesByYearIncludingEmpty']['2026'],0)
        self.assertEqual(m['chronologicalEvaluation']['nonemptyOOSFolds'],0)
        self.assertEqual(m['finalCompleteOOSFold']['oos']['trades'],0)
        self.assertIsNone(m['medianR'])

    def test_new_and_lost_triggers_are_not_assumed_shared(self):
        b=self.result([self.trade()]);c=self.result([self.trade(trigger='10:58:00'),self.trade('AMD')])
        x=compare_admissions(b,c,{},{});self.assertEqual(x['sharedTriggers'],0)
        self.assertEqual(len(x['newlyAdmitted']),2);self.assertEqual(len(x['noLongerAdmitted']),1)
        self.assertIsNone(x['newlyAdmitted'][0]['otherVariantEvent'])

    def test_decisions_stream_atomic_reasons_and_transitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'decisions.jsonl'
            ev=e.Event('ir1','AAPL');ev.move('SETUP',clock(10,50));ev.move('CANCELLED',clock(10,51),'CONTEXT_MARKET_DIRECTION_FILTER')
            p.write_text(json.dumps(dict(strategy=ev.strategy,symbol=ev.symbol,date='2024-06-03',state=ev.state,transitions=ev.transitions,diagnostics=ev.fields))+'\n')
            x,matched=decision_breakdown(p,{('AAPL','2024-06-03')})
            self.assertEqual(x['terminalReasons']['ir1:CONTEXT_MARKET_DIRECTION_FILTER'],1)
            self.assertEqual(x['attainedStates']['ir1:SETUP'],1)
            self.assertEqual(x['byFacet']['timeOfDay']['10:00–11:00']['ir1:CONTEXT_MARKET_DIRECTION_FILTER'],1)
            self.assertEqual(len(matched),1)

    def test_mutating_decisions_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'decisions.jsonl';p.write_text('')
            with patch('research.analyze_v4_frequency.input_hash',side_effect=['before','after']):
                with self.assertRaisesRegex(ValueError,'changed'): decision_breakdown(p)

    def campaign(self,path):
        jobs=[]
        for name in ('baseline-ir1-2bps','baseline-ir2-2bps','baseline-ir3-2bps','baseline-all-2bps','ir1-r1-ir1-2bps'):
            d=path/name;d.mkdir();jobs.append(dict(name=name,directory=str(d)))
            r=self.result();r['runId']=name
            r['configuration']['strategy']=['ir1','ir2','ir3'] if 'all' in name else ['ir1' if name.startswith('ir1-r1') else name.split('-')[1]]
            r['configuration']['researchVariant']='ir1-r1' if name.startswith('ir1-r1') else 'baseline'
            (d/'result.json').write_text(json.dumps(r));(d/'audit.json').write_text(json.dumps(dict(runId=name,symbols={'SPY':dict(sha256='same')},validationPassed=True)))
            (d/'decisions.jsonl').write_text('')
        (path/'campaign.json').write_text(json.dumps(dict(phase='completed',jobs=jobs,completed=[dict(name=j['name']) for j in jobs])))

    def test_complete_campaign_without_gate_logs_never_fabricates_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);self.campaign(p);r=analyze_campaign(p)
            self.assertTrue(r['comparison']['comparable'])
            self.assertEqual(r['candidateSelection'],'BLOCKED_PENDING_QUANTIFIED_BOTTLENECK_REVIEW')
            self.assertTrue(all(x['gateMeasurementStatus']=='NOT_MEASURED' for x in r['jobs'].values()))

    def test_incomplete_campaign_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'campaign.json').write_text('{"phase":"running"}')
            with self.assertRaisesRegex(ValueError,'completed'): analyze_campaign(p)

    def test_cross_job_changed_hash_or_failed_audit_refused(self):
        for field,value,reason in (('validationPassed',False,'Mechanical audit failed'),('symbols',{'SPY':dict(sha256='changed')},'Source hash changed')):
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);self.campaign(p);audit=p/'baseline-ir2-2bps'/'audit.json'
                d=json.loads(audit.read_text());d[field]=value;audit.write_text(json.dumps(d))
                with self.assertRaisesRegex(ValueError,reason): analyze_campaign(p)

    def test_analysis_fresh_directories_inputs_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);campaign=p/'run'/'full'/'stamp';campaign.mkdir(parents=True);self.campaign(campaign)
            before={f.relative_to(campaign):f.read_bytes() for f in campaign.rglob('*') if f.is_file()}
            for _ in range(2): self.assertEqual(main(['--campaign',str(campaign),'--timestamp-kind','end']),0)
            outputs=list((p/'run'/'funnel').glob('*/v4_funnel_diagnostics.json'))
            self.assertEqual(len(outputs),2)
            self.assertEqual(before,{f.relative_to(campaign):f.read_bytes() for f in campaign.rglob('*') if f.is_file()})

    def test_timestamp_assertion_never_reinterprets_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);campaign=p/'run'/'full'/'stamp';campaign.mkdir(parents=True);self.campaign(campaign)
            with self.assertRaisesRegex(ValueError,'timestampKind'): main(['--campaign',str(campaign),'--timestamp-kind','start'])
            self.assertFalse(list((p/'run'/'funnel').glob('*/v4_funnel_diagnostics.json')))


class MonitorTests(unittest.TestCase):
    def test_status_dead_analysis_is_interrupted_and_has_independent_state(self):
        # Extract the pure monitor helper; avoid starting any server services.
        source=Path(__file__).resolve().parents[1]/'server.py';tree=ast.parse(source.read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_v4_frequency_status')
        namespace={'_v4_read_dict':lambda p:json.loads(p.read_text()),'_v4_processes':lambda name:[]}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);d=root/'funnel'/'new';d.mkdir(parents=True)
            (d/'state.json').write_text('{"phase":"running","pid":123,"progress":40}')
            x=namespace['_v4_frequency_status'](root)
            self.assertEqual(x['phase'],'interrupted');self.assertFalse(x['running'])
            namespace['_v4_processes']=lambda name:[dict(pid=123)]
            self.assertTrue(namespace['_v4_frequency_status'](root)['running'])


if __name__=='__main__': unittest.main()
