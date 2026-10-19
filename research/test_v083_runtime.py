"""Separate code-only runtime, immutable original bundle: offline tests only."""
import contextlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from research.spot import v083_spot_worker as worker
from research.spot.prepare_v083_runtime import build
from research.spot.worker_storage import digest,extract_archive


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.addCleanup(self.temp.cleanup)
        self.oldjob='v083-'+'a'*16;self.newjob='v083-'+'b'*16
        self.oldcommit='a'*40;self.newcommit='b'*40

    def fixture(self):
        bundle=self.root/'bundle'
        for relative in ('source/research/evolution/run_v083.py','source/research/evolution/calibrate_v083.py',
                         'selection/selection_plan.json','data/SPY.csv'):
            p=bundle/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}' if p.suffix=='.json' else '# old immutable\n')
        payload={p.relative_to(bundle).as_posix():digest(p) for p in bundle.rglob('*') if p.is_file()}
        meta={'version':2,'job_id':self.oldjob,'source_commit':self.oldcommit,'population_bounds':[2,50],
              'finalOosGate':'CLOSED','train_dates':['2022-01-01','2023-12-31'],'payload_files':payload,
              'selection_plan_sha256':digest(bundle/'selection/selection_plan.json')}
        (bundle/'bundle_manifest.json').write_text(json.dumps(meta))
        runtime=self.root/'runtime';files={}
        for relative in ('source/research/evolution/run_v083.py','source/research/evolution/calibrate_v083.py',
                         'source/research/spot/v083_spot_worker.py','source/research/spot/worker_storage.py'):
            p=runtime/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# new approved runtime\n');files[relative]=digest(p)
        (runtime/'runtime_manifest.json').write_text(json.dumps({'version':1,'source_commit':self.newcommit,
              'payload_files':files,'researchOnly':True,'finalOosGate':'CLOSED'}))
        return bundle,runtime

    def test_runtime_validated_without_changing_original_bundle(self):
        bundle,runtime=self.fixture();before={p:digest(p) for p in bundle.rglob('*') if p.is_file()}
        worker.validate_bundle(bundle,self.oldjob,20,self.oldcommit)
        repo,meta=worker.validate_runtime(runtime,self.newcommit)
        self.assertEqual(repo,runtime/'source');self.assertEqual(meta['source_commit'],self.newcommit)
        self.assertEqual(before,{p:digest(p) for p in before})
        with self.assertRaisesRegex(ValueError,'MANIFEST_MISMATCH'):
            worker.validate_bundle(bundle,self.newjob,20,self.newcommit)

    def test_runtime_hash_and_code_only_gate(self):
        _,runtime=self.fixture()
        p=runtime/'source/research/evolution/run_v083.py';p.write_text('# mutation')
        with self.assertRaisesRegex(ValueError,'PAYLOAD_HASH'):worker.validate_runtime(runtime,self.newcommit)
        _,runtime=self.fixture();p=runtime/'data/SPY.csv';p.parent.mkdir();p.write_text('2026 forbidden')
        manifest=runtime/'runtime_manifest.json';meta=json.loads(manifest.read_text());meta['payload_files']['data/SPY.csv']=digest(p);manifest.write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError,'CODE_ONLY'):worker.validate_runtime(runtime,self.newcommit)

    def test_new_runtime_source_requires_explicit_runtime(self):
        self.fixture()
        with self.assertRaisesRegex(ValueError,'NEW_SOURCE_REQUIRES_SEPARATE_RUNTIME'):
            worker.run(workspace=self.root,job_id=self.newjob,population=20,source_commit=self.newcommit,
                       bundle_job_id=self.oldjob,bundle_source_commit=self.oldcommit)

    def test_same_bundle_reused_with_new_job_and_distinct_runtime(self):
        bundle,runtime=self.fixture()
        before={p:digest(p) for p in bundle.rglob('*') if p.is_file()}
        store=unittest.mock.MagicMock();store.prefix='v083/runs/'+self.newjob
        def stop_execute(cmd,**kwargs):
            self.assertIn(str(runtime/'source/research/evolution/calibrate_v083.py'),cmd)
            self.assertIn(str(bundle/'selection/selection_plan.json'),cmd)
            raise ValueError('TEST_STOP_BEFORE_TRAIN')
        with patch('research.evolution.available_data.verify_plan',return_value=({'finalOosGate':'CLOSED','sessions':[]},{'SPY':bundle/'data/SPY.csv'})),patch.object(worker,'execute',side_effect=stop_execute):
            with self.assertRaisesRegex(ValueError,'TEST_STOP_BEFORE_TRAIN'):
                worker.run(workspace=self.root,job_id=self.newjob,population=20,source_commit=self.newcommit,
                           bundle_job_id=self.oldjob,bundle_source_commit=self.oldcommit,runtime_dir=runtime,store=store)
        proof=json.loads((self.root/'output/source_provenance.json').read_text())
        self.assertEqual(proof['sourceCommit'],self.newcommit);self.assertEqual(proof['bundleSourceCommit'],self.oldcommit)
        self.assertEqual(before,{p:digest(p) for p in before})

    def test_offline_archive_clean_commit_rejects_dirty_and_no_overwrite(self):
        repo=self.root/'repo';repo.mkdir()
        subprocess.run(['git','init','-q',str(repo)],check=True)
        for relative in ('research/evolution/run_v083.py','research/evolution/calibrate_v083.py',
                         'research/spot/v083_spot_worker.py','research/spot/worker_storage.py'):
            p=repo/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# pinned\n')
        subprocess.run(['git','-C',str(repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(repo),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture'],check=True)
        archive=self.root/'runtime.tar.gz';result=build(repo=repo,output=archive)
        self.assertEqual(result['sha256'],digest(archive));extract_archive(archive,self.root/'extract')
        worker.validate_runtime(self.root/'extract/runtime',result['source_commit'])
        with self.assertRaisesRegex(ValueError,'ALREADY_EXISTS'):build(repo=repo,output=archive)
        (repo/'research/evolution/run_v083.py').write_text('# dirty\n')
        with self.assertRaisesRegex(ValueError,'NOT_CLEAN_AND_PINNED'):build(repo=repo,output=self.root/'dirty.tar.gz')


if __name__=='__main__':unittest.main()
