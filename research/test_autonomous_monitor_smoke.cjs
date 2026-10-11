// Offline monitor / export fixtures. No training or broker endpoints.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
assert.match(html,/id="autonomousResearchCard"/);assert.match(html,/src="autonomous-research.js"/);
const code=fs.readFileSync('autonomous-research.js','utf8');new vm.Script(code);
assert(!code.includes('/api/trading'));assert(!code.includes('/api/v1/orders'));
const nodes=new Map();let click,requests=[],anchors=[];
function node(id){if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',addEventListener(t,fn){click=fn;}});return nodes.get(id);}
const id='a'.repeat(24),job='b'.repeat(32);
const ctx=vm.createContext({AWS_API_BASE:'https://offline.invalid',settingsHeaders:()=>({Authorization:'Bearer offline'}),clearTimeout(){},setTimeout(){return 1},Date,Promise,
 document:{getElementById:node,body:{appendChild(){}},createElement(){const a={click(){anchors.push({...a});},remove(){}};return a;}},
 async fetch(url,options){requests.push({url,options});const data=url.endsWith('/status')?{ok:true,current:{state:'planning',hypothesis:'<script>evil()</script>',finalOosGate:'CLOSED'},recent:[{id,iteration:1,state:'completed'}]}:url.endsWith('/ticket')?{ok:true,downloadUrl:'/api/research/export/jobs/'+job+'/download?ticket=temporary'}:{ok:true,jobId:job,state:'completed'};return {ok:true,json:async()=>data};}});
new vm.Script(code).runInContext(ctx);
(async()=>{
 await new Promise(resolve=>setImmediate(resolve));
 assert.match(node('autonomousCurrent').innerHTML,/&lt;script&gt;/);assert(!node('autonomousCurrent').innerHTML.includes('<script>evil'));
 assert.equal(requests[0].options.headers.Authorization,undefined);
 await click({target:{closest:()=>({dataset:{iteration:id},disabled:false})}});
 const create=requests.find(r=>r.options.method==='POST'&&!r.url.endsWith('/ticket'));
 assert.deepEqual(JSON.parse(create.options.body),{type:'autonomous_iteration',runIds:[id]});
 assert.equal(anchors.length,1);assert.equal(anchors[0].rel,'noreferrer');
 assert(requests.every(r=>r.url.includes('/api/research/')));
 console.log('PASS: autonomous read-only monitor, escaped hypothesis, isolated authenticated iteration export, CLOSED Final OOS');
})().catch(e=>{console.error(e);process.exitCode=1});
