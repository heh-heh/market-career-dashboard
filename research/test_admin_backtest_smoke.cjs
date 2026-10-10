// Offline selected-engine smoke: no network, broker or browser dependency.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'..','admin.html'),'utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
assert.equal(ids.length,new Set(ids).size);
const engines=['simple-v1','v3','v4-ir1','v4-ir2','v4-ir3','v4-all'];
for(const id of engines)assert.ok(html.includes(`option value="${id}"`));
const nodes=new Map(ids.map(id=>[id,{value:'',style:{},innerHTML:'',textContent:'',disabled:false,
  classList:{add(){},remove(){},toggle(){},contains(){return false;}}}]));
nodes.get('btEngineSelect').value='simple-v1';
let calls=[],downloads=[],delayed=null;
class LocalURL extends URL{}
LocalURL.createObjectURL=()=> 'synthetic-url';LocalURL.revokeObjectURL=()=>{};
const context=vm.createContext({console,URL:LocalURL,location:{},setInterval(){},alert(e){throw Error(e)},
 localStorage:{getItem(){return ''},removeItem(){}},
 document:{getElementById(id){assert.ok(nodes.has(id));return nodes.get(id)},querySelectorAll(){return []},
   body:{appendChild(){}},createElement(){return {click(){downloads.push(this.download)},remove(){}}}},
 async fetch(url,options){
   const parsed=new URL(url),body=options.body?JSON.parse(options.body):{};
   const engine=parsed.searchParams.get('engine')||body.engine;
   calls.push({path:parsed.pathname,engine,method:options.method||'GET'});
   assert.match(parsed.pathname,/^\/api\/backtest\/(engines|status|start|download)$/);
   const data={engine,breakdown:engine==='v4-all'||engine==='v3'?{IR_SENTINEL:{trades:2}}:engine==='v4-ir3'?{REGIME_SENTINEL:{trades:2}}:engine?.startsWith('v4-')?{STOCK_SENTINEL:{trades:2}}:{SESSION_SENTINEL:{trades:2}},phase:'completed',progress:100,downloadAvailable:true,summary:{trades:2,winRatePct:50,sumTradeReturnPct:1},
     byStrategy:{IR_SENTINEL:{trades:2}},bySymbol:{STOCK_SENTINEL:{trades:2}},byRegime:{REGIME_SENTINEL:{trades:2}},
     bySession:{SESSION_SENTINEL:{trades:2}},byYear:{2024:{trades:2}},byExitReason:{STOP:{trades:2}}};
   if(delayed&&engine==='v4-ir1'){await new Promise(resolve=>delayed.resolve=resolve)}
   return {ok:true,async json(){return data},async blob(){return {}}};
 }});
new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1],{filename:'admin.html'}).runInContext(context);
(async()=>{
 for(const engine of engines){
   nodes.get('btEngineSelect').value=engine;
   await nodes.get('btEngineSelect').onchange();
   assert.match(nodes.get('simpleBtState').innerHTML,new RegExp(engine));
   const expected=engine==='v4-all'||engine==='v3'?'IR_SENTINEL':engine==='v4-ir3'?'REGIME_SENTINEL':engine.startsWith('v4-')?'STOCK_SENTINEL':'SESSION_SENTINEL';
   assert.match(nodes.get('simpleBtSessions').innerHTML,new RegExp(expected));
   await nodes.get('simpleBtStart').onclick();
   assert.ok(calls.some(c=>c.path==='/api/backtest/start'&&c.engine===engine));
   await nodes.get('simpleBtDownload').onclick();
   assert.ok(downloads.some(d=>d.startsWith(engine+'-backtest-')));
 }
 delayed={};nodes.get('btEngineSelect').value='v4-ir1';
 const old=vm.runInContext('refreshSimpleBacktest()',context);
 nodes.get('btEngineSelect').value='v4-ir2';await vm.runInContext('refreshSimpleBacktest()',context);
 delayed.resolve();await old;
 assert.match(nodes.get('simpleBtState').innerHTML,/v4-ir2/);
 console.log('PASS: 6 engine status/start/download routes, dynamic breakdowns, stale responses ignored');
})().catch(e=>{console.error(e);process.exitCode=1});
