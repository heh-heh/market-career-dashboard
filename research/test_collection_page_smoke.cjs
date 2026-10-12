const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('collection.html','utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
assert.equal(ids.length,new Set(ids).size,'unique ids');
for(const id of ['start','pause','login','logout','password'])assert.ok(!ids.includes(id),'no direct controls: '+id);
assert.ok(html.includes('admin.html#collectors'),'admin page link is available');
const nodes=Object.fromEntries(ids.map(id=>[id,{textContent:'',innerHTML:'',onclick:null}]));
const requests=[];
const context=vm.createContext({document:{getElementById:id=>nodes[id]},location:{pathname:'/api/research/collection50/page'},setInterval:()=>{},
 fetch:async(url,opt)=>{requests.push({url,opt});return{ok:true,status:200,json:async()=>({ok:true,collectionFrom:'2000-01-01',collectionTo:'2026-10-31',currentSymbol:'NVDA',scheduler:{mode:'PAUSED',automatic:true,stale:false,collector:null},symbols:[{symbol:'<bad>',stored:5,historyCursor:'2026-09-30'}]})}}});
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]).runInContext(context);
(async()=>{
 await new Promise(setImmediate);
 assert.equal(nodes.mode.textContent,'PAUSED');
 assert.match(nodes.summary.textContent,/2000-01-01.*2026-10-31/);
 assert.ok(nodes.symbols.innerHTML.includes('&lt;bad&gt;'),'XSS escaping');
 await nodes.refresh.onclick();
 assert.ok(requests.length>=2);
 assert.ok(requests.every(x=>x.url==='/api/research/collection50/status'&&x.opt.cache==='no-store'&&!x.opt.body),'GET status only');
 console.log('PASS: collection page read-only status, safe rendering and admin link');
})().catch(e=>{console.error(e);process.exitCode=1});
