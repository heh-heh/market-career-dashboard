const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.join(__dirname,'..'),h=fs.readFileSync(root+'/admin.html','utf8'),js=fs.readFileSync(root+'/v082-web.js','utf8');
for(const id of ['v082WebPanel','v082WebCapacity','v082WebStats','v082WebLineage'])assert.ok(h.includes('id="'+id+'"'));
assert.ok(h.includes('id="v08WebPanel"'),'V08 retained');
assert.match(h,/data-v082="smoke" type="button" disabled/);
assert.match(h,/data-v082-retry="calibrate">자원 산정 재실행<\/button>/);
assert.match(js,/V082_BACKEND_UNSUPPORTED/);assert.match(js,/V082_WEB_1/);assert.match(js,/2026 OOS CLOSED/);
new vm.Script(js);
const nodes=new Map(),buttons=Array.from(h.matchAll(/data-v082="(\w+)"/g)).map(x=>({dataset:{v082:x[1]},addEventListener(){},disabled:true}));
function get(id){if(!nodes.has(id))nodes.set(id,{textContent:'',innerHTML:'',disabled:true,addEventListener(){}});return nodes.get(id)}
let calls=[];let answer={protocol:'OLD'};
const context=vm.createContext({document:{getElementById:get,querySelectorAll:q=>q==='[data-v082]'?buttons:[]},location:{hash:''},token:()=>true,req:async(url,opts)=>{calls.push(JSON.parse(opts.body).command);return {ok:true,output:JSON.stringify(answer)}},confirm:()=>true,setInterval(){},Promise,API:'',console});
vm.runInContext(js.replace('})();','globalThis.test={refresh,action,render};})();'),context);
(async()=>{
 await context.test.action('train');assert.equal(calls.length,0,'Unsupported backend never receives start');
 await context.test.refresh();assert.ok(buttons.every(b=>b.disabled));
 answer={protocol:'V082_WEB_1',stages:{},runs:{},initialPopulation:50,maxPopulation:50};
 await context.test.refresh();assert.ok(buttons.find(b=>b.dataset.v082==='smoke').disabled===false);
 assert.ok(buttons.find(b=>b.dataset.v082==='train').disabled);
 context.test.render({protocol:'V082_WEB_1',stages:{smoke:{phase:'completed'},calibrate:{phase:'completed'}},capacity:{population:50,status:"MEASURED_SAFE"},runs:{},generations:[]});
 assert.match(get('v082WebStats').innerHTML,/50/);assert.equal(buttons.find(b=>b.dataset.v082==='train').disabled,false);
 context.test.render({protocol:'V082_WEB_1',stages:{smoke:{phase:'completed'},calibrate:{phase:'failed',error:'V082_RUN_EXIT_2'}},capacity:{trials:[{label:'measured-1',resourceReport:{guardPassed:false,failure:'RESOURCE_TARGET_EXCEEDED:cpuPct:75.5>75.0'}}],status:'BLOCKED'},runs:{},generations:[]});
 assert.match(get('v082WebFeedback').textContent,/RESOURCE_TARGET_EXCEEDED:cpuPct:75\.5>75\.0/);
 assert.match(get('v082WebFeedback').textContent,/BLOCKED/);
 context.test.render({protocol:'V082_WEB_1',stages:{smoke:{phase:'running'}},runs:{smoke:{indicatorAnalysis:{AAPL:{symbol:'<img>',decision:'WAIT',reason:'INDICATOR_CONFLICT',botId:'b0',decisionTimestamp:'2023-06-01T13:00:00-04:00',indicators:{rsi14:55,spread:null},portfolio:{held:1,allowedPositions:1}}}}},generations:[]});
 assert.match(get('v082SymbolAnalysis').innerHTML,/rsi14/);assert.match(get('v082SymbolAnalysis').innerHTML,/&lt;img&gt;/);assert.ok(!get('v082SymbolAnalysis').innerHTML.includes('<img>'));
 console.log('PASS: V082 protocol gate, disabled unsupported actions, measured capacity and legacy V08 retained');
})().catch(e=>{console.error(e);process.exit(1)});
