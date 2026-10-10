// Read-only public monitor, offline synthetic payload; no engine launch.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
for(const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);
const start=html.indexOf('async function loadEvolutionMonitor()'),end=html.indexOf('async function loadV4ResearchMonitor()',start);
const nodes=new Map(['evolutionV0Status','evolutionV0Metrics','evolutionV0Log'].map(id=>[id,{textContent:'',innerHTML:''}]));
let requests=[],response={ok:true,phase:'completed',split:'train',currentHistoricalDate:'2024-06-07',dataMode:'PROVISIONAL_UNREVIEWED_DATA',dataOrigin:'synthetic',generation:4,population:20,alive:5,dead:10,neutral:5,bestDailyReturn:.01,meanDailyReturn:-.002,medianDailyReturn:0,latestLog:'Smoke only'};
let httpOK=true;
const ctx=vm.createContext({AWS_API_BASE:'https://offline.invalid',esc:x=>String(x),document:{getElementById:id=>nodes.get(id)},
async fetch(url){requests.push(url);return {ok:httpOK,status:httpOK?200:404,json:async()=>response}}});
new vm.Script(html.slice(start,end)).runInContext(ctx);
(async()=>{
 await vm.runInContext('loadEvolutionMonitor()',ctx);
 assert.match(nodes.get('evolutionV0Status').textContent,/SYNTHETIC TEST/);
 assert.match(nodes.get('evolutionV0Status').textContent,/PROVISIONAL_UNREVIEWED_DATA/);
 assert.match(nodes.get('evolutionV0Metrics').innerHTML,/Mean Daily: -0.200%/);
 assert.match(nodes.get('evolutionV0Metrics').innerHTML,/Population: 20/);
 httpOK=false;await vm.runInContext('loadEvolutionMonitor()',ctx);
 assert.match(nodes.get('evolutionV0Status').textContent,/404/);
 assert.ok(requests.every(x=>x.includes('/api/research/evolution/status?')));
 assert.equal(requests.length,2);
 assert.match(html,/loadTickCollectorMonitor\(\);loadV4ResearchMonitor\(\);loadEvolutionMonitor\(\);loadBacktestMonitor\(\)/);
 console.log('PASS: Evolution readonly monitor, provisional/synthetic labels, metrics, stale backend, existing polling preserved');
})().catch(e=>{console.error(e);process.exitCode=1});
