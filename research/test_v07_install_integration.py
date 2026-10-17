"""EC2-like overlay installation, without AWS, market data, or real services."""
import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('v07_integrated_installer',ROOT/'scripts/install_v07_web_console_overlay.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class BundleCITests(unittest.TestCase):
    def test_ci_bundle_is_sha_verified_and_node_parsed(self):
        source=(ROOT/'admin.html').read_text()
        manifest=json.loads((ROOT/installer.UI_MANIFEST).read_text())
        parts=installer.verify_bundle(source,manifest)
        node=shutil.which('node')
        self.assertIsNotNone(node,'CI requires Node to certify the fallback bundle')
        result=subprocess.run([node,'--check'],input=parts['runtime'],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        for revision in ('759238f','b76f0a0','17c5f64'):
            old=subprocess.check_output(['git','show',revision+':admin.html'],cwd=ROOT).decode()
            body=installer.owned_runtime(old)[2]
            self.assertIn(installer.sha(body.encode()),manifest['legacyRuntimeHashes'])
            self.assertEqual(subprocess.run([node,'--check'],input=body,text=True,capture_output=True).returncode,0)

    def test_bad_bundle_or_unrelated_js_change_never_approved_without_node(self):
        wanted=(ROOT/'admin.html').read_bytes();manifest=json.loads((ROOT/installer.UI_MANIFEST).read_text())
        with self.assertRaisesRegex(installer.Blocked,'V07_BUNDLE_HASH_MISMATCH'):
            installer.verify_bundle(wanted.decode().replace('let v07WebPending=false;','let v07WebPending=true;'),manifest)
        with patch.object(installer.shutil,'which',return_value=None):
            bad=wanted.replace(b'const TOKEN_KEY=',b'const unrelatedInjected=true; const TOKEN_KEY=')
            with self.assertRaisesRegex(installer.Blocked,'NON_V07_JAVASCRIPT_CHANGED'):
                installer.validate_admin(wanted,bad,wanted,manifest)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.target=self.root/'production';self.target.mkdir()
        self.backups=self.root/'research'/'backups'
        self.base={n:subprocess.check_output(['git','show','759238f:'+n],cwd=ROOT) for n in ('server.py','admin.html','v07_web_jobs.py')}
        self.wanted=(ROOT/'admin.html').read_bytes()
        self.manifest=json.loads((ROOT/installer.UI_MANIFEST).read_text())
        (self.target/'server.py').write_bytes(b'# EC2 local auth and broker customization\n'+self.base['server.py'])
        (self.target/'v07_web_jobs.py').write_bytes(self.base['v07_web_jobs.py'])
        (self.target/'admin.html').write_bytes(self.no_v07_html())
        # Protected data/config/artifacts remain byte-identical through every test.
        for name in ('toss_auth.py','.env','data/toss_1m/AAPL.csv','data/toss_ticks/raw.sqlite3','v07-results/run.json'):
            p=self.target/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'PROTECTED_TEST_FIXTURE')
        self.patches=[patch.object(installer,'TARGET',self.target),patch.object(installer,'BACKUP_ROOT',self.backups),
            patch.object(installer,'MANAGED',True),patch.object(installer,'git',side_effect=self.git),
            patch.object(installer,'payload',return_value=((ROOT/'v07_web_jobs.py').read_bytes(),'fixture-head'))]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def git(self,*args):
        revision,name=args[1].split(':',1)
        return self.base[name] if revision=='759238f' else (ROOT/name).read_bytes()

    def no_v07_html(self):
        text=self.base['admin.html'].decode()
        doc=installer.HtmlParts(text);node=doc.one('v07WebPanel')
        text=text[:node['start']]+text[node['end']:]
        a,b,_,_=installer.owned_runtime(text);text=text[:a]+text[b:]
        text=text.replace("if(name==='ai')refreshAI();", "if(name==='ai'){refreshAI(); window.ec2CustomNavigation=true;}")
        return text.encode()

    def snapshot(self):
        return {str(p.relative_to(self.target)):(p.read_bytes(),p.stat().st_mode,p.stat().st_mtime_ns) for p in self.target.rglob('*') if p.is_file()}

    @contextlib.contextmanager
    def no_node(self):
        # Use an actual PATH containing Git but NO Node; don't just suppress errors.
        bins=self.root/'git-only-bin';bins.mkdir(exist_ok=True)
        if not (bins/'git').exists():(bins/'git').symlink_to(shutil.which('git'))
        with patch.dict(os.environ,{'PATH':str(bins)}):
            self.assertIsNone(shutil.which('node'))
            yield

    @contextlib.contextmanager
    def fake_operations(self,health=True):
        real_stat=os.stat
        def mounted(path,*a,**kw):
            if str(path)=='/mnt/research':return type('Mount',(),{'st_dev':real_stat('/').st_dev+1})()
            return real_stat(path,*a,**kw)
        with patch.object(installer.os,'geteuid',return_value=0),patch.object(installer,'market_guard'), \
             patch.object(installer,'health',return_value=health),patch.object(installer.os,'stat',side_effect=mounted), \
             patch.object(installer,'research_idle',contextlib.nullcontext), \
             patch.object(installer,'command',return_value=b'active') as commands:
            yield commands

    def test_ec2_no_node_no_v07_custom_nav_check_is_read_only(self):
        before=self.snapshot()
        with self.no_node(),patch.object(sys,'argv',['installer','--managed','--check']),contextlib.redirect_stdout(io.StringIO()) as out:
            installer.main()
        result=json.loads(out.getvalue());self.assertEqual(result['phase'],'READY')
        self.assertEqual(result['javascriptValidation'],'VERIFIED_V07_SHA256_EXISTING_JS_PRESERVED')
        self.assertEqual(before,self.snapshot())
        self.assertFalse(self.backups.exists())
        self.assertTrue(all(x.get('afterSha256') for x in result['changes']))

    def test_apply_without_node_and_second_install_are_identical(self):
        original=self.snapshot();planned,_=installer.plan()
        with self.no_node(),self.fake_operations() as commands,contextlib.redirect_stdout(io.StringIO()) as out:
            installer.apply()
        installed=json.loads(out.getvalue());self.assertEqual(installed['phase'],'COMMITTED')
        for name,(_,wanted) in planned.items():self.assertEqual((self.target/name).read_bytes(),wanted)
        self.assertTrue((self.target/'server.py').read_bytes().startswith(b'# EC2 local auth and broker customization'))
        for name,record in original.items():
            if name not in planned:self.assertEqual((self.target/name).read_bytes(),record[0])
        manifest=json.loads(next(self.backups.glob('*/manifest.json')).read_text())
        for name,expected in manifest['newHashes'].items():self.assertEqual(installer.sha((self.target/name).read_bytes()),expected)
        before=self.snapshot()
        with self.no_node(),self.fake_operations() as commands,contextlib.redirect_stdout(io.StringIO()) as out:
            installer.apply()
            self.assertFalse(any('restart' in c.args for c in commands.call_args_list))
        self.assertEqual(json.loads(out.getvalue())['phase'],'ALREADY_INSTALLED')
        self.assertEqual(before,self.snapshot())
        parsed=installer.HtmlParts((self.target/'admin.html').read_text())
        for name in ('v07WebPanel','v07ManagedPanel','v07ManagedRuntime'):self.assertIsNotNone(parsed.one(name))

    def test_minimal_append_console_and_different_indentation(self):
        minimal='''<!doctype html><html><body><section id='ai'><p>EC2 AI</p></section>
\t<section id='console'>\n\t\t<div class='terminal' id='consoleOut'>local</div></section>
<script>function appendConsole ( v ) { window.localConsole=v; }
function navigateAdmin(name){window.customName=name;}</script></body></html>'''
        (self.target/'admin.html').write_text(minimal)
        with self.no_node():parts,_=installer.plan()
        merged=parts['admin.html'][1].decode()
        self.assertIn('function navigateAdmin(name){window.customName=name;}',merged)
        self.assertIn("\t\t<div class='terminal' id='consoleOut'>local</div>",merged)
        self.assertEqual(installer.unrelated_scripts(minimal),installer.unrelated_scripts(merged))

    def test_prior_deployed_v07_card_and_runtime_upgrade_without_overwriting_legacy_js(self):
        previous=subprocess.check_output(['git','show','48117be:admin.html'],cwd=ROOT)
        (self.target/'admin.html').write_bytes(previous)
        with self.no_node():
            planned,_=installer.plan()
        updated=planned['admin.html'][1].decode()
        original=previous.decode()
        self.assertIn('id="v07AiLog"',updated)
        self.assertIn('id="v07ManagedPanel"',updated)
        self.assertEqual(updated.count('id="v07ManagedPanel"'),1)
        self.assertEqual(installer.unrelated_scripts(original),installer.unrelated_scripts(updated))
        self.assertEqual(original,(self.target/'admin.html').read_text())

    def test_partial_known_installations_fill_missing_pieces_without_duplicates(self):
        for remove in ('web','managed','runtime'):
            text=self.wanted.decode();doc=installer.HtmlParts(text)
            if remove=='runtime':
                a,b,_,_=installer.owned_runtime(text);text=text[:a]+text[b:]
            else:
                n=doc.one('v07WebPanel' if remove=='web' else 'v07ManagedPanel');text=text[:n['start']]+text[n['end']:]
            (self.target/'admin.html').write_text(text)
            with self.no_node():parts,_=installer.plan()
            merged=parts['admin.html'][1].decode();parsed=installer.HtmlParts(merged)
            self.assertIsNotNone(parsed.one('v07WebPanel'));self.assertIsNotNone(parsed.one('v07ManagedPanel'))
            self.assertEqual(merged.count('let v07WebPending=false;'),1)
            self.assertEqual(installer.unrelated_scripts(text),installer.unrelated_scripts(merged))

    def test_unknown_owned_edits_duplicate_ids_and_incomplete_script_fail_closed(self):
        cases=[(self.wanted.replace(b'command:"v07 train"',b'command:"untrusted"') if b'command:"v07 train"' in self.wanted else self.wanted.replace(b'const cmd="v07 "',b'const cmd="evil "'),'LOCAL_V07_JS_CONFLICT'),
               (self.wanted.replace(b'id="v07ManagedPanel"',b'id="v07WebPanel"'),'DUPLICATE_DOM_ID'),
               (self.wanted.replace(b'let v07WebPending=false;',b'let v07WebPending=true;'),'LOCAL_V07_JS_CONFLICT')]
        for data,reason in cases:
            (self.target/'admin.html').write_bytes(data);before=self.snapshot()
            with self.no_node(),self.assertRaisesRegex(installer.Blocked,reason):installer.plan()
            self.assertEqual(self.snapshot(),before)

    def test_node_available_but_broken_and_disappearing_node_have_clear_errors(self):
        parts,_=installer.plan()
        self.assertEqual(installer.LAST_VALIDATION,'NODE_SYNTAX_AND_V07_SHA256')
        with patch.object(installer.shutil,'which',return_value='/nonexistent/node'):
            with self.assertRaisesRegex(installer.Blocked,'NODE_DISAPPEARED_DURING_CHECK'):
                installer.validate_admin(parts['admin.html'][0],parts['admin.html'][1],self.wanted,self.manifest)
        broken=parts['admin.html'][1].replace(b'function appendConsole(v)',b'function appendConsole((v)')
        # Proof of preservation must fail before even attempting Node syntax approval.
        with self.assertRaisesRegex(installer.Blocked,'NON_V07_JAVASCRIPT_CHANGED'):
            installer.validate_admin(parts['admin.html'][0],broken,self.wanted,self.manifest)

    def test_mid_install_failure_rolls_back_every_changed_file(self):
        before=self.snapshot();real=installer.atomically;calls=[]
        def fail_second(path,data,st):
            calls.append(path.name)
            if len(calls)==2:raise OSError('synthetic disk write failure')
            return real(path,data,st)
        with self.no_node(),self.fake_operations(),patch.object(installer,'atomically',side_effect=fail_second):
            with self.assertRaisesRegex(OSError,'synthetic disk write failure'):installer.apply()
        after=self.snapshot()
        self.assertEqual(set(before),set(after))
        for name in before:self.assertEqual(before[name][:2],after[name][:2])
        manifest=json.loads(next(self.backups.glob('*/manifest.json')).read_text())
        self.assertEqual(manifest['phase'],'ROLLED_BACK')

    def test_change_after_preflight_and_changed_owned_module_are_not_overwritten(self):
        original=installer.plan
        def changed():
            result=original();(self.target/'server.py').write_bytes(b'# concurrent change\n'+(self.target/'server.py').read_bytes());return result
        with self.no_node(),self.fake_operations(),patch.object(installer,'plan',side_effect=changed):
            with self.assertRaisesRegex(installer.Blocked,'CHANGED_(SINCE_PREFLIGHT|DURING_BACKUP)'):installer.apply()
        self.assertTrue((self.target/'server.py').read_bytes().startswith(b'# concurrent change'))
        (self.target/'v07_web_jobs.py').write_bytes(b'# unexpected local module')
        with self.assertRaisesRegex(installer.Blocked,'EXISTING_WEB_MODULE_DIFFERS'):installer.plan()

    def test_worker_or_inherited_flock_blocks_apply_before_changes(self):
        before=self.snapshot();real_read=Path.read_bytes
        def live_worker(p):
            if str(p).endswith('/cmdline'):return b'/usr/bin/python\x00v07_web_jobs.py\x00--worker\x00train\x00job\x00'
            return real_read(p)
        with self.fake_operations(),patch.object(Path,'read_bytes',live_worker):
            with self.assertRaisesRegex(installer.Blocked,'V07_WORKER_ACTIVE'):installer.apply()
        self.assertEqual(before,self.snapshot())
        fake=self.root/'lock';handle=fake.open('a+');fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Exercise actual research_idle acquisition on a controlled lock path.
        real_path=installer.Path
        def paths(value):
            if value=='/mnt/research/v07-web-jobs/research.lock':return fake
            return real_path(value)
        try:
            with patch.object(installer,'Path',side_effect=paths):
                with self.assertRaisesRegex(installer.Blocked,'RESEARCH_CHILD_ACTIVE'):
                    with installer.research_idle():pass
        finally:handle.close()



class ExtraIntegrationTests(InstallationTests):
    def test_approved_legacy_upgrade_without_node_and_partial_runtime_block(self):
        (self.target/'admin.html').write_bytes(self.base['admin.html'])
        with self.no_node():parts,_=installer.plan()
        merged=parts['admin.html'][1].decode()
        self.assertEqual(installer.owned_runtime(merged)[2],installer.runtime_block(self.wanted.decode()))
        self.assertEqual(installer.unrelated_scripts(self.base['admin.html'].decode()),installer.unrelated_scripts(merged))
        text=self.no_v07_html().decode().replace('function appendConsole(v)', 'async function refreshV07Web(){return;}\nfunction appendConsole(v)')
        (self.target/'admin.html').write_text(text)
        with self.no_node(),self.assertRaisesRegex(installer.Blocked,'LOCAL_V07_JS_CONFLICT'):installer.plan()

    def test_generated_runtime_has_single_listeners_and_preserves_navigation(self):
        with self.no_node():parts,_=installer.plan()
        output=parts['admin.html'][1].decode();owned=installer.owned_runtime(output)
        self.assertEqual(owned[3],'dedicated')
        # Definitions precede the original console startup script; no late script race.
        self.assertLess(output.index('id="v07ManagedRuntime"'),output.index('function appendConsole(v)'))
        script=r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
let input='';process.stdin.on('data',c=>input+=c);process.stdin.on('end',async()=>{
 const nodes=new Map(),events={},commands=[];let intervals=0,customCalls=0;
 const get=id=>{if(!nodes.has(id))nodes.set(id,{classList:{contains:()=>false},disabled:false});return nodes.get(id)};
 const ai={dataset:{tab:'ai'},onclick:()=>customCalls++,addEventListener:(k,f)=>{(events[k]??=[]).push(f)}};
 const custom=ai.onclick;
 const controls=[{dataset:{v07:'audit'},disabled:false}];
 const ctx=vm.createContext({
 document:{getElementById:get,querySelectorAll:s=>s==='[data-v07]'?controls:s.includes('#adminSubnav')?[ai]:[]},
 window:{addEventListener:(k,f)=>{(events[k]??=[]).push(f)}},location:{hash:'#ai'},currentAdminGroup:'ai',
 token:()=> 'synthetic',API:'not-a-real-server',
 req:async(_path,options)=>{const command=JSON.parse(options.body).command;commands.push(command);return{output:command==='v07 report'?'{}':'synthetic fixture'}},
 setText:()=>{},setInterval:()=>intervals++,confirm:()=>true,alert:()=>{},appendConsole:()=>{},
 });
 vm.runInContext(input,ctx);
 assert.equal(ai.onclick,custom);assert.equal(intervals,1);
 assert.equal(events.click.length,1);assert.equal(events.hashchange.length,1);
 ai.onclick();await events.click[0]();assert.equal(customCalls,1);
 await controls[0].onclick();assert.equal(commands.filter(c=>c==='v07 audit').length,1);
 assert.equal(typeof get('v07ManagedDownload').onclick,'function');
 console.log('generated runtime events PASS');
});
'''
        result=subprocess.run([shutil.which('node'),'-e',script],input=owned[2],text=True,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('events PASS',result.stdout)

    def test_health_failure_rolls_back_and_only_api_service_restarts(self):
        before=self.snapshot()
        # First health is preflight, 18 failed post-install probes, then recovered API.
        with self.no_node(),self.fake_operations() as commands,patch.object(installer,'health',side_effect=[True]+[False]*18+[True]),patch.object(installer.time,'sleep'):
            with self.assertRaisesRegex(installer.Blocked,'API_HEALTH_CHECK_FAILED'):installer.apply()
        after=self.snapshot()
        self.assertEqual(set(before),set(after))
        for name in before:self.assertEqual(before[name][:2],after[name][:2])
        for call in commands.call_args_list:
            if 'restart' in call.args:self.assertEqual(call.args[0:3],('systemctl','restart',installer.SERVICE))
        manifest=json.loads(next(self.backups.glob('*/manifest.json')).read_text())
        self.assertEqual(manifest['phase'],'ROLLED_BACK')

    def test_invalid_marker_order_is_explicit_not_substring_exception(self):
        text=self.wanted.decode().replace('// V07_RUNTIME_BEGIN\n','// TEMP_BEGIN\n').replace('// V07_RUNTIME_END','// V07_RUNTIME_BEGIN\n').replace('// TEMP_BEGIN','// V07_RUNTIME_END')
        (self.target/'admin.html').write_text(text)
        with self.assertRaisesRegex(installer.Blocked,'RUNTIME_MARKER_ORDER_INVALID'):installer.plan()


if __name__=='__main__':unittest.main()
