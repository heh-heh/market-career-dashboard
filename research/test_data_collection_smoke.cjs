// Preserve the upstream combined collection tab, without external network calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.join(__dirname,'..'),html=fs.readFileSync(path.join(root,'index.html'),'utf8');
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
assert.match(html,/data-view="v9">데이터 수집/);
assert.ok(html.includes('Toss Raw Tick'));
assert.ok(html.includes('/api/backtest/monitor'));
assert.ok(!fs.existsSync(path.join(root,'tick-status.html')));
const a=html.indexOf('async function loadTickCollectorMonitor()'),b=html.indexOf('let btTimer=',a);
assert.ok(a>0&&b>a);
const nodes=new Map();
for(const m of html.matchAll(/id="(tick[^\"]+)"/g))nodes.set(m[1],{textContent:'',innerHTML:''});
let observed=[];
const ctx=vm.createContext({AWS_API_BASE:'https://offline.invalid',esc:x=>String(x),formatKSTDateTime:x=>x||'-',
 document:{getElementById:id=>nodes.get(id)},async fetch(url){observed.push(url);return{ok:true,json:async()=>({ok:true,state:'collecting',marketSession:'AFTER',sessionDate:'2026-10-09',calendarSource:'toss_market_calendar',symbols:['AAPL'],ticksBySymbol:{AAPL:2},ticksTotal:2,subscribed:30,symbolCount:30})}}});
new vm.Script(html.slice(a,b)).runInContext(ctx);
(async()=>{
 await vm.runInContext('loadTickCollectorMonitor()',ctx);
 assert.equal(observed.length,1);assert.ok(observed[0].includes('/api/ticks/status'));
 assert.match(nodes.get('tickMarketSession').textContent,/AFTER/);
 assert.match(nodes.get('tickBySymbol').innerHTML,/AAPL/);
 assert.equal(nodes.get('tickSubscribed').textContent,'30 / 30');
 console.log('PASS: combined data collection tab, tick status rendering, no standalone page, index JavaScript syntax');
})().catch(e=>{console.error(e);process.exitCode=1});
