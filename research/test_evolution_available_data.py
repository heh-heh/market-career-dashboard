"""Small synthetic-only availability/selection regressions; raw EC2 data is never used."""
import contextlib
import csv
from dataclasses import replace
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from research.backtest_v4_strategies import MINUTE,exchange_sessions,file_sha256
from research.evolution.available_data import audit_all,select_windows,verify_plan,compare_conditions
from research.evolution.config import Config
from research.evolution.engine import evaluate_day
from research.evolution.features import POLICY_FEATURES
from research.evolution.population import Bot,daily_evaluation,initial_population
from research.evolution.run_evolution_v0 import main as strict_main
from research.evolution.run_v07_available import main,run,parser,validation_adequacy
from research.test_evolution_v0 import fixture,write_fixtures,buy_genome

ROOT=Path(__file__).resolve().parents[1]
CFG=ROOT/'research/evolution/v07_5d_regime_timing_config.json'


def remove_minutes(path,day,minute_labels=None):
    with path.open() as f:
        reader=csv.DictReader(f);fields=reader.fieldnames
        rows=[r for r in reader if not (r['timestamp'].startswith(day) and
                (minute_labels is None or r['timestamp'] in minute_labels))]
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)


def prepare(root,dates,symbols=('AAPL','TQQQ'),mutate=None):
    write_fixtures(root/'data',dates)
    if mutate:mutate(root/'data')
    audit_all(root/'data',root/'audit',dates[0],dates[-1],list(symbols))
    return select_windows(root/'audit',root/'plan')


