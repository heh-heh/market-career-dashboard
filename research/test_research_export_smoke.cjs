// Offline DOM/API fixtures; no archive, EC2 or trading requests.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const rootPath=process.env.RESEARCH_EXPORT_TEST_ROOT||path.join(__dirname,'..');
const html=fs.readFileSync(path.join(rootPath,'index.html'),'utf8');
for(const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);
assert.match(html,/id="researchExportCenter"/);assert.match(html,/src="research-export.js"/);assert.match(html,/href="research-export.css"/);
const code=fs.readFileSync(path.join(rootPath,'research-export.js'),'utf8');new vm.Script(code);
assert(!code.includes('/api/v1/orders'));assert(!code.includes('/api/trading/'));
const nodes=new Map();let click,selected=[],requests=[],anchors=[],statusText='';
function node(id){if(!nodes.has(id))nodes.set(id,{textContent:'',innerHTML:'',value:'',addEventListener(type,fn){click=fn;},querySelectorAll(sel){return selected.filter(x=>sel.includes(x.kind));}});return nodes.get(id);}
const root=node('researchExportCenter');
const catalog={ok:true,sources:[{id:'evolution',available:true,files:5,estimatedBytes:1024}],symbols:['SPY','QQQ'],runs:[{id:'a'.repeat(24),label:'V0.7 <script>evil()</script>',runId:'run1',population:20,meanValidation:null}],jobs:[]};
const ctx=vm.createContext({console,AWS_API_BASE:'https://offline.invalid',settingsHeaders:()=>({Authorization:'Bearer offline'}),clearTimeout(){},setTimeout(){return 1},URL:{createObjectURL(){return 'blob:summary'},revokeObjectURL(){}},document:{getElementById:node,createElement(){const a={click(){anchors.push({...a})},remove(){}};return a;},body:{appendChild(){}}},async fetch(url,options){requests.push({url,options});let value=url.endsWith('/catalog')?catalog:url.endsWith('/ticket')?{ok:true,downloadUrl:'/api/research/export/jobs/'+ 'b'.repeat(32)+'/download?ticket=temporary'}:{ok:true,jobId:'b'.repeat(32),type:'evolution_summary',state:'completed',progressPct:100};return {ok:true,json:async()=>value};}});
new vm.Script(code).runInContext(ctx);
async function action(name,job){const button={dataset:{export:name,job},disabled:false};await click({target:{closest:()=>button}});}
(async()=>{
 await action('raw');assert.equal(requests.length,0);assert.match(node('exportMessage').textContent,/종목/);
 await action('refresh');assert.match(node('exportRuns').innerHTML,/&lt;script&gt;/);assert(!node('exportRuns').innerHTML.includes('<script>evil'));
 assert(!node('exportSymbols').innerHTML.includes('checked'));assert.equal(node('exportFrom').value,'');
 await action('evolution_summary');assert.deepEqual(JSON.parse(requests.at(-1).options.body),{type:'evolution_summary'});
 selected=[{kind:'exportSymbol',value:'SPY'}];node('exportType').value='toss_ticks';node('exportFrom').value='2024-01-01';node('exportTo').value='2024-03-01';const count=requests.length;
 await action('raw');assert.equal(requests.length,count);assert.match(node('exportMessage').textContent,/31일/);
 await action('download','b'.repeat(32));assert.equal(anchors.length,1);assert(anchors[0].href.includes('/download?ticket='));assert.equal(anchors[0].rel,'noreferrer');assert(!anchors[0].href.includes('Bearer'));
 assert(requests.every(r=>r.url.includes('/api/research/export/')));
 console.log('PASS: export UI auth, escaped metadata, explicit raw selection, tick limits, jobs, native scoped download; no trading endpoints');
})().catch(e=>{console.error(e);process.exitCode=1});
