import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dashboard_data import atomic_json,read_dashboard,validate_dashboard


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def fixture():
    quotes=[{'symbol':'S'+str(n),'price':10+n,'source':'Yahoo Finance'} for n in range(12)]
    return {'updatedAt':'2026-10-28T00:00:00+00:00','stocks':quotes[:10],'indices':quotes[10:],
            'news':[{'title':'news'}],'employmentNews':[{'title':'career'}],'jobPostings':[{'title':'job'}]}


class HomeTests(unittest.TestCase):
    def test_atomic_invalid_keeps_previous(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'dashboard.json';atomic_json(p,fixture());before=p.read_bytes()
            with self.assertRaises(ValueError): atomic_json(p,{'price':float('nan')})
            self.assertEqual(before,p.read_bytes());self.assertEqual(read_dashboard(p),fixture())
            with self.assertRaises(ValueError):validate_dashboard({**fixture(),'stocks':[]})
            with self.assertRaises(ValueError):validate_dashboard({**fixture(),'indices':[]})

    def test_cached_only_is_not_success(self):
        v=fixture()
        for item in v['stocks']+v['indices']:item['source']='cached'
        with self.assertRaises(ValueError):validate_dashboard(v,require_fresh=True)

    def test_read_only_routes_never_load_broker(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'DASHBOARD_ONLY':'true','DASHBOARD_DATA_DIR':d}):
            atomic_json(Path(d)/'dashboard.json',fixture())
            server=module('home_server','server.py')
            self.assertIsNone(server.LIVE_TRADER)
            self.assertNotIn('LiveAutoTrader',server.__dict__)
            http=server.DashboardHTTPServer(('127.0.0.1',0),server.Handler)
            t=threading.Thread(target=http.serve_forever,daemon=True);t.start()
            base='http://127.0.0.1:'+str(http.server_address[1])
            def req(path,method='GET',origin='https://heh-heh.github.io'):
                r=urllib.request.Request(base+path,method=method,headers={'Origin':origin})
                try:resp=urllib.request.urlopen(r,timeout=3)
                except urllib.error.HTTPError as e:resp=e
                with resp:return resp.status,resp.headers,json.load(resp)
            try:
                status,h,data=req('/api/health');self.assertEqual(status,200)
                self.assertFalse(data['tradingEnabled']);self.assertTrue(data['dataReady'])
                self.assertEqual(h['Access-Control-Allow-Origin'],'https://heh-heh.github.io')
                self.assertIsNone(req('/api/health',origin='https://evil.example')[1].get('Access-Control-Allow-Origin'))
                for p in ('stocks','indices','news','jobs','employment','dashboard','quotes'):
                    self.assertEqual(req('/api/'+p)[0],200)
                for p in ('/admin.html','/api/settings','/api/trading/status','/api/backtest/monitor','/.env','/api/research/spot/start'):
                    self.assertEqual(req(p)[0],403)
                for p in ('/api/login','/api/trading/live/arm','/api/trading/paper/order','/api/research/start'):
                    self.assertEqual(req(p,'POST')[0],403)
            finally:http.shutdown();http.server_close();t.join()

    def test_cross_process_duplicate_lock(self):
        collector=module('home_collector','scripts/collect_dashboard.py')
        with tempfile.TemporaryDirectory() as d:
            with (Path(d)/'collector.lock').open('a') as f:
                fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with patch.object(collector.subprocess,'Popen') as popen:
                    self.assertEqual(collector.run_once(d,10),75);popen.assert_not_called()

    def test_supervisor_failure_keeps_good_data(self):
        collector=module('home_collector_failure','scripts/collect_dashboard.py')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'dashboard.json';atomic_json(p,fixture());before=p.read_bytes()
            with patch.object(collector.subprocess,'Popen') as mock:
                mock.return_value.wait.return_value=3;mock.return_value.poll.return_value=3
                self.assertEqual(collector.run_once(d,10),1)
                self.assertIn('pass_fds',mock.call_args.kwargs)
            self.assertEqual(before,p.read_bytes())
            self.assertEqual(json.loads((Path(d)/'collector_status.json').read_text())['phase'],'FAILED')

    def test_timeout_terminates_worker_without_publish(self):
        collector=module('home_collector_timeout','scripts/collect_dashboard.py')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'dashboard.json';atomic_json(p,fixture());before=p.read_bytes()
            with patch.object(collector.subprocess,'Popen') as mock,patch.object(collector.os,'killpg') as kill:
                mock.return_value.wait.side_effect=[subprocess.TimeoutExpired('worker',10),0]
                mock.return_value.poll.return_value=None;mock.return_value.pid=123
                self.assertEqual(collector.run_once(d,10),1);kill.assert_called()
            self.assertEqual(before,p.read_bytes())

    def test_jobs_deduplicate_cache_and_cap_details(self):
        worker=module('home_update','scripts/update_data.py')
        item={'link':'https://jobs.example/job?id=1','title':'Python 서버 개발자','publishedAt':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'company':'ABC'}
        with patch.object(worker,'direct_job_list',return_value=[item,item]),patch.object(worker,'rss',return_value=[]),patch.object(worker,'parse_job_detail',return_value={'detailText':'Python server'}) as detail,patch.dict(os.environ,{'JOB_LIST_PAGES':'1','JOB_DETAIL_LIMIT':'1'}):
            result,_=worker.collect_job_postings({});self.assertEqual(len(result),1);self.assertEqual(detail.call_count,1)
            worker.collect_job_postings({});self.assertEqual(detail.call_count,1)
            self.assertNotEqual(worker.canonical_job_link('https://a/jobs?id=1'),worker.canonical_job_link('https://a/jobs?id=2'))
            self.assertEqual(worker.canonical_job_link('https://a/jobs?id=1&utm_source=x'),'https://a/jobs?id=1')

    def test_request_timeout_and_response_size(self):
        worker=module('home_update_network','scripts/update_data.py')
        with patch.object(worker.urllib.request,'urlopen') as op:
            op.return_value.__enter__.return_value.read.return_value=b'{}'
            self.assertEqual(worker.get('https://example.com',timeout=60),b'{}')
            self.assertLessEqual(op.call_args.kwargs['timeout'],8)
            op.return_value.__enter__.return_value.read.return_value=b'x'*(8*1024*1024+1)
            with self.assertRaises(ValueError):worker.get('https://example.com',attempts=1)

    def test_failed_discovery_preserves_section_timestamp(self):
        worker=module('home_update_failed','scripts/update_data.py')
        old={'news':[{'title':'old'}],'newsUpdatedAt':'2026-01-01T00:00:00Z'}
        with patch.object(worker,'rss',return_value=[]):self.assertEqual(worker.collect_stock_news(old),(old['news'],old['newsUpdatedAt']))

    def test_https_configuration_rejects_insecure_and_unverified(self):
        config=module('home_config','scripts/configure_dashboard_api.py')
        with self.assertRaises(ValueError):config.verify('http://example.com')
        with patch.object(config.urllib.request,'urlopen',side_effect=OSError('offline')):
            with self.assertRaises(OSError):config.verify('https://example.com')