class AvailabilityAuditTests(unittest.TestCase):
    def test_default_universe_all_symbols_missing_files_reported_not_implicit_blacklist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_fixtures(root/'data',['2023-06-01'])
            before={p.name:file_sha256(p) for p in (root/'data').iterdir()}
            audit_all(root/'data',root/'audit','2023-06-01','2023-06-01')
            info=json.loads((root/'audit/audit.json').read_text())
            self.assertEqual(len(info['symbols']),30)
            self.assertEqual(info['missingSourceSymbols']['NVDA'],'SOURCE_FILE_MISSING')
            self.assertEqual(info['perSymbol']['NVDA']['missingSessions'],1)
            self.assertEqual(before,{p.name:file_sha256(p) for p in (root/'data').iterdir()})
            self.assertEqual(info['finalOosGate'],'CLOSED');self.assertFalse(info['priceAdjustmentConfirmed'])

    def test_partial_day_exact_missing_end_labels_and_symbol_returns_next_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dates=['2023-06-01','2023-06-02']
            def missing(data):remove_minutes(data/'AAPL.csv',dates[0],[dates[0]+'T09:31:00-04:00',dates[0]+'T09:32:00-04:00'])
            plan=prepare(root,dates,mutate=missing)
            p=json.loads(plan.read_text());self.assertNotIn('AAPL',p['sessions'][0]['usablePrefixSymbols'])
            self.assertIn('AAPL',p['sessions'][1]['usablePrefixSymbols'])
            self.assertTrue(p['sessions'][0]['eligible'])
            records=[json.loads(x) for x in (root/'audit/symbol_sessions.jsonl').read_text().splitlines()]
            a=next(x for x in records if x['symbol']=='AAPL' and x['sessionDate']==dates[0])
            self.assertEqual(a['missingMinutes'],2);self.assertEqual(a['missingMinuteRanges'][0]['count'],2)
            self.assertEqual(a['missingMinuteRanges'][0]['firstEnd'],dates[0]+'T09:31:00-04:00')

    def test_benchmark_missing_no_replacement_and_breaks_consecutive_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dates=list(exchange_sessions('2023-06-01','2023-06-14'))
            plan=prepare(root,dates,mutate=lambda d:remove_minutes(d/'QQQ.csv',dates[2]))
            p=json.loads(plan.read_text());self.assertEqual(p['usableNonoverlappingWindows'],1)
            self.assertEqual(p['potentialRollingWindows'],3);self.assertIsNone(p['smokeRange'])
            self.assertEqual(p['interruptedPartialWindows'],1)
            self.assertEqual(p['sessions'][2]['referenceFailures'][0]['symbol'],'QQQ')
            self.assertEqual(p['sessions'][2]['reason'],'BENCHMARK_SESSION_INCOMPLETE')
            self.assertTrue(p['selectionBiasWarnings'])

    def test_missing_benchmark_file_zero_usable_windows_and_appear_requires_reaudit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dates=list(exchange_sessions('2023-06-01','2023-06-07'))
            plan=prepare(root,dates,mutate=lambda d:(d/'SOXX.csv').unlink())
            self.assertEqual(json.loads(plan.read_text())['usableNonoverlappingWindows'],0)
            (root/'data/SOXX.csv').write_text('timestamp,open,high,low,close,volume\n')
            with self.assertRaisesRegex(ValueError,'appeared'):verify_plan(plan)

    def test_empty_symbol_window_and_new_listing_not_entire_research_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dates=['2023-06-01','2023-06-02']
            plan=prepare(root,dates,mutate=lambda d:remove_minutes(d/'AAPL.csv',dates[0]))
            p=json.loads(plan.read_text());self.assertTrue(all(x['eligible'] for x in p['sessions']))
            self.assertIn('AAPL',p['sessions'][1]['usablePrefixSymbols'])
            self.assertFalse(p['baselineComparability']['dataSelectionComparable'])

    def test_hash_mutation_and_evidence_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan=prepare(root,['2023-06-01'])
            p=json.loads(plan.read_text());p['sessions'][0]['eligible']=False
            plan.write_text(json.dumps(p))
            with self.assertRaisesRegex(ValueError,'EVIDENCE_MISMATCH'):verify_plan(plan)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan=prepare(root,['2023-06-01'])
            with (root/'data/TQQQ.csv').open('a') as f:f.write('\n')
            with self.assertRaisesRegex(ValueError,'INPUT_HASH_CHANGED'):verify_plan(plan)

    def test_derived_windows_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan=prepare(root,['2023-06-01'])
            value=json.loads(plan.read_text());value['smokeRange']=dict(fromDate='2023-06-01',toDate='2023-06-14',sessions=10)
            plan.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'DERIVED_WINDOWS_MISMATCH'):verify_plan(plan)

    def test_naive_timezone_ambiguity_fails_instead_of_becoming_symbol_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_fixtures(root/'data',['2023-06-01'])
            p=root/'data/TQQQ.csv';p.write_text(p.read_text().replace('-04:00',''))
            with self.assertRaises(ValueError):audit_all(root/'data',root/'audit','2023-06-01','2023-06-01',['TQQQ'])
            self.assertFalse((root/'audit/audit.json').exists())

    def test_oos_numerics_never_decoded_and_requested_2026_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_fixtures(root/'data',['2023-06-01'])
            with (root/'data/TQQQ.csv').open('a') as f:f.write('2026-01-02T10:00:00-05:00,TQQQ,DO_NOT_DECODE,x,x,x,x\n')
            audit_all(root/'data',root/'audit','2023-06-01','2023-06-01',['TQQQ'])
            self.assertTrue((root/'audit/audit.json').exists())
            with self.assertRaisesRegex(ValueError,'FINAL_OOS'):audit_all(root/'data',root/'bad','2026-01-01','2026-01-02')
        a=parser().parse_args(['--selection-plan','/does/not/exist','--from-date','2026-01-01','--to-date','2026-02-01'])
        with patch('research.evolution.run_v07_available.verify_plan',side_effect=AssertionError('data touched')):
            with self.assertRaisesRegex(ValueError,'FINAL_OOS'):run(a)

    def test_est_edt_early_close_ranges(self):
        for day,offset,count in [('2023-03-10','-05:00',390),('2023-03-13','-04:00',390),('2023-11-24','-05:00',210)]:
            with self.subTest(day=day),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);prepare(root,[day])
                first=json.loads((root/'audit/symbol_sessions.jsonl').read_text().splitlines()[0])
                self.assertEqual(first['expectedMinutes'],count);self.assertTrue(first['firstRegularEnd'].endswith(offset))


