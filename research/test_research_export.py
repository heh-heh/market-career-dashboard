"""Offline small fixtures only; no source data, broker calls or real exports."""
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

from research_export import ResearchExporter, SafeRoot, VERSION, stream_json, finalize_manifest
from research_export_metadata import summarize_run, summary_csv

REPO = Path(__file__).resolve().parents[1]

class ExportFixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.roots = {k:self.base/k for k in ('evolution','v4','results','minutes','ticks','metadata')}
        for p in self.roots.values(): p.mkdir()
        self.output = self.base/'exports'
        self.manager = ResearchExporter(REPO, roots=self.roots, export_dir=self.output, min_free_bytes=0, reserve_fraction=0)
        self.write('evolution','run-a/run.json',dict(runId='run-a',version='V0.7',fitnessMode='stability',selectionMode='internal_holdout_shortlist',trainPeriod=['2022-01-01','2023-12-31'],population=20,generation=5))
        self.write('evolution','run-a/status.json',dict(meanValidationReturn=.01,medianValidationReturn=.005,positiveBots=12,negativeBots=8,verdict='WEAK',finalOosAllowed=False))
        self.write('evolution','run-a/final_population.json',[{'botId':'1','weight':.2}])
        self.write('evolution','run-a/config.json',{'mutation':.1})
        self.write('metadata','README.md','Research only. No final OOS evaluation.')
        self.write('evolution','run-a/trades.csv','symbol,pnl\nTQQQ,1\n')
        self.write('evolution','run-a/generations.jsonl','{"generation":1}\n')
        self.write('evolution','run-a/lineage.jsonl','{"parent":"1"}\n')
    def tearDown(self):
        self.manager.close(); self.tmp.cleanup()
    def write(self,root,relative,value):
        p=self.roots[root]/relative;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(value) if isinstance(value,(dict,list)) else value);return p
    def run_job(self,body,success=True):
        job=self.manager.create(body)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            job=self.manager.get(job['jobId'])
            if job['state'] in ('completed','failed'): break
            time.sleep(.01)
        self.assertEqual(job['state'],'completed' if success else 'failed',job)
        return job
    def archive(self,job): return zipfile.ZipFile(self.manager.download(job['jobId'])[0])

