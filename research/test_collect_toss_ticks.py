"""Offline protocol fixtures sourced from official AsyncAPI 1.2.2. No Toss requests."""
import asyncio
import ast
import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import collect_toss_ticks as c

FIXTURE=json.loads((Path(__file__).parent/'fixtures/toss_asyncapi_1_2_2.json').read_text())
EX=FIXTURE['examples']
NOW=datetime(2026,6,18,14,30,tzinfo=timezone.utc)

def trade():return copy.deepcopy(EX['tradeMessage'])
def book():
    # Official KR example uses the same channel-independent schema. US USD variant.
    data=copy.deepcopy(EX['orderbookMessage']);data['topic']='orderbook:us:AAPL';data['data']['currency']='USD'
    return data

def ack():return {'type':'subscriptions','id':'connection','subscribed':['trade:us:AAPL','orderbook:us:AAPL'],'rejected':[]}


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.storage=c.Storage(self.temp.name,['AAPL'],stop_bytes=0,warn_bytes=0)
        self.addCleanup(self.storage.close)
        self.engine=c.Collector(self.storage,lambda force=False:'unused')
        self.engine.connection_id='connection';self.engine.ack_id='connection'

    async def send(self,obj):return await self.engine.handle(json.dumps(obj),NOW)

    async def test_subscription_ack_and_declarative_topics(self):
        await self.send(ack());self.assertTrue(self.engine.acked)
        d=c.declaration(['AAPL'],'id')
        self.assertEqual(d,[{'id':'id'},{'type':'trade:us','codes':['AAPL']},{'type':'orderbook:us','codes':['AAPL']}])
        universe=json.loads((c.ROOT/'research/universe_v3.json').read_text())['symbols']
        self.assertEqual(sum(len(x.get('codes',[])) for x in c.declaration(universe,'id')),60)
        with self.assertRaises(ValueError):c.declaration([f'X{x}' for x in range(51)],'id')

    async def test_trade_preserves_strings_source_receive_time(self):
        await self.send(ack());row=await self.send(trade())
        self.assertEqual(row['kind'],'trade');self.assertEqual(row['price'],'243.26')
        self.assertEqual(row['volume'],'8');self.assertEqual(row['received_at_utc'],NOW.isoformat())
        self.assertEqual(row['source_timestamp'],trade()['data']['timestamp'])
        self.assertEqual(row['source_timestamp_utc'],NOW.isoformat())
        self.assertEqual(self.engine.state['tradeMessages'],1)

    async def test_orderbook_all_levels_and_spread(self):
        data=book();data['data']['asks'].append({'price':'71501','volume':'3.125'})
        row=await self.send(data)
        self.assertEqual(row['kind'],'orderbook');self.assertEqual(row['ask_depth'],2)
        self.assertEqual(row['spread'],'100');self.assertEqual(row['bid_size'],'10')
        self.assertEqual(json.loads(row['normalized_json'])['asks'],data['data']['asks'])

    async def test_null_book_timestamp_not_fabricated(self):
        data=book();data['data']['timestamp']=None
        row=await self.send(data);self.assertIsNone(row['source_timestamp_utc'])
        self.assertIn('SOURCE_TIMESTAMP_UNAVAILABLE',row['warnings'])

    async def test_pong_updates_heartbeat(self):
        await self.send(EX['pong']);self.assertEqual(self.engine.state['lastPongAt'],NOW.isoformat())

    async def test_plain_ping_and_missing_pong_forces_reconnect(self):
        sent=[]
        class WS:
            async def send(self,value):sent.append(value)
        async def immediate(_):pass
        with patch.object(c.asyncio,"sleep",new=immediate):
            with self.assertRaisesRegex(ConnectionError,"PING timeout"):
                await self.engine.keepalive(WS())
        self.assertEqual(sent,["PING"])

    async def test_error_and_server_shutdown_force_reconnect(self):
        for name in ['errorFrame','serverShutdown']:
            with self.assertRaises(ConnectionError):await self.send(EX[name])
        self.assertEqual(self.engine.queue.qsize(),2)

    async def test_malformed_json_is_preserved(self):
        row=await self.engine.handle('{broken',NOW)
        self.assertEqual(row['kind'],'malformed_json');self.assertEqual(row['raw_payload'],b'{broken')

    async def test_unknown_topic_and_schema_are_logged(self):
        data=trade();data['topic']='personal:order:1'
        row=await self.send(data);self.assertEqual(row['kind'],'unknown_schema')
        data=trade();data['data']['price']=123
        row=await self.send(data);self.assertEqual(row['kind'],'unknown_schema')
        data=trade();data['data']['futureField']='x'
        row=await self.send(data);self.assertEqual(row['kind'],'trade');self.assertTrue(row['warnings'])

    async def test_naive_timestamp_rejected_without_guessing(self):
        data=trade();data['data']['timestamp']='2026-06-18T14:30:00'
        row=await self.send(data);self.assertEqual(row['kind'],'unknown_schema')
        self.assertIsNone(row['source_timestamp_utc'])

    async def test_rejected_symbol_removed_on_reconnect(self):
        a=ack();a['subscribed']=['trade:us:AAPL'];a['rejected']=[dict(target='orderbook:us:AAPL',code='stock-not-found',message='not found')]
        await self.send(a)
        self.assertTrue(self.engine.acked);self.assertEqual(len(self.engine.state['subscriptionRejected']),1)
        self.assertEqual(c.declaration(['AAPL'],'next',self.engine.excluded),[{'id':'next'},{'type':'trade:us','codes':['AAPL']}])

    async def test_invalid_or_incomplete_ack_not_accepted(self):
        a=ack();a['subscribed']=a['subscribed'][:1]
        row=await self.send(a);self.assertEqual(row['kind'],'invalid_ack');self.assertFalse(self.engine.acked)

    async def test_duplicate_looking_legitimate_trades_stored_twice(self):
        first=await self.send(trade());second=await self.send(trade())
        self.storage.append([first,second]);self.storage.close()
        with sqlite3.connect(self.storage.path) as conn:
            self.assertEqual(conn.execute('select count(*) from frames').fetchone()[0],2)
            self.assertEqual([x[0] for x in conn.execute('select local_receive_sequence from frames order by id')],[1,2])

    async def test_daily_rotation_preserves_prior_partition(self):
        a=await self.engine.handle(json.dumps(trade()),NOW)
        b=await self.engine.handle(json.dumps(trade()),NOW+timedelta(days=1))
        self.storage.append([a,b]);self.storage.close()
        files=list(Path(self.temp.name).glob('*/*.sqlite3'));self.assertEqual(len(files),2)
        for p in files:
            m=json.loads(p.with_suffix('.manifest.json').read_text())
            self.assertTrue(m['finalized']);self.assertTrue(m['lossy']);self.assertEqual(len(m['sha256']),64)

    async def test_receive_date_clock_rollback_never_overwrites_segment(self):
        for received in (NOW,NOW+timedelta(days=1),NOW+timedelta(seconds=1)):
            row=await self.engine.handle(json.dumps(trade()),received)
            self.storage.append([row])
        self.storage.close()
        files=list(Path(self.temp.name).glob("*/*.sqlite3"))
        self.assertEqual(len(files),3)
        self.assertEqual(len(list(Path(self.temp.name).glob("*/*.manifest.json"))),3)
        for path in files:
            with sqlite3.connect(path) as conn:self.assertEqual(conn.execute("select count(*) from frames").fetchone()[0],1)

    async def test_flush_on_graceful_shutdown(self):
        await self.send(trade())
        async def receiver():
            await self.engine.stop.wait()
        self.engine.reconnect_loop=receiver
        task=asyncio.create_task(self.engine.run());await asyncio.sleep(.05);self.engine.stop.set()
        await asyncio.wait_for(task,3)
        state=json.loads((Path(self.temp.name)/'status.json').read_text())
        self.assertFalse(state['running'])
        with sqlite3.connect(self.storage.path) as conn:self.assertEqual(conn.execute('select count(*) from frames').fetchone()[0],1)

    async def test_disk_stop_never_deletes_data_or_hangs(self):
        self.storage.stop_bytes=10**30
        await self.send(trade())
        with self.assertRaises(c.DiskFull):await asyncio.wait_for(self.engine.run(),3)
        self.assertFalse(self.engine.state['running'])

    async def test_reconnect_after_disconnect(self):
        calls=[];self.engine.backoff_cap=.001
        async def connection():
            calls.append(1)
            if len(calls)==2:self.engine.stop.set();return
            raise ConnectionError('offline mock')
        self.engine.connection=connection
        with patch.object(c.random,'uniform',return_value=0):await self.engine.reconnect_loop()
        self.assertEqual(len(calls),2);self.assertEqual(self.engine.state['reconnects'],1)

    async def test_mock_websocket_handshake_ack_and_market_data(self):
        engine=self.engine
        class WS:
            def __init__(self):self.sent=[]
            async def send(self,raw):self.sent.append(raw)
            async def recv(self):
                a=ack();a['id']=engine.ack_id;return json.dumps(a)
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
            def __aiter__(self):return self
            async def __anext__(self):
                if len(self.sent)==1:self.sent.append('received');return json.dumps(trade())
                engine.stop.set();raise StopAsyncIteration
        ws=WS();kwargs={}
        def connect(url,**kw):kwargs.update(kw);self.assertEqual(url,c.WS_URL);return ws
        engine.connector=connect
        with self.assertRaises(ConnectionError):await engine.connection()
        self.assertEqual(kwargs.get('additional_headers',kwargs.get('extra_headers')),{'Authorization':'Bearer unused'})
        self.assertEqual(engine.state['tradeMessages'],1)

    def test_market_calendar_official_intervals(self):
        cal=c.MarketCalendar();cal.update(FIXTURE['calendar'],NOW)
        self.assertEqual(cal.classify(c.aware_timestamp('2026-03-25T22:35:00+09:00')),('REGULAR','2026-03-25'))
        self.assertEqual(cal.classify(c.aware_timestamp('2026-03-25T09:05:00+09:00')),('DAY','2026-03-25'))
        self.assertEqual(cal.classify(c.aware_timestamp('2020-01-01T00:00:00Z')),('UNKNOWN',None))

    def test_market_only_imports_and_endpoints(self):
        source=Path(c.__file__).read_text();tree=ast.parse(source)
        modules=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        self.assertNotIn('server',modules);self.assertNotIn('live_trader',modules)
        self.assertNotIn('/api/v1/orders',source);self.assertNotIn('personal:order',source)


if __name__=='__main__':unittest.main()
