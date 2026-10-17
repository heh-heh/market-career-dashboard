"""Mock workers plus real loopback HTTP authentication; no EC2/services touched."""
import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import v081_web_jobs as jobs


class WebTests(unittest.TestCase):
    def test_no_job_protocol_and_arbitrary_commands_rejected(self):
        with patch.object(jobs,'_storage'),patch.object(jobs,'_job',return_value=None):
            r=jobs.report();self.assertEqual(r['protocol'],'V081_WEB_1');self.assertEqual(r['runs'],{})
        for command in (['v081','train','--2026'],['v081','reset'],['v081','stop']):
            self.assertRaises(ValueError,jobs.console,command)
    def test_prior_zero_generation_counts_from_actual_fixture_files_readonly(self):
        import v08_web_jobs as prior
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'oldrun';folder.mkdir();job=root/'job';job.mkdir()
            (folder/'run.json').write_text(json.dumps(dict(completedTrainGenerations=0,selectedSessionCount=0)))
            path=folder/'windows.jsonl';path.write_text((json.dumps(dict(reason='COHORT_HELD_SYMBOL_OBSERVATION_GAP'))+'\n')*7)
            before=path.read_bytes()
            with patch.object(prior,'RESULTS',root),patch.object(prior,'_job',return_value=job),patch.object(prior,'_state',return_value=dict(runDir=str(folder))):
                result=jobs._prior_zero_diagnosis()
            self.assertEqual(result['counts']['windows.jsonl:COHORT_HELD_SYMBOL_OBSERVATION_GAP'],7)
            self.assertEqual(path.read_bytes(),before)

    def test_killmode_policy_block_without_changing_service(self):
        with patch('v07_web_jobs.Path.read_text',return_value='market-career-dashboard.service'),patch('v07_web_jobs.subprocess.check_output',return_value='control-group'):
            with self.assertRaisesRegex(ValueError,'API_CGROUP_WOULD_KILL_RESEARCH'):jobs.legacy._check_service_lifetime()
    def test_detached_worker_inherits_locks_and_readonly_plan(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            root=Path(tmp);legacy=root/'legacy';legacy.mkdir();plan=root/'plan.json';plan.write_text('{}')
            for name,value in [('ROOT',root),('PYTHON',Path(__file__)),('_plan',lambda:plan),('_storage',lambda:None),('_verify_source',lambda:None),('_other_active',lambda:None)]:stack.enter_context(patch.object(jobs,name,value))
            stack.enter_context(patch.object(jobs.legacy,'ROOT',legacy));stack.enter_context(patch.object(jobs.legacy,'_check_service_lifetime'))
            spawn=stack.enter_context(patch.object(jobs.subprocess,'Popen'))
            jobs.start('smoke');kwargs=spawn.call_args.kwargs
            self.assertTrue(kwargs['start_new_session']);self.assertEqual(len(kwargs['pass_fds']),2)
            self.assertEqual(plan.read_text(),'{}')
            with (legacy/'research.lock').open('rb') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError,'BUSY'):jobs.start('smoke')
    def test_new_campaign_requires_explicit_command_and_keeps_old_artifacts(self):
        with patch.object(jobs,'start',return_value='STARTED') as spawn:
            jobs.console(['v081','new'])
        spawn.assert_called_once_with('smoke',new_job=True)

    def test_no_valid_smoke_cannot_unlock_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=root/'run';run.mkdir();job=root/'job';job.mkdir()
            (job/'smoke.json').write_text(json.dumps(dict(phase='completed',runDir=str(run))))
            (run/'run.json').write_text(json.dumps(dict(phase='completed',finalOosGate='CLOSED',version='V081_SURVIVAL_1',completedTrainGenerations=0)))
            with patch.object(jobs,'RESULTS',root):
                with self.assertRaisesRegex(ValueError,'NO_VALID_GENERATIONS'):jobs._verified_run(job,'smoke')
    def test_api_restart_guard_checks_held_flock(self):
        import server
        with tempfile.TemporaryDirectory() as tmp:
            # Patch only fixed probe roots through a scoped fake Path factory.
            original=server.Path;root=Path(tmp);folder=root/'v081-web-jobs';folder.mkdir();path=folder/'research.lock';path.touch()
            def p(value):return root if str(value)=='/mnt/research' else original(value)
            with path.open('rb') as lock,patch.object(server,'Path',side_effect=p):
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                self.assertTrue(server._evolution_workers_active())
    def test_real_http_requires_admin_for_console_and_download(self):
        import server
        with patch.object(jobs,'_storage'),patch.object(jobs,'_job',return_value=None),patch.object(server.Handler,'log_message',lambda *args:None):
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            token='synthetic-v081';server.SESSIONS[token]=time.time()+60
            try:
                base='http://127.0.0.1:'+str(http.server_port)
                for endpoint,data in [('/api/research/v081/download',None),('/api/admin/console',json.dumps({'command':'v081 smoke'}).encode())]:
                    req=urllib.request.Request(base+endpoint,data=data,headers={'Content-Type':'application/json'})
                    with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req)
                    self.assertEqual(caught.exception.code,401)
                req=urllib.request.Request(base+'/api/admin/console',data=json.dumps({'command':'v081 report'}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                with urllib.request.urlopen(req) as response:r=json.load(response)
                self.assertEqual(json.loads(r['output'])['protocol'],'V081_WEB_1')
            finally:server.SESSIONS.pop(token,None);http.shutdown();http.server_close();thread.join()


class InstallerTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('v081installer',Path(__file__).resolve().parents[1]/'scripts/install_v081_admin_api_overlay.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
    def test_check_readonly_and_apply_failure_rolls_back(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            root=Path(tmp);target=root/'server.py';target.write_bytes(b'# local server retained\n')
            controller=root/'v081_web_jobs.py';proof=root/'proof/source_revision.json'
            content=b'# new controller\n';server=b'# local server retained\n# added hook\n'
            audit=dict(serverBefore=m.sha(target.read_bytes()),serverAfter=m.sha(server),controllerSha=m.sha(content),
                serverChanged=True,controllerNeeded=True,controllerChanged=True,sourceNeeded=False,proofChanged=True,branchCommit='a'*40)
            for name,value in [('TARGET',target),('CONTROLLER',controller),('PROOF',proof),('BACKUPS',root/'backups'),('SOURCE',root/'source'),('PIN','a'*40)]:stack.enter_context(patch.object(m,name,value))
            stack.enter_context(patch.object(m,'guard',return_value=(audit,server,content)));stack.enter_context(patch.object(m,'active_workers',return_value=False))
            with patch('sys.argv',['installer','--check']):m._main()
            self.assertFalse(controller.exists());self.assertFalse(proof.exists());self.assertFalse((root/'backups').exists())
            stack.enter_context(patch.object(m.subprocess,'run'));stack.enter_context(patch.object(m,'health',return_value=False))
            with patch('sys.argv',['installer','--apply']):
                with self.assertRaisesRegex(ValueError,'HEALTH'):m._main()
            self.assertEqual(target.read_bytes(),b'# local server retained\n');self.assertFalse(controller.exists());self.assertFalse(proof.exists())
            self.assertTrue(list((root/'backups').glob('*/server.py')))


if __name__=='__main__':unittest.main()
