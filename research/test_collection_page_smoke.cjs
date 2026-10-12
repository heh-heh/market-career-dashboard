const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('collection.html','utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
assert.equal(ids.length,new Set(ids).size,'unique ids');
for(const id of ['start','pause','login','logout','password'])assert.ok(!ids.includes(id),'no direct controls: '+id);
assert.ok(html.includes('admin.html#collectors'),'admin page link is available');
assert.ok(ids.includes('logEvents')&&ids.includes('logSummary'),'log panel is visible');
const nodes=Object.fromEntries(ids.map(id=>[id,{textContent:'',innerHTML:'',onclick:null,scrollTop:0,clientHeight:100,scrollHeight:100}]));
const requests=[];
const context=vm.createContext({document:{getElementById:id=>nodes[id]},location:{pathname:'/api/research/collection50/page'},setInterval:()=>{},
 fetch:async(url,opt)=>{
  requests.push({url,opt});
  return {ok:true,status:200,json:async()=>url.endsWith('/logs')
   ?{ok:true,sanitized:true,events:[{at:'2026-10-12T04:00:00+00:00',kind:'MODE_CHANGED',mode:'MARKET_CLOSED'},
       {at:'2026-10-12T04:00:10+00:00',kind:'CANDLE_PAGE',symbol:'AAPL',page:4,added:390,stored:1200,code:'<bad>'}]}
   :{ok:true,collectionFrom:'2000-01-01',collectionTo:'2026-10-31',currentSymbol:'NVDA',scheduler:{mode:'PAUSED',automatic:true,stale:false,collector:null},symbols:[{symbol:'<bad>',stored:5,historyCursor:'2026-09-30'}]}
  };
 }});
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]).runInContext(context);
(async()=>{
 await new Promise(setImmediate);
 assert.equal(nodes.mode.textContent,'PAUSED');
 assert.match(nodes.summary.textContent,/2000-01-01.*2026-10-31/);
 assert.ok(nodes.symbols.innerHTML.includes('&lt;bad&gt;'),'symbol html escaped');
 assert.match(nodes.logEvents.textContent,/AAPL/);
 assert.match(nodes.logEvents.textContent,/신규 390봉/);
 assert.match(nodes.logEvents.textContent,/2026/);
 assert.equal(nodes.logEvents.innerHTML,'','activity events rendered only via textContent');
 await nodes.refresh.onclick();await new Promise(setImmediate);
 assert.ok(requests.length>=4);
 assert.ok(requests.every(x=>(x.url==='/api/research/collection50/status'||x.url==='/api/research/collection50/logs')
   &&x.opt.cache==='no-store'&&!x.opt.body),'GET public status and sanitized logs only');
 console.log('PASS: collection status/log rendering, KST event text, safe escaping, no direct controls');
})().catch(e=>{console.error(e);process.exitCode=1});
