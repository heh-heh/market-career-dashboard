// V081 is primary; V08 remains an independently usable legacy workspace.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const path=require('node:path');
const root=path.join(__dirname,'..');
const html=fs.readFileSync(root+'/admin.html','utf8');
const js=fs.readFileSync(root+'/v08-web.js','utf8');
assert.match(html,/class="section-title">V0\.8\.1 Adaptive Evolution AI 연구/);
assert.match(html,/id="v08WebPanel"/);
assert.match(html,/#v07ManagedPanel,#v07WebPanel\{display:none!important\}/);
assert.match(html,/<script src="v08-web\.js"><\/script>/);
for(const id of ['v08WebStats','v08WebStages','v08WebFeedback','v08ReturnChart','v08MddChart','v08FitnessChart','v08WebLog','v08WebLineage','v08WebRaw','v08WebDownload'])
 assert.ok(html.includes('id="'+id+'"'),'Missing '+id);
for(const cmd of ['smoke','train','holdout','validation','export'])assert.match(html,new RegExp('data-v08="'+cmd+'"'));
assert.match(js,/\/api\/admin\/console/);
assert.match(js,/\/api\/research\/v08\/download/);
assert.match(js,/2026 OOS CLOSED/);
assert.doesNotMatch(js,/eval\(|new Function\(/);
new vm.Script(js);
assert.ok(!/data-v07="train"/.test(html.slice(html.indexOf('id="v08WebPanel"'),html.indexOf('<!-- V07_MANAGED_CARD_BEGIN -->'))));
console.log('PASS: V081 primary and retained V0.8 workspace, web stage actions, safe downloads and JS syntax');
