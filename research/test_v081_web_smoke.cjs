const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.join(__dirname,'..'),h=fs.readFileSync(root+'/admin.html','utf8'),js=fs.readFileSync(root+'/v081-web.js','utf8');
for(const id of ['v081WebPanel','v081WebCapacity','v081WebStats','v081WebLineage'])assert.ok(h.includes('id="'+id+'"'));
assert.ok(h.includes('id="v08WebPanel"'),'V08 retained');
assert.match(h,/data-v081="smoke" type="button" disabled/);
assert.match(js,/V081_BACKEND_UNSUPPORTED/);assert.match(js,/V081_WEB_1/);assert.match(js,/2026 OOS CLOSED/);
new vm.Script(js);
const nodes=new Map(),buttons=Array.from(h.matchAll(/data-v081="(\w+)"/g)).map(x=>({dataset:{v081:x[1]},addEventListener(){},disabled:true}));
function get(id){if(!nodes.has(id))nodes.set(id,{textContent:'',innerHTML:'',disabled:true,addEventListener(){}});return nodes.get(id)}
let calls=[];let answer={protocol:'OLD'};
const context=vm.createContext({document:{getElementById:get,querySelectorAll:q=>q==='[data-v081]'?buttons:[]},location:{hash:''},token:()=>true,req:async(url,opts)=>{calls.push(JSON.parse(opts.body).command);return {ok:true,output:JSON.stringify(answer)}},confirm:()=>true,setInterval(){},Promise,API:'',console});
vm.runInContext(js.replace('})();','globalThis.test={refresh,action,render};})();'),context);
(async()=>{
 await context.test.action('train');assert.equal(calls.length,0,'Unsupported backend never receives start');
 await context.test.refresh();assert.ok(buttons.every(b=>b.disabled));
 answer={protocol:'V081_WEB_1',stages:{},runs:{},initialPopulation:100,maxPopulation:1000};
 await context.test.refresh();assert.ok(buttons.find(b=>b.dataset.v081==='smoke').disabled===false);
 assert.ok(buttons.find(b=>b.dataset.v081==='train').disabled);
 context.test.render({protocol:'V081_WEB_1',stages:{smoke:{phase:'completed'},calibrate:{phase:'completed'}},capacity:{recommendation:{recommendedPopulation:225}},runs:{},generations:[]});
 assert.match(get('v081WebStats').innerHTML,/225/);assert.equal(buttons.find(b=>b.dataset.v081==='train').disabled,false);
 console.log('PASS: V081 protocol gate, disabled unsupported actions, measured capacity and legacy V08 retained');
})().catch(e=>{console.error(e);process.exit(1)});
