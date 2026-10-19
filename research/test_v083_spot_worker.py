"""Offline transport/worker tests. No credentials, SDK clients or compute."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from research.spot import v083_spot_worker as worker
from research.spot.worker_storage import DurableStore, digest, extract_archive


class MemoryS3:
    def __init__(self): self.objects = {}; self.serial = 0
    def put_object(self, **kw):
        self.serial += 1; v = str(self.serial)
        raw = kw['Body'].read() if hasattr(kw['Body'], 'read') else kw['Body']
        self.objects[(kw['Key'], v)] = (raw, kw['Metadata'])
        return {'VersionId': v}
    def head_object(self, **kw):
        raw, metadata = self.objects[(kw['Key'], kw['VersionId'])]
        return {'ContentLength': len(raw), 'Metadata': metadata}
    def get_object(self, **kw):
        raw, _ = self.objects[(kw['Key'], kw['VersionId'])]
        return {'Body': io.BytesIO(raw)}


class SpotWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.job = 'v083-' + 'a' * 16
        self.store = DurableStore(MemoryS3(), 'reviewed-bucket', 'v083/runs/' + self.job)

    def archive(self, name, payload=b'ok', type=None):
        path = self.root / 'test.tar.gz'
        with tarfile.open(path, 'w:gz') as t:
            member = tarfile.TarInfo(name); member.size = len(payload)
            if type: member.type = type; member.linkname = '/etc/passwd'; member.size = 0
            t.addfile(member, io.BytesIO(payload) if not type else None)
        return path

    def test_bounded_extraction_and_no_overwrite(self):
        a = self.archive('bundle/data/TQQQ.csv')
        extract_archive(a, self.root / 'safe')
        self.assertEqual((self.root / 'safe/bundle/data/TQQQ.csv').read_bytes(), b'ok')
        with self.assertRaises(FileExistsError): extract_archive(a, self.root / 'safe')
        with self.assertRaisesRegex(ValueError, 'LIMIT'): extract_archive(a, self.root / 'small', max_bytes=1)

    def test_traversal_secret_and_symlink_rejected(self):
        for name, kind in [('../outside', None), ('bundle/.env', None), ('bundle/creds', tarfile.SYMTYPE), ('/absolute', None)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                extract_archive(self.archive(name, type=kind), self.root / 'target')

    def test_versioned_manifest_roundtrip_and_duplicate_looking_artifacts(self):
        src = self.root / 'src'; src.mkdir()
        (src / 'a.json').write_text('{"value":1}')
        (src / 'b.json').write_text('{"value":1}')
        ref = self.store.snapshot(src, phase='completed', job_id=self.job, source_commit='a'*40)
        manifest = self.store.restore(ref, self.root / 'restore')
        self.assertEqual(len(manifest['files']), 2)
        self.assertEqual((self.root / 'restore/a.json').read_bytes(), (src / 'a.json').read_bytes())
        self.assertEqual(manifest['finalOosGate'], 'CLOSED')

    def test_corrupt_committed_object_rejected(self):
        src = self.root / 'src'; src.mkdir(); (src / 'data.json').write_text('{}')
        ref = self.store.snapshot(src, phase='completed', job_id=self.job, source_commit='a'*40)
        item = ref['manifest']['files'][0]
        self.store.client.objects[(item['key'], item['versionId'])] = (b'xx', {'sha256':item['sha256']})
        with self.assertRaisesRegex(ValueError, 'HASH'): self.store.restore(ref, self.root / 'restore')

    def test_versioning_required_and_upload_failure(self):
        src = self.root / 'src'; src.mkdir(); (src / 'data.json').write_text('{}')
        with patch.object(self.store.client, 'put_object', return_value={}):
            with self.assertRaisesRegex(ValueError, 'VERSIONING_REQUIRED'):
                self.store.snapshot(src, phase='completed', job_id=self.job, source_commit='a'*40)
        with patch.object(self.store.client, 'put_object', side_effect=OSError('offline')):
            with self.assertRaises(OSError): self.store.snapshot(src, phase='completed', job_id=self.job, source_commit='a'*40)

    def test_checkpoint_copies_only_committed_prefix(self):
        src = self.root / 'run-id'; src.mkdir()
        raw = b'{"day":1}\n'
        (src / 'generations.jsonl').write_bytes(raw + b'{"partial":true}\n')
        checkpoint = {'resumeVersion':'V083_RESUME_1','commitState':'COMMITTED','artifacts':[
            {'path':'generations.jsonl','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'appendOnly':True}]}
        (src / 'checkpoint.json').write_text(json.dumps(checkpoint))
        ref = self.store.checkpoint(src, job_id=self.job, source_commit='a'*40)
        self.store.restore(ref, self.root / 'restore')
        self.assertEqual((self.root / 'restore/run-id/generations.jsonl').read_bytes(), raw)
        self.assertIn(b'partial', (src / 'generations.jsonl').read_bytes())

    def test_checkpoint_hash_mismatch_fails(self):
        src = self.root / 'run'; src.mkdir(); (src / 'data.json').write_text('{}')
        (src / 'checkpoint.json').write_text(json.dumps({'resumeVersion':'V083_RESUME_1','commitState':'COMMITTED',
            'artifacts':[{'path':'data.json','bytes':2,'sha256':'0'*64}]}))
        with self.assertRaisesRegex(ValueError,'LEDGER_HASH'):
            self.store.checkpoint(src, job_id=self.job, source_commit='a'*40)

    def test_worker_bundle_verifies_every_payload_before_import(self):
        bundle = self.root / 'bundle'; source = bundle / 'source/research/evolution'; source.mkdir(parents=True)
        (source / 'run_v083.py').write_text('# pinned')
        (source / 'calibrate_v083.py').write_text('# pinned')
        (bundle / 'selection').mkdir(); (bundle / 'selection/selection_plan.json').write_text('{}')
        payload = {p.relative_to(bundle).as_posix():digest(p) for p in bundle.rglob('*') if p.is_file()}
        meta={'version':2,'job_id':self.job,'source_commit':'a'*40,'population_bounds':[2,50],
              'finalOosGate':'CLOSED','train_dates':['2022-01-01','2023-12-31'],
              'payload_files':payload,'selection_plan_sha256':digest(bundle/'selection/selection_plan.json')}
        (bundle / 'bundle_manifest.json').write_text(json.dumps(meta))
        worker.validate_bundle(bundle,self.job,50,'a'*40)
        (source / 'run_v083.py').write_text('raise RuntimeError("must not import")')
        with self.assertRaisesRegex(ValueError,'PAYLOAD_HASH'):
            worker.validate_bundle(bundle,self.job,50,'a'*40)

    def test_execute_text_log_and_error_status(self):
        import sys
        with self.assertRaisesRegex(ValueError,'FAILED_2'):
            worker.execute([sys.executable,'-c','raise SystemExit(2)'],cwd=self.root,
                           log_file=self.root/'test.log',status_file=self.root/'status.json',stage='train')
        self.assertEqual(json.loads((self.root/'status.json').read_text())['phase'],'failed')

    def test_timeout_and_interruption_stop_only_owned_child(self):
        import sys
        for kwargs, reason in [({'deadline':0},'MAX_RUNTIME'),({'interrupted':lambda:True},'INTERRUPTION')]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError,reason):
                worker.execute([sys.executable,'-c','import time; time.sleep(60)'],cwd=self.root,
                               log_file=self.root/'test.log',status_file=self.root/'status.json',stage='train',**kwargs)

    def test_one_shot_workflow_uploads_checkpoint_then_completion(self):
        def fake_execute(cmd, **kw):
            if kw['stage'] == 'calibrate':
                path=self.root/'output/capacity/measured-run';path.mkdir(parents=True)
                (path/'capacity.json').write_text('{"status":"MEASURED_SAFE"}')
            else:
                self.assertEqual(Path(cmd[cmd.index('--capacity-report')+1]),self.root/'output/capacity/measured-run/capacity.json')
                path=self.root/'output/train/run-0001';path.mkdir(parents=True)
                (path/'status.json').write_text(json.dumps({'completedTrainGenerations':2,'meanWindowReturn':.01}))
                (path/'checkpoint.json').write_text(json.dumps({'resumeVersion':'V083_RESUME_1',
                    'commitState':'COMMITTED','artifacts':[]}))
            kw['observe']()
        with patch.object(worker,'validate_bundle',return_value={}), \
             patch('research.evolution.available_data.verify_plan',return_value=({'finalOosGate':'CLOSED','sessions':[]},{'SPY':Path('unused')})), \
             patch.object(worker,'execute',side_effect=fake_execute):
            ref=worker.run(workspace=self.root,job_id=self.job,population=2,source_commit='a'*40,store=self.store)
        phases=[]
        for (key,version),(raw,meta) in self.store.client.objects.items():
            if '/commits/' in key: phases.append(json.loads(raw)['phase'])
        self.assertIn('checkpoint',phases);self.assertIn('completed',phases)
        latest=[json.loads(raw) for (key,v),(raw,_) in self.store.client.objects.items() if key.endswith('/live_status.json')][-1]
        self.assertEqual(latest['phase'],'completed');self.assertEqual(latest['job_id'],self.job)
        self.assertIsNotNone(latest['checkpoint']);self.assertIn('versionId',ref)
        self.assertEqual(latest['training']['completedTrainGenerations'],2)
        self.assertEqual(latest['training']['meanWindowReturn'],.01)

    def test_workflow_error_preserves_local_and_uploads_failure(self):
        with patch.object(worker,'validate_bundle',return_value={}), \
             patch('research.evolution.available_data.verify_plan',return_value=({'finalOosGate':'CLOSED','sessions':[]},{'SPY':Path('unused')})), \
             patch.object(worker,'execute',side_effect=ValueError('TEST_TRAIN_FAILURE')):
            with self.assertRaisesRegex(ValueError,'TEST_TRAIN_FAILURE'):
                worker.run(workspace=self.root,job_id=self.job,population=2,source_commit='a'*40,store=self.store)
        self.assertEqual(json.loads((self.root/'output/final_status.json').read_text())['phase'],'failed')
        latest=[json.loads(raw) for (key,v),(raw,_) in self.store.client.objects.items() if key.endswith('/live_status.json')][-1]
        self.assertEqual(latest['phase'],'failed')

    def test_imdsv2_interruption_notice_uses_token_no_broker(self):
        notice=worker.InterruptionNotice()
        token=unittest.mock.MagicMock();token.__enter__.return_value.read.return_value=b'private-imds-token'
        action=unittest.mock.MagicMock();action.__enter__.return_value.read.return_value=b'{"action":"terminate"}'
        with patch('urllib.request.urlopen',side_effect=[token,action]) as opened:
            self.assertTrue(notice())
        self.assertEqual(opened.call_args_list[0].args[0].method,'PUT')
        self.assertIn('169.254.169.254',opened.call_args_list[1].args[0].full_url)

    def test_staging_rejects_final_oos_rows_and_source_allowlist_excludes_controller(self):
        from research.spot.prepare_v083_spot_bundle import scan_before_oos, files_for_worker
        file=self.root/'TQQQ.csv';file.write_text('timestamp,open\n2026-01-01T00:00:00Z,100\n')
        with self.assertRaisesRegex(ValueError,'2026_SOURCE_ROW_BLOCKED'):scan_before_oos(file)
        repo=Path(__file__).resolve().parents[1]
        paths=[p.relative_to(repo).as_posix() for p in files_for_worker(repo)]
        self.assertIn('research/spot/worker_storage.py',paths)
        self.assertNotIn('research/spot/v083_spot_lifecycle.py',paths)
        self.assertFalse(any(p.startswith(('server.py','live_','toss_auth')) for p in paths))

    def test_durable_upload_budget_fail_closed(self):
        src=self.root/'src';src.mkdir();(src/'artifact.json').write_text('{}')
        store=DurableStore(MemoryS3(),'reviewed-bucket','v083/runs/'+self.job,max_upload_bytes=1)
        with self.assertRaisesRegex(ValueError,'UPLOAD_BUDGET_EXCEEDED'):
            store.snapshot(src,phase='completed',job_id=self.job,source_commit='a'*40)
        self.assertEqual(store.put_count,0)

    def test_infra_has_no_instance_or_broker_access(self):
        path=Path(__file__).parent/'spot/infra/worker.json'
        t=json.loads(path.read_text())
        self.assertNotIn('AWS::EC2::Instance',[v['Type'] for v in t['Resources'].values()])
        text=json.dumps(t)
        for action in ('ec2:RunInstances','ssm:SendCommand','secretsmanager:'): self.assertNotIn(action,text)
        self.assertEqual(t['Resources']['WorkerSecurityGroup']['Properties']['SecurityGroupIngress'],[])


if __name__ == '__main__': unittest.main()
