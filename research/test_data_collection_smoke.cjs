// Static smoke: the public research tab has one automatic collection summary.
// Old standalone Tick/expanded-universe panels and collector controls are retired.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.join(__dirname,'..');
const index=fs.readFileSync(path.join(root,'index.html'),'utf8');
const collection=fs.readFileSync(path.join(root,'collection.html'),'utf8');
const admin=fs.readFileSync(path.join(root,'admin.html'),'utf8');
for(const html of [index,collection,admin])
 for(const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))
  new vm.Script(m[1]);
assert.ok(!index.includes('data-view="v9"'),'main nav must hide internal research tab');
assert.match(admin,/id="researchExportCenter"/);
assert.match(admin,/id="downloads" class="panel"/);
assert.match(index,/통합 자동 수집/);
assert.match(index,/id="collection50Summary"/);
assert.match(index,/id="collectionScheduler"/);
assert.match(index,/새 자동 수집 현황 보기/);
assert.ok(index.includes('/api/research/collection50/status'));
assert.ok(!index.includes('id="tickCollectorPhase"'),'old Tick panel removed');
assert.ok(!index.includes('id="btUniverseCards"'),'old expanded-universe panel removed');
assert.ok(!index.includes('id="btLog"'),'old log panel removed');
assert.ok(collection.includes('admin.html#collectors'),'collection links to admin controls');
assert.ok(!collection.includes('id="start"')&&!collection.includes('id="pause"'),'collection status is read-only');
assert.ok(admin.includes('id="collectionResume"')&&admin.includes('id="collectionPause"'),'central controls retained in admin');
assert.ok(!fs.existsSync(path.join(root,'tick-status.html')),'no stale separate Tick page');
console.log('PASS: consolidated read-only collection UI, admin controls retained, JS syntax');
