"""Offline regression tests for the CURRENT upstream CSV tick collector.

The previous SQLite/two-channel implementation is not the deployed collector.
Tests exercise actual trade-only protocol and persistence without Toss traffic.
"""
import asyncio
import ast
import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import collect_toss_ticks as c

FIXTURE=json.loads((Path(__file__).parent/'fixtures/toss_asyncapi_1_2_2.json').read_text())
EX=FIXTURE['examples']

def trade(): return copy.deepcopy(EX['tradeMessage'])
def ack(): return dict(type='subscriptions',subscribed=['trade:us:AAPL'],rejected=[])

class WebSocket:
    def __init__(self,frames): self.frames=iter(frames);self.sent=[]
    async def send(self,value):self.sent.append(value)
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False
    def __aiter__(self):return self
    async def __anext__(self):
        try:value=next(self.frames)
        except StopIteration:raise StopAsyncIteration
        if isinstance(value,Exception):raise value
        return value if isinstance(value,str) else json.dumps(value)

class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.meta={}
        self.writer=c.TickWriter(self.root,'2026-06-18',['AAPL'],8,.1,self.meta)

    async def stream(self,frames):
        ws=WebSocket(frames)
        with patch.object(c,'access_token',return_value='offline'),patch.object(c,'write_status'),patch.object(c.websockets,'connect',return_value=ws) as conn:
            await c.stream_once(['AAPL'],self.writer,c.as_utc_timestamp('2099-01-01T00:00:00Z'),self.meta,False)
        self.ws=ws;self.connection=conn
        return ws

    async def flush(self):
        stop=asyncio.Event();stop.set();await self.writer.run(stop)
        path=self.root/'2026-06-18/AAPL.csv'
        return list(csv.DictReader(path.open())) if path.exists() else []

    async def test_subscription_ack_and_declarative_topics(self):
        ws=await self.stream([ack()]);request=json.loads(ws.sent[0])
        self.assertEqual(request[1:], [{'type':'trade:us','codes':['AAPL']}])
        self.assertEqual(self.meta['subscribed'],['trade:us:AAPL'])
        self.assertTrue(self.meta['connections'][-1]['acked'])
        self.assertEqual(len(c.load_symbols(None)),30)

    async def test_trade_preserves_numeric_strings_and_source_timestamp(self):
        await self.stream([ack(),trade()]);rows=await self.flush()
        self.assertEqual(rows[0]['price'],'243.26');self.assertEqual(rows[0]['volume'],'8')
        self.assertEqual(rows[0]['source_timestamp'],trade()['data']['timestamp'])
        self.assertIsNotNone(c.as_utc_timestamp(rows[0]['received_at']).tz)

    async def test_handshake_bearer_token(self):
        await self.stream([ack()]);kw=self.connection.call_args.kwargs
        self.assertEqual(kw.get('additional_headers',kw.get('extra_headers')),{'Authorization':'Bearer offline'})
        self.assertIsNone(kw['ping_interval'])

    async def test_orderbook_not_mislabelled_as_trade(self):
        frame=copy.deepcopy(EX['orderbookMessage']);frame['topic']='orderbook:us:AAPL'
        await self.stream([ack(),frame]);self.assertEqual(await self.flush(),[])

    async def test_null_trade_timestamp_counted_invalid(self):
        frame=trade();frame['data']['timestamp']=None
        await self.stream([ack(),frame]);self.assertEqual(self.writer.invalid_messages,1)
        self.assertEqual(await self.flush(),[])

    async def test_pong_is_not_persisted_as_trade(self):
        await self.stream([ack(),EX['pong'],trade()]);self.assertEqual(len(await self.flush()),1)

    async def test_plain_ping_keepalive(self):
        ws=WebSocket([])
        calls=0
        async def sleeper(_):
            nonlocal calls
            calls+=1
            if calls>1:raise asyncio.CancelledError
        with patch.object(c.asyncio,'sleep',new=sleeper):
            with self.assertRaises(asyncio.CancelledError):await c.keepalive(ws)
        self.assertEqual(ws.sent,['PING'])

    async def test_error_frame_raises_for_reconnect(self):
        with self.assertRaisesRegex(RuntimeError,'Toss WebSocket error'):
            await self.stream([ack(),EX['errorFrame']])
        self.assertIsNotNone(self.meta['connections'][-1]['disconnectedAt'])

    async def test_disconnect_records_end_and_flushes_received_ticks(self):
        with self.assertRaises(ConnectionError):await self.stream([ack(),trade(),ConnectionError('offline')])
        self.assertEqual(len(await self.flush()),1)
        self.assertTrue(self.meta['connections'][-1]['acked'])
        self.assertIn('disconnectedAt',self.meta['connections'][-1])

    async def test_malformed_json_counted_and_next_valid_frame_survives(self):
        await self.stream([ack(),'{broken',trade()]);self.assertEqual(self.writer.invalid_messages,1)
        self.assertEqual(len(await self.flush()),1)

    async def test_unknown_topic_not_persisted(self):
        frame=trade();frame['topic']='personal:order:1'
        await self.stream([ack(),frame]);self.assertEqual(await self.flush(),[])

    async def test_unknown_symbol_counted_invalid(self):
        frame=trade();frame['topic']='trade:us:BAD'
        await self.stream([ack(),frame]);self.assertEqual(self.writer.invalid_messages,1)

    async def test_naive_source_timestamp_preserved_literally_not_reinterpreted(self):
        frame=trade();frame['data']['timestamp']='2026-06-18T14:30:00'
        await self.stream([ack(),frame]);rows=await self.flush()
        self.assertEqual(rows[0]['source_timestamp'],frame['data']['timestamp'])

    async def test_rejected_subscription_fails_closed(self):
        frame=ack();frame['rejected']=[dict(target='trade:us:AAPL',code='stock-not-found')]
        with self.assertRaisesRegex(RuntimeError,'incomplete Toss subscription'):await self.stream([frame])
        self.assertEqual(self.meta['rejectedSubscriptions'],frame['rejected'])

    async def test_incomplete_ack_fails_closed(self):
        frame=ack();frame['subscribed']=[]
        with self.assertRaisesRegex(RuntimeError,'incomplete Toss subscription'):await self.stream([frame])

    async def test_duplicate_looking_trades_preserved_twice(self):
        await self.stream([ack(),trade(),trade()]);rows=await self.flush()
        self.assertEqual(len(rows),2);self.assertEqual([r['connection_seq'] for r in rows],['1','2'])

    async def test_bounded_queue_records_drops(self):
        self.writer.queue=asyncio.Queue(maxsize=1)
        await self.stream([ack(),trade(),trade()]);self.assertEqual(self.writer.local_drops,1)
        self.assertEqual(len(await self.flush()),1)
        self.assertEqual(self.meta['localQueueDrops'],1)

    async def test_graceful_writer_shutdown_flushes_pending_and_metadata(self):
        await self.stream([ack(),trade()]);await self.flush()
        meta=json.loads((self.root/'2026-06-18/session_meta.json').read_text())
        self.assertEqual(meta['ticksTotal'],1);self.assertEqual(meta['queueDepth'],0)

    async def test_daily_partition_and_restart_append(self):
        await self.stream([ack(),trade()]);await self.flush()
        self.writer=c.TickWriter(self.root,'2026-06-18',['AAPL'],8,.1,self.meta)
        await self.stream([ack(),trade()]);self.assertEqual(len(await self.flush()),2)
        nextwriter=c.TickWriter(self.root,'2026-06-19',['AAPL'],8,.1,{})
        self.assertNotEqual(nextwriter.session_dir,self.writer.session_dir)
        self.assertTrue((self.root/'2026-06-18/AAPL.csv').exists())

    async def test_reconnect_and_stop_drain_without_compressing_active_session(self):
        stop=asyncio.Event();calls=[]
        async def stream(*args,**kwargs):
            calls.append(kwargs['force_token'])
            if len(calls)==1:raise ConnectionError('offline')
            stop.set()
        async def sleeper(_):pass
        args=SimpleNamespace(data_dir=self.root,symbols_list=['AAPL'],queue_size=8,flush_seconds=.1,min_free_gb=0)
        window=dict(sessionDate='2026-06-18',openAt='2026-06-18T13:30:00Z',closeAt='2099-01-01T00:00:00Z',marketSession='REGULAR')
        with patch.object(c,'stream_once',new=stream),patch.object(c,'disk_free_gb',return_value=10),patch.object(c,'write_status'),patch.object(c.asyncio,'sleep',new=sleeper):
            await c.collect_market_session(args,window,stop)
        meta=json.loads((self.root/'2026-06-18/session_meta.json').read_text())
        self.assertEqual(calls,[False,True]);self.assertEqual(meta['reconnects'],1)
        self.assertEqual(meta['endReason'],'service_stopped');self.assertTrue(meta['lossySource'])

    def test_market_only_imports_and_calendar_sessions(self):
        source=Path(c.__file__).read_text();tree=ast.parse(source)
        modules=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        self.assertNotIn('live_trader',modules);self.assertNotIn('/api/v1/orders',source)
        day={k:dict(startTime=f'2026-06-18T{h:02}:00:00Z',endTime=f'2026-06-18T{h+1:02}:00:00Z') for k,h in [('dayMarket',0),('preMarket',10),('regularMarket',14),('afterMarket',20)]}
        windows=c._calendar_windows(day,'2026-06-18')
        self.assertEqual([w['marketSession'] for w in windows],['DAY','PRE','REGULAR','AFTER'])
        self.assertTrue(windows[-1]['isFinalSession'])

if __name__=='__main__':unittest.main()
