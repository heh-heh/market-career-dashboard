// Offline monitor rendering; no service, collector, or backtest launch.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
for(const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);
const start=html.indexOf('async function loadV4ResearchMonitor()'),end=html.indexOf('let btTimer=',start);
const nodes=new Map();for(const m of html.matchAll(/id="(v4[^\"]+)"/g))nodes.set(m[1],{innerHTML:'',textContent:'',style:{}});
let requests=[],response={ok:true,audit:{phase:'completed',resultReady:true,resultStatus:'PASS_MECHANICAL_AUDIT_ONLY'},
 activeStage:'funnel',funnel:{stage:'funnel',available:true,running:true,phase:'running',progress:40,currentJob:'baseline-ir3-2bps',completedJobs:2,totalJobs:5,
 current:{phase:'running',researchVariant:'baseline',topRejectionReason:{CLOCK_DAILY_SESSION_INCOMPLETE:20}},log:'Read-only analysis'},
 full:{stage:'full',available:true,phase:'completed',progress:100,completedJobs:5,totalJobs:5},
 roadmap:[{label:'Funnel diagnostics',phase:'running',detail:'Artifact analysis'},{label:'Candidate validation',phase:'blocked',detail:'No candidate selected'}]};
const ctx=vm.createContext({AWS_API_BASE:'https://offline.invalid',esc:x=>String(x).replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c])),formatKSTDateTime:x=>x,
 document:{getElementById:id=>nodes.get(id)},async fetch(url){requests.push(url);return{ok:true,json:async()=>response}}});
new vm.Script(html.slice(start,end)).runInContext(ctx);
(async()=>{
 await vm.runInContext('loadV4ResearchMonitor()',ctx);
 assert.match(nodes.get('v4ResearchCurrentJob').innerHTML,/Funnel diagnostics/);
 assert.match(nodes.get('v4ResearchCurrentJob').innerHTML,/CLOCK_DAILY_SESSION_INCOMPLETE/);
 assert.match(nodes.get('v4ResearchRoadmap').innerHTML,/Candidate validation/);
 assert.equal(nodes.get('v4ResearchJobs').textContent,'2 / 5');
 assert.equal(nodes.get('v4ResearchProgressBar').style.width,'40%');
 // Older backend payload remains usable; existing full job is still shown.
 response={ok:true,full:{stage:'full',available:true,phase:'completed',progress:100,completedJobs:5,totalJobs:5}};
 await vm.runInContext('loadV4ResearchMonitor()',ctx);
 assert.match(nodes.get('v4ResearchCurrentJob').innerHTML,/Full.*완료/);
 assert.ok(requests.every(x=>x.includes('/api/research/v4/status?')));
 assert.equal(requests.length,2);
 console.log('PASS: frequency monitor, blocked candidate, existing full monitor, readonly endpoint, JavaScript syntax');
})().catch(e=>{console.error(e);process.exitCode=1});
