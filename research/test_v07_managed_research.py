"""Deterministic managed-research regressions. No EC2, broker, or real CSVs."""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import v07_web_jobs as jobs
from v07_research_report import analyze
from research.test_v07_web_jobs import V07WebJobsTests, FakeProcess
from research.test_v07_web_install import installer


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fake_run(job, stage='train', *, valid=True):
    path = job/stage/'20261016T120000-available-abcd1234'
    info = dict(runId=path.name, phase='completed', finalOosGate='CLOSED',
        fromDate='2022-01-01' if stage=='train' else '2024-01-01',
        toDate='2023-12-31' if stage=='train' else '2024-12-31',
        completedTrainGenerations=1, resultsValid=valid, inputHashesVerified=valid,
        requestedSessions=504, skippedGenerationAttempts=2, interruptedPartialWindows=1)
    write(path/'run.json', info); write(path/'status.json', info)
    write(path/'final_population.json', [{'genome': {'weights': [0.]}, 'genome_hash': 'test'}])
    return path


class ManagedJobTests(V07WebJobsTests):
    def complete(self, stage):
        jobs._write(jobs._current()/(stage+'.json'), dict(stage=stage, phase='completed'))

    def prepared(self):
        with patch.object(jobs.subprocess, 'Popen', side_effect=FakeProcess):
            jobs.start('prepare')
            for stage in ('prepare','audit','select','smoke'):
                self.complete(stage)
        fake_run(jobs._current(), 'smoke')
        return jobs._current()

    def test_long_stages_explicit_allowlist_and_oos_not_a_command(self):
        job=self.prepared()
        with patch.object(jobs.subprocess,'Popen',side_effect=FakeProcess) as spawn:
            jobs.start('train')
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            self.assertTrue(spawn.call_args.kwargs['pass_fds'])
            self.assertEqual(spawn.call_args.kwargs['env']['V07_JOB_LOCK_FD'],str(spawn.call_args.kwargs['pass_fds'][0]))
            for cmd in (['v07','train','2026'],['v07','final-oos'],['v07','retry','prepare'],['v07','train;rm']):
                with self.assertRaises(ValueError):jobs.console(cmd)
        self.assertTrue((job/'train.json').is_file())

    def test_global_flock_blocks_duplicate_even_without_current_pid(self):
        self.prepared()
        with (self.root/'research.lock').open('a+') as held:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(jobs.subprocess,'Popen') as spawn:
                with self.assertRaisesRegex(ValueError,'V07_BUSY'):jobs.start('train')
                spawn.assert_not_called()
        with patch.object(jobs.subprocess,'Popen',side_effect=FakeProcess):jobs.start('train')

    def test_api_restart_identifies_wrong_pid_and_reboot_identity(self):
        job=self.prepared()
        jobs._write(job/'train.json',dict(stage='train',phase='running',pid=os.getpid(),processIdentity={'bootId':'old','startTicks':'0'}))
        self.assertEqual(jobs._state(job,'train')['phase'],'interrupted')
        self.assertIn('interrupted', jobs.status())

    def test_api_cgroup_guard_and_interactive_detach(self):
        original=Path.read_text
        def cg(path,*a,**kw):
            return '0::/system.slice/market-career-dashboard.service' if str(path)=='/proc/self/cgroup' else original(path,*a,**kw)
        with patch.object(Path,'read_text',cg),patch.object(jobs.subprocess,'check_output',return_value='control-group\n'):
            with self.assertRaisesRegex(ValueError,'API_CGROUP_WOULD_KILL_RESEARCH'):jobs._check_service_lifetime()
        with patch.object(Path,'read_text',cg),patch.object(jobs.subprocess,'check_output',return_value='process\n'):
            jobs._check_service_lifetime()

    def test_retry_preserves_existing_run_bytes_and_starts_new_process(self):
        job=self.prepared(); run=fake_run(job)
        before=(run/'run.json').read_bytes()
        jobs._write(job/'train.json',dict(stage='train',phase='running',pid=99999999))
        with patch.object(jobs.subprocess,'Popen',side_effect=FakeProcess):jobs.start('train',retry=True)
        self.assertEqual((run/'run.json').read_bytes(),before)
        self.assertTrue(list((job/'retries').glob('train-*')))
        self.assertNotEqual(jobs._json(job/'train.json')['logFile'],'train.log')

    def test_frozen_both_years_same_source_and_no_mutation(self):
        job=self.prepared(); run=fake_run(job); self.complete('train');jobs._seal_training(job)
        self.assertEqual(jobs._train_source(job),run)
        jobs._write(run/'final_population.json',[])
        with self.assertRaisesRegex(ValueError,'FROZEN_TRAINING_HASH_MISMATCH'):jobs._train_source(job)

    def test_incomplete_training_cannot_be_sealed(self):
        job=self.prepared(); fake_run(job,valid=False)
        with self.assertRaisesRegex(ValueError,'FULL_RANGE_TRAINING_NOT_VERIFIED'):jobs._seal_training(job)

    def test_snapshot_real_artifacts_no_fake_progress(self):
        job=self.prepared(); run=fake_run(job)
        write(run/'status.json',dict(phase='running',completedSessions=37,requestedSessions=504,completedTrainGenerations=2))
        write(run/'window_0000.json',dict(dates=['2022-01-03'],bots=[dict(windowReturn=.01,fitness=.002,maxDrawdown=.03)],selection={}))
        s=jobs.snapshot()
        self.assertEqual(s['runs']['train']['completedSessions'],37)
        self.assertEqual(s['generations'][0]['returns']['mean'],.01)
        self.assertEqual(s['finalOosGate'],'CLOSED')

    def test_completed_export_download_and_manifest(self):
        job=self.prepared(); run=fake_run(job); self.complete('train')
        write(run/'used_sessions.json',[]);write(run/'evaluation_sessions.json',[])
        # Analysis can flag inconsistent artificial fixture; never turn it into PASS.
        jobs._export(job);self.complete('export')
        path=jobs.download()
        with zipfile.ZipFile(path) as z:
            manifest=json.loads(z.read('export_manifest.json'))
            self.assertEqual(manifest['finalOosGate'],'CLOSED')
            self.assertIn('analysis.json',z.namelist())
            for row in manifest['files']:
                import hashlib
                self.assertEqual(hashlib.sha256(z.read(row['path'])).hexdigest(),row['sha256'])
            self.assertFalse(any('token' in n or 'toss_1m' in n for n in z.namelist()))

    def test_disk_guard_refuses_export_and_leaves_no_archive(self):
        job=self.prepared();fake_run(job);self.complete('train')
        from collections import namedtuple
        disk=namedtuple('disk','total used free')(100,99,1)
        with patch.object(jobs.shutil,'disk_usage',return_value=disk):
            with self.assertRaisesRegex(ValueError,'RESOURCE_GUARD'):jobs._export(job)
        self.assertFalse((job/'research-results.zip').exists())

    def test_export_symlink_and_interrupted_flush_are_rejected(self):
        job=self.prepared();run=fake_run(job);self.complete('train')
        (run/'status.json').unlink();(run/'status.json').symlink_to(run/'run.json')
        with self.assertRaisesRegex(ValueError,'UNSAFE'):jobs._export(job)
        self.assertFalse(list(job.glob('export-*.tmp')))


