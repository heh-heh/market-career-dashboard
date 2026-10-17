const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.join(__dirname,'..'),html=fs.readFileSync(root+'/admin.html','utf8');
const js=fs.readFileSync(root+'/past-research.js','utf8');
assert.match(html,/data-tab="ai"[^>]*>연구<\/button>\s*<button data-tab="bots"[^>]*>봇<\/button>\s*<button data-tab="past"[^>]*>과거<\/button>/);
assert.match(html,/<section id="past" class="panel"/);
assert.match(html,/<div id="legacyResearchRuntime" hidden inert aria-hidden="true">/);
assert.ok(html.indexOf('<div id="legacyResearchRuntime"')>html.indexOf('<section id="past"'));
assert.ok(html.indexOf('id="v082WebPanel"')>html.indexOf('id="legacyResearchRuntime"'));
assert.ok(html.indexOf('id="v083WebPanel"')<html.indexOf('id="legacyResearchRuntime"'));
assert.match(html,/src="past-research.js"/);assert.match(html,/href="past-research.css"/);
assert.doesNotMatch(js,/\b(?:v082|v081|v08|v07)\s+(?:smoke|train|retry|holdout|validation|export)\b/);
new vm.Script(js);
const nodes=new Map();
function node(id){
 if(!nodes.has(id))nodes.set(id,{id,value:id==='pastResearchVersion'?'v082':'',
 textContent:'',innerHTML:'',disabled:false,hidden:false,handlers:{},
 addEventListener(name,fn){this.handlers[name]=fn},
 replaceChildren(){this.innerHTML=''},
 });
 return nodes.get(id);
}
const requests=[];
const shape=(versions,data)=>({ok:true,output:JSON.stringify(data)});
const responses={
 v082:shape('v082',{jobId:'historic-v082',finalOosGate:'CLOSED',stages:{train:{phase:'failed'}},runs:{train:{population:50}},generations:[
 {generation:1,meanReturn:-.1,parents:10,population:50,dates:['2023-03-03']},
 {generation:2,meanReturn:.03,parents:2,population:50,dates:['2023-03-10']},
 {generation:3,meanReturn:null,parents:null,population:50,dates:['2023-03-17']}
 ]}),
 v081:shape('v081',{jobId:'historic-v081',finalOosGate:'CLOSED',stages:{train:{phase:'completed'}},runs:{train:{population:100}},generations:[{generation:1,meanReturn:.015,parents:3,population:100}]}),
 v08:shape('v08',{jobId:'historic-v08',finalOosGate:'CLOSED',stages:{train:{phase:'completed'}},runs:{},generations:[{generation:1,meanReturn:.08,parents:15}]}),
 v07:shape('v07',{jobId:'historic-v07',finalOosGate:'CLOSED',stages:{train:{phase:'completed'}},runs:{train:{population:40}},generations:[
  {generation:1,returns:{mean:-.02},selection:{parentPoolSize:4}}
 ]})
};
const context=vm.createContext({
 document:{getElementById:node},window:{},location:{hash:''},token:()=>true,
 req:async(_,opts)=>{const command=JSON.parse(opts.body).command;requests.push(command);assert.match(command,/^v0(?:7|8|81|82) report$/);return responses[command.split(' ')[0]];},
 console
});
vm.runInContext(js,context);
(async()=>{
 await context.window.refreshPastResearch();
 assert.deepEqual(requests,['v082 report']);
 assert.match(node('pastResearchStatus').textContent,/실패 기록/);
 assert.match(node('pastReturnChart').innerHTML,/-10\.0%/);
 assert.match(node('pastReturnChart').innerHTML,/3\.0%/);
 assert.match(node('pastSurvivalChart').innerHTML,/20\.00%/);
 assert.match(node('pastSurvivalChart').innerHTML,/4\.00%/);
 assert.match(node('pastSurvivalLegend').textContent,/표시 2\/3/);
 assert.ok(!node('pastReturnChart').innerHTML.includes('NaN'));
 node('pastResearchVersion').value='v08';
 await context.window.refreshPastResearch();
 assert.match(node('pastReturnLegend').textContent,/8\.00%/);
 assert.match(node('pastSurvivalLegend').textContent,/검증된 값이 없습니다/);
 node('pastResearchVersion').value='v07';
 await context.window.refreshPastResearch();
 assert.match(node('pastReturnChart').innerHTML,/-2\.0%/);
 assert.match(node('pastSurvivalChart').innerHTML,/10\.00%/);
 node('pastResearchVersion').value='v081';
 await context.window.refreshPastResearch();
 assert.match(node('pastSurvivalChart').innerHTML,/3\.0%/);
 assert.equal(requests.length,4);
 // A tab return should reuse stored results; explicit refresh re-reads from API.
 node('pastResearchVersion').value='v082';
 await context.window.refreshPastResearch();assert.equal(requests.length,4);
 await node('pastResearchRefresh').handlers.click();assert.equal(requests.length,5);
 assert.deepEqual([...new Set(requests)].sort(),['v07 report','v08 report','v081 report','v082 report'].sort());
 console.log('PASS: Past tab, V07-V082 readonly archive, actual return and selected-parent survival charts, no fabricated denominator');
})().catch(e=>{console.error(e);process.exit(1)});