class InstallTests(unittest.TestCase):
    def simulate(self,fail):
        import shutil
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo=root/'repo';repo.mkdir();bin_dir=root/'bin';bin_dir.mkdir()
            for rel in ('server.py','dashboard_data.py','scripts/update_data.py','scripts/collect_dashboard.py','scripts/configure_dashboard_api.py','.dockerignore','deploy/dashboard/Dockerfile','deploy/dashboard/compose.yml','deploy/dashboard/install.sh'):
                target=repo/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,target)
            for args in (['init','-b','main'],['config','user.name','test'],['config','user.email','test@example.invalid'],['add','.'],['commit','-qm','fixture'],['remote','add','origin','https://github.com/heh-heh/market-career-dashboard.git'],['update-ref','refs/remotes/origin/main','HEAD']):
                subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True)
            data=root/'data';data.mkdir();atomic_json(data/'dashboard.json',fixture());before=(data/'dashboard.json').read_bytes()
            docker='''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
a=sys.argv[1:]
with open(os.environ['DOCKER_TRACE'],'a') as f:f.write(json.dumps(a)+'\\n')
if a[0]=='inspect':
 n=a[-1]
 if n not in ('market-collector-test','market-dashboard-api-stage-1'):sys.exit(1)
 print('true' if '-f' in a else '[]')
elif a[0]=='ps':print('chess-academy running\\nportainer running')
elif a[0]=='compose' and 'run' in a and os.environ['FAIL_RUN']=='1':sys.exit(9)
'''
            curl='''#!/usr/bin/env python3
import json,sys,os
from pathlib import Path
a=sys.argv[1:]
if 'POST' in a:print('403',end='')
elif '/api/dashboard' in a[-1]:print((Path(os.environ['DASHBOARD_DATA_DIR'])/'dashboard.json').read_text())
else:print(json.dumps({'mode':'dashboard-only','tradingEnabled':False,'ok':True}))
'''
            for name,text in {'docker':docker,'curl':curl,'id':'#!/bin/sh\necho 1000\n','pgrep':'#!/bin/sh\nexit 1\n','systemctl':'#!/bin/sh\nexit 1\n'}.items():
                p=bin_dir/name;p.write_text(text);p.chmod(0o755)
            env={**os.environ,'PATH':str(bin_dir)+':'+os.environ['PATH'],'REPO_DIR':str(repo),'DASHBOARD_DEPLOY_DIR':str(root/'deploy'),'DASHBOARD_DATA_DIR':str(data),'DOCKER_TRACE':str(root/'trace'),'FAIL_RUN':str(int(fail))}
            result=subprocess.run(['bash',str(ROOT/'deploy/dashboard/install.sh')],env=env,text=True,capture_output=True)
            actions=[json.loads(line) for line in (root/'trace').read_text().splitlines()]
            self.assertEqual((data/'dashboard.json').read_bytes(),before)
            self.assertEqual(subprocess.check_output(['git','-C',str(repo),'status','--porcelain']),b'')
            self.assertTrue(list((root/'deploy/backups').glob('*/dashboard.json')))
            self.assertTrue(all('chess' not in ' '.join(a).lower() and 'portainer' not in ' '.join(a).lower() for a in actions))
            if fail:
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn(['start','market-collector-test'],actions)
                self.assertIn(['start','market-dashboard-api-stage-1'],actions)
            else:self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_install_success_isolated_from_other_projects(self):self.simulate(False)
    def test_install_failure_restores_legacy_services_and_keeps_data(self):self.simulate(True)

if __name__=='__main__':unittest.main()