class CausalSubsetTests(unittest.TestCase):
    def test_unused_missing_symbol_does_not_invalidate_other_symbol(self):
        rows,o,c=fixture();del rows['AAPL'][o+100*MINUTE]
        cfg=Config(**json.loads(CFG.read_text()));bot=Bot('b',buy_genome(cfg),0,[],'2024-06-03')
        old,_=evaluate_day([bot],rows,o,c,['TQQQ'],cfg)
        new,_=evaluate_day([bot],rows,o,c,['TQQQ'],cfg,data_policy='causal_available')
        self.assertFalse(daily_evaluation(bot,old[0],'2024-06-03',cfg)['evaluationValid'])
        self.assertTrue(daily_evaluation(bot,new[0],'2024-06-03',cfg)['evaluationValid'])
        self.assertTrue(new[0].trades)

    def test_available_policy_cannot_be_accidentally_applied_to_legacy_fitness(self):
        rows,o,c=fixture(count=65);cfg=Config();bot=Bot('b',buy_genome(cfg),0,[],'2024-06-03')
        with self.assertRaisesRegex(ValueError,'isolated'):evaluate_day([bot],rows,o,c,['TQQQ'],cfg,data_policy='causal_available')

    def test_held_gap_no_fabricated_fill_and_sticky_invalid_after_recovery(self):
        rows,o,c=fixture();missing=o+35*MINUTE;del rows['TQQQ'][missing]
        cfg=Config(**json.loads(CFG.read_text()));bot=Bot('b',buy_genome(cfg),0,[],'2024-06-03')
        accounts,_=evaluate_day([bot],rows,o,c,['TQQQ'],cfg,data_policy='causal_available');a=accounts[0]
        self.assertEqual(a.cancellations['HELD_SYMBOL_OBSERVATION_GAP'],1)
        self.assertFalse(daily_evaluation(bot,a,'2024-06-03',cfg)['evaluationValid'])
        for t in a.trades:
            self.assertNotEqual(t['entryTimestamp'],(missing+MINUTE).isoformat())
            self.assertNotEqual(t['exitTimestamp'],(missing+MINUTE).isoformat())
        self.assertTrue(a.trades) # recovery exit is observed, still not fitness eligible

    def test_future_missing_symbol_bar_does_not_remove_earlier_signal(self):
        rows,o,c=fixture();cfg=Config(**json.loads(CFG.read_text()));bot=Bot('b',buy_genome(cfg),0,[],'2024-06-03')
        a,_=evaluate_day([bot],rows,o,c,['TQQQ'],cfg,data_policy='causal_available')
        del rows['TQQQ'][o+300*MINUTE]
        b,_=evaluate_day([bot],rows,o,c,['TQQQ'],cfg,data_policy='causal_available')
        self.assertEqual(a[0].trades[0],b[0].trades[0])


