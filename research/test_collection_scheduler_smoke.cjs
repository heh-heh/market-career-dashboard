const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const index=fs.readFileSync('index.html','utf8'),admin=fs.readFileSync('admin.html','utf8');
for(const html of [index,admin])for(const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);
const ids=['collection50Summary','collection50Symbols','collectionScheduler'];
const nodes=Object.fromEntries(ids.map(id=>[id,{textContent:'',innerHTML:''}]));
const a=index.indexOf('async function loadCollection50()'),b=index.indexOf('const btPhaseNames=',a);
let requests=[];
const context=vm.createContext({AWS_API_BASE:'https://offline.invalid',document:{getElementById:id=>nodes[id]},esc:x=>String(x),
 fetch:async url=>{requests.push(url);return{ok:true,json:async()=>({ok:true,currentSymbol:'AAPL',scheduler:{mode:'MARKET_CLOSED',collector:'backfill',automatic:true,etTime:'2024-07-26T18:00:00-04:00'},symbols:[{symbol:'AAPL',stored:390,verifiedTradingDays:1,missingTradingDays:6,http429Count:2,restRequests:10,downloadAvailable:true},{symbol:'TQQQ',downloadAvailable:false}]})}}});
new vm.Script(index.slice(a,b)).runInContext(context);
(async()=>{
 await vm.runInContext('loadCollection50()',context);
 assert.equal(requests.length,1);assert.match(requests[0],/collection50\/status/);
 assert.match(nodes.collectionScheduler.textContent,/MARKET_CLOSED/);
 assert.match(nodes.collection50Symbols.innerHTML,/누락 6일/);
 assert.match(nodes.collection50Symbols.innerHTML,/disabled/);
 assert.ok(index.includes('SHA256 불일치'));
 assert.ok(admin.includes('/api/research/collection50/control'));
 assert.ok(admin.includes('collectionPause'));
 console.log('PASS: scheduler + coverage rendering, one local status call, admin controls, index/admin syntax');
})().catch(e=>{console.error(e);process.exitCode=1});
