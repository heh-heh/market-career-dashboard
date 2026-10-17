"""Small synthetic V081 breeding/data/capacity regressions. No EC2 or market access."""
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
from research.evolution.population import initial_population,reproduce
from research.evolution.survival_breeding import actual_mutation
from research.evolution.adaptive_capacity import ResourceMonitor,recommend,next_population
from research.evolution.v081_diagnostics import smoke_window,diagnose
from research.evolution.run_v081 import main
from research.evolution.run_v07_available import parser,run
from research.evolution.audit_v07_data import resource_preflight
from research.backtest_v4_strategies import exchange_sessions
from research.test_evolution_available_data import prepare,remove_minutes
from research.test_evolution_v08_profit_only import records

CFG=Path(__file__).parent/'evolution/v081_adaptive_config.json'


def config():return Config(**json.loads(CFG.read_text()))


def evaluations(bots,n=0):
    rows=records(bots,n)
    for i,e in enumerate(rows):e.update(windowMaxDrawdown=.1+i*.001,windowPnl=(1. if i<n else -1.-i),dailyPnl=(1. if i<n else -1.-i))
    return rows


def measurement(**extra):
    return dict(samples=10,guardPassed=True,peak=dict(cpuPct=40,memoryPct=35,swapPct=0,processRssMb=50),maxSessionSeconds=1,
                meanSessionSeconds=.5,completedGenerations=1,**extra)


class V081BreedingTests(unittest.TestCase):
    def setUp(self):self.cfg=config();self.bots=initial_population(self.cfg,random.Random(1),'2022-01-03')
    def breed(self,rows,cfg=None):return reproduce(self.bots,rows,cfg or self.cfg,random.Random(2),1,'2022-01-10')

    def test_one_profit_never_uses_losers(self):
        children,m=self.breed(evaluations(self.bots,1))
        self.assertEqual(m['parentPoolSize'],1);self.assertFalse(m['preservationParents'])
        self.assertTrue(all(c.parent_ids==[self.bots[0].bot_id] for c in children));self.assertEqual(m['crossoverCount'],0)
    def test_all_profit_candidates_included(self):
        for n in (5,30):
            c,m=self.breed(evaluations(self.bots,n));self.assertEqual(m['parentPoolSize'],n)
            self.assertEqual(set(m['parentIds']),{b.bot_id for b in self.bots[:n]})
            self.assertTrue(all(len(c.parent_ids)==len(set(c.parent_ids)) for c in c))
    def test_zero_profit_preservation_two_and_real_mutation_all_children(self):
        rows=evaluations(self.bots);before=[b.record() for b in self.bots]
        # Force normal mutation to do nothing: recovery must still perturb a gene.
        c,m=self.breed(rows,replace(self.cfg,mutation_probability=0,mutation_strength=0))
        self.assertEqual(len(c),100);self.assertEqual(m['parentIds'],[self.bots[0].bot_id,self.bots[1].bot_id])
        self.assertEqual(m['eliteCount'],0);self.assertEqual(m['mutatedChildren'],100)
        self.assertTrue(all(x.mutation_count>=1 for x in c));self.assertFalse(m['randomRecovery'])
        self.assertTrue(all(set(x.parent_ids)==set(m['parentIds']) for x in c))
        self.assertEqual(before,[b.record() for b in self.bots])
        c2,m2=self.breed(rows,replace(self.cfg,mutation_probability=0,mutation_strength=0))
        self.assertEqual([x.record() for x in c],[x.record() for x in c2]);self.assertEqual(m,m2)
    def test_preservation_ties_pnl_mdd_fitness_id_and_dynamic_population(self):
        rows=evaluations(self.bots)
        for e in rows:e.update(windowPnl=-1,windowMaxDrawdown=.1,fitness=.5)
        rows[4]['windowMaxDrawdown']=.01;rows[5]['windowMaxDrawdown']=.01;rows[5]['fitness']=.8
        c,m=self.breed(rows,replace(self.cfg,population_size=225))
        self.assertEqual(len(c),225);self.assertEqual(m['parentIds'],[self.bots[5].bot_id,self.bots[4].bot_id])
    def test_single_valid_fallback_no_self_crossover_invalid_all_blocks(self):
        rows=evaluations(self.bots)
        for e in rows[1:]:e['evaluationValid']=False
        c,m=self.breed(rows);self.assertEqual(m['parentPoolSize'],1);self.assertEqual(m['crossoverCount'],0)
        self.assertTrue(all(x.parent_ids==[self.bots[0].bot_id] and x.mutation_count>0 for x in c))
        rows[0]['windowComplete']=False
        with self.assertRaisesRegex(ValueError,'NO_VALID_FIVE_SESSION'):self.breed(rows)
    def test_v08_random_recovery_and_v07_gates_not_changed(self):
        rows=evaluations(self.bots)
        for policy in ('v08_profit_only','legacy'):
            _,m=self.breed(rows,replace(self.cfg,selection_policy=policy));self.assertTrue(m['randomRecovery'])


