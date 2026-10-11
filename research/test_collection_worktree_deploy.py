"""Real detached Git fixtures + fake systemd; no production, credentials or data."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch

from scripts import deploy_collection_worktree as d


class FakeHost:
    def __init__(self,layout):
        self.layout=layout;self.events=[];self.failed_start=False;self.failed_health=False;self.stuck=False
        self.units={u:{'exists':u!=d.COLLECTORS[0],'active':u in (d.API,d.COLLECTORS[1]),'enabled':'enabled'} for u in d.COLLECTORS+[d.API]}
        self.validator=None
    def validate_stage(self,stage,log):
        if self.validator: self.validator(stage)
        d.write_json(log,{'tests':'fake host; no production import'})
    def before_apply(self): return os.getuid(),os.getgid()
    def system_identity(self): return os.getuid(),os.getgid()
    def broker(self): self.events.append(('broker_probe',None))
    def check_environment(self): pass
    def research_idle(self): pass
    def app_service_matches(self): pass
    def graceful_stop_settings(self,units): pass
    def snapshot(self): return copy.deepcopy(self.units)
    def ctl(self,action,unit=None,required=True):
        self.events.append((action,unit))
        if action=='daemon-reload': return '',0
        item=self.units[unit]
        if action=='stop':
            if not (self.stuck and unit==d.COLLECTORS[1]): item['active']=False
        elif action=='start':
            if unit==d.COLLECTORS[0] and self.failed_start: raise d.Blocked('SYNTHETIC_START_FAILURE')
            item['active']=True;item['exists']=True
            if unit==d.COLLECTORS[0]: d.write_json(self.layout.state/'ownership.json',{'managed':True})
        elif action=='disable': item['enabled']='disabled'
        elif action=='enable': item['enabled']='enabled'
        return ('active' if item['active'] else 'inactive'),0
    def no_workers(self,allow_scheduler=False):
        if any(self.units[u]['active'] for u in d.COLLECTORS if not (allow_scheduler and u==d.COLLECTORS[0])):
            raise d.Blocked('COLLECTOR_STILL_RUNNING')
    def no_api(self):
        if self.units[d.API]['active']: raise d.Blocked('API_NOT_STOPPED')
    def verify(self,page):
        self.no_workers(allow_scheduler=True)
        if self.failed_health: raise d.Blocked('SYNTHETIC_HEALTH_FAILURE')
        if d.hashes(d.read(self.layout.target/'collection.html'))!=page: raise d.Blocked('BAD_PAGE')
        if not json.loads((self.layout.state/'control.json').read_text())['paused']: raise d.Blocked('NOT_PAUSED')
        return {'api':True,'pageSha256':page,'scheduler':'PAUSED','broker':True,'finalOosGate':'CLOSED'}
    def recovery_verify(self,active):
        self.no_workers()
        return {'api':'RESPONDING' if active else 'RESTORED_INACTIVE','brokerTransport':True}


class WorktreeDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.target=self.root/'production';self.target.mkdir()
        self.run_git(self.target,'init','-b','strategy-engine-v3')
        self.run_git(self.target,'config','user.name','Synthetic deploy test')
        self.run_git(self.target,'config','user.email','synthetic@example.invalid')
        for name,data in {'server.py':'def first():\n return "old"\n\n\ndef last():\n return "local"\n',
                'toss_auth.py':'def token():\n return "base-auth"\n','toss_token_broker.py':'# preserved broker\n',
                'collection.html':'old page','data/dashboard.json':'{"source":"public-base"}',
                'requirements.txt':'','scripts/collect_research_1m.py':'# old worker\n'}.items():
            p=self.target/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)
        repo=Path(__file__).resolve().parents[1]
        for name in ('ops/research-collection-scheduler.service','ops/collection-worker-dropin/10-scheduler.conf'):
            p=self.target/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((repo/name).read_bytes())
        self.run_git(self.target,'add','.');self.run_git(self.target,'commit','-m','base')
        self.base=self.run_git(self.target,'rev-parse','HEAD').strip()
        self.source=self.root/'detached';self.run_git(self.target,'worktree','add','--detach',str(self.source),self.base)
        p=self.source/'server.py';p.write_text(p.read_text().replace('"old"','"new"'))
        (self.source/'toss_auth.py').write_text('def token():\n return "new-auth"\n')
        (self.source/'collection.html').write_text('new page')
        (self.source/'data/dashboard.json').write_text('{"source":"upstream-public"}')
        (self.source/'new_app.py').write_text('version="new"\n')
        self.run_git(self.source,'add','.');self.run_git(self.source,'commit','-m','source')
        self.sha=self.run_git(self.source,'rev-parse','HEAD').strip()
        self.run_git(self.target,'update-ref','refs/remotes/origin/strategy-engine-v3',self.sha)
        (self.target/'toss_auth.py').write_text('def token():\n return "operational-local-auth"\n')
        (self.target/'data/dashboard.json').write_text('{"source":"operational-live"}')
        (self.target/'data/toss_1m').mkdir();(self.target/'data/toss_1m/TQQQ.sqlite3').write_bytes(b'important market bytes')
        (self.target/'untracked-backup.log').write_text('preserve history')
        self.layout=d.Layout(self.target,self.root/'units',self.root/'state',self.root/'config/collection.env',self.root/'broker.sock')
        self.layout.env.parent.mkdir();self.layout.env.write_text('PRIVATE_SETTING=synthetic\n')
        self.host=FakeHost(self.layout);self.deploy=d.Deployment(self.source,self.layout,self.root/'deployments',self.host)
    def run_git(self,root,*args):
        r=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True)
        if r.returncode: raise AssertionError(r.stderr)
        return r.stdout
    def analyze(self):
        with patch.object(d.shutil,'disk_usage',return_value=type('Disk',(),{'free':100*1024**3,'total':200*1024**3})()): return self.deploy.analyze()
    def row(self,plan,name): return next(r for r in plan['files'] if r['path']==name)

    def test_detached_source_dirty_operational_auth_and_data_preserved(self):
        head=self.run_git(self.target,'rev-parse','HEAD');index=(self.target/'.git/index').read_bytes()
        data=(self.target/'data/dashboard.json').read_bytes();auth=(self.target/'toss_auth.py').read_bytes()
        txn,plan=self.analyze();self.assertEqual(plan['blocked'],[])
        self.assertEqual(self.row(plan,'toss_auth.py')['action'],'PRESERVE_AUTH')
        self.assertTrue(self.row(plan,'toss_auth.py')['overlapConflict'])
        self.assertTrue(plan['preservedRuntimeFiles'][0]['overlapConflict'])
        self.assertFalse(any(r['path'].startswith('data/') for r in plan['files']))
        self.assertEqual(self.host.events,[],'analysis must not touch systemd')
        result=self.deploy.apply(txn)
        self.assertEqual(result['phase'],'COMMITTED');self.assertEqual(result['sourceCommit'],self.sha)
        self.assertEqual((self.target/'server.py').read_bytes(),(self.source/'server.py').read_bytes())
        self.assertEqual((self.target/'data/dashboard.json').read_bytes(),data);self.assertEqual((self.target/'toss_auth.py').read_bytes(),auth)
        self.assertEqual((self.target/'data/toss_1m/TQQQ.sqlite3').read_bytes(),b'important market bytes')
        self.assertEqual((self.target/'untracked-backup.log').read_text(),'preserve history')
        self.assertEqual(self.layout.env.read_text(),'PRIVATE_SETTING=synthetic\n')
        self.assertEqual(self.run_git(self.target,'rev-parse','HEAD'),head);self.assertEqual((self.target/'.git/index').read_bytes(),index)
        self.assertTrue(json.loads((self.layout.state/'control.json').read_text())['paused'])
        self.assertFalse(any(u==d.BROKER and action in ('stop','start','restart','disable') for action,u in self.host.events))

    def test_nonconflicting_local_code_merge_is_tested_and_applied(self):
        p=self.target/'server.py';p.write_text(p.read_text().replace('"local"','"operator"'))
        self.host.validator=lambda stage:self.assertIn('"operator"',(stage/'server.py').read_text())
        txn,plan=self.analyze();self.assertEqual(self.row(plan,'server.py')['action'],'MERGED')
        self.deploy.apply(txn);text=p.read_text();self.assertIn('"operator"',text);self.assertIn('"new"',text)

    def test_same_line_conflict_blocks_before_mutation(self):
        p=self.target/'server.py';p.write_text(p.read_text().replace('"old"','"operational"'));before=p.read_bytes()
        txn,plan=self.analyze();self.assertIn('server.py: LOCAL_REMOTE_CONFLICT',plan['blocked'])
        with self.assertRaisesRegex(d.Blocked,'PREFLIGHT'):self.deploy.apply(txn)
        self.assertEqual(p.read_bytes(),before);self.assertEqual(self.host.events,[])

    def test_untracked_app_collision_is_not_overwritten(self):
        (self.target/'new_app.py').write_text('operator-owned code')
        txn,plan=self.analyze();self.assertIn('new_app.py: UNTRACKED_APPLICATION_COLLISION',plan['blocked'])
        self.assertEqual((self.target/'new_app.py').read_text(),'operator-owned code')

    def test_target_edited_after_plan_is_rejected(self):
        txn,plan=self.analyze();(self.target/'server.py').write_text('late user edit')
        with self.assertRaisesRegex(d.Blocked,'LOCAL_FILE_CHANGED'):self.deploy.apply(txn)
        self.assertEqual(self.host.events,[])

    def test_edit_between_preflight_and_backup_is_not_overwritten_or_outaged(self):
        txn,_=self.analyze()
        def concurrent_edit():
            (self.target/'server.py').write_text('operator changed during preflight')
            return os.getuid(),os.getgid()
        with patch.object(self.host,'before_apply',side_effect=concurrent_edit):
            with self.assertRaisesRegex(d.Blocked,'LOCAL_FILE_CHANGED_AFTER_PREFLIGHT'):self.deploy.apply(txn)
        self.assertEqual((self.target/'server.py').read_text(),'operator changed during preflight')
        self.assertTrue(self.host.units[d.API]['active']);self.assertTrue(self.host.units[d.COLLECTORS[1]]['active'])
        self.assertFalse(any(action=='stop' for action,_ in self.host.events))

    def test_stage_corruption_rejected(self):
        txn,plan=self.analyze();(txn/'stage/server.py').write_text('corrupt')
        with self.assertRaisesRegex(d.Blocked,'STAGE_HASH'):self.deploy.apply(txn)

    def test_systemd_config_changes_after_plan_rejected(self):
        txn,plan=self.analyze();p=self.layout.systemd/d.COLLECTORS[0];p.parent.mkdir();p.write_text('operator config')
        with self.assertRaisesRegex(d.Blocked,'SYSTEMD_CHANGED'):self.deploy.apply(txn)
        self.assertEqual(p.read_text(),'operator config')

    def test_systemd_unknown_existing_configuration_blocks(self):
        # Unknown non-base dropin must be reconciled instead of blindly installed.
        template='ops/collection-worker-dropin/10-scheduler.conf'
        self.run_git(self.target,'rm',template);self.run_git(self.target,'commit','-m','remove base config')
        path=self.layout.systemd/(d.COLLECTORS[1]+'.d')/'10-scheduler.conf';path.parent.mkdir(parents=True);path.write_text('unknown settings')
        txn,plan=self.analyze();self.assertTrue(any('EXISTING_SYSTEMD_CONFIG' in x for x in plan['blocked']))

    def test_old_worktree_scheduler_configuration_is_not_silently_preserved(self):
        p=self.layout.systemd/d.COLLECTORS[0];p.parent.mkdir(parents=True)
        p.write_bytes((self.source/'ops/research-collection-scheduler.service').read_bytes().replace(b'/home/ubuntu/market-career-dashboard',b'/old/worktree'))
        txn,plan=self.analyze();self.assertTrue(any('SCHEDULER_CONFIG_REQUIRES_REVIEW' in x for x in plan['blocked']))

    def test_failed_start_restores_code_configs_api_and_keeps_collectors_off(self):
        original=(self.target/'server.py').read_bytes();mode=(self.target/'server.py').stat().st_mode
        txn,plan=self.analyze();self.host.failed_start=True
        with self.assertRaisesRegex(d.Blocked,'START_FAILURE'):self.deploy.apply(txn)
        journal=json.loads((txn/'journal.json').read_text());self.assertEqual(journal['phase'],'ROLLED_BACK')
        self.assertEqual((self.target/'server.py').read_bytes(),original);self.assertEqual((self.target/'server.py').stat().st_mode,mode)
        self.assertFalse((self.target/'new_app.py').exists());self.assertFalse((self.layout.systemd/d.COLLECTORS[0]).exists())
        self.assertTrue(self.host.units[d.API]['active']);self.assertFalse(any(self.host.units[u]['active'] for u in d.COLLECTORS))
        self.assertTrue(json.loads((self.layout.state/'control.json').read_text())['paused'])
        self.assertTrue((txn/'backups').is_dir())

    def test_failed_http_health_rolls_back(self):
        before=(self.target/'collection.html').read_bytes();txn,_=self.analyze();self.host.failed_health=True
        with self.assertRaisesRegex(d.Blocked,'HEALTH_FAILURE'):self.deploy.apply(txn)
        self.assertEqual((self.target/'collection.html').read_bytes(),before)
        self.assertEqual(json.loads((txn/'journal.json').read_text())['phase'],'ROLLED_BACK')

    def test_stuck_collector_prevents_any_application_replacement(self):
        before=(self.target/'server.py').read_bytes();txn,_=self.analyze();self.host.stuck=True
        with self.assertRaisesRegex(d.Blocked,'COLLECTOR_STILL'):self.deploy.apply(txn)
        self.assertEqual((self.target/'server.py').read_bytes(),before)
        self.assertNotIn(('start',d.COLLECTORS[0]),self.host.events)
        self.assertTrue(self.host.units[d.API]['active'],'blocked collector must not bring down unchanged API')
        self.assertEqual(json.loads((txn/'journal.json').read_text())['phase'],'RECOVERY_BLOCKED')

    def test_process_death_recovery_and_idempotent_rollback(self):
        txn,_=self.analyze();self.deploy.apply(txn)
        journal=json.loads((txn/'journal.json').read_text());journal['phase']='APPLYING';d.write_json(txn/'journal.json',journal)
        result=self.deploy.rollback(txn);self.assertEqual(result['phase'],'ROLLED_BACK')
        self.assertEqual(self.deploy.rollback(txn)['phase'],'ROLLED_BACK')

    def test_analyzed_only_transaction_cannot_be_rolled_back(self):
        txn,_=self.analyze()
        with self.assertRaisesRegex(d.Blocked,'TRANSACTION_NOT_STARTED'):self.deploy.rollback(txn)
        self.assertEqual(self.host.events,[])

    def test_external_post_deploy_edits_are_not_discarded_on_rollback(self):
        txn,_=self.analyze();self.deploy.apply(txn);(self.target/'server.py').write_text('new operator edit')
        with self.assertRaisesRegex(d.Blocked,'ROLLBACK_EXTERNAL_EDIT'):self.deploy.rollback(txn)
        self.assertEqual((self.target/'server.py').read_text(),'new operator edit')
        self.assertFalse(any(self.host.units[u]['active'] for u in d.COLLECTORS))

    def test_shared_lock_cannot_be_reentered(self):
        with d.lock(self.root/'lock'):
            with self.assertRaises(d.Blocked):
                with d.lock(self.root/'lock'):pass

    def test_active_research_blocks_before_systemd_mutation(self):
        txn,_=self.analyze()
        with patch.object(self.host,'research_idle',side_effect=d.Blocked('RESEARCH_RUNNING')):
            with self.assertRaisesRegex(d.Blocked,'RESEARCH_RUNNING'):self.deploy.apply(txn)
        self.assertFalse((txn/'journal.json').exists())
        self.assertFalse(any(action=='stop' for action,_ in self.host.events))

    def test_symlink_and_protected_backup_roots_rejected(self):
        (self.target/'server.py').unlink();(self.target/'server.py').symlink_to(self.root/'outside')
        with self.assertRaisesRegex(d.Blocked,'SYMLINK'):self.analyze()
        with self.assertRaises(d.Blocked):d.Deployment(self.source,self.layout,self.target/'data/exports',self.host)
        with self.assertRaises(d.Blocked):d.Deployment(self.source,self.layout,'/home/ubuntu/evolution-results/exports',self.host)

    def test_same_second_same_size_python_replacement_does_not_use_old_pyc(self):
        p=self.target/'server.py';py_compile.compile(str(p),doraise=True)
        txn,_=self.analyze();before=p.stat().st_mtime_ns
        # Force the file replacement's mtime into the original pyc second.
        original_atomic=d.atomic
        def same_time(path,*args,**kwargs):
            original_atomic(path,*args,**kwargs)
            if Path(path)==p:os.utime(p,ns=(before,before))
        with patch.object(d,'atomic',side_effect=same_time):self.deploy.apply(txn)
        spec=importlib.util.spec_from_file_location('deployed_app',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.first(),'new')

    def test_rate_control_permissions_roll_back_without_erasing_new_cooldowns(self):
        path=self.target/'data/.toss_api_rate.lock';path.write_text('old cooldown');path.chmod(0o600)
        txn,_=self.analyze();self.deploy.apply(txn);self.assertEqual(path.stat().st_mode&0o777,0o660)
        path.write_text('new cooldown');self.deploy.rollback(txn)
        self.assertEqual(path.read_text(),'new cooldown');self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_real_source_composite_validation_is_isolated_and_sanitized(self):
        # Export committed application files, never market data, from the actual
        # repository. Run the exact production preflight test runner in this stage.
        repo=Path(__file__).resolve().parents[1];stage=self.root/'real-stage';stage.mkdir()
        sha=d.git(repo,'rev-parse','HEAD').stdout.decode().strip()
        for name in d.tracked(repo,sha):
            if d.application(name) or name in d.STAGE_ONLY:
                d.atomic(stage/name,d.blob(repo,sha,name),0o600)
        (self.target/'.venv').symlink_to(Path(sys.prefix),target_is_directory=True)
        host=d.Host(self.layout);log=self.root/'preflight-results.json'
        try: host.validate_stage(stage,log)
        except d.Blocked:
            self.fail(log.read_text() if log.exists() else 'Missing runtime dependencies')
        results=json.loads(log.read_text());self.assertFalse(results['rawOutputPersisted'])
        self.assertTrue(all(r['exitCode']==0 for r in results['results']))
        self.assertGreater(results['results'][-1]['tests'],30)
        self.assertEqual((self.target/'data/toss_1m/TQQQ.sqlite3').read_bytes(),b'important market bytes')


if __name__=='__main__': unittest.main()
