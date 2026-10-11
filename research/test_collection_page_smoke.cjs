const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('collection.html','utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
assert.equal(ids.length,new Set(ids).size);
const nodes=Object.fromEntries(ids.map(id=>[id,{textContent:'',innerHTML:'',disabled:false,hidden:false,value:''}]));
const stored=new Map(),requests=[];
let stale=false;
const context=vm.createContext({document:{getElementById:id=>nodes[id]},location:{pathname:'/api/research/collection50/page'},
 sessionStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},setInterval:()=>{},
 fetch:async(url,opt)=>{requests.push({url,opt});return{ok:true,status:200,json:async()=>url==='/api/login'?{ok:true,token:'synthetic'}:url.endsWith('/control')?{ok:true,paused:JSON.parse(opt.body).paused}:{ok:true,collectionFrom:'2000-01-01',collectionTo:'2026-10-31',currentSymbol:'NVDA',scheduler:{mode:'PAUSED',automatic:true,stale,collector:null},symbols:[{symbol:'<bad>',stored:5,historyCursor:'2026-09-30'}]}}}});
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]).runInContext(context);
(async()=>{
 await new Promise(setImmediate);
 assert.equal(nodes.start.disabled,true,'no unauthenticated start');
 assert.match(nodes.summary.textContent,/2000-01-01.*2026-10-31/);
 assert.ok(nodes.symbols.innerHTML.includes('&lt;bad&gt;'),'escape symbols');
 nodes.password.value='synthetic password';await nodes.login.onclick();
 assert.equal(nodes.password.value,'');assert.equal(nodes.start.disabled,false);
 await nodes.start.onclick();
 const start=requests.find(x=>x.url.endsWith('/control'));
 assert.deepEqual(JSON.parse(start.opt.body),{paused:false});
 assert.equal(start.opt.headers.Authorization,'Bearer synthetic');
 stale=true;await nodes.refresh.onclick();assert.equal(nodes.start.disabled,true,'stale scheduler cannot start');
 assert.ok(requests.every(x=>/^\/api\/(login|logout|research\/collection50\/(status|control))$/.test(x.url)),'local control/status only, no market or live order endpoints');
 console.log('PASS: collection page login, start, stale gate, mobile fields, escaped rendering; local authenticated control only');
})().catch(e=>{console.error(e);process.exitCode=1});
