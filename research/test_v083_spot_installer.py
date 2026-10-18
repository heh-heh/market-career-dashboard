"""Offline installer regression: no operating files/services/AWS are touched."""
import contextlib
from datetime import datetime,timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def module():
    spec=importlib.util.spec_from_file_location('v083_spot_installer',Path(__file__).parents[1]/'scripts/install_v083_admin_api_overlay.py')
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


class Weekend:
    @staticmethod
    def now(tz=None):return datetime(2022,1,2,12,tzinfo=timezone.utc)


class SpotInstallerTests(unittest.TestCase):
    def test_surgical_routes_preserve_custom_admin_and_idempotent(self):
        m=module()
        server='''# local Token Broker settings retained
class Handler:
    def do_GET(self):
        path = self.path.split('?')[0]
        custom_local_get(path)
    def do_POST(self):
        path=self.path.split("?")[0]
        custom_local_post(path)
'''
        result=m.add_spot_routes(server)
        self.assertIn('custom_local_get(path)',result);self.assertIn('custom_local_post(path)',result)
        self.assertIn('# local Token Broker settings retained',result)
        self.assertEqual(result.count(m.SPOT_GET_ADD),1);self.assertEqual(result.count(m.SPOT_POST_ADD),1)
        self.assertEqual(m.add_spot_routes(result),result)
        self.assertIn('safe_error(exc)',result)
        compile(result,'server.py','exec')

    def test_published_server_routes_are_identical_to_overlay(self):
        m=module();server=(Path(__file__).parents[1]/'server.py').read_text()
        self.assertEqual(m.add_spot_routes(server),server)

    def test_unknown_partial_routes_fail_closed(self):
        m=module()
        text='''class Handler:
    def do_GET(self):
        path=self.path.split("?")[0]
        if path == "/api/research/v083/spot/plan": custom()
    def do_POST(self):
        path=self.path.split("?")[0]
        pass
'''
        with self.assertRaisesRegex(ValueError,'LOCAL_V083_SPOT_GET_CONFLICT'):m.add_spot_routes(text)

    def fixture(self,m,root,stack):
        target=root/'server.py';target.write_bytes(b'# operating local server\n')
        controller=root/'v083_web_jobs.py';controller.write_bytes(b'# operating controller\n')
        control=root/'v083_spot_control.py';control.write_bytes(b'# prior spot control\n')
        lifecycle=root/'research/spot/v083_spot_lifecycle.py';lifecycle.parent.mkdir(parents=True)
        lifecycle.write_bytes(b'# prior spot lifecycle\n')
        proof=root/'proof/source_revision.json';proof.parent.mkdir();proof.write_text('{"old":true}')
        original={p:p.read_bytes() for p in (target,controller,control,lifecycle,proof)}
        payloads={'v083_spot_control.py':b'# new safe control\n','research/spot/v083_spot_lifecycle.py':b'# new safe lifecycle\n'}
        bodies={name:dict(sha256=m.sha(body),beforeSha256=m.sha(path.read_bytes()),changed=True)
                for (name,body),path in zip(payloads.items(),(control,lifecycle))}
        server=b'# operating local server\n# authenticated spot routes\n';content=b'# next controller\n'
        audit=dict(serverBefore=m.sha(target.read_bytes()),serverAfter=m.sha(server),controllerSha=m.sha(content),
                   serverChanged=True,controllerChanged=True,sourceNeeded=False,proofChanged=True,spotModules=bodies)
        for name,value in [('TARGET',target),('CONTROLLER',controller),('PROOF',proof),('BACKUPS',root/'backups'),
                           ('SOURCE',root/'source'),('PIN','a'*40),('SPOT_TARGETS',dict(zip(payloads,(control,lifecycle))))]:
            stack.enter_context(patch.object(m,name,value))
        stack.enter_context(patch.object(m,'guard',return_value=(audit,server,content)))
        stack.enter_context(patch.object(m,'spot_payloads',return_value=payloads))
        stack.enter_context(patch.object(m,'active_workers',return_value=False))
        stack.enter_context(patch.object(m,'datetime',Weekend))
        restart=stack.enter_context(patch.object(m.subprocess,'run'))
        return original,audit,payloads,restart

    def test_check_readonly_and_all_module_rollback_on_failed_health(self):
        m=module()
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack,contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp);original,audit,payloads,restart=self.fixture(m,root,stack)
            with patch('sys.argv',['installer','--check']):m._main()
            self.assertEqual({p:p.read_bytes() for p in original},original)
            self.assertFalse((root/'backups').exists());restart.assert_not_called()
            with patch.object(m,'health',return_value=False),patch('sys.argv',['installer','--apply']):
                with self.assertRaisesRegex(ValueError,'API_HEALTH_CHECK_FAILED'):m._main()
            self.assertEqual({p:p.read_bytes() for p in original},original)
            backups=list((root/'backups').iterdir());self.assertEqual(len(backups),1)
            for name in payloads:self.assertTrue((backups[0]/name).is_file())

    def test_success_records_payload_proof_and_second_apply_no_restart(self):
        m=module()
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack,contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp);original,audit,payloads,restart=self.fixture(m,root,stack)
            with patch.object(m,'health',return_value=True),patch('sys.argv',['installer','--apply']):m._main()
            for name,body in payloads.items():self.assertEqual(m.SPOT_TARGETS[name].read_bytes(),body)
            proof=json.loads(m.PROOF.read_text())
            self.assertEqual(proof['spotModules'],{k:{'sha256':m.sha(v)} for k,v in payloads.items()})
            self.assertEqual(restart.call_count,1)
            nextaudit=dict(audit,serverChanged=False,controllerChanged=False,proofChanged=False,
                           spotModules={k:dict(v,changed=False) for k,v in audit['spotModules'].items()})
            with patch.object(m,'guard',return_value=(nextaudit,m.TARGET.read_bytes(),m.CONTROLLER.read_bytes())),patch('sys.argv',['installer','--apply']):m._main()
            self.assertEqual(restart.call_count,1)

    def test_active_worker_blocks_before_backup_or_write(self):
        m=module()
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack,contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp);original,audit,payloads,restart=self.fixture(m,root,stack)
            with patch.object(m,'active_workers',return_value=True),patch('sys.argv',['installer','--apply']):
                with self.assertRaisesRegex(ValueError,'RESEARCH_WORKER_RUNNING'):m._main()
            self.assertEqual({p:p.read_bytes() for p in original},original)
            self.assertFalse((root/'backups').exists());restart.assert_not_called()


if __name__=='__main__':unittest.main()
