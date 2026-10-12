const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('collection.html','utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
assert.equal(ids.length,new Set(ids).size,'unique ids');
for(const id of ['start','pause','login','logout','password'])assert.ok(!ids.includes(id),'no direct controls: '+id);
assert.ok(html.includes('admin.html#collectors'),'admin page link is available');
assert.ok(ids.includes('logEvents')&&ids.includes('logSummary'),'log panel is visible');
const nodes=Object.fromEntries(ids.map(id=>[id,{textContent:'',innerHTML:'',onclick:null,scrollTop:0,clientHeight:100,scrollHeight:100,style:{},attributes:{},setAttribute(name,v){this.attributes[name]=v}}]));
const requests=[];
const rows=[{symbol:'<bad>',stored:5,historyCursor:null,coveragePct:100},
 {symbol:'AAPL',stored:30,historyCursor:'2000-01-01T00:00:00Z',coveragePct:0},
 ...Array.from({length:48},(_,i)=>({symbol:'S'+String(i).padStart(2,'0'),stored:0,historyCursor:null}))];
const context=vm.createContext({document:{getElementById:id=>nodes[id]},location:{pathname:'/api/research/collection50/page'},setInterval:()=>{},
 fetch:async(url,opt)=>{
  requests.push({url,opt});
  return {ok:true,status:200,json:async()=>url.endsWith('/logs')
   ?{ok:true,sanitized:true,events:[{at:'2026-10-12T04:00:00+00:00',kind:'MODE_CHANGED',mode:'MARKET_CLOSED'},
       {at:'2026-10-12T04:00:10+00:00',kind:'CANDLE_PAGE',symbol:'AAPL',page:4,added:390,stored:1200,code:'<bad>'}]}
   :{ok:true,collectionFrom:'2000-01-01',collectionTo:'2026-10-31',currentSymbol:'NVDA',scheduler:{mode:'PAUSED',automatic:true,stale:false,collector:null},symbols:rows}
  };
 }});
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]).runInContext(context);
(async()=>{
 await new Promise(setImmediate);
 assert.equal(nodes.mode.textContent,'PAUSED');
 assert.match(nodes.summary.textContent,/2000-01-01.*2026-10-31/);
 assert.ok(nodes.symbols.innerHTML.includes('&lt;bad&gt;'),'symbol html escaped');
 assert.ok(nodes.symbols.innerHTML.includes('AAPL'),'symbol names rendered');
 assert.ok(nodes.symbols.innerHTML.includes('role="progressbar"'),'per-symbol progress bars');
 assert.ok(nodes.symbols.innerHTML.includes('100.0%'),'cursor at start means timeline traversed, not coverage');
 assert.ok(nodes.symbols.innerHTML.includes('0.0%'),'no cursor remains zero despite false coveragePct');
 assert.equal(nodes.overallPct.textContent,'2.0%','100% on 1 of 50 symbols gives 2% overall');
 assert.equal(nodes.overallBar.style.width,'2%');
 assert.equal(nodes.overallTrack.attributes['aria-valuenow'],'2');
 assert.equal(vm.runInContext("traversalPct({historyCursor:null},0,100)",context),0);
 assert.equal(vm.runInContext("traversalPct({historyCursor:new Date(50).toISOString()},0,100)",context),50);
 assert.equal(vm.runInContext("traversalPct({historyCursor:new Date(200).toISOString()},0,100)",context),0);
 assert.equal(vm.runInContext("traversalPct({historyCursor:new Date(-10).toISOString()},0,100)",context),100);
 assert.ok(html.includes('class="overview"'),'responsive two-column top layout exists');
 assert.ok(html.indexOf('상태 새로고침')<html.indexOf('자동 전환 상태'),'left refresh/right scheduler order');
 assert.ok(html.includes('grid-template-columns:1fr'),'mobile stacks top panels');
 assert.match(nodes.logEvents.textContent,/AAPL/);
 assert.match(nodes.logEvents.textContent,/신규 390봉/);
 assert.match(nodes.logEvents.textContent,/2026/);
 assert.equal(nodes.logEvents.innerHTML,'','activity events rendered only via textContent');
 await nodes.refresh.onclick();await new Promise(setImmediate);
 assert.ok(requests.length>=4);
 assert.ok(requests.every(x=>(x.url==='/api/research/collection50/status'||x.url==='/api/research/collection50/logs')
   &&x.opt.cache==='no-store'&&!x.opt.body),'GET public status and sanitized logs only');
 console.log('PASS: aggregate 50-symbol cursor progress, per-symbol progress, responsive overview, logs, no direct controls');
})().catch(e=>{console.error(e);process.exitCode=1});
