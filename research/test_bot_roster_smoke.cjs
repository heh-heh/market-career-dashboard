const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'..'),h=fs.readFileSync(root+'/admin.html','utf8');
const js=fs.readFileSync(root+'/bot-roster.js','utf8');
assert.match(h,/data-tab="ai" data-parent="ai"[^>]*>연구<\/button>\s*<button data-tab="bots" data-parent="ai"[^>]*>봇<\/button>/);
for(const id of ['botRosterPanel','botRosterRows','botRosterDetail','botRosterSearch','botRosterRefresh'])assert.ok(h.includes('id="'+id+'"'));
assert.match(h,/src="bot-roster.js"/);
assert.match(h,/href="bot-roster.css"/);
assert.ok(!js.includes('/api/trading/')&&!js.includes('/api/live/'),'view cannot request trading APIs');
new vm.Script(js);
const elements=new Map();
function get(id){
 if(!elements.has(id))elements.set(id,{id,value:'',innerHTML:'',textContent:'',disabled:false,
   addEventListener(type,fn){this['on'+type]=fn},querySelectorAll(){return []},
   classList:{contains(){return true}}});
 return elements.get(id);
}
const toggles=['current','evaluated'].map(x=>({dataset:{rosterView:x},attributes:{},
 addEventListener(_,fn){this.onclick=fn},setAttribute(k,v){this.attributes[k]=v}}));
let response={ok:true,output:JSON.stringify({protocol:'V082_BOTS_1',expectedBots:50,
 stage:'smoke',phase:'completed',completedGenerations:2,evaluatedDates:['2023-01-03','2023-01-09'],
 message:'historical only',currentBots:Array.from({length:50},(_,i)=>({id:'g0002-b'+String(i).padStart(4,'0'),
 generation:2,origin:'MUTATION',parents:['g0001-b0000'],inheritedLifetime:{pnlUsd:2},latestEvaluation:null})),
 evaluatedBots:Array.from({length:50},(_,i)=>({id:'g0001-b'+String(i).padStart(4,'0'),
 generation:1,survival:i===0?'SURVIVE':'DEAD',selectedParent:i===0,return:i===0?0.05:-0.02,
 trades:4,fitness:0.1}))})};
const context=vm.createContext({
 document:{getElementById:get,querySelectorAll:s=>s==='[data-roster-view]'?toggles:[]},
 window:{},location:{hash:''},token:()=>'logged-in',req:async()=>response,setInterval(){},console});
vm.runInContext(js,context);
(async()=>{
 await context.window.refreshBotRoster();
 assert.match(get('botRosterSummary').innerHTML,/50 \/ 50/);
 assert.match(get('botRosterRows').innerHTML,/g0002-b0000/);
 assert.ok(!get('botRosterRows').innerHTML.includes('2.00%'),'inherited pnl is not own performance');
 toggles[1].onclick();
 assert.match(get('botRosterRows').innerHTML,/g0001-b0000/);
 assert.match(get('botRosterRows').innerHTML,/5.00%/);
 assert.match(get('botRosterRows').innerHTML,/선발/);
 assert.match(get('botRosterStatus').textContent,/2026/);
 console.log('PASS: Bots submenu, 50 rows, history separated from offspring, static auth-only UI');
})().catch(e=>{console.error(e);process.exit(1)});