class AvailableTrainingTests(unittest.TestCase):
    def test_ten_sessions_twenty_bots_two_generations_repeatable_and_matches_strict_when_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dates=list(exchange_sessions('2023-06-01','2023-06-14'));plan=prepare(root,dates)
            args=['--selection-plan',str(plan),'--smoke','--population','20','--seed','42','--results-dir',str(root/'results'),'--data-origin','synthetic']
            before={p.name:file_sha256(p) for p in (root/'data').iterdir()}
            with contextlib.redirect_stdout(io.StringIO()):a=main(args);b=main(args)
            info=json.loads((a/'run.json').read_text());self.assertEqual(info['completedTrainGenerations'],2);self.assertEqual(info['generation'],2)
            self.assertEqual(info['skippedGenerationAttempts'],0);self.assertEqual(info['selectedSessionCount'],10)
            self.assertTrue(info['baselineComparability']['dataSelectionComparable']);self.assertEqual(info['finalOosGate'],'CLOSED')
            self.assertEqual((a/'final_population.json').read_bytes(),(b/'final_population.json').read_bytes())
            with contextlib.redirect_stdout(io.StringIO()):
                strict=strict_main(['--data-dir',str(root/'data'),'--results-dir',str(root/'strict'),'--config',str(CFG),
                    '--symbols','AAPL,TQQQ','--from-date',dates[0],'--to-date',dates[-1],'--population','20','--seed','42','--provisional','--data-origin','synthetic'])
            old=json.loads((strict/'final_population.json').read_text());new=json.loads((a/'final_population.json').read_text())
            self.assertTrue(compare_conditions(info,strict,True)['sameConditions'])
            self.assertEqual([r['genome_hash'] for r in old],[r['genome_hash'] for r in new])
            for x,y in zip(old,new):
                self.assertAlmostEqual(x['lifetime_pnl'],y['lifetime_pnl']);self.assertEqual(x['lifetime_days'],y['lifetime_days'])
            self.assertEqual(before,{p.name:file_sha256(p) for p in (root/'data').iterdir()})

    def test_benchmark_break_discards_partial_window_without_lifetime_contamination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dates=list(exchange_sessions('2023-06-01','2023-06-14'))
            plan=prepare(root,dates,mutate=lambda d:remove_minutes(d/'QQQ.csv',dates[2]))
            with contextlib.redirect_stdout(io.StringIO()):
                out=main(['--selection-plan',str(plan),'--from-date',dates[0],'--to-date',dates[-1],
                    '--results-dir',str(root/'results'),'--population','20','--data-origin','synthetic'])
            info=json.loads((out/'run.json').read_text());self.assertEqual(info['completedTrainGenerations'],1)
            self.assertEqual(info['skippedGenerationAttempts'],1)
            self.assertEqual(json.loads((out/'used_sessions.json').read_text()),dates[3:8])
            excluded=[json.loads(x) for x in (out/'excluded_sessions.jsonl').read_text().splitlines()]
            self.assertEqual({r['date'] for r in excluded},set(dates[:3]+dates[8:]))
            self.assertFalse(info['baselineComparability']['dataSelectionComparable'])
            evaluated=json.loads((out/'last_evaluated_population.json').read_text())
            self.assertTrue(all(b['lifetime_days']==5 for b in evaluated))

    def test_runtime_held_gap_discards_whole_population_window_and_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dates=list(exchange_sessions('2023-06-01','2023-06-14'))
            plan=prepare(root,dates,mutate=lambda d:remove_minutes(d/'TQQQ.csv',dates[2],[dates[2]+'T10:06:00-04:00']))
            cfg=Config(**json.loads(CFG.read_text()));genome=buy_genome(cfg)
            weights=list(genome.weights);weights[POLICY_FEATURES.index('return_15m')]=1
            bot=Bot('b',replace(genome,weights=tuple(weights)),0,[],dates[0])
            with patch('research.evolution.run_v07_available.initial_population',return_value=[bot]),contextlib.redirect_stdout(io.StringIO()):
                out=main(['--selection-plan',str(plan),'--from-date',dates[0],'--to-date',dates[-1],
                    '--results-dir',str(root/'results'),'--population','1','--data-origin','synthetic'])
            info=json.loads((out/'run.json').read_text())
            self.assertEqual(info['skippedGenerationAttempts'],1)
            self.assertEqual(info['completedTrainGenerations'],1)
            self.assertEqual(json.loads((out/'used_sessions.json').read_text()),dates[3:8])
            failure=json.loads((out/('attempt_0000_'+dates[2])/'metrics.json').read_text())
            self.assertEqual(failure['bots'][0]['cancellations']['HELD_SYMBOL_OBSERVATION_GAP'],1)
            self.assertFalse(failure['bots'][0]['evaluationValid'])
            evaluated=json.loads((out/'last_evaluated_population.json').read_text())
            self.assertEqual(evaluated[0]['lifetime_days'],5)
            self.assertEqual(info['finalOosGate'],'CLOSED')

    def test_input_mutated_during_simulation_invalidates_result(self):
        from research.evolution import run_v07_available as runner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan=prepare(root,list(exchange_sessions('2023-06-01','2023-06-14')))
            original=runner.evaluate_day;once=[]
            def mutate(*args,**kw):
                result=original(*args,**kw)
                if not once:
                    with (root/'data/TQQQ.csv').open('a') as f:f.write('\n')
                    once.append(True)
                return result
            with patch.object(runner,'evaluate_day',side_effect=mutate),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):main(['--selection-plan',str(plan),'--smoke','--population','2','--results-dir',str(root/'results'),'--data-origin','synthetic'])
            info=json.loads(next((root/'results').glob('*/run.json')).read_text())
            self.assertEqual(info['phase'],'error');self.assertFalse(info['resultsValid'])
            self.assertIn('INPUT_HASH_CHANGED',info['error'])

    def test_full_annual_calendar_but_insufficient_trades_is_still_unverified(self):
        calendar=exchange_sessions('2024-01-01','2024-12-31');selected=list(calendar)
        coverage,sample,annual=validation_adequacy(calendar,'2024-01-01',selected,[],True,{'b':dict(trades=2)},30)
        self.assertTrue(coverage);self.assertFalse(sample);self.assertFalse(annual)
        self.assertFalse(validation_adequacy(calendar,'2024-01-01',selected[:-1],selected[-1:],True,{'b':dict(trades=100)},30)[2])

    def test_partial_holdout_stays_unverified_and_source_population_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dates=['2024-06-03','2024-06-04','2024-06-05'];plan=prepare(root,dates)
            c=Config(**json.loads(CFG.read_text()));source=root/'source';source.mkdir()
            bots=initial_population(c,random.Random(1),'2023-01-03')
            (source/'run.json').write_text(json.dumps(dict(runId='train',phase='completed',completedTrainGenerations=1,
                dataSelectionPolicy='V07_CAUSAL_AVAILABLE_PREFIX_V1',symbols=['AAPL','TQQQ'],config=c.snapshot())))
            (source/'final_population.json').write_text(json.dumps([b.record() for b in bots]));sha=file_sha256(source/'final_population.json')
            with contextlib.redirect_stdout(io.StringIO()):
                out=main(['--selection-plan',str(plan),'--source-run',str(source),'--from-date',dates[0],'--to-date',dates[-1],
                    '--results-dir',str(root/'results'),'--data-origin','synthetic'])
            summary=json.loads((out/'validation_summary.json').read_text());info=json.loads((out/'run.json').read_text())
            self.assertFalse(summary['annualValidationComplete']);self.assertFalse(summary['resultsValid'])
            self.assertEqual(info['evaluationStatus'],'PARTIAL_UNVERIFIED')
            self.assertTrue(all(b['validationReturn'] is None for b in summary['bots']))
            self.assertEqual(sha,file_sha256(source/'final_population.json'))

    def test_no_implicit_long_training_and_no_smoke_relaxation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan=prepare(root,list(exchange_sessions('2023-06-01','2023-06-07')))
            with self.assertRaisesRegex(SystemExit,'2'):
                with contextlib.redirect_stderr(io.StringIO()):main(['--selection-plan',str(plan),'--smoke'])
            a=parser().parse_args(['--selection-plan',str(plan)])
            with self.assertRaisesRegex(ValueError,'Explicit bounded'):run(a)


if __name__=='__main__':unittest.main()