class ExportTests(ExportFixtures):
    def test_path_traversal_rejected(self):
        for body in ({'type':'evolution_full','path':'../'}, {'type':'evolution_full','runIds':['../../etc/passwd']}, {'type':'toss_1m','symbols':['../SPY'],'from':'2024-01-01','to':'2024-01-02'}):
            with self.assertRaises(ValueError): self.manager.create(body)
        for p in ('../run.json','/etc/passwd','run-a/../../x','run-a\\run.json'):
            with self.assertRaises(ValueError):
                with SafeRoot(self.roots['evolution']).file(p): pass
    def test_whitelist_disjoint_output(self):
        with self.assertRaises(ValueError): ResearchExporter(REPO,roots=self.roots,export_dir=self.roots['minutes']/'out')
        with self.assertRaises(ValueError): self.manager.create({'type':'outside_root'})
    def test_symlink_escape_and_hardlinks(self):
        outside=self.base/'outside.json';outside.write_text('{"secret":"x"}')
        (self.roots['evolution']/'escape.json').symlink_to(outside)
        (self.roots['evolution']/'linked').symlink_to(self.base,target_is_directory=True)
        os.link(outside,self.roots['evolution']/'hard.json')
        files=list(SafeRoot(self.roots['evolution']).walk())
        self.assertFalse(any(f['relative'].startswith(('escape','linked','hard')) for f in files))
        with self.assertRaises(OSError):
            with SafeRoot(self.roots['evolution']).file('linked/outside.json'): pass
        with self.assertRaises(ValueError):
            with SafeRoot(self.roots['evolution']).file('hard.json'): pass
    def test_sensitive_filenames_excluded(self):
        for name in ('.env','x.pem','x.key','token_cache.json','credentials.json','server.log','session.json','oauth.json'):
            self.write('evolution','run-a/'+name,'Bearer DO_NOT_EXPORT')
        job=self.run_job({'type':'evolution_full'})
        with self.archive(job) as z:
            self.assertFalse(any(p.endswith(('server.log','.env','.key','.pem','token_cache.json','session.json')) for p in z.namelist()))
            self.assertNotIn(b'DO_NOT_EXPORT',b''.join(z.read(p) for p in z.namelist()))
    def test_secret_contents_removed(self):
        self.write('evolution','run-a/diagnostic.json',{'access_token':'hidden','nested':{'cookie':'hidden','value':3},'note':'Authorization: Bearer hidden'})
        self.write('evolution','run-a/decisions.csv','symbol,authorization,note\nSPY,hidden,Bearer hidden\n')
        job=self.run_job({'type':'evolution_full'})
        with self.archive(job) as z:
            doc=json.loads(z.read('evolution/run-a/diagnostic.json'))
            self.assertNotIn('access_token',doc);self.assertEqual(doc['nested'],{'value':3})
            self.assertNotIn(b'hidden',z.read('evolution/run-a/decisions.csv'))
    def test_summary_archive_mobile_scope(self):
        job=self.run_job({'type':'evolution_summary'})
        with self.archive(job) as z:
            self.assertIn('evolution/run-a/final_population.json',z.namelist())
            self.assertIn('metadata/README.md',z.namelist())
            self.assertNotIn('evolution/run-a/trades.csv',z.namelist())
            self.assertNotIn('evolution/run-a/generations.jsonl',z.namelist())
            self.assertIn('evolution_research_summary.csv',z.namelist())
    def test_selected_run_isolation(self):
        self.write('evolution','run-b/run.json',{'runId':'run-b','version':'V0.6'})
        id_a=next(r['id'] for r in self.manager.catalog()['public']['runs'] if r['runId']=='run-a')
        job=self.run_job({'type':'evolution_full','runIds':[id_a]})
        with self.archive(job) as z: self.assertFalse(any(p.startswith('evolution/run-b/') for p in z.namelist()))
    def test_population_and_trades_selection(self):
        for kind,present,absent in [('evolution_population','final_population.json','trades.csv'),('evolution_trades','trades.csv','final_population.json')]:
            with self.archive(self.run_job({'type':kind})) as z:
                self.assertIn('evolution/run-a/'+present,z.namelist());self.assertNotIn('evolution/run-a/'+absent,z.namelist())
    def minute_source(self):
        self.write('minutes','TQQQ.csv','timestamp,open,high,low,close,volume,currency\n2024-01-02T14:31:00+00:00,10,11,9,10,5,USD\n2024-01-03T14:31:00+00:00,11,12,10,11,6,USD\n')
        self.write('minutes','AAPL.csv','timestamp,open,high,low,close,volume,currency\n2024-01-02T14:31:00+00:00,10,11,9,10,5,USD\n')
    def test_selective_symbol_date(self):
        self.minute_source();job=self.run_job({'type':'toss_1m','symbols':['TQQQ'],'from':'2024-01-03','to':'2024-01-03'})
        with self.archive(job) as z:
            text=z.read('minutes/TQQQ.csv').decode(); self.assertNotIn('2024-01-02',text);self.assertIn('2024-01-03',text)
            self.assertNotIn('minutes/AAPL.csv',z.namelist())
    def test_end_boundary_and_dst(self):
        self.write('minutes','SPY.csv','timestamp,close\n2024-01-03T05:00:00Z,100\n2024-07-03T04:00:00Z,101\n')
        for day,match in [('2024-01-02','2024-01-03'),('2024-07-02','2024-07-03')]:
            with self.archive(self.run_job({'type':'toss_1m','symbols':['SPY'],'from':day,'to':day})) as z:
                self.assertIn(match,z.read('minutes/SPY.csv').decode())
    def test_naive_minute_fails_without_declaration(self):
        self.write('minutes','SPY.csv','timestamp,close\n2024-01-02T09:31:00,100\n')
        with patch.dict(os.environ,{},clear=True):
            job=self.run_job({'type':'toss_1m','symbols':['SPY'],'from':'2024-01-02','to':'2024-01-02'},False)
        self.assertIn('Timezone-naive',job['error'])
    def test_gzip_source(self):
        self.minute_source(); p=self.roots['minutes']/'TQQQ.csv';data=p.read_bytes();p.unlink()
        with gzip.open(str(p)+'.gz','wb') as f:f.write(data)
        with self.archive(self.run_job({'type':'toss_1m','symbols':['TQQQ'],'from':'2024-01-02','to':'2024-01-02'})) as z:
            self.assertIn('minutes/TQQQ.csv',z.namelist())
    def test_tick_partition_duplicates_preserved(self):
        row='2024-01-02T10:00:00,2024-01-02T15:00:00Z,10,5,USD,c1,1\n'
        self.write('ticks','2024-01-02/TQQQ.csv','source_timestamp,received_at,price,volume,currency,connection_id,connection_seq\n'+row+row)
        self.write('ticks','2024-01-03/TQQQ.csv','price\n11\n')
        with self.archive(self.run_job({'type':'toss_ticks','symbols':['TQQQ'],'from':'2024-01-02','to':'2024-01-02'})) as z:
            self.assertEqual(z.read('ticks/2024-01-02/TQQQ.csv').decode().replace('\r\n','\n').count(row),2)
            self.assertNotIn('ticks/2024-01-03/TQQQ.csv',z.namelist())
            self.assertIn('LOSSY',json.loads(z.read('export_manifest.json'))['tickCompleteness'])
    def test_raw_requires_explicit_bounded_selection(self):
        for body in ({'type':'toss_1m'},{'type':'toss_ticks','symbols':['SPY'],'from':'2024-01-01','to':'2024-03-01'}):
            with self.assertRaises(ValueError):self.manager.create(body)
    def test_manifest_hashes_sizes(self):
        job=self.run_job({'type':'evolution_full'});path,_=self.manager.download(job['jobId'])
        with zipfile.ZipFile(path) as z:
            m=json.loads(z.read('export_manifest.json'));self.assertEqual(m['archiveBytes'],path.stat().st_size)
            for entry in m['files']:
                self.assertEqual(entry['sha256'],hashlib.sha256(z.read(entry['path'])).hexdigest())
                self.assertEqual(entry['exportedBytes'],len(z.read(entry['path'])))
                if 'sourceRelative' in entry:
                    original=(self.roots[entry['root']]/entry['sourceRelative']).read_bytes()
                    self.assertEqual(entry['sourceSha256'],hashlib.sha256(original[:entry['snapshotBytes']]).hexdigest())
    def test_disk_guard_fails_without_source_changes(self):
        path=self.roots['evolution']/'run-a/run.json';before=path.read_bytes()
        with patch('research_export.shutil.disk_usage',return_value=type('D',(),dict(total=100,used=100,free=0))()):
            job=self.run_job({'type':'evolution_full'},False)
        self.assertIn('DISK_SPACE_GUARD',job['error']);self.assertEqual(before,path.read_bytes());self.assertFalse(list(self.output.glob('*.zip')))
    def test_summary_size_limit(self):
        self.manager.summary_limit=1;job=self.run_job({'type':'evolution_summary'},False)
        self.assertIn('Summary exceeds',job['error'])
    def test_no_default_raw_or_unknown_run(self):
        job=self.run_job({'type':'evolution_full','runIds':['0'*24]},False);self.assertIn('Run ID missing',job['error'])
    def test_atomic_failure_no_partial_archive(self):
        self.write('evolution','run-a/broken.json','{"x":')
        job=self.run_job({'type':'evolution_full'},False)
        self.assertFalse((self.output/(job['jobId']+'.tmp')).exists());self.assertFalse((self.output/(job['jobId']+'.zip')).exists())
    def test_interrupted_export_recovery(self):
        key='a'*32
        job=dict(owner=VERSION,jobId=key,type='evolution_full',state='running',createdAt=datetime.now(timezone.utc).isoformat())
        (self.output/(key+'.job.json')).write_text(json.dumps(job));(self.output/(key+'.tmp')).write_text('partial')
        self.manager._recover();self.assertEqual(self.manager.get(key)['state'],'failed');self.assertFalse((self.output/(key+'.tmp')).exists())
    def test_cleanup_only_owned_output(self):
        job=self.run_job({'type':'evolution_summary'});key=job['jobId'];self.manager.jobs[key]['finishedAt']=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
        unrelated=self.output/'important.json';unrelated.write_text('keep')
        self.manager.cleanup();self.assertTrue(unrelated.exists());self.assertTrue((self.roots['evolution']/'run-a/run.json').exists());self.assertFalse((self.output/(key+'.zip')).exists())
    def test_v07_bundle_lineage_and_periods(self):
        for name,period in [('holdout',['2024-01-01','2024-12-31']),('development',['2025-01-01','2025-12-31'])]:
            self.write('evolution',name+'/run.json',dict(runId=name,version='V0.7',fromDate=period[0],toDate=period[1],sourceRun='run-a'))
            self.write('evolution',name+'/validation_summary.json',dict(meanValidationReturn=.01))
        self.write('evolution','shortlist/run.json',dict(runId='shortlist',version='V0.7',selectionMode='internal_holdout_shortlist',sourceRun='run-a'))
        self.write('evolution','shortlist/final_population.json',[{'bot':'1'}]);self.write('evolution','shortlist/shortlist.json',[{'bot':'1'}])
        self.write('evolution','development/generalization_diagnostics.json',dict(verdict='WEAK'))
        job=self.run_job({'type':'V07_FULL_RESEARCH_BUNDLE'})
        self.assertTrue(job['bundleEvidence']['completeEvidence'])
        with self.archive(job) as z:self.assertIn('evolution/shortlist/final_population.json',z.namelist())
    def test_incomplete_v07_explicit(self):
        job=self.run_job({'type':'V07_FULL_RESEARCH_BUNDLE'});self.assertFalse(job['bundleEvidence']['completeEvidence'])
    def test_ticket_scoped_one_use_expiry(self):
        job=self.run_job({'type':'evolution_summary'});key=job['jobId'];ticket=self.manager.ticket(key)
        self.assertTrue(self.manager.consume_ticket(ticket,key));self.assertFalse(self.manager.consume_ticket(ticket,key))
        t=self.manager.ticket(key);self.assertFalse(self.manager.consume_ticket(t,'b'*32))
        t=self.manager.ticket(key);self.manager.tickets[t]=(key,time.time()-1);self.assertFalse(self.manager.consume_ticket(t,key))
    def test_metadata_no_fabrication_and_formula_csv(self):
        row=summarize_run('abc',[],{'run.json':{'version':'V0.5','fitnessMode':'structural'}})
        self.assertEqual(row['label'],'V0.5 Structural');self.assertIsNone(row['meanValidation']);self.assertIsNone(row['finalOosAllowed'])
        self.assertIn(b"'=CMD",summary_csv([dict(runId='=CMD',value=-1)]))
    def test_json_bounded_sanitization_and_invalid(self):
        out=[];stream_json(io.StringIO(json.dumps({'population':[{'weight':i,'token':'hidden'} for i in range(200)]})),out.append)
        result=json.loads(b''.join(out));self.assertEqual(len(result['population']),200);self.assertNotIn('token',result['population'][0])
        for text in ('[1,]','{"x":1,"x":2}','NaN','1e999','{"x":'):
            with self.assertRaises(ValueError):stream_json(io.StringIO(text),lambda b:None)
    def test_zip64_manifest_size(self):
        b=io.BytesIO()
        with patch('zipfile.ZIP64_LIMIT',50):
            with zipfile.ZipFile(b,'w',allowZip64=True) as z:
                z.writestr('payload','a'*100);expected=finalize_manifest(z,{'files':[]})
        self.assertEqual(expected,len(b.getvalue()))
    def test_partial_csv_tail_not_fabricated(self):
        self.write('evolution','run-a/trades.csv','symbol,pnl\nSPY,1\nSPY,')
        with self.archive(self.run_job({'type':'evolution_trades'})) as z:
            self.assertEqual(z.read('evolution/run-a/trades.csv'),b'symbol,pnl\r\nSPY,1\r\n')
    def test_compressed_plain_collision_rejected(self):
        p=self.roots['evolution']/'run-a/trades.csv'
        with gzip.open(str(p)+'.gz','wb') as f:f.write(p.read_bytes())
        self.assertIn('Ambiguous',self.run_job({'type':'evolution_full'},False)['error'])
    def test_empty_date_range_fails(self):
        self.minute_source();j=self.run_job({'type':'toss_1m','symbols':['TQQQ'],'from':'2020-01-01','to':'2020-01-02'},False)
        self.assertIn('no candles',j['error'])

    def test_live_append_snapshot_and_rewrite_rejection(self):
        import research_export as module
        path=self.roots['evolution']/'run-a/trades.csv'
        original=module.sanitize;changed=False
        def append(value):
            nonlocal changed
            if not changed and value == 'symbol':
                with path.open('a') as f:f.write('QQQ,2\n')
                changed=True
            return original(value)
        with patch('research_export.sanitize',side_effect=append):job=self.run_job({'type':'evolution_trades'})
        with self.archive(job) as z:
            self.assertNotIn(b'\r\nQQQ,',z.read('evolution/run-a/trades.csv'))
            entry=next(e for e in json.loads(z.read('export_manifest.json'))['files'] if e.get('sourceRelative')=='run-a/trades.csv')
            self.assertTrue(entry['appendDuringRead'])
        changed=False
        def rewrite(value):
            nonlocal changed
            if not changed and value == 'symbol':path.write_text('symbol,pnl\nQQQ,9\n');changed=True
            return original(value)
        with patch('research_export.sanitize',side_effect=rewrite):failed=self.run_job({'type':'evolution_trades'},False)
        self.assertRegex(failed['error'],'modified|mutated')
    def test_text_multiline_private_key_redacted(self):
        self.write('evolution','run-a/config.txt','x=1\n-----BEGIN PRIVATE KEY-----\nTOP_PRIVATE_DATA\n-----END PRIVATE KEY-----\nTOSS_TOKEN=secret-value\n')
        with self.archive(self.run_job({'type':'evolution_full'})) as z:
            text=z.read('evolution/run-a/config.txt')
            self.assertNotIn(b'TOP_PRIVATE_DATA',text);self.assertNotIn(b'secret-value',text)

