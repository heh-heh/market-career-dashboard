"""No network/Toss/systemd calls: collection range, migration and deploy controls."""
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import research_collection_scheduler as scheduler
from scripts import collect_research_1m as worker
from research.test_research_collection_scheduler import tick


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def engine(self, until=worker.COLLECTION_UNTIL, fetch=None):
        return worker.Backfill(self.root, ['AAPL'], self.root/'permit', worker.COLLECTION_SINCE, until, fetch or Mock())

    def run_step(self, engine):
        with patch.object(engine,'allowed',return_value=True), patch.object(worker,'token',return_value='synthetic'), patch.object(worker,'check_disk'), patch.object(worker,'coverage',return_value={
                'sessionCounts': {'2024-07-26': {'expected':390, 'observed':0}}, 'missingDates':['2024-07-26']}):
            engine.step_symbol('AAPL'); engine.persist()

    def test_october_inclusive_et_boundary_and_final_oos_still_closed(self):
        from research.collection_download import source_rows
        self.assertEqual(scheduler.aware(worker.COLLECTION_UNTIL).isoformat(),'2026-11-01T03:59:59+00:00')
        worker.validate_collection_range(worker.COLLECTION_SINCE, worker.COLLECTION_UNTIL)
        with self.assertRaisesRegex(ValueError,'COLLECTION_RANGE'):
            worker.validate_collection_range(worker.COLLECTION_SINCE,'2026-11-01T00:00:00-04:00')
        with self.assertRaises(ValueError): list(source_rows(self.root,'AAPL',2026))

    def test_future_collection_cursor_is_last_completed_minute(self):
        now=scheduler.aware('2026-10-11T12:34:56.123456+00:00')
        self.assertEqual(worker.collection_cursor(worker.COLLECTION_UNTIL,now),'2026-10-11T12:34:00+00:00')

    def test_control_is_group_readable_even_with_private_api_umask(self):
        old=os.umask(0o077)
        try: scheduler.save(self.root/'control.json',{'paused':False},share_group=True)
        finally: os.umask(old)
        self.assertEqual((self.root/'control.json').stat().st_mode & 0o777,0o640)
        self.assertEqual((self.root/'control.json').stat().st_gid,self.root.stat().st_gid)

    def test_predictable_temporary_symlink_is_not_followed(self):
        victim=self.root/'victim';victim.write_text('preserve')
        (self.root/f'control.json.{os.getpid()}.tmp').symlink_to(victim)
        scheduler.save(self.root/'control.json',{'paused':True},share_group=True)
        self.assertEqual(victim.read_text(),'preserve')

    def test_newest_cursor_before_gap_and_persistent_descending_pages(self):
        fetch = Mock(return_value={'candles':[tick('2026-10-01T14:31:00+00:00')], 'nextBefore':'2026-09-30T20:00:00+00:00'})
        engine=self.engine('2026-10-01T23:59:59-04:00',fetch)
        self.run_step(engine)
        self.assertEqual(fetch.call_args.args[2],scheduler.aware(engine.until).isoformat())
        self.assertIsNone(engine.progress['AAPL']['currentGap'])
        restarted=self.engine(engine.until,fetch)
        fetch.return_value={'candles':[], 'nextBefore':None}
        self.run_step(restarted)
        self.assertEqual(fetch.call_args.args[2],'2026-09-30T20:00:00+00:00')
        self.assertEqual(restarted.progress['AAPL']['direction'],'newest_to_oldest')

    def test_range_widening_backs_up_old_checkpoints_without_deleting_rows(self):
        old=self.engine('2025-12-31T23:59:59+00:00')
        old.state['symbols']['AAPL']={'initialized':True,'historyCursor':'2022-01-01T00:00:00+00:00','gapQueue':[],'coverage':{},'terminal':'API_BOUNDARY_UNVERIFIED'}
        old.persist()
        before=(self.root/'_scheduler_AAPL.json').read_bytes()
        db=worker.open_research_db(self.root,'AAPL')
        worker.insert_page(db,[tick()],old.since,old.until); db.close()
        upgraded=self.engine(fetch=Mock(return_value={'candles':[tick('2026-10-01T14:31:00+00:00')], 'nextBefore':'2026-09-30T20:00:00+00:00'}))
        self.run_step(upgraded)
        backups=list(self.root.glob('_scheduler_AAPL.range-backup-*'))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_bytes(),before)
        self.assertEqual(len(list(self.root.glob('_scheduler_checkpoint.range-backup-*'))),1)
        with sqlite3.connect(self.root/'AAPL.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM candles').fetchone()[0],2)
        resumed=self.engine()
        self.assertEqual(resumed.state['range'],[worker.COLLECTION_SINCE, worker.COLLECTION_UNTIL])

    def test_narrowing_is_refused(self):
        self.engine().persist()
        with self.assertRaisesRegex(ValueError,'NARROWING_BLOCKED'): self.engine('2025-12-31T23:59:59+00:00')

    def test_source_timestamp_in_future_is_not_stored(self):
        db=worker.open_research_db(self.root,'AAPL');self.addCleanup(db.close)
        with patch.object(worker,'datetime',wraps=datetime) as clock:
            clock.now.return_value=scheduler.aware('2026-10-01T12:00:00+00:00')
            with self.assertRaisesRegex(ValueError,'API_FUTURE_TIMESTAMP'):
                worker.insert_page(db,[tick('2026-10-02T14:31:00+00:00')],worker.COLLECTION_SINCE,worker.COLLECTION_UNTIL)
        self.assertEqual(db.execute('SELECT count(*) FROM candles').fetchone()[0],0)

    def test_recent_edge_catchup_does_not_reset_history_cursor_or_counter(self):
        fetch=Mock(return_value={'candles':[tick('2026-10-01T14:31:00+00:00')], 'nextBefore':'2026-09-30T20:00:00+00:00'})
        engine=self.engine(fetch=fetch);self.run_step(engine)
        state=engine.state['symbols']['AAPL']
        state.update(historyCursor='2022-01-01T00:00:00+00:00', lastHeadAt=time.time()-13*3600, zeroAddedPages=4)
        engine.persist()
        fetch.return_value={'candles':[], 'nextBefore':None}
        self.run_step(engine)
        self.assertEqual(state['historyCursor'],'2022-01-01T00:00:00+00:00')
        self.assertEqual(state['zeroAddedPages'],4)
        self.assertNotIn('headCursor',state)
        self.assertGreater(scheduler.aware(fetch.call_args.args[2]),scheduler.aware('2025-12-31T23:59:00+00:00'))

    def test_no_control_file_keeps_scheduler_paused(self):
        args=SimpleNamespace(state_dir=self.root,preopen_seconds=90)
        launch=Mock();engine=scheduler.Scheduler(args,launch)
        with patch.object(scheduler,'official_regular',return_value=None),patch.object(scheduler.time,'sleep',side_effect=lambda _:setattr(engine,'stopping',True)):
            engine.run()
        launch.assert_not_called()

    def test_tick_to_backfill_waits_for_exit_and_revokes_permission(self):
        args=SimpleNamespace(state_dir=self.root,data_dir=self.root/'candles',tick_dir=self.root/'ticks',symbols_file=None,preopen_seconds=90)
        tick_child=Mock(pid=1);tick_child.poll.return_value=None
        backfill_child=Mock(pid=2);backfill_child.poll.return_value=None
        launch=Mock(side_effect=[tick_child,backfill_child]);engine=scheduler.Scheduler(args,launch)
        opened='2024-07-26T15:00:00+00:00';closed='2024-07-26T22:00:00+00:00'
        engine.step(scheduler.choose_mode(opened,scheduler.regular_clock(opened)),scheduler.aware(opened))
        result=engine.step(scheduler.choose_mode(closed,scheduler.regular_clock(closed)),scheduler.aware(closed))
        self.assertEqual(result['mode'],'SWITCHING')
        self.assertEqual(launch.call_count,1)
        self.assertFalse(scheduler.backfill_permitted(self.root/'permit.json',scheduler.aware(closed)))
        tick_child.terminate.assert_called_once()
        tick_child.poll.return_value=0
        engine.step(scheduler.choose_mode(closed,scheduler.regular_clock(closed)),scheduler.aware(closed))
        engine.retry_at=0
        engine.step(scheduler.choose_mode(closed,scheduler.regular_clock(closed)),scheduler.aware(closed))
        self.assertEqual(engine.role,'backfill')

    def test_deployment_script_syntax_and_preservation_guards(self):
        file=scheduler.ROOT/'scripts/deploy_collection.sh'
        subprocess.run(['bash','-n',str(file)],check=True)
        source=file.read_text()
        self.assertNotIn('reset --hard',source)
        self.assertNotIn('clean -',source)
        self.assertNotIn('rm ',source)
        self.assertNotIn('git merge',source.replace('Git-merge',''))
        self.assertIn('deploy_collection_worktree.py',source)
        self.assertIn('--source',source)
        self.assertIn('--target',source)

    def test_pages_build_contains_only_public_assets(self):
        workflow=(scheduler.ROOT/'.github/workflows/pages.yml').read_text()
        block=workflow.split('      - name: Assemble public UI only\n')[1].split('      - name: Upload artifact')[0]
        commands='\n'.join(line[10:] for line in block.splitlines() if line.startswith('          '))
        assets=['index.html','admin.html','collection.html','backtest-v2.html','research-export.js','research-export.css','autonomous-research.js','data/dashboard.json','portfolio/index.html']
        for name in assets:
            target=self.root/name;target.parent.mkdir(exist_ok=True)
            shutil.copyfile(scheduler.ROOT/name,target)
        (self.root/'.env').write_text('synthetic-secret')
        (self.root/'data/toss_1m').mkdir();(self.root/'data/toss_1m/raw.csv').write_text('private-data')
        subprocess.run(['bash','-e','-c',commands],cwd=self.root,check=True)
        files={str(p.relative_to(self.root/'_site')) for p in (self.root/'_site').rglob('*') if p.is_file()}
        self.assertEqual(files,set(assets)|{'.nojekyll','deployment.json'})