class ArtifactAnalysisTests(unittest.TestCase):
    def test_one_selected_two_invalid_attempts_reconciled_no_fitness_contamination(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);run=fake_run(root)
            good=['2022-01-03','2022-01-04','2022-01-05','2022-01-06','2022-01-07']
            write(run/'used_sessions.json',good);write(run/'evaluation_sessions.json',good+['2022-01-10'])
            (run/'windows.jsonl').write_text('\n'.join(json.dumps(x) for x in [dict(status='SELECTED',generation=0,dates=good),dict(status='INVALID_DATA',dates=['2022-01-10'],reason='COHORT_HELD_SYMBOL_OBSERVATION_GAP')]))
            (run/'excluded_sessions.jsonl').write_text(json.dumps(dict(date='2022-01-10',reason='COHORT_HELD_SYMBOL_OBSERVATION_GAP'))+'\n')
            write(run/'window_0000.json',dict(dates=good,bots=[dict(windowReturn=.03,fitness=.01,maxDrawdown=.02,survival='SURVIVE')],selection=dict(parentIds=['p'],mutationCount=2)))
            write(run/'attempt_0001_2022-01-10/metrics.json',dict(bots=[dict(botId='p',date='2022-01-10',evaluationValid=False,fitness=1000.,cancellations=dict(HELD_SYMBOL_OBSERVATION_GAP=2))]))
            r=analyze(run)
            self.assertEqual(r['accountingIntegrity'],'PASS')
            self.assertEqual(r['heldGapObservationCount'],2)
            self.assertEqual(r['generations'][0]['fitness']['mean'],.01)
            self.assertEqual(r['exclusionReasons']['COHORT_HELD_SYMBOL_OBSERVATION_GAP'],1)
            write(run/'used_sessions.json',good+['2022-01-10'])
            self.assertEqual(analyze(run)['accountingIntegrity'],'FAIL')

    def test_missing_ledgers_never_claim_complete_and_2026_block(self):
        with tempfile.TemporaryDirectory() as t:
            run=fake_run(Path(t))
            self.assertNotEqual(analyze(run)['accountingIntegrity'],'PASS')
            x=json.loads((run/'run.json').read_text());x['toDate']='2026-01-01';write(run/'run.json',x)
            with self.assertRaisesRegex(ValueError,'PRE2026'):analyze(run)

    def test_readonly_audit_symbol_and_benchmark_counts(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);run=fake_run(root);audit=root/'audit';audit.mkdir()
            row=dict(symbol='QQQ',sessionDate='2022-01-03',complete=False,missingMinutes=2,expectedMinutes=390)
            (audit/'symbol_sessions.jsonl').write_text(json.dumps(row)+'\n')
            before=(audit/'symbol_sessions.jsonl').read_bytes()
            r=analyze(run,audit)
            self.assertEqual(r['missingBySymbol']['QQQ']['missingMinutes'],2)
            self.assertEqual(r['benchmarkIncompleteSessions']['QQQ'],1)
            self.assertEqual((audit/'symbol_sessions.jsonl').read_bytes(),before)

    def test_frozen_partial_results_month_regime_and_daily_data(self):
        with tempfile.TemporaryDirectory() as t:
            run=fake_run(Path(t),stage='holdout');info=json.loads((run/'run.json').read_text());info['sourceRunId']='train';write(run/'run.json',info)
            write(run/'used_sessions.json',['2024-01-02']);write(run/'evaluation_sessions.json',['2024-01-02'])
            write(run/'validation_summary.json',dict(evaluationStatus='PARTIAL_UNVERIFIED',resultsValid=False))
            path=run/'attempt_0000_2024-01-02';write(path/'metrics.json',dict(bots=[dict(date='2024-01-02',botId='a',evaluationValid=True,dailyReturn=.01)]))
            (path/'trades.csv').write_text('entryRegime,exitTimestamp,pnlUsd\nBULL,2024-01-02T15:00:00-05:00,1.0\n')
            r=analyze(run)
            self.assertFalse(r['validation']['resultsValid'])
            self.assertEqual(r['validationMonthly']['2024-01']['pnlUsd'],1.)
            self.assertEqual(r['validationRegimes']['BULL']['trades'],1)