class V081CapacityTests(unittest.TestCase):
    def test_stepwise_repeated_only_no_unmeasured_extrapolation(self):
        trials=[]
        for n in (100,150,225):trials.append(dict(population=n,warmupValid=True,measuredRuns=[measurement(),measurement()]))
        self.assertEqual(recommend(trials,1000,900)['recommendedPopulation'],225)
        bad=measurement();bad['peak']['memoryPct']=70
        trials.append(dict(population=337,warmupValid=True,measuredRuns=[measurement(),bad]))
        self.assertEqual(recommend(trials,1000,900)['recommendedPopulation'],225)
        self.assertEqual(next_population(1000,1000),None)
    def test_unsafe_100_insufficient_samples_or_invalid_generation_blocks(self):
        for key,value in [('samples',1),('completedGenerations',0),('guardPassed',False),('maxSessionSeconds',1000)]:
            r=measurement();r[key]=value
            self.assertIsNone(recommend([dict(population=100,warmupValid=True,measuredRuns=[measurement(),r])],1000,900)['recommendedPopulation'])
    def test_resource_monitor_bounds_stops_and_no_os_commands(self):
        with patch('research.evolution.adaptive_capacity.psutil.cpu_percent',return_value=1):
            m=ResourceMonitor();m.sample();m.sample();self.assertGreaterEqual(m.report()['samples'],2)
        m.failure='CPU';self.assertRaisesRegex(ValueError,'RESOURCE_PAUSED',m.check)
    def test_calibration_repeats_isolated_workloads_and_certificate_integrity(self):
        import research.evolution.calibrate_v081 as c
        from research.backtest_intraday_v4 import atomic_json
        from research.backtest_v4_strategies import file_sha256
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp);data=root/'data';data.mkdir();plan=root/'plan.json';plan.write_text('{}')
            dates=list(exchange_sessions('2023-06-01','2023-06-07'))
            evidence=dict(dataRoot=str(data),sessions=[dict(sessionDate=d,eligible=True,partialPrefixSymbols=[]) for d in dates])
            calls=[]
            def trial(n,index,out,args,window):
                calls.append((n,index));folder=out/f'{n}-{index}';folder.mkdir()
                report=measurement();report.pop('completedGenerations')
                report['peak']['processRssMb']=50+n*.05
                source=folder/'run.json'
                info=dict(config=replace(Config(**json.loads(args.config.read_text())),population_size=n,adaptive_resources=False).snapshot(),phase='completed',population=n,completedTrainGenerations=1,
                    resourceReport=report,sourcePlanSha256=file_sha256(plan),codeHashes=c.code_hashes())
                atomic_json(source,info)
                return dict(report,completedGenerations=1,runFile=str(source),runSha256=file_sha256(source))
            with patch.object(c,'verify_plan',return_value=(evidence,{})),patch.object(c,'run_trial',side_effect=trial):
                out=c.main(['--selection-plan',str(plan),'--out',str(root/'capacity'),'--max-population','150'])
            self.assertEqual(calls,[(100,'warmup'),(100,'measured-0'),(100,'measured-1'),(150,'warmup'),(150,'measured-0'),(150,'measured-1')])
            cert=c.verify_capacity(out,plan,out.parent/'trial_config.json',150)
            self.assertEqual(cert['recommendation']['recommendedPopulation'],150)
            cert['trials'][0]['measuredRuns'][0]['peak']['memoryPct']=1
            out.write_text(json.dumps(cert))
            with self.assertRaisesRegex(ValueError,'MEASUREMENT_MISMATCH'):c.verify_capacity(out,plan,out.parent/'trial_config.json',150)

    def test_explicit_extra_ebs_and_wrong_mount_rejected(self):
        def storage(path):
            mount='/mnt/research-extra' if str(path).startswith('/mnt/research-extra') else '/'
            return dict(mount=dict(target=mount,source='/dev/extra' if mount!='/' else '/dev/root'))
        with patch('research.evolution.audit_v07_data.storage_info',side_effect=storage):
            self.assertTrue(resource_preflight('/mnt/research-extra/results',require_research_mount=True,research_mount='/mnt/research-extra')['researchMountSeparate'])
            with self.assertRaisesRegex(ValueError,'MOUNT_REQUIRED'):
                resource_preflight('/mnt/research-extra/results',require_research_mount=True)


