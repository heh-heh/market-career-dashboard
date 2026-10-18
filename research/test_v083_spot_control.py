"""Offline HTTP/approval integration; injected AWS never provisions resources."""
import contextlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import v083_spot_control as control
from research.test_v083_spot_lifecycle import sample


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.cfg=sample()
        self.stack.enter_context(patch.object(control.lifecycle,'CONTROL_DIR',Path(self.temp.name)))
        self.stack.enter_context(patch.object(control.lifecycle,'load_config',side_effect=lambda *a:json.loads(json.dumps(self.cfg))))
        self.stack.enter_context(patch.object(control,'_local_blockers',return_value=[]))
        self.stack.enter_context(patch.object(control,'_campaign_guard'))
        self.launch=self.stack.enter_context(patch.object(control.lifecycle,'launch',return_value=dict(status='REQUESTED_ONE_TIME_SPOT',instance_id='i-fake')))

    def prepare(self):return control.prepare(dict(population=10,instanceType='c6i.2xlarge',maxHours=8))
    def approve(self,r):return control.approve(dict(requestId=r['requestId'],configHash=r['configHash'],acknowledgeCost=True,confirmation='APPROVE_PAID_SPOT'))
    def start(self,r):return control.start(dict(requestId=r['requestId'],confirmation='CREATE_ONE_PAID_SPOT'))

    def test_plan_and_prepare_never_contact_aws(self):
        with patch.object(control.lifecycle,'_api',side_effect=AssertionError('no AWS')):
            self.assertTrue(control.plan()['runnable']);self.prepare()
        self.launch.assert_not_called()

    def test_separate_cost_then_creation_consent_and_single_use(self):
        r=self.prepare()
        with self.assertRaisesRegex(ValueError,'NOT_APPROVED'):self.start(r)
        with self.assertRaisesRegex(ValueError,'CONSENT'):control.approve(dict(requestId=r['requestId']))
        self.approve(r);result=self.start(r)
        self.assertEqual(result['phase'],'REQUESTED_ONE_TIME_SPOT')
        control.lifecycle.verify_approval(self.cfg,self.launch.call_args.kwargs['approval_record'])
        with self.assertRaisesRegex(ValueError,'ALREADY_USED'):self.start(r)
        with self.assertRaisesRegex(ValueError,'ACTIVE'):self.prepare()
        self.launch.assert_called_once()

    def test_expired_or_changed_config_cannot_be_approved(self):
        r=self.prepare();self.cfg['population']=11
        with self.assertRaisesRegex(ValueError,'CONFIG_CHANGED'):self.approve(r)
        self.cfg['population']=10
        with patch.object(control.time,'time',return_value=time.time()+601):
            with self.assertRaisesRegex(ValueError,'EXPIRED'):self.approve(r)
        self.launch.assert_not_called()

    def test_launch_uncertainty_blocks_retry_and_redacts_aws_error(self):
        r=self.prepare();self.approve(r)
        self.launch.side_effect=TimeoutError('secret authorization bearer should not appear')
        with self.assertRaises(TimeoutError):self.start(r)
        record=control._load_request(control._root(),r['requestId'])
        self.assertEqual(record['phase'],'CREATE_UNKNOWN');self.assertNotIn('secret',record['lastError'])
        with self.assertRaisesRegex(ValueError,'ACTIVE'):self.prepare()
        self.assertEqual(self.launch.call_count,1)

    def test_private_approval_not_in_public_status(self):
        r=self.prepare();self.approve(r)
        value=control.status();self.assertNotIn('approval',value['request']);self.assertNotIn('config',value['request'])

    def test_durable_completion_and_no_instance_required(self):
        r=self.prepare();self.approve(r);self.start(r)
        remote=dict(available=True,data=dict(phase='completed',resultCommit=dict(sha256='a'*64)))
        with patch.object(control.lifecycle,'status',return_value=dict(instances=[dict(id='i-fake')])),patch.object(control,'_remote_status',return_value=remote):
            self.assertEqual(control.status()['request']['phase'],'REQUESTED_ONE_TIME_SPOT')
        with patch.object(control.lifecycle,'status',return_value=dict(instances=[])),patch.object(control,'_remote_status',return_value=remote):
            self.assertEqual(control.status()['request']['phase'],'COMPLETED')
        with patch.object(control.lifecycle,'status',side_effect=AssertionError('no further AWS reads')),patch.object(control,'_remote_status',side_effect=AssertionError('no further S3 reads')):
            self.assertTrue(control.status()['remote']['cached'])
        # Preparing does not create/relaunch; lifecycle separately requires fresh job ID.
        self.prepare();self.assertEqual(self.launch.call_count,1)

    def test_verified_terminated_instance_without_result_is_interrupted(self):
        r=self.prepare();self.approve(r);self.start(r)
        with patch.object(control.lifecycle,'status',return_value=dict(instances=[],lastInstance=dict(state='terminated'))),patch.object(control,'_remote_status',return_value=dict(available=False)):
            result=control.status()
        self.assertEqual(result['request']['phase'],'INTERRUPTED')
        self.assertEqual(result['request']['lastError'],'SPOT_TERMINATED_WITHOUT_DURABLE_FINAL_RESULT')

    def test_no_s3_reads_without_specific_cost_consent(self):
        with patch.object(control.lifecycle,'_api',side_effect=AssertionError('S3 request not approved')):
            self.assertFalse(control._remote_status(self.cfg)['available'])

    def test_stop_is_explicit_and_preserves_pending_until_termination_observed(self):
        r=self.prepare();self.approve(r);self.start(r)
        with patch.object(control.lifecycle,'terminate',return_value=dict(terminated=['i-fake'])) as terminate:
            result=control.stop(dict(requestId=r['requestId'],confirmation='STOP_OWN_SPOT'))
            self.assertEqual(result['phase'],'STOP_REQUESTED');terminate.assert_called_once()
        with self.assertRaisesRegex(ValueError,'ACTIVE'):self.prepare()

    def test_population_change_and_arbitrary_paths_are_blocked(self):
        for body in (dict(population=51),dict(population=20),dict(path='/etc/passwd'),dict(maxHours=9)):
            with self.assertRaises(ValueError):control.prepare(body)
        for ident in ('../a','/tmp/a','x'*32):
            with self.assertRaisesRegex(ValueError,'ID_INVALID'):control.approve(dict(requestId=ident,configHash='a',acknowledgeCost=True,confirmation='APPROVE_PAID_SPOT'))

    def test_real_http_authentication_and_safe_error(self):
        import server
        with patch.object(server.Handler,'log_message',lambda *a:None):
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            token='mock-spot-control';server.SESSIONS[token]=time.time()+60
            try:
                base='http://127.0.0.1:'+str(http.server_port)
                for path,data in [('plan',None),('status',None),('prepare',b'{}'),('approve',b'{}'),('start',b'{}'),('stop',b'{}')]:
                    req=urllib.request.Request(base+'/api/research/v083/spot/'+path,data=data)
                    with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req)
                    self.assertEqual(caught.exception.code,401)
                req=urllib.request.Request(base+'/api/research/v083/spot/plan',headers={'Authorization':'Bearer '+token})
                with urllib.request.urlopen(req) as response:self.assertEqual(json.load(response)['protocol'],control.PROTOCOL)
                with patch.object(control,'dispatch',side_effect=RuntimeError('secret/token must not leak')):
                    with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req)
                    self.assertNotIn('secret',caught.exception.read().decode())
            finally:server.SESSIONS.pop(token,None);http.shutdown();http.server_close();thread.join()


if __name__=='__main__':unittest.main()