class LockInheritanceTests(unittest.TestCase):
    def test_child_keeps_flock_after_parent_closes_until_child_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'lock'
            lock=path.open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            code='import sys;print("ready",flush=True);sys.stdin.read()'
            with subprocess.Popen([sys.executable,'-c',code],pass_fds=(lock.fileno(),),stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,start_new_session=True) as child:
                self.assertEqual(child.stdout.readline().strip(),'ready');lock.close()
                with path.open('a+') as again:
                    with self.assertRaises(BlockingIOError):fcntl.flock(again,fcntl.LOCK_EX|fcntl.LOCK_NB)
                child.communicate('exit',timeout=5)
            with path.open('a+') as again:fcntl.flock(again,fcntl.LOCK_EX|fcntl.LOCK_NB)


if __name__=='__main__':unittest.main()

class ManagedInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.target=Path(self.tmp.name);self.repo=Path(jobs.__file__).parent
        self.old={n:subprocess.check_output(['git','show','759238f:'+n],cwd=self.repo) for n in ('server.py','admin.html','v07_web_jobs.py')}
        for name,data in self.old.items():(self.target/name).write_bytes(data)
        self.patches=[patch.object(installer,'TARGET',self.target),patch.object(installer,'MANAGED',True),
            patch.object(installer,'payload',return_value=((self.repo/'v07_web_jobs.py').read_bytes(),'test-head')),
            patch.object(installer,'git',side_effect=self.git)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def git(self,*args):
        ref=args[1];version,name=ref.split(':',1)
        return self.old[name] if version=='759238f' else (self.repo/name).read_bytes()
    def test_merge_preserves_nonoverlapping_local_customizations_and_known_old_module(self):
        original=(self.target/'server.py').read_bytes();(self.target/'server.py').write_bytes(b'# EC2 custom token broker kept\n'+original)
        parts,_=installer.plan()
        self.assertTrue(parts['server.py'][1].startswith(b'# EC2 custom token broker kept\n'))
        self.assertIn(b'/api/research/v07/download',parts['server.py'][1])
        self.assertIn(b'v07ManagedPanel',parts['admin.html'][1])
        self.assertIn('v07_research_report.py',parts)
        self.assertEqual((self.target/'server.py').read_bytes(),b'# EC2 custom token broker kept\n'+original)
    def test_deleted_old_web_script_and_local_admin_customizations_are_preserved(self):
        # EC2 intentionally removed the old V0.7 JS, while changing unrelated UI.
        # Install the requested NEW V0.7 controls without replacing EC2 changes.
        local=(self.target/'admin.html').read_bytes().decode()
        start=local.index('let v07WebPending=false;')
        stop=local.index('function appendConsole(v)',start)
        local=local[:start]+local[stop:]
        local=local.replace('<div class="section-title">통합 운영 콘솔</div>',
                            '<div class="section-title">EC2 맞춤 통합 운영 콘솔</div>')
        (self.target/'admin.html').write_text(local)
        parts,_=installer.plan()
        result=parts['admin.html'][1].decode()
        self.assertIn('EC2 맞춤 통합 운영 콘솔',result)
        self.assertEqual(result.count('id="v07ManagedPanel"'),1)
        self.assertEqual(result.count('let v07WebPending=false;'),1)
        self.assertIn('function renderV07Managed(d)',result)
        self.assertEqual((self.target/'admin.html').read_text(),local)

    def test_missing_old_web_panel_can_be_added_with_no_loss(self):
        local=(self.target/'admin.html').read_bytes().decode()
        start=local.index('        <div class="card" id="v07WebPanel">')
        stop=local.index('        <div id="consoleOut"',start)
        local=local[:start]+local[stop:]
        (self.target/'admin.html').write_text(local)
        parts,_=installer.plan()
        result=parts['admin.html'][1].decode()
        self.assertEqual(result.count('id="v07WebPanel"'),1)
        self.assertIn('id="v07WebStatus"',result)
        self.assertEqual(result.count('id="v07ManagedPanel"'),1)
        self.assertEqual((self.target/'admin.html').read_text(),local)

    def test_old_ec2_admin_missing_all_v07_ui_and_custom_console_indent(self):
        # Actual EC2 diagnostics: no V0.7 JS or card; appendConsole remains,
        # and consoleOut can be indented differently than the GitHub example.
        local=(self.target/'admin.html').read_bytes().decode()
        card_start=local.index('        <div class="card" id="v07WebPanel">')
        card_end=local.index('        <div id="consoleOut"', card_start)
        local=local[:card_start]+local[card_end:]
        js_start=local.index('let v07WebPending=false;')
        js_end=local.index('function appendConsole(v)',js_start)
        local=local[:js_start]+local[js_end:]
        local=local.replace('        <div id="consoleOut"', '      <div id="consoleOut"',1)
        (self.target/'admin.html').write_text(local)
        self.assertNotIn('id="v07WebPanel"',local)
        self.assertNotIn('let v07WebPending=false;',local)
        parts,_=installer.plan()
        merged=parts['admin.html'][1].decode()
        self.assertEqual(merged.count('id="v07WebPanel"'),1)
        self.assertEqual(merged.count('id="v07ManagedPanel"'),1)
        self.assertEqual(merged.count('let v07WebPending=false;'),1)
        self.assertIn('      <div id="consoleOut"',merged)
        self.assertEqual((self.target/'admin.html').read_text(),local)

    def test_unknown_local_module_and_conflicting_ui_block_without_writes(self):
        (self.target/'v07_web_jobs.py').write_bytes(b'# local modification\n'+self.old['v07_web_jobs.py'])
        with self.assertRaisesRegex(installer.Blocked,'EXISTING_WEB_MODULE_DIFFERS'):installer.plan()
        (self.target/'v07_web_jobs.py').write_bytes(self.old['v07_web_jobs.py'])
        (self.target/'admin.html').write_bytes(self.old['admin.html'].replace(b'command:"v07 log"',b'command:"EC2 local log"'))
        with self.assertRaisesRegex(installer.Blocked,'LOCAL_V07_JS_CONFLICT'):installer.plan()


class FrozenCommandTests(ManagedJobTests):
    def test_training_and_frozen_commands_exact_years_source_and_ebs(self):
        job=self.prepared();fake_run(job);self.complete('train');jobs._seal_training(job)
        write(job/'selection/selection_plan.json', {})
        review=self.root/'review';(review/'research/evolution').mkdir(parents=True)
        (review/'research/evolution/run_v07_available.py').touch()
        data=self.root/'data';data.mkdir();calls=[]
        class Child:
            pid=os.getpid()
            stdout=io.BytesIO(b'finished\n')
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def wait(self):return 0
        def spawn(argv,**kw):
            calls.append((argv,kw));child=Child();child.stdout=io.BytesIO(b'finished\n');return child
        def output(argv,**kw):return '' if 'status' in argv else jobs.REV+'\n'
        with patch.object(jobs,'REVIEW',review),patch.object(jobs,'PYTHON',Path(sys.executable)),patch.object(jobs,'DATA',data),patch.object(jobs.subprocess,'check_output',side_effect=output),patch.object(jobs.subprocess,'Popen',side_effect=spawn):
            for stage in ('train','holdout','validation'):jobs._run_fixed(stage,job)
        for (stage,year),(argv,kw) in zip((('train','2022'),('holdout','2024'),('validation','2025')),calls):
            self.assertEqual(argv[argv.index('--from-date')+1],year+'-01-01')
            self.assertIn('--require-ebs',argv);self.assertNotIn('--allow-final-oos',argv)
            self.assertEqual('--source-run' in argv,stage!='train')
            if stage!='train':self.assertEqual(argv[argv.index('--source-run')+1],str(jobs._train_source(job)))
            self.assertEqual(kw['env']['OPENBLAS_NUM_THREADS'],'1')
        self.assertEqual(calls[0][0][calls[0][0].index('--to-date')+1],'2023-12-31')

class ManagedHTTPTests(unittest.TestCase):
    def test_auth_download_and_console_allowlist_keep_live_untouched(self):
        import threading,time,urllib.request,urllib.error
        from http.server import ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ,{'PAPER_V3_DIR':tmp}):import server
            archive=Path(tmp)/'results.zip';archive.write_bytes(b'synthetic-zip')
            session='synthetic-v07-admin';server.SESSIONS[session]=time.time()+60
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            def request(path,auth=True,body=None):
                headers={'Authorization':'Bearer '+session} if auth else {}
                if body is not None:headers['Content-Type']='application/json'
                req=urllib.request.Request('http://127.0.0.1:'+str(http.server_port)+path,headers=headers,data=json.dumps(body).encode() if body else None)
                try:
                    with urllib.request.urlopen(req,timeout=3) as r:return r.status,r.read()
                except urllib.error.HTTPError as e:
                    with e:return e.code,e.read()
            try:
                flags=dict(server.TRADING_STATE)
                with patch.object(jobs,'download',return_value=archive) as download,patch.object(server.LIVE_TRADER,'_api',side_effect=AssertionError('no orders')):
                    self.assertEqual(request('/api/research/v07/download',False)[0],401);download.assert_not_called()
                    self.assertEqual(request('/api/research/v07/download'),(200,b'synthetic-zip'))
                    self.assertEqual(request('/api/admin/console',False,dict(command='v07 train'))[0],401)
                    self.assertEqual(request('/api/admin/console',True,dict(command='v07 train 2026'))[0],400)
                with patch.object(jobs,'download',side_effect=ValueError('EXPORT_NOT_READY')):
                    self.assertEqual(request('/api/research/v07/download')[0],409)
                self.assertEqual(dict(server.TRADING_STATE),flags)
            finally:
                http.shutdown();thread.join(3);http.server_close();server.SESSIONS.pop(session,None)

