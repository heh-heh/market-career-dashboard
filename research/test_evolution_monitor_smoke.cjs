// Read-only Evolution monitor UI smoke; no engine launch or broker actions.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
for(const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);
const start=html.indexOf('async function loadEvolutionMonitor()'),end=html.indexOf('async function loadV4ResearchMonitor()',start);
const ids=['evolutionV0Status','evolutionV0ProgressText','evolutionV0ProgressBar','evolutionV0Range','evolutionV0Generation','evolutionV0Sessions','evolutionV0Population','evolutionV0Survival','evolutionV0BestBot','evolutionV0BestDaily','evolutionV0MeanDaily','evolutionV0Lifetime','evolutionV0Trades','evolutionV0Parents','evolutionV0Mutations','evolutionV0Neutral','evolutionV0Log','evolutionV0RecentRuns'];
const nodes=new Map(ids.map(id=>[id,{textContent:'',innerHTML:'',style:{}}]));
let requests=[],response={ok:true,available:true,phase:'completed',smoke:true,split:'train',fromDate:'2024-06-03',toDate:'2024-06-07',currentHistoricalDate:'2024-06-07',dataMode:'PROVISIONAL_UNREVIEWED_DATA',dataOrigin:'toss',generation:4,population:20,alive:9,dead:11,neutral:0,invalid:0,bestBot:'g0004-b0018',bestDailyReturn:.0089,meanDailyReturn:-.00133,medianDailyReturn:-.0012,bestLifetimeEquity:100.89,tradesToday:151,parentPoolSize:5,mutationCount:106,completedSessions:5,requestedSessions:5,progressPct:100,latestLog:'Completed offline evaluation',recentRuns:[{phase:'completed',smoke:true,fromDate:'2024-06-03',toDate:'2024-06-07',generation:4,completedSessions:5,requestedSessions:5,progressPct:100,bestDailyReturn:.0089,meanDailyReturn:-.00133,bestLifetimeEquity:100.89,tradesToday:151}]};
let httpOK=true;
const ctx=vm.createContext({AWS_API_BASE:'https://offline.invalid',esc:x=>String(x),document:{getElementById:id=>nodes.get(id)},async fetch(url){requests.push(url);return {ok:httpOK,status:httpOK?200:404,json:async()=>response}}});
new vm.Script(html.slice(start,end)).runInContext(ctx);
(async()=>{
 await vm.runInContext('loadEvolutionMonitor()',ctx);
 assert.match(nodes.get('evolutionV0Status').innerHTML,/완료/);
 assert.equal(nodes.get('evolutionV0ProgressText').textContent,'100.0%');
 assert.equal(nodes.get('evolutionV0ProgressBar').style.width,'100%');
 assert.equal(nodes.get('evolutionV0Sessions').textContent,'5 / 5');
 assert.equal(nodes.get('evolutionV0Population').textContent,'20');
 assert.equal(nodes.get('evolutionV0Survival').textContent,'9 / 11');
 assert.equal(nodes.get('evolutionV0BestDaily').textContent,'0.890%');
 assert.equal(nodes.get('evolutionV0MeanDaily').textContent,'-0.133%');
 assert.match(nodes.get('evolutionV0RecentRuns').innerHTML,/151/);
 httpOK=false;await vm.runInContext('loadEvolutionMonitor()',ctx);
 assert.match(nodes.get('evolutionV0Status').innerHTML,/404/);
 assert.ok(requests.every(x=>x.includes('/api/research/evolution/status?')));
 assert.match(html,/loadTickCollectorMonitor\(\);loadV4ResearchMonitor\(\);loadEvolutionMonitor\(\);loadBacktestMonitor\(\)/);
 console.log('PASS: Evolution progress, results, recent runs and 3-second polling UI');
})().catch(e=>{console.error(e);process.exitCode=1});
