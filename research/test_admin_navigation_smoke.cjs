// Offline tests for grouped dashboard/admin navigation and the two-console layout.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'..'),index=fs.readFileSync(path.join(root,'index.html'),'utf8');
const admin=fs.readFileSync(path.join(root,'admin.html'),'utf8');
const indexNav=index.match(/<nav aria-label="메인 메뉴">([\s\S]*?)<\/nav>/);
assert.ok(indexNav,'public nav exists');
const items=[...indexNav[1].matchAll(/data-view="([^"]+)"[^>]*>([^<]+)</g)].map(x=>[x[1],x[2]]);
assert.deepEqual(items,[['v0','종합'],['v1','주식'],['v2','취업 시장'],
                       ['v5','채용공고'],['v4','동향'],['v7','관리자']]);
assert.ok(!indexNav[1].includes('v9'),'internal research removed from public navigation');
const groups=[...admin.matchAll(/data-group="(stocks|ai|manage|data)"[^>]*>([^<]+)</g)].map(x=>x[2]);
assert.deepEqual(groups,['주식','AI','관리','데이터']);
const children=[...admin.matchAll(/data-tab="([^"]+)" data-parent="([^"]+)"[^>]*>([^<]+)</g)]
  .map(x=>[x[2],x[1],x[3]]);
assert.deepEqual(children,[['stocks','trade','거래'],['stocks','paper','모의매매'],
 ['stocks','backtest','백테스트'],['ai','ai','연구'],
 ['manage','console','콘솔'],['manage','api','API 관리'],['manage','settings','설정'],
 ['data','downloads','다운로드 센터'],['data','collectors','수집 관리']]);
assert.match(admin,/class="console-layout"/);
const consoleStart=admin.indexOf('<section id="console"'),consoleEnd=admin.indexOf('</section>',consoleStart);
const area=admin.slice(consoleStart,consoleEnd);
assert.ok(area.includes('aria-label="서버 콘솔"')&&area.includes('aria-label="Git 콘솔"'));
assert.ok(area.indexOf('id="consoleOut"')<area.indexOf('id="gitOut"'));
assert.ok(area.includes('id="consoleIn"')&&area.includes('id="gitMergeRef"'));
assert.ok(!admin.includes('id="server" class="panel"')&&!admin.includes('id="git" class="panel"'));
for(const id of ['credentialsForm','credentialsSave','tossAppKey','tossAppSecret','genericApiKey',
                 'researchExportCenter','collectionDownloadBtn','collectionResume','collectionPause'])
 assert.ok(admin.includes('id="'+id+'"'),'missing '+id);
assert.ok(admin.includes('src="research-export.js"')&&admin.includes('href="research-export.css"'));
assert.ok(admin.includes('id="openResearchMonitor"')&&admin.includes("location.href='./#v9'"));
assert.ok(index.includes("openReadOnlyResearchFromLink"),"legacy research monitor accessible only by direct link");
assert.ok(admin.includes('2022')&&admin.includes('2025'),'historic collection download scope present');
for(const html of [index,admin])for(const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))
 new vm.Script(m[1]);
const start=admin.indexOf('const adminGroups='),end=admin.indexOf('let apiFeatureState=',start);
assert.ok(start>=0&&end>start);
const btn=(dataset)=>({dataset,hidden:false,active:false,attributes:{},
 classList:{toggle(name,value){assert.equal(name,'active');this.owner.active=value;}},
 setAttribute(name,val){this.attributes[name]=val}});
function makeBtn(dataset){const out=btn(dataset);out.classList.owner=out;return out}
const parent=['stocks','ai','manage','data'].map(g=>makeBtn({group:g}));
const subs=children.map(([g,id])=>makeBtn({parent:g,tab:id}));
const panels=children.map(([,id])=>({id,active:false,classList:{toggle(_,value){this.owner.active=value}}}));
for(const p of panels)p.classList.owner=p;
const calls=[];
const ctx=vm.createContext({location:{hash:'#trade'},history:{replaceState(...args){calls.push(args)}},
 document:{querySelector(sel){const m=sel.match(/^\[data-tab="([^"]+)"\]$/);return subs.find(x=>x.dataset.tab===m?.[1])||null},
 querySelectorAll(sel){if(sel==='[data-group]')return parent;
  if(sel==='#adminSubnav [data-tab]')return subs;
  if(sel==='.panel')return panels;return []}},
 refreshTrade(){},refreshPaper(){},refreshSimpleBacktest(){},refreshAI(){},refreshGit(){},
 refreshApiFeatures(){},refreshCredentials(){},refreshCollectionSymbols(){},refreshCollectors(){}});
new vm.Script(admin.slice(start,end)).runInContext(ctx);
for(const target of ['console','downloads','collectors','settings','paper','ai']){
 vm.runInContext('navigateAdmin("'+target+'")',ctx);
 const group=children.find(x=>x[1]===target)[0];
 assert.equal(parent.find(x=>x.dataset.group===group).active,true);
 assert.equal(panels.find(x=>x.id===target).active,true);
 assert.equal(subs.find(x=>x.dataset.tab===target).hidden,false);
 assert.ok(subs.filter(x=>x.dataset.parent!==group).every(x=>x.hidden));
 assert.ok(panels.filter(x=>x.id!==target).every(x=>!x.active));
}
console.log('PASS: public six-section menu, nested admin navigation, 2-column consoles, settings/data hub and JS syntax');
