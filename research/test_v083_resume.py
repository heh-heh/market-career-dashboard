"""Tiny deterministic committed-generation recovery; no AWS or real data."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.backtest_v4_strategies import exchange_sessions,file_sha256
from research.evolution.run_v083 import CONFIG
from research.evolution.run_v07_available import parser,run
from research.evolution import v083_resume
from research.test_evolution_available_data import prepare


class ResumeTests(unittest.TestCase):
    def test_interrupted_partial_day_restores_rng_ledger_and_exact_genomes(self):
        from research.evolution.engine import evaluate_day
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);days=list(exchange_sessions('2023-06-01','2023-06-14'))
            plan=prepare(root,days)
            def arguments(output):
                a=parser().parse_args(['--selection-plan',str(plan),'--config',str(CONFIG),'--population','2',
                    '--from-date',days[0],'--to-date',days[-1],'--results-dir',str(root/output),'--data-origin','synthetic'])
                a.v083_mode=True
                return a
            with patch('research.evolution.run_v07_available.resource_preflight',return_value={}),contextlib.redirect_stdout(io.StringIO()):
                uninterrupted=run(arguments('full'))
                calls=0
                def interrupt(*args,**kwargs):
                    nonlocal calls
                    calls+=1
                    if calls==7:raise RuntimeError('SYNTHETIC_SPOT_INTERRUPTION')
                    return evaluate_day(*args,**kwargs)
                with patch('research.evolution.run_v07_available.evaluate_day',side_effect=interrupt):
                    with self.assertRaisesRegex(RuntimeError,'SYNTHETIC_SPOT_INTERRUPTION'):run(arguments('interrupted'))
                old=next((root/'interrupted').iterdir())
                oldhash={p.relative_to(old).as_posix():file_sha256(p) for p in old.rglob('*') if p.is_file()}
                a=arguments('resumed');a.resume_from=old
                resumed=run(a)
            self.assertEqual(oldhash,{p.relative_to(old).as_posix():file_sha256(p) for p in old.rglob('*') if p.is_file()})
            for name in ['final_population.json','last_evaluated_population.json','window_0000.json','window_0001.json','lineage.jsonl','used_sessions.json','evaluation_sessions.json']:
                self.assertEqual(file_sha256(uninterrupted/name),file_sha256(resumed/name),name)
            for p in uninterrupted.glob('attempt_*/*'):
                if p.is_file():self.assertEqual(file_sha256(p),file_sha256(resumed/p.relative_to(uninterrupted)),str(p))
            states=[json.loads(x) for x in (resumed/'generations.jsonl').read_text().splitlines()]
            self.assertEqual([s['date'] for s in states],days)
            info=json.loads((resumed/'run.json').read_text())
            self.assertEqual(info['completedTrainGenerations'],2)
            self.assertEqual(info['totalProcessedSessions'],10)
            cp=json.loads((resumed/'checkpoint.json').read_text())
            kwargs=dict(code_hashes=cp['runInfo']['codeHashes'],config=cp['runInfo']['config'],plan_hash=cp['sourcePlanSha256'],
                first=days[0],last=days[-1],seed=42,population=2)
            v083_resume.verify(resumed,**kwargs)
            with self.assertRaisesRegex(ValueError,'CODEHASHES'):v083_resume.verify(resumed,**dict(kwargs,code_hashes={}))
            with self.assertRaisesRegex(ValueError,'CONFIG'):v083_resume.verify(resumed,**dict(kwargs,config={}))
            with self.assertRaisesRegex(ValueError,'SOURCEPLANSHA256'):v083_resume.verify(resumed,**dict(kwargs,plan_hash='changed'))
            target=resumed/'window_0000.json';target.write_text(target.read_text()+' ')
            with self.assertRaisesRegex(ValueError,'INTEGRITY'):v083_resume.verify(resumed,**kwargs)

    def test_traversal_rejected_and_legacy_checkpoint_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'checkpoint.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'UNSUPPORTED'):
                v083_resume.verify(root,code_hashes={},config={},plan_hash='',first='',last='',seed=0,population=2)

    def test_generation_zero_and_partial_jsonl_append_are_commit_safe(self):
        import random
        from dataclasses import replace
        from research.evolution.config import Config
        from research.evolution.population import initial_population
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.mkdir();target=Path(tmp)/'restored';target.mkdir()
            cfg=replace(Config(**json.loads(CONFIG.read_text())),population_size=2)
            bots=initial_population(cfg,random.Random(42),'2023-06-01')
            info=dict(codeHashes={},config=cfg.snapshot(),sourcePlanSha256='plan',fromDate='2023-06-01',toDate='2023-06-14',
                seed=42,population=2,version='V083_TOP_TWO_1',finalOosGate='CLOSED')
            # A crash can leave a partially written NEXT record. Prefix remains valid.
            (source/'windows.jsonl').write_bytes(b'{"committed": true}\n')
            payload=dict(completedGeneration=0,throughDate=None,population=[b.record() for b in bots],rngState=random.Random(42).getstate(),
                sourcePlanSha256='plan',finalOosGate='CLOSED',runInfo=info,state=dict(selected=[]))
            v083_resume.commit(source,payload)
            with (source/'windows.jsonl').open('ab') as f:f.write(b'{"partial":')
            kwargs=dict(code_hashes={},config=cfg.snapshot(),plan_hash='plan',first='2023-06-01',last='2023-06-14',seed=42,population=2)
            src,cp=v083_resume.verify(source,**kwargs)
            v083_resume.restore_artifacts(src,cp,target)
            self.assertEqual((target/'windows.jsonl').read_bytes(),b'{"committed": true}\n')
            cp['artifacts'][0]['path']='../escape'
            (source/'checkpoint.json').write_text(json.dumps(cp))
            with self.assertRaisesRegex(ValueError,'PATH_INVALID'):v083_resume.verify(source,**kwargs)