class CollectionPageTests(unittest.TestCase):
    def test_existing_deploy_apply_refreshes_only_installed_scheduler(self):
        import server
        for active,expected in (('active',True),('inactive',False)):
            with patch.object(server,'_autonomous_processes',return_value=[]),patch.object(server,'_v07_rebuild_processes',return_value=[]),patch.object(server,'_git_branch',return_value='strategy-engine-v3'),patch.object(server,'_git_run',return_value=(0,'')),patch.object(server,'_install_runtime_requirements',return_value=(0,'OK')),patch.object(server,'_systemctl_value',return_value=(0,active)),patch.object(server,'_console_run',return_value=(0,'OK')) as commands:
                self.assertEqual(server.deploy_apply()[0],0)
            calls=[c.args[0] for c in commands.call_args_list]
            self.assertEqual(['systemctl','--no-block','restart','research-collection-scheduler.service'] in calls,expected)
            self.assertEqual(calls[-1],['systemctl','--no-block','restart','market-career-dashboard.service'])

    def test_page_and_authenticated_ready_only_start(self):
        import server
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            session='collection-deploy-test';server.SESSIONS[session]=time.time()+60
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            def request(path,auth=False,body=None):
                headers={'Authorization':'Bearer '+session} if auth else {}
                return urlopen(Request('http://127.0.0.1:'+str(http.server_port)+path,headers=headers,data=json.dumps(body).encode() if body is not None else None),timeout=3)
            try:
                with patch.object(scheduler,'STATE_DIR',state):
                    with request('/api/research/collection50/page') as response:
                        self.assertEqual(response.headers['Cache-Control'],'no-store')
                        page=response.read().decode()
                        self.assertIn('조회 전용',page)
                        self.assertIn('admin.html#collectors',page)
                        self.assertNotIn('id="start"',page)
                        self.assertNotIn('id="pause"',page)
                    for auth,expected in ((False,401),(True,409)):
                        with self.assertRaises(HTTPError) as exc:request('/api/research/collection50/control',auth,{'paused':False})
                        self.assertEqual(exc.exception.code,expected)
                    scheduler.save(state/'status.json',{'mode':'PAUSED','automatic':True,'updatedAt':datetime.now(timezone.utc).isoformat()})
                    with request('/api/research/collection50/control',True,{'paused':False}) as response:self.assertFalse(json.load(response)['paused'])
                    self.assertFalse(json.loads((state/'control.json').read_text())['paused'])
                    with request('/api/research/collection50/status') as response:
                        value=json.load(response)
                        self.assertEqual(value['collectionTo'],'2026-10-31')
                        self.assertEqual(value['finalOosGate'],'CLOSED')
                    from research.collection_activity import emit
                    with patch.dict(os.environ, {'COLLECTION_50_DIR': str(state)}):
                        self.assertTrue(emit(state, 'CANDLE_PAGE', symbol='AAPL', page=5, added=42, stored=500,
                                             authorization='Bearer private'))
                        with request('/api/research/collection50/logs') as response:
                            self.assertEqual(response.headers['Cache-Control'], 'no-store')
                            result = json.load(response)
                            self.assertTrue(result['ok'])
                            self.assertEqual(result['events'][-1]['symbol'], 'AAPL')
                            self.assertEqual(result['events'][-1]['added'], 42)
                            self.assertNotIn('private', json.dumps(result))
                    scheduler.save(state/'status.json',{'mode':'PAUSED','automatic':False,'updatedAt':datetime.now(timezone.utc).isoformat()})
                    with self.assertRaises(HTTPError) as exc:request('/api/research/collection50/control',True,{'paused':False})
                    self.assertEqual(exc.exception.code,409)
            finally:
                http.shutdown();thread.join();http.server_close();server.SESSIONS.pop(session,None)


if __name__=='__main__': unittest.main()