class ExportAPITests(ExportFixtures):
    # Inherit fixtures, not duplicate the complete suite.
    def test_http_auth_and_download(self):
        import server
        token='offline-export-test'
        with patch.object(server,'RESEARCH_EXPORTER',self.manager),patch.object(server.Handler,'log_message',lambda *a:None),patch.dict(server.SESSIONS,{token:time.time()+60}):
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(http.server_port)
            def req(path,method='GET',body=None,auth=True):
                return urllib.request.urlopen(urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Authorization':'Bearer '+token} if auth else {}))
            try:
                with self.assertRaises(urllib.error.HTTPError) as cm:req('/api/research/export/catalog',auth=False)
                self.assertEqual(cm.exception.code,401)
                with req('/api/research/export/catalog') as response:self.assertTrue(json.load(response)['ok'])
                with self.assertRaises(urllib.error.HTTPError) as cm:req('/api/research/export/jobs','POST',{'type':'evolution_summary'},auth=False)
                self.assertEqual(cm.exception.code,401)
                with req('/api/research/export/jobs','POST',{'type':'evolution_summary'}) as response:
                    self.assertEqual(response.status,202);self.assertIn(json.load(response)['state'],('queued','running'))
                job=self.run_job({'type':'evolution_summary'});key=job['jobId']
                with self.assertRaises(urllib.error.HTTPError):req('/api/research/export/jobs/'+key+'/download',auth=False)
                with req('/api/research/export/jobs/'+key+'/download') as response:
                    self.assertEqual(response.headers['Content-Type'],'application/zip');self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.read())))
                with req('/api/research/export/jobs/'+key+'/ticket','POST',{}) as response:download=json.load(response)['downloadUrl']
                with req(download,auth=False) as response:self.assertEqual(response.status,200)
                with self.assertRaises(urllib.error.HTTPError):req(download,auth=False)
                with req('/api/research/export/summary?format=csv') as response:self.assertIn(b'run-a',response.read())
            finally:http.shutdown();http.server_close();thread.join()

if __name__=='__main__':unittest.main()