class V081PilotTests(unittest.TestCase):
    def test_audited_prefix_eligible_not_equal_complete_window(self):
        dates=list(exchange_sessions('2023-06-01','2023-06-21'))
        sessions=[dict(sessionDate=d,eligible=True,partialPrefixSymbols=['AAPL'] if i<7 else []) for i,d in enumerate(dates)]
        w=smoke_window(dict(sessions=sessions));self.assertEqual(w['dates'],dates[7:12]);self.assertEqual(w['sessions'],5)
        self.assertIn('EX_POST_COMPLETE_DAY',w['selectionBiasWarnings'][-1])
        with self.assertRaisesRegex(ValueError,'NO_VERIFIED_FIVE'):smoke_window(dict(sessions=sessions[:8]))
    def test_failure_classification_bounded_and_no_artifact_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'run.json').write_text(json.dumps(dict(completedTrainGenerations=0)))
            line=json.dumps(dict(reason='COHORT_HELD_SYMBOL_OBSERVATION_GAP',dates=['2022-01-03']))+'\n'
            (root/'windows.jsonl').write_text(line*30);d=diagnose(root)
            self.assertEqual(d['causes']['windows.jsonl:COHORT_HELD_SYMBOL_OBSERVATION_GAP'],30)
            self.assertEqual(len(d['samples']['windows.jsonl:COHORT_HELD_SYMBOL_OBSERVATION_GAP']),5)
            self.assertEqual((root/'windows.jsonl').read_text(),line*30)
    def test_100_bot_pilot_valid_generation_with_checkpoint_separate_from_v08(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp);dates=list(exchange_sessions('2023-06-01','2023-06-14'));plan=prepare(root,dates)
            from research.evolution.run_v08_profit_only import main as v08
            pressure=dict(cpuPct=1,memoryPct=1,swapPct=0,processRssMb=1)
            with patch('research.evolution.run_v07_available.sample_resources',return_value=pressure),patch('psutil.cpu_percent',return_value=1),patch.object(ResourceMonitor,'start',lambda m:m),patch.object(ResourceMonitor,'check'),patch.object(ResourceMonitor,'report',return_value=measurement()):
                out=main(['--selection-plan',str(plan),'--pilot','--results-dir',str(root/'v081'),'--data-origin','synthetic'])
                old=v08(['--selection-plan',str(plan),'--smoke','--results-dir',str(root/'v08'),'--data-origin','synthetic'])
            info=json.loads((out/'run.json').read_text());self.assertGreaterEqual(info['completedTrainGenerations'],1)
            self.assertEqual(info['version'],'V081_SURVIVAL_1');self.assertEqual(info['population'],100)
            self.assertTrue((out/'checkpoint.json').is_file());self.assertTrue((out/'pilot_selection.json').is_file())
            self.assertNotEqual(old.parent,out.parent)
            self.assertEqual(json.loads((old/'run.json').read_text())['version'],'V08_PROFIT_ONLY_1')
            self.assertEqual(info['finalOosGate'],'CLOSED')
    def test_zero_generation_mechanism_before_after_window_discovery_same_data(self):
        from research.test_evolution_v0 import buy_genome
        from research.evolution.features import POLICY_FEATURES
        from research.evolution.population import Bot
        from research.backtest_v4_strategies import file_sha256
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp);dates=list(exchange_sessions('2023-06-01','2023-06-23'))
            def gaps(data):
                for d in dates[:7]:remove_minutes(data/'TQQQ.csv',d,[d+'T10:06:00-04:00'])
            plan=prepare(root,dates,symbols=('TQQQ',),mutate=gaps)
            cfg=config();genome=buy_genome(cfg);weights=list(genome.weights);weights[POLICY_FEATURES.index('return_15m')]=1
            genome=replace(genome,weights=tuple(weights))
            def bots(*args):return [Bot(f'b{i:03d}',genome,0,[],dates[0]) for i in range(100)]
            before={p.name:file_sha256(p) for p in (root/'data').iterdir()}
            pressure=dict(cpuPct=1,memoryPct=1,swapPct=0,processRssMb=1)
            args=parser().parse_args(['--selection-plan',str(plan),'--config',str(CFG.with_name('v08_profit_only_config.json')),'--smoke','--population','100','--results-dir',str(root/'old'),'--data-origin','synthetic'])
            with patch('research.evolution.run_v07_available.initial_population',side_effect=bots),patch('research.evolution.run_v07_available.sample_resources',return_value=pressure),patch('psutil.cpu_percent',return_value=1),patch.object(ResourceMonitor,'start',lambda m:m),patch.object(ResourceMonitor,'check'),patch.object(ResourceMonitor,'report',return_value=measurement()):
                old=run(args)
                new=main(['--selection-plan',str(plan),'--pilot','--results-dir',str(root/'new'),'--data-origin','synthetic'])
            a=json.loads((old/'run.json').read_text());b=json.loads((new/'run.json').read_text())
            self.assertEqual(a['completedTrainGenerations'],0);self.assertEqual(a['skippedGenerationAttempts'],7)
            self.assertGreaterEqual(b['completedTrainGenerations'],1)
            self.assertEqual(diagnose(old)['causes']['windows.jsonl:COHORT_HELD_SYMBOL_OBSERVATION_GAP'],7)
            self.assertEqual(before,{p.name:file_sha256(p) for p in (root/'data').iterdir()})

    def test_2026_block_before_plan_or_data_access(self):
        with self.assertRaisesRegex(ValueError,'OOS|2026'):
            main(['--selection-plan','/not-read','--from-date','2026-01-01','--to-date','2026-01-02'])


if __name__=='__main__':unittest.main()
