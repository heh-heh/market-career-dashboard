"""No AWS/network. Execute generated shell only with non-provisioning stubs."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock,patch
from research.spot import v083_spot_lifecycle as spot
from research.spot.diagnose_v083_spot import diagnose
from research.test_v083_spot_lifecycle import sample,managed
import v083_spot_control as control


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cfg=sample();self.ec2=MagicMock()
        self.instance={**managed('i-0123456789abcdef0',self.cfg['job_id'],state='terminated'), 'ClientToken':self.cfg['job_id'], 'StateReason':{'Code':'Client.UserInitiatedShutdown'}}
        spot._write_registry(self.root,dict(job_id=self.cfg['job_id'],phase='CREATE_UNKNOWN'))
        self.ec2.describe_instances.return_value={'Reservations':[{'Instances':[self.instance]}]}

    def test_definitive_service_rejection_then_new_job_and_no_old_reuse(self):
        from botocore.exceptions import ClientError
        self.ec2.describe_instances.return_value={'Reservations':[]}
        self.ec2.run_instances.side_effect=ClientError({'Error':{'Code':'VcpuLimitExceeded'}},'RunInstances')
        (self.root/'launch-registry.json').unlink()
        with patch.dict(os.environ,{'V083_SPOT_LAUNCH_ENABLED':'YES','V083_SPOT_REAPER_READY':'YES'}),patch.object(spot.subprocess,'run',return_value=type('R',(),{'returncode':0})()),patch.object(spot,'preflight_readonly',return_value={'root_device':'/dev/sda1'}):
            def launch(c):return spot.launch(c,ec2=self.ec2,s3=MagicMock(),iam=MagicMock(),approval=True,approval_record=spot.issue_approval(c),control_dir=self.root)
            with self.assertRaises(ClientError):launch(self.cfg)
            self.assertEqual(spot.allocation_record(self.root)['phase'],'FAILED')
            new={**self.cfg,'job_id':'v083-ffffffffffffffff'}
            self.ec2.run_instances.side_effect=None;self.ec2.run_instances.return_value={'Instances':[{'InstanceId':'i-11111111111111111'}]}
            self.assertEqual(launch(new)['status'],'REQUESTED_ONE_TIME_SPOT')
            with self.assertRaisesRegex(ValueError,'JOB_ALREADY_ATTEMPTED'):launch(self.cfg)
        self.assertEqual(self.ec2.run_instances.call_count,2)

    def test_confirmed_terminated_allocation_reconciles_not_retries(self):
        r=spot.reconcile_job(self.cfg,ec2=self.ec2,control_dir=self.root)
        self.assertEqual(r['phase'],'INTERRUPTED');self.assertTrue(r['allInstancesTerminated'])
        self.assertEqual(spot.allocation_record(self.root)['phase'],'INTERRUPTED')
        self.ec2.run_instances.assert_not_called()
        self.assertEqual(json.loads((self.root/'reconciliations'/ (self.cfg['job_id']+'.json')).read_text())[-1]['phase'],'INTERRUPTED')

    def test_absence_different_token_or_tags_never_clear_uncertainty(self):
        for instances in ([],[{**self.instance,'ClientToken':'different'}],[{**self.instance,'Tags':[]}]):
            self.ec2.describe_instances.return_value={'Reservations':[{'Instances':instances}]}
            r=spot.reconcile_job(self.cfg,ec2=self.ec2,control_dir=self.root)
            self.assertFalse(r['allocationKnown']);self.assertEqual(r['phase'],'CREATE_UNKNOWN')
            self.assertEqual(spot.allocation_record(self.root)['phase'],'CREATE_UNKNOWN')

    def test_one_live_instance_blocks_terminal_transition(self):
        self.ec2.describe_instances.return_value={'Reservations':[{'Instances':[self.instance,{**self.instance,'InstanceId':'i-11111111111111111','State':{'Name':'running'}}]}]}
        r=spot.reconcile_job(self.cfg,ec2=self.ec2,control_dir=self.root)
        self.assertEqual(r['phase'],'RUNNING');self.assertFalse(r['allInstancesTerminated'])

    def test_duplicate_before_allocation_is_failed_not_unknown(self):
        from research.test_v083_spot_control import ControlTests
        fixture=ControlTests();fixture.setUp()
        try:
            request=fixture.prepare();fixture.approve(request)
            fixture.launch.side_effect=ValueError('SPOT_JOB_ALREADY_ATTEMPTED_NEW_APPROVAL_AND_JOB_REQUIRED')
            with self.assertRaisesRegex(ValueError,'JOB_ALREADY_ATTEMPTED'):fixture.start(request)
            stored=control._load_request(control._root(),request['requestId'])
            self.assertEqual(stored['phase'],'FAILED');self.assertIs(stored['allocationAttempted'],False)
        finally:fixture.doCleanups()

    def test_force_reconcile_legacy_requested_registry(self):
        spot._write_registry(self.root,dict(job_id=self.cfg['job_id'],phase='REQUESTED',instance_id=self.instance['InstanceId']))
        def response(**kw):
            active=any(f['Name']=='instance-state-name' for f in kw.get('Filters',[]))
            return {'Reservations':[] if active else [{'Instances':[self.instance]}]}
        self.ec2.describe_instances.side_effect=response
        r=spot.status(self.cfg,ec2=self.ec2,control_dir=self.root,force_reconcile=True)
        self.assertTrue(r['reconciliation']['allInstancesTerminated']);self.assertFalse(r['reconciliationRequired'])

    def test_unknown_web_request_uses_positive_termination_evidence(self):
        with patch.object(spot,'CONTROL_DIR',self.root),patch.object(control,'plan',return_value={'protocol':control.PROTOCOL}),patch.object(spot,'status',return_value={'instances':[],'reconciliation':{'allocationKnown':True,'allInstancesTerminated':True,'phase':'INTERRUPTED','reason':'SPOT_TERMINATED_WITHOUT_DURABLE_FINAL_RESULT'}}),patch.object(control,'_remote_status',return_value={'available':False}):
            with control._lock() as root:
                control._write(root/'latest.json',{'requestId':'a'*32})
                control._write(root/('a'*32+'.json'),{'requestId':'a'*32,'phase':'CREATE_UNKNOWN','config':self.cfg})
            self.assertEqual(control.status()['request']['phase'],'INTERRUPTED')


class BootstrapTests(unittest.TestCase):
    def test_python_dependency_failure_exports_only_fixed_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bin=root/'bin';bin.mkdir();job=root/'job';capture=root/'capture';capture.mkdir()
            def stub(name,body):
                p=bin/name;p.write_text('#!/bin/bash\n'+body);p.chmod(0o755)
            stub('shutdown','echo "$*" >> "$CAPTURE/shutdown"\n')
            stub('sync','exit 0\n')
            stub('timeout','shift; exec "$@"\n')
            stub('aws','if [ "$1 $2" = "s3 cp" ]; then cp "$3" "$CAPTURE/$(basename "$3")"; fi\n')
            stub('dnf','echo "No match for argument; authorization=VERY_SECRET"; exit 23\n')
            script=spot.make_user_data(sample()).replace('JOB=/opt/v083-one-shot','JOB='+str(job))
            syntax=subprocess.run(['bash','-n'],input=script,text=True,capture_output=True)
            self.assertEqual(syntax.returncode,0,syntax.stderr)
            run=subprocess.run(['bash','-s'],input=script,text=True,capture_output=True,env={**os.environ,'PATH':str(bin)+':'+os.environ['PATH'],'CAPTURE':str(capture)},timeout=15)
            self.assertEqual(run.returncode,23)
            diag=json.loads((capture/'bootstrap-status.json').read_text())
            self.assertEqual(diag['stage'],'python_packages');self.assertEqual(diag['errorCode'],'PACKAGE_NOT_AVAILABLE')
            self.assertEqual(diag['phase'],'FAILED');self.assertEqual(diag['exitCode'],23)
            self.assertNotIn('VERY_SECRET',run.stdout+(capture/'bootstrap-status.json').read_text())
            self.assertIn('-h now',(capture/'shutdown').read_text())
            self.assertFalse(diag['rawLogsExported'])

    def test_pinned_runtime_and_old_immutable_data_reference(self):
        c=sample();c.update(runtime_key='v083/runtime/new.tar.gz',runtime_version_id='version1',runtime_sha256='c'*64,bundle_job_id='v083-ffffffffffffffff',bundle_source_commit='d'*40)
        script=spot.make_user_data(c)
        self.assertIn('runtime/source/research/spot/v083_spot_worker.py',script)
        self.assertIn('--bundle-job-id v083-ffffffffffffffff',script)
        self.assertIn('--runtime-dir "$JOB/runtime"',script)
        self.assertNotIn("filter='data'",script);self.assertNotIn('aws s3 sync',script)
        self.assertLess(script.index("trap 'finish $?' EXIT"),script.index('shutdown -h +480'))
        c.pop('runtime_sha256')
        with self.assertRaisesRegex(ValueError,'REFERENCE_INCOMPLETE'):spot.validate_config(c)

    def test_diagnosis_never_exports_userdata_or_console_secrets(self):
        cfg=sample();ec2=MagicMock();i={**managed('i-0123456789abcdef0',cfg['job_id'],state='terminated'),'ClientToken':cfg['job_id']}
        ec2.describe_instances.return_value={'Reservations':[{'Instances':[i]}]}
        ec2.describe_instance_attribute.return_value={'UserData':{'Value':base64.b64encode(b'private-token SECRET').decode()}}
        ec2.get_console_output.return_value={'Output':base64.b64encode(b'ModuleNotFoundError authorization=SECRET').decode()}
        r=diagnose(cfg,i['InstanceId'],ec2=ec2,s3=MagicMock())
        self.assertIn('PYTHON_MODULE_MISSING',r['consoleErrorCodes']);self.assertNotIn('SECRET',json.dumps(r))
        self.assertIsNone(r['s3Diagnostics'])

    def test_terminated_instance_still_reports_evidence_when_console_unavailable(self):
        cfg=sample();ec2=MagicMock();i={**managed('i-0123456789abcdef0',cfg['job_id'],state='terminated'),'ClientToken':cfg['job_id']}
        ec2.describe_instances.return_value={'Reservations':[{'Instances':[i]}]}
        ec2.describe_instance_attribute.side_effect=RuntimeError('secret');ec2.get_console_output.side_effect=RuntimeError('secret')
        report=diagnose(cfg,i['InstanceId'],ec2=ec2)
        self.assertEqual(report['instanceState'],'terminated');self.assertIsNone(report['userDataSha256'])
        self.assertEqual(report['failureCause'],'EVIDENCE_INSUFFICIENT');self.assertNotIn('secret',json.dumps(report))

    def test_s3_bootstrap_fallback_is_explicitly_cost_gated(self):
        from botocore.exceptions import ClientError
        s3=MagicMock();cfg={**sample(),'monitor_reads_approved':True}
        error=ClientError({'Error':{'Code':'NoSuchKey'}},'GetObject')
        raw=json.dumps({'job_id':cfg['job_id'],'schema':'V083_BOOTSTRAP_1','finalOosGate':'CLOSED','phase':'FAILED','stage':'bundle_hash','exitCode':1,'errorCode':'SHA256_MISMATCH'}).encode()
        s3.get_object.side_effect=[error,{'Body':io.BytesIO(raw)}]
        self.assertEqual(control._remote_status(cfg,s3=s3)['data']['errorCode'],'SHA256_MISMATCH')
        s3.get_object.side_effect=[ClientError({'Error':{'Code':'AccessDenied'}},'GetObject'),{'Body':io.BytesIO(raw)}]
        self.assertEqual(control._remote_status(cfg,s3=s3)['data']['stage'],'bundle_hash')
        s3.get_object.reset_mock();self.assertFalse(control._remote_status(sample(),s3=s3)['available']);s3.get_object.assert_not_called()


if __name__=='__main__':unittest.main()
