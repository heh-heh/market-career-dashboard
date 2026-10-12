"""Offline synthetic scheduler, backfill, rate limiter and download regressions."""
import csv
import gzip
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import research_collection_scheduler as scheduler
from scripts import collect_research_1m as backfill
from scripts import collect_toss_1m as legacy
from research.collection_download import source_rows, year_download
import toss_rate_limit

UTC = timezone.utc

def tick(at='2024-07-26T13:31:00+00:00', price='100'):
    return dict(timestamp=at, openPrice=price, highPrice=price, lowPrice=price,
                closePrice=price, volume='1', currency='USD')


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def official(self, when):
        return scheduler.regular_clock(when)

    def test_open_closed_and_tick_outage_never_authorizes_backfill(self):
        now = '2024-07-26T15:00:00+00:00'
        self.assertEqual(scheduler.choose_mode(now, self.official(now))['mode'], 'MARKET_OPEN')
        self.assertEqual(scheduler.choose_mode(now, None)['mode'], 'MARKET_OPEN')
        now = '2024-07-26T22:00:00+00:00'
        self.assertEqual(scheduler.choose_mode(now, self.official(now))['mode'], 'MARKET_CLOSED')
        self.assertEqual(scheduler.choose_mode(now, None)['mode'], 'ERROR')

    def test_dst_holiday_weekend_and_early_close(self):
        self.assertEqual(self.official('2024-03-08T00:00:00+00:00')['openAt'][11:16], '14:30')
        self.assertEqual(self.official('2024-03-11T00:00:00+00:00')['openAt'][11:16], '13:30')
        for now in ('2024-07-04T15:00:00+00:00', '2024-07-06T15:00:00+00:00'):
            self.assertFalse(self.official(now)['isOpen'])
        self.assertEqual(self.official('2024-11-29T17:00:00+00:00')['closeAt'][11:16], '18:00')
        self.assertFalse(self.official('2024-11-29T18:00:00+00:00')['isOpen'])

    def test_conflict_paused_and_preopen(self):
        now = '2024-07-26T13:29:00+00:00'
        clock = self.official(now)
        self.assertEqual(scheduler.choose_mode(now, clock)['mode'], 'SWITCHING')
        self.assertEqual(scheduler.choose_mode(now, clock, paused=True)['mode'], 'PAUSED')
        self.assertEqual(scheduler.choose_mode(now, {**clock,'closeAt':'2024-07-26T19:00:00+00:00'})['mode'], 'ERROR')
        with self.assertRaises(ValueError): scheduler.aware('2024-07-26T13:29:00')

    def test_transition_waits_for_child_flush(self):
        args = SimpleNamespace(state_dir=self.root,data_dir=self.root/'data',symbols_file=self.root/'symbols',tick_dir=self.root/'ticks',preopen_seconds=90)
        child = Mock(pid=123)
        child.poll.return_value = None
        launch = Mock(return_value=child)
        engine = scheduler.Scheduler(args, launch)
        now = scheduler.aware('2024-07-26T12:00:00+00:00')
        engine.step(scheduler.choose_mode(now, self.official(now)), now)
        self.assertEqual(engine.role, 'backfill')
        self.assertTrue(scheduler.backfill_permitted(self.root/'permit.json', now))
        opened = scheduler.aware('2024-07-26T13:30:00+00:00')
        result = engine.step(scheduler.choose_mode(opened, self.official(opened)), opened)
        self.assertEqual(result['mode'], 'SWITCHING')
        self.assertEqual(launch.call_count, 1)
        child.terminate.assert_called_once()
        self.assertFalse(scheduler.backfill_permitted(self.root/'permit.json', opened))
        child.poll.return_value = 0
        engine.step(scheduler.choose_mode(opened, self.official(opened)), opened)
        engine.retry_at = 0
        engine.step(scheduler.choose_mode(opened, self.official(opened)), opened)
        self.assertEqual(engine.role, 'tick')
        self.assertIn('--regular-only', launch.call_args.args[0])

    def test_lease_expiry_and_restart_lock(self):
        now = scheduler.aware('2024-07-26T12:00:00+00:00')
        file = self.root/'permit.json'
        scheduler.save(file,dict(mode='MARKET_CLOSED', leaseExpiresAt=(now+timedelta(seconds=4)).isoformat(),requestDeadline='2024-07-26T13:28:30+00:00'))
        self.assertTrue(scheduler.backfill_permitted(file, now))
        self.assertFalse(scheduler.backfill_permitted(file, now+timedelta(seconds=5)))
        with scheduler.exclusive_lock(self.root/'lock'):
            with self.assertRaises(RuntimeError):
                with scheduler.exclusive_lock(self.root/'lock'): pass
        with scheduler.exclusive_lock(self.root/'lock'): pass

    def test_multigroup_limiter_keeps_other_groups(self):
        file = self.root/'rate'
        with patch.object(toss_rate_limit,'LOCK_PATH',file):
            toss_rate_limit.wait_for_slot('MARKET_INFO')
            toss_rate_limit.wait_for_slot('MARKET_DATA_CHART')
            toss_rate_limit.wait_for_slot('MARKET_INFO')
        self.assertIn('MARKET_DATA_CHART=',file.read_text())
        self.assertIn('MARKET_INFO=',file.read_text())

    def test_guard_checked_after_shared_rate_wait(self):
        with patch.object(legacy, 'wait_for_slot'), patch.object(legacy,'urlopen') as network:
            with self.assertRaisesRegex(RuntimeError, 'BACKFILL_REQUEST_BLOCKED'):
                legacy.fetch_page('test', 'AAPL', request_guard=Mock(side_effect=[True,False]), error_recorder=Mock())
            network.assert_not_called()

    def test_http401_refresh_and429_retry_after(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"result":{"candles":[]}}'
        def error(code,body=b'',headers=None):return HTTPError('https://offline.invalid',code,'error',headers or {},io.BytesIO(body))
        with patch.object(legacy,'urlopen',side_effect=[error(401,b'invalid-token'),response]),patch.object(legacy,'wait_for_slot'),patch.object(legacy.time,'sleep'):
            refresh=Mock(return_value='refreshed')
            self.assertEqual(legacy.fetch_page('test','AAPL',refresh_token=refresh,error_recorder=Mock()), {'candles':[]})
            refresh.assert_called_once()
        with patch.object(legacy,'urlopen',side_effect=[error(429,headers={'Retry-After':'45'}),response]),patch.object(legacy,'wait_for_slot'),patch.object(legacy.time,'sleep') as sleep, patch.object(legacy,'mark_rate_limited'):
            legacy.fetch_page('test','AAPL',error_recorder=Mock())
            sleep.assert_called_once_with(45)

    def test_broker_unavailable_no_local_mint(self):
        import toss_auth
        with patch.dict(os.environ, {'TOSS_TOKEN_SOCKET':'/nonexistent.sock'}),patch.object(toss_auth.urllib.request,'urlopen') as mint:
            with self.assertRaises(OSError): legacy.token()
            mint.assert_not_called()

    def test_decimal_identical_duplicate_offsets_and_conflict_rollback(self):
        db=backfill.open_research_db(self.root,'AAPL');self.addCleanup(db.close)
        since, until='2024-07-26T00:00:00+00:00','2024-07-26T23:59:00+00:00'
        self.assertEqual(backfill.insert_page(db,[tick()],since,until)[0],1)
        self.assertEqual(backfill.insert_page(db,[tick('2024-07-26T22:31:00+09:00')],since,until)[0],0)
        with self.assertRaisesRegex(ValueError,'CONFLICTING_DUPLICATE'):
            backfill.insert_page(db,[tick('2024-07-26T13:32:00+00:00'),tick(price='101')],since,until)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM candles').fetchone()[0],1)

    def test_end_timestamp_full_day_coverage_and_missing_gap(self):
        db=backfill.open_research_db(self.root,'AAPL');self.addCleanup(db.close)
        start=scheduler.aware('2024-07-26T13:31:00+00:00')
        bars=[tick((start+timedelta(minutes=i)).isoformat()) for i in range(390)]
        since,until='2024-07-26T00:00:00+00:00','2024-07-26T23:59:00+00:00'
        backfill.insert_page(db,bars,since,until)
        self.assertEqual(backfill.coverage(db,since,until)['verifiedTradingDays'],1)
        db.execute('DELETE FROM candles WHERE timestamp=?',(bars[30]['timestamp'],));db.commit()
        self.assertEqual(backfill.coverage(db,since,until)['missingTradingDays'],1)

    def test_page_outside_gap_does_not_claim_complete_and_checkpoint_resumes(self):
        since,until='2024-07-26T00:00:00+00:00','2024-07-26T23:59:00+00:00'
        fetch=Mock(return_value={'candles':[tick('2024-07-23T13:31:00+00:00')], 'nextBefore':'2024-07-23T13:30:00+00:00'})
        worker=backfill.Backfill(self.root,['AAPL'],self.root/'permit',since,until,fetch)
        with patch.object(worker,'allowed',return_value=True),patch.object(backfill,'token',return_value='test'),patch.object(backfill,'check_disk'):
            worker.step_symbol('AAPL');worker.persist()
        record=worker.progress['AAPL']
        self.assertEqual(record['knownGapOutcomes']['2024-07-26']['observed'],0)
        self.assertEqual(record['verifiedTradingDays'],0)
        resumed=backfill.Backfill(self.root,['AAPL'],self.root/'permit',since,until,fetch)
        self.assertEqual(resumed.state["nextIndex"],worker.state["nextIndex"])
        self.assertEqual(json.loads((self.root/"_scheduler_AAPL.json").read_text()),worker.state["symbols"]["AAPL"])

    def test_fifty_symbol_rotation_errors_do_not_block_others(self):
        symbols=['S'+str(i) for i in range(50)]
        worker=backfill.Backfill(self.root,symbols,self.root/'permit','2024-01-01T00:00:00+00:00','2024-12-31T23:00:00+00:00')
        observed=[]
        def step(symbol):
            observed.append(symbol)
            if symbol=='S49':worker.stop=True
            if symbol=='S0':raise RuntimeError('MOCK_ERROR')
        with patch.object(worker,'allowed',side_effect=lambda:not worker.stop),patch.object(worker,'step_symbol',side_effect=step),patch.object(backfill.time,'sleep'):
            worker.run()
        self.assertEqual(observed,symbols)
        self.assertEqual(worker.progress['S0']['errorCode'],'MOCK_ERROR')

    def test_invalid_ohlcv_collection_boundary_and_research_final_oos(self):
        db=backfill.open_research_db(self.root,'AAPL');self.addCleanup(db.close)
        with self.assertRaises(ValueError):backfill.insert_page(db,[{**tick(),'lowPrice':'200'}],'2024-01-01T00:00:00+00:00','2025-12-31T23:59:00+00:00')
        with self.assertRaisesRegex(ValueError,'COLLECTION_RANGE'):
            backfill.insert_page(db,[tick('2026-01-05T15:00:00+00:00')],'2024-01-01T00:00:00+00:00','2026-12-31T23:59:00+00:00')
        self.assertEqual(backfill.insert_page(db,[tick('2026-01-05T15:00:00+00:00')],backfill.COLLECTION_SINCE,backfill.COLLECTION_UNTIL)[0],1)
        with self.assertRaises(ValueError):list(source_rows(self.root,'AAPL',2026))
        with self.assertRaises(ValueError):list(source_rows(self.root,'../AAPL',2024))

    def test_existing_db_backup_precedes_additive_index(self):
        file=self.root/'AAPL.sqlite3'
        with sqlite3.connect(file) as db:db.execute('CREATE TABLE candles (timestamp TEXT PRIMARY KEY,open TEXT,high TEXT,low TEXT,close TEXT,volume TEXT,currency TEXT)')
        with patch.object(backfill,'check_disk'):
            db=backfill.open_research_db(self.root,'AAPL');db.close()
        marker=json.loads((self.root/'AAPL.scheduler-backup.json').read_text())
        self.assertTrue((self.root/marker['backup']).exists())
        with sqlite3.connect(self.root/marker['backup']) as original:
            self.assertFalse(original.execute("SELECT name FROM sqlite_master WHERE name='scheduler_candle_instant'").fetchone())

    def test_sqlite_download_before_csv_export_sha_and_readonly(self):
        data=self.root/'data';data.mkdir()
        db=backfill.open_research_db(data,'AAPL')
        backfill.insert_page(db,[tick()],'2024-01-01T00:00:00+00:00','2024-12-31T23:59:00+00:00');db.close()
        reserve=SimpleNamespace(free=100*1024**3,total=200*1024**3)
        with patch('research.collection_download.shutil.disk_usage',return_value=reserve):
            with year_download(data,'AAPL',2024,self.root/'export') as (file,meta):
                self.assertEqual(meta['sha256'],hashlib.sha256(file.read_bytes()).hexdigest())
                self.assertEqual(meta['rows'],1)
                self.assertEqual(len(list(csv.DictReader(gzip.open(file,'rt')))),1)
                saved=file
            self.assertFalse(saved.exists())
        self.assertFalse((data/'AAPL.csv.gz').exists())

    def test_download_disk_guard_and_symlink(self):
        with patch('research.collection_download.shutil.disk_usage',return_value=SimpleNamespace(free=1,total=100*1024**3)):
            with self.assertRaises(RuntimeError):
                with year_download(self.root/'source','AAPL',2024,self.root/'output'):pass
        (self.root/'link').symlink_to(self.root/'source')
        with self.assertRaises(ValueError):list(source_rows(self.root/'link','AAPL',2024))

    def test_rate_cooldown_only_blocks_its_group(self):
        file = self.root/'rate'
        with patch.object(toss_rate_limit, 'LOCK_PATH', file):
            toss_rate_limit.mark_rate_limited('MARKET_DATA_CHART', 30)
            toss_rate_limit.wait_for_slot('MARKET_INFO')
            self.assertIn('cooldown:MARKET_DATA_CHART=', file.read_text())
            guard = Mock(return_value=False)
            with self.assertRaisesRegex(RuntimeError, 'BACKFILL_REQUEST_BLOCKED'):
                toss_rate_limit.wait_for_slot('MARKET_DATA_CHART', request_guard=guard)

    def test_long_retry_after_deferred_without_early_request(self):
        error = HTTPError('https://offline.invalid',429,'error',{'Retry-After':'120'},io.BytesIO(b''))
        with patch.object(legacy, 'urlopen', side_effect=error) as network, patch.object(legacy,'wait_for_slot'), patch.object(legacy,'mark_rate_limited') as mark:
            with self.assertRaisesRegex(RuntimeError,'RATE_LIMIT_RETRY_DEFERRED') as exc:
                legacy.fetch_page('test','AAPL',error_recorder=Mock(),request_guard=lambda:True)
            self.assertEqual(exc.exception.retry_after,120)
            self.assertEqual(network.call_count,1)
            mark.assert_called_once_with('MARKET_DATA_CHART',120)

    def test_future_response_rejected_before_database_insert(self):
        worker=backfill.Backfill(self.root,['AAPL'],self.root/'permit','2024-07-26T00:00:00+00:00','2024-07-26T23:59:00+00:00',Mock(return_value={'candles':[tick('2024-07-29T13:31:00+00:00')]}))
        with patch.object(worker,'allowed',return_value=True),patch.object(backfill,'token',return_value='test'),patch.object(backfill,'check_disk'):
            with self.assertRaisesRegex(ValueError,'API_RESPONSE_AFTER_CURSOR'):worker.step_symbol('AAPL')
        with sqlite3.connect(self.root/'AAPL.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM candles').fetchone()[0],0)

    def test_partial_page_commit_restart_duplicate_is_safe(self):
        db=backfill.open_research_db(self.root,'AAPL')
        start,end='2024-07-26T00:00:00+00:00','2024-07-26T23:59:00+00:00'
        backfill.insert_page(db,[tick()],start,end);db.close()
        with patch.object(backfill,'check_disk'):
            db=backfill.open_research_db(self.root,'AAPL')
        self.assertEqual(backfill.insert_page(db,[tick()],start,end)[0],0)
        db.close()

    def test_raw_api_error_bodies_never_logged(self):
        with patch.object(legacy,'ERROR_LOG', self.root/'errors.jsonl'):
            legacy.record_error('AAPL',None,0,'unsafe secret token',401,'Authorization: Bearer hidden')
        text=(self.root/'errors.jsonl').read_text()
        self.assertNotIn('hidden',text)
        self.assertNotIn('unsafe secret',text)
        self.assertIn('REQUEST_FAILED',text)

    def test_offline_sqlite_bundle_sha_no_csv_required(self):
        from scripts.export_collection50_research import export
        data=self.root/'data';data.mkdir()
        db=backfill.open_research_db(data,'AAPL')
        backfill.insert_page(db,[tick()],'2024-01-01T00:00:00+00:00','2024-12-31T23:59:00+00:00');db.close()
        report=export(data,self.root/'bundle',['AAPL'],[2024])
        self.assertEqual(report['files'][0]['rows'],1)
        self.assertEqual(report['files'][0]['sha256'],hashlib.sha256((self.root/'bundle/AAPL-2024.csv.gz').read_bytes()).hexdigest())

    def test_worker_lock_is_cross_process(self):
        import subprocess
        lock=self.root/'worker.lock'
        script="from research_collection_scheduler import exclusive_lock; import sys; "+"\ntry:\n with exclusive_lock(sys.argv[1]): pass\nexcept RuntimeError: sys.exit(23)"
        with scheduler.exclusive_lock(lock):
            result=subprocess.run([__import__('sys').executable,'-c',script,str(lock)],cwd=scheduler.ROOT,capture_output=True)
            self.assertEqual(result.returncode,23,result.stderr)

    def test_broker_stale_socket_recovery_duplicate_lock_and_sigterm(self):
        import socket
        import subprocess
        import sys
        path=self.root/'broker.sock'
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as stale:stale.bind(str(path))
        credentials=self.root/'synthetic.json';credentials.write_text('{"toss":{}}')
        command=[sys.executable,str(scheduler.ROOT/'toss_token_broker.py'),'--socket',str(path),'--secrets',str(credentials),'--group-gid',str(os.getgid()),'--allow-uid',str(os.getuid())]
        child=subprocess.Popen(command,env={**os.environ,'TOSS_TOKEN_SOCKET':''},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+5
            response=None
            while time.monotonic()<deadline:
                try:
                    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                        client.settimeout(1);client.connect(str(path));client.sendall(b'{}\n');response=json.loads(client.recv(16384))
                    break
                except (ConnectionRefusedError,FileNotFoundError):time.sleep(.02)
            self.assertEqual(response,{'ok':False})  # empty credentials: no OAuth HTTP call
            duplicate=subprocess.run(command,env={**os.environ,'TOSS_TOKEN_SOCKET':''},capture_output=True,timeout=3)
            self.assertNotEqual(duplicate.returncode,0)
            self.assertIn(b'BROKER_ALREADY_RUNNING',duplicate.stderr)
        finally:
            child.terminate();out,error=child.communicate(timeout=3)
        self.assertEqual(child.returncode,0,error)
        self.assertFalse(path.exists())

    def test_legacy_request_cannot_bypass_managed_regular_session_guard(self):
        scheduler.save(self.root/'ownership.json',{'managed':True})
        with patch.object(scheduler,'STATE_DIR',self.root),patch.object(legacy,'urlopen') as network:
            with self.assertRaisesRegex(RuntimeError,'BACKFILL_REQUEST_BLOCKED'):
                legacy.fetch_page('test','AAPL',error_recorder=Mock())
            network.assert_not_called()

    def test_existing_fifty_defaults_preserved(self):
        self.assertEqual(backfill.selected_symbols(None),legacy.DEFAULT_SYMBOLS)
        self.assertEqual(len(backfill.selected_symbols(None)),50)

    def test_401_passes_exact_rejected_token_to_broker(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"result":{"candles":[]}}'
        error=HTTPError('https://offline.invalid',401,'error',{},io.BytesIO(b'invalid-token'))
        refresh=Mock(return_value='new')
        with patch.object(legacy,'urlopen',side_effect=[error,response]),patch.object(legacy,'wait_for_slot'),patch.object(legacy.time,'sleep'):
            legacy.fetch_page('rejected','AAPL',refresh_token=Mock(),refresh_rejected_token=refresh,error_recorder=Mock())
        refresh.assert_called_once_with('rejected')


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_websocket_receiver_stops_and_drains(self):
        import asyncio
        from scripts import collect_toss_ticks as ticks
        with tempfile.TemporaryDirectory() as directory:
            stop=asyncio.Event()
            started=asyncio.Event()
            async def blocked(*args,**kwargs):
                started.set()
                await asyncio.Event().wait()
            args=SimpleNamespace(data_dir=Path(directory),symbols_list=['AAPL'],queue_size=8,flush_seconds=.1,min_free_gb=0)
            window=dict(sessionDate='2024-07-26',openAt='2024-07-26T13:30:00+00:00',closeAt='2099-01-01T00:00:00+00:00',marketSession='REGULAR')
            with patch.object(ticks,'stream_once',new=blocked),patch.object(ticks,'disk_free_gb',return_value=100),patch.object(ticks,'write_status'):
                task=asyncio.create_task(ticks.collect_market_session(args,window,stop))
                await asyncio.wait_for(started.wait(),1)
                stop.set()
                await asyncio.wait_for(task,1)
            meta=json.loads((Path(directory)/'2024-07-26/session_meta.json').read_text())
            self.assertEqual(meta['endReason'],'service_stopped')


    async def test_low_disk_pause_exits_promptly_on_stop(self):
        import asyncio
        from scripts import collect_toss_ticks as ticks
        with tempfile.TemporaryDirectory() as directory:
            stop=asyncio.Event()
            waiting=asyncio.Event()
            args=SimpleNamespace(data_dir=Path(directory),symbols_list=['AAPL'],
                                 queue_size=1000,flush_seconds=.1,min_free_gb=10)
            window=dict(sessionDate='2024-07-26',openAt='2024-07-26T13:30:00+00:00',
                        closeAt='2099-01-01T00:00:00+00:00',marketSession='REGULAR')
            def status(**kw):
                if kw.get('state') == 'paused_low_disk':
                    waiting.set()
            with patch.object(ticks,'disk_free_gb',return_value=0),patch.object(ticks,'write_status',side_effect=status):
                task=asyncio.create_task(ticks.collect_market_session(args,window,stop))
                await asyncio.wait_for(waiting.wait(),2)
                stop.set()
                await asyncio.wait_for(task,2)
            meta=json.loads((Path(directory)/'2024-07-26/session_meta.json').read_text())
            self.assertEqual(meta['endReason'],'service_stopped')

    async def test_idle_tick_daemon_exits_on_os_sigterm(self):
        import asyncio
        import subprocess
        import sys
        from scripts import collect_toss_ticks as ticks
        program=(
            'import asyncio\n'
            'from types import SimpleNamespace\n'
            'from scripts import collect_toss_ticks as ticks\n'
            'ticks.market_window=lambda: dict(isOpen=False,sessionDate="2099-01-02",'
            'marketSession="REGULAR",calendarSource="test",openAt="2099-01-02T14:30:00+00:00",'
            'closeAt="2099-01-02T21:00:00+00:00")\n'
            'def status(**kw):\n'
            ' if kw.get("state")=="waiting_for_market_open": print("IDLE_READY",flush=True)\n'
            'ticks.write_status=status\n'
            'asyncio.run(ticks.daemon(SimpleNamespace(regular_only=False,symbols_list=["AAPL"])))\n'
        )
        child=subprocess.Popen([sys.executable,'-c',program],cwd=ticks.ROOT,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            line=await asyncio.wait_for(asyncio.to_thread(child.stdout.readline),timeout=15)
            self.assertEqual(line.strip(),'IDLE_READY')
            child.terminate()
            out,error=await asyncio.wait_for(asyncio.to_thread(child.communicate),timeout=8)
            self.assertEqual(child.returncode,0,error)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()


class CollectionAPITests(unittest.TestCase):
    def test_public_status_admin_download_and_control(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.dict(os.environ,{'PAPER_V3_DIR':str(root/'paper'), 'COLLECTION_50_DIR':str(root/'data'), 'COLLECTION_DOWNLOAD_DIR':str(root/'exports')}):
                import server
                (root/'data').mkdir()
                db=backfill.open_research_db(root/'data','AAPL')
                backfill.insert_page(db,[tick()],'2024-01-01T00:00:00+00:00','2024-12-31T23:59:00+00:00');db.close()
                session='offline-synthetic-admin';server.SESSIONS[session]=time.time()+60
                http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
                thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
                def request(route,auth=False,body=None):
                    headers={'Authorization':'Bearer '+session} if auth else {}
                    return urlopen(Request('http://127.0.0.1:'+str(http.server_port)+route,headers=headers,data=json.dumps(body).encode() if body is not None else None),timeout=5)
                try:
                    with patch.object(scheduler,'STATE_DIR',root/'status'),patch('research.collection_download.shutil.disk_usage',return_value=SimpleNamespace(free=100*1024**3,total=200*1024**3)),patch.object(legacy,'urlopen',side_effect=AssertionError('status must not call Toss')):
                        with request('/api/research/collection50/status') as response:self.assertTrue(json.load(response)['ok'])
                        for route,body in (('/api/research/collection50/download/AAPL?year=2024',None),('/api/research/collection50/control',{'paused':True})):
                            with self.assertRaises(HTTPError) as exc:request(route,body=body)
                            self.assertEqual(exc.exception.code,401)
                        with request('/api/research/collection50/download/AAPL?year=2024',True) as response:
                            data=response.read();self.assertEqual(hashlib.sha256(data).hexdigest(),response.headers['X-Content-SHA256'])
                        with request('/api/research/collection50/control',True,{'paused':True}) as response:self.assertTrue(json.load(response)['paused'])
                        self.assertTrue(json.loads((root/'status/control.json').read_text())['paused'])
                        with patch.object(server,'_systemctl_value',return_value=(0,'active')), patch.object(server,'_console_run') as command:
                            with self.assertRaisesRegex(ValueError,'CENTRAL_COLLECTION_SCHEDULER'):server.control_collector('rawTick','start')
                            command.assert_not_called()
                        with self.assertRaises(HTTPError) as exc:request('/api/research/collection50/download/AAPL?year=2026',True)
                        self.assertEqual(exc.exception.code,400)
                finally:
                    http.shutdown();thread.join();http.server_close();server.SESSIONS.pop(session,None)


if __name__=='__main__':unittest.main()
