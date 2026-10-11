"""Offline operational contract tests. No broker calls, real datasets or training."""
from dataclasses import replace
from pathlib import Path
import io
import json
import shutil
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from research.backtest_v4_strategies import exchange_sessions, MINUTE
from research.evolution.completeness import SessionCoverage, completeness_facts
from research.autonomous.config import LoopConfig
from research.autonomous.diagnostics import identify_baseline, BaselineCandidateError
from research.autonomous.artifact_reader import discover_runs
from research.autonomous.experiment_registry import Registry, write_json, read_json
from research.autonomous.export_client import ExportClient, extraction_disk_reserve
from research.autonomous.runner import choose_valid_v07_baseline
from research.autonomous.safety_gate import resource_gate, ResourcePauseError
from research.autonomous.storage import filesystem_status
from research.test_autonomous_research import fixed_artifacts


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.config=LoopConfig(registry=self.root/'registry',lock=self.root/'lock',min_free_bytes=0,disk_reserve_fraction=0)

    def test_holiday_not_requested_and_early_close_correct(self):
        cal=exchange_sessions('2024-07-03','2024-07-05')
        self.assertEqual(list(cal),['2024-07-03','2024-07-05'])
        self.assertEqual((cal['2024-07-03'][1]-cal['2024-07-03'][0])/MINUTE,210)
        facts=completeness_facts(dict(fromDate='2024-07-03',toDate='2024-07-05',requestedSessions=2,completedSessions=2,missingSessions=[]))
        self.assertIsNone(facts['rootCause'])

    def test_holiday_false_missing_is_calendar_error_not_accepted(self):
        facts=completeness_facts(dict(fromDate='2024-07-03',toDate='2024-07-05',missingSessions=['2024-07-04']))
        self.assertEqual(facts['rootCause'],'BASELINE_CALENDAR_MISMATCH')
        self.assertEqual(facts['nonTradingDates'],['2024-07-04'])

    def test_actual_trading_day_missing_strictly_rejected(self):
        facts=completeness_facts(dict(fromDate='2024-07-03',toDate='2024-07-05',missingSessions=['2024-07-05']))
        self.assertEqual(facts['rootCause'],'BASELINE_DATA_MISSING')

    def test_dst_session_clock_and_symbol_benchmark_coverage(self):
        for day in ('2024-03-08','2024-03-11','2024-11-01','2024-11-04','2024-07-03'):
            opening,closing=exchange_sessions(day,day)[day]
            rows={s:{opening+i*MINUTE:object() for i in range(int((closing-opening)/MINUTE))} for s in ('AAPL','QQQ')}
            coverage=SessionCoverage(rows)
            coverage.observe(day,rows,opening,closing)
            self.assertEqual(coverage.incomplete,0)
            rows.pop('QQQ')
            coverage.observe(day,rows,opening,closing)
            self.assertEqual(coverage.snapshot()['samples'][0]['failures'][0]['symbol'],'QQQ')

    def test_bounded_incomplete_coverage_samples(self):
        coverage=SessionCoverage(['AAPL'],sample_limit=2)
        for day,(a,b) in exchange_sessions('2024-06-03','2024-06-14').items():
            coverage.observe(day,{},a,b)
        self.assertEqual(len(coverage.samples),2)
        self.assertTrue(coverage.snapshot()['samplesTruncated'])

    def test_missing_holdout_explained_in_safe_fields(self):
        _,hold,_,_=fixed_artifacts(self.root/'bundle')
        p=hold/'run.json'; info=read_json(p)
        info.update(missingSessions=['2024-06-03'],resultsValid=False)
        write_json(p,info)
        with self.assertRaises(BaselineCandidateError) as caught:
            identify_baseline(discover_runs(self.root/'bundle'))
        rejected=caught.exception.as_dict()
        self.assertEqual(rejected['stage'],'internal_holdout')
        self.assertEqual(rejected['rootCause'],'BASELINE_DATA_MISSING')
        write_json(self.root/'diagnostic.json',rejected)
        self.assertEqual(read_json(self.root/'diagnostic.json')['missingDates'],['2024-06-03'])

    def test_invalid_all_candidates_keep_exact_reason(self):
        _,hold,_,_=fixed_artifacts(self.root/'bundle')
        info=read_json(hold/'run.json'); info.update(resultsValid=False)
        write_json(hold/'run.json',info)
        client=Mock(); client.collect.return_value=(self.root/'bundle','a'*64,{})
        choice=choose_valid_v07_baseline(client,{},[dict(id='b'*24,runId='dev')])
        self.assertIsNone(choice['baseline'])
        self.assertEqual(choice['rejected'][0]['rootCause'],'BASELINE_RESULTS_INVALID')

    def test_read_only_metadata_audit_identifies_fixture_baseline(self):
        from research.autonomous.audit_v07_baseline import audit
        train,hold,short,dev=fixed_artifacts(self.root/'bundle')
        before=(hold/'run.json').read_bytes()
        output=audit(self.root/'bundle')
        self.assertEqual(output['selection']['selectedRunId'],'dev')
        self.assertEqual(output['selection']['code'],'BASELINE_VALID')
        self.assertEqual(output['finalOosGate'],'CLOSED')
        self.assertEqual((hold/'run.json').read_bytes(),before)

    def test_incomplete_coverage_blocks_metrics_and_quarterly_selection(self):
        from research.autonomous.diagnostics import validation_metrics
        from research.autonomous.quarterly import quarterly_metrics
        train,hold,_,dev=fixed_artifacts(self.root/'bundle')
        summary=read_json(dev/'validation_summary.json')
        summary['resultsValid']=False
        with self.assertRaisesRegex(ValueError,'BASELINE_RESULTS_INVALID'):
            validation_metrics(summary,read_json(train/'final_population.json'),read_json(hold/'validation_summary.json'))
        info=read_json(hold/'run.json')
        info['sessionCoverage']={'incompleteSessions':1}
        write_json(hold/'run.json',info)
        with self.assertRaisesRegex(ValueError,'BASELINE_DATA_MISSING'):
            quarterly_metrics(train,hold)

    def test_small_export_reserve_and_4gb_cap_independent(self):
        cfg=replace(self.config,min_free_bytes=10*1024**3,disk_reserve_fraction=.1)
        need=extraction_disk_reserve(dict(archiveBytes=8*1024**2,bytesProcessed=10*1024**2),cfg)
        self.assertLess(need,100*1024**2)
        self.assertEqual(cfg.max_extracted_bytes,4*1024**3)
        gib=1024**3
        disk=shutil._ntuple_diskusage(40*gib,int(27.75*gib),int(12.25*gib))
        result=resource_gate(cfg,dict(cpuPct=0,memoryPct=30,swapPct=0),disk,expected_bytes=need)
        self.assertLess(result['requiredFreeGb'],10.1)

    def test_bind_mount_device_and_disk_query_follow_requested_path(self):
        mounted=self.root/'registry';mounted.mkdir()
        text=f'1 0 8:0 / / rw - ext4 /dev/root rw\n2 1 259:1 /evolution-autonomous {mounted} rw - ext4 /dev/nvme1n1 rw'
        with patch('research.autonomous.storage.shutil.disk_usage',return_value=shutil._ntuple_diskusage(251,45,206)) as disk:
            status=filesystem_status(mounted,mountinfo=text)
        disk.assert_called_once_with(mounted)
        self.assertEqual(status['device'],'/dev/nvme1n1')
        self.assertEqual(status['freeBytes'],206)
        with patch('research.autonomous.safety_gate.shutil.disk_usage',return_value=shutil._ntuple_diskusage(251,45,206)) as disk:
            resource_gate(self.config,dict(cpuPct=0,memoryPct=0,swapPct=0))
        disk.assert_called_once_with(self.config.registry)

    def test_hard_pressure_no_retry_and_success_samples_recorded(self):
        hard=dict(cpuPct=0,memoryPct=90,swapPct=0)
        disk=shutil._ntuple_diskusage(100,0,100)
        observed=[]
        with patch('research.autonomous.safety_gate.time.sleep') as sleep:
            with self.assertRaises(ResourcePauseError) as caught:
                resource_gate(self.config,hard,disk,observer=observed.append)
        sleep.assert_not_called()
        self.assertEqual(caught.exception.code,'HARD_RESOURCE_PRESSURE')
        self.assertEqual(observed[0]['samples'],1)

    def test_completed_dry_run_update_refused(self):
        r=Registry(self.config.registry); d=r.create(); r.update(d,'completed',dryRun=True)
        original=(d/'experiment.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'IMMUTABLE'):
            r.update(d,'failed',dryRun=False)
        self.assertEqual((d/'experiment.json').read_bytes(),original)

    def test_export_disabled_dependency_no_secret_body_echo(self):
        with patch.dict('os.environ',RESEARCH_EXPORT_TOKEN='test-secret'):
            client=ExportClient(self.config,opener=Mock())
        error=urllib.error.HTTPError('http://localhost',503,'disabled',{},io.BytesIO(json.dumps(dict(error='API_DISABLED',feature='research_export',secret='do-not-echo')).encode()))
        client.opener.open.side_effect=error
        with self.assertRaisesRegex(ValueError,'API_DEPENDENCY_DISABLED: dependency=research_export'):
            client.open('/api/research/export/catalog')

    def test_cli_feature_switches_fail_before_any_research(self):
        from research.autonomous.safety_gate import api_feature_gate
        settings=self.root/'api.json'
        settings.write_text(json.dumps({'features':{'autonomous_ai':False}}))
        with patch.dict('os.environ',API_FEATURES_FILE=str(settings)):
            with self.assertRaisesRegex(ValueError,'dependency=autonomous_ai'):
                api_feature_gate()


class FeatureSettingTests(unittest.TestCase):
    def setUp(self):
        from research.test_simple_momentum_api import server
        self.server=server
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(server,'API_FEATURES_FILE',Path(self.tmp.name)/'api.json').start()
        patch.object(server,'API_FEATURE_STATE',server._api_feature_defaults()).start()
        self.live=Mock();self.paper=Mock();self.simple=Mock()
        patch.object(server,'LIVE_TRADER',self.live).start()
        patch.object(server,'PAPER_V3',self.paper).start()
        patch.object(server,'SIMPLE_PAPER',self.simple).start()
        patch.object(server,'TRADING_STATE',dict(engine_enabled=True,live_armed=True,live_auto_enabled=True)).start()

    def test_persistence_live_off_and_corruption_fail_closed(self):
        s=self.server;s.set_api_feature('live_trading',False)
        self.assertFalse(s._load_api_feature_state()['live_trading'])
        self.live.disarm.assert_called_once();self.live.set_engine.assert_called_with(False)
        self.assertFalse(s.TRADING_STATE['live_auto_enabled'])
        s.API_FEATURES_FILE.write_text('{broken')
        self.assertFalse(s._load_api_feature_state()['live_trading'])

    def test_engine_off_even_when_disarm_status_fails(self):
        self.live.disarm.side_effect=RuntimeError('synthetic status failure')
        self.server.set_api_feature('live_trading',False)
        self.live.set_engine.assert_called_with(False)
        self.assertFalse(self.server.TRADING_STATE['live_auto_enabled'])

    def test_paper_off_and_control_plane_never_gated(self):
        s=self.server;s.set_api_feature('paper_trading',False)
        self.paper.set_enabled.assert_called_with(False);self.simple.set_enabled.assert_called_with(False)
        for route in ('/api/admin/api-features','/api/login','/api/admin/console','/api/admin/git','/api/admin/collectors/control','/api/admin/ai/status','/api/admin/ai/log'):
            self.assertEqual(s.api_feature_requirements(route,'POST'),[])

    def test_ai_export_dependency_and_helpers_fail_before_process(self):
        s=self.server;s.set_api_feature('research_export',False)
        self.assertEqual(s.disabled_api_feature('/api/admin/ai/start','POST'),'research_export')
        self.assertEqual(s.api_disabled_response('/api/admin/ai/start','research_export')['error'],'DEPENDENCY_DISABLED')
        with patch.object(s.subprocess,'Popen',side_effect=AssertionError('no process allowed')):
            with self.assertRaisesRegex(ValueError,'API_DEPENDENCY_DISABLED'):
                s.start_autonomous_research('dry-run')

    def test_changes_require_admin_auth_http(self):
        from http.server import ThreadingHTTPServer
        import threading,urllib.request
        s=self.server
        with patch.object(s.Handler,'log_message',lambda *args:None):
            http=ThreadingHTTPServer(('127.0.0.1',0),s.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            try:
                request=urllib.request.Request(f'http://127.0.0.1:{http.server_port}/api/admin/api-features',data=b'{"feature":"live_trading","enabled":false}',headers={'Content-Type':'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request,timeout=3)
                self.assertEqual(caught.exception.code,401)
                self.assertTrue(s.api_feature_enabled('live_trading'))
            finally:
                http.shutdown();thread.join();http.server_close()


if __name__=='__main__':unittest.main()