class RollbackTests(unittest.TestCase):
    def test_rollback_hash_guards_and_restores_only_application_files(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);target=root/'target';target.mkdir();backups=root/'backups';backup=backups/'install-1';backup.mkdir(parents=True)
            (target/'server.py').write_bytes(b'new');(target/'data.csv').write_bytes(b'preserve')
            (backup/'server.py').write_bytes(b'old')
            write(backup/'manifest.json',dict(changedFiles=['server.py'],newHashes={'server.py':installer.sha(b'new')},oldHashes={'server.py':installer.sha(b'old')}))
            with patch.object(installer,'TARGET',target),patch.object(installer,'BACKUP_ROOT',backups),patch.object(installer.os,'geteuid',return_value=0),patch.object(installer,'market_guard'),patch.object(installer,'command',return_value=b''),patch.object(installer,'health',return_value=True),patch.object(installer,'research_idle',contextlib.nullcontext):
                (target/'server.py').write_bytes(b'EC2 local edits')
                with self.assertRaisesRegex(installer.Blocked,'POST_INSTALL_EDITS'):installer.rollback(backup)
                (target/'server.py').write_bytes(b'new')
                with contextlib.redirect_stdout(io.StringIO()):installer.rollback(backup)
            self.assertEqual((target/'server.py').read_bytes(),b'old')
            self.assertEqual((target/'data.csv').read_bytes(),b'preserve')
