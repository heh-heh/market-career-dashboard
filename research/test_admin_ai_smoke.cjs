// Current AI tab focuses on V083; V07-V082 archival markup is hidden and inert.
// Server-side legacy APIs remain for authenticated read-only Past charts and audit compatibility.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'admin.html'),'utf8');
const server=fs.readFileSync(path.join(root,'server.py'),'utf8');
const ai=html.match(/<section id="ai" class="panel">([\s\S]*?)<\/section>/);
assert.ok(ai,'AI research panel exists');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
assert.equal(ids.length,new Set(ids).size,'no duplicate admin DOM IDs');
assert.match(html,/data-tab="ai" data-parent="ai"[^>]*>연구</);
assert.match(ai[1],/id="v083WebPanel"/);
assert.match(ai[1],/id="v083PopulationInput"[^>]*min="2" max="50"/);
const legacy=html.match(/<div id="legacyResearchRuntime" hidden inert aria-hidden="true">([\s\S]*?)<section id="api"/);
assert.ok(legacy,'legacy reports and controls are in the inert compatibility archive');
for(const id of ['v07ManagedPanel','v07ManagedSummary','v07ManagedRefresh',
                 'v07ManagedGraph','v07ManagedAnalysis','v07AuditDetail','v07AiLog',
                 'v07ManagedDownload','v07WebStatus','v07WebLog',
                 'apiFeatureSummary','apiFeatureRefresh','apiFeatureCards'])
  assert.ok(ids.includes(id),'missing '+id);
for(const command of ['analyze','train','holdout','validation','export'])
  assert.match(legacy[1],new RegExp('data-v07="'+command+'"'));
for(const stage of ['train','holdout','validation'])
  assert.match(legacy[1],new RegExp('data-v07-retry="'+stage+'"'));
assert.match(ai[1],/2026 OOS CLOSED/);
assert.match(legacy[1],/2022~2023/);
assert.match(html,/req\("\/api\/admin\/console"/);
assert.match(html,/command:"v07 report"/);
assert.match(html,/setText\("v07AiLog",results\[1\]\.output/);
assert.match(html,/fetch\(API\+"\/api\/research\/v07\/download"/);
assert.match(server,/if path=="\/api\/admin\/console":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/if path=="\/api\/admin\/ai\/status":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/if path=="\/api\/admin\/ai\/start":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/if path=="\/api\/admin\/ai\/rebuild-baseline":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/if path=="\/api\/admin\/api-features":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/DRY_RUN_REVIEW_REQUIRED/);
assert.match(server,/finalOosGate":"CLOSED"/);
assert.match(server,/disabled_api_feature\(path,"GET"\)/);
assert.match(server,/disabled_api_feature\(path,"POST"\)/);
assert.match(server,/LIVE_TRADER\.disarm\(\)/);
assert.match(html,/\/api\/admin\/api-features/);
const obsolete=['aiGate','aiDryRunBtn','aiRunBtn','aiBaselineRebuildBtn',
 'aiProcessRam','aiPipeline','aiCheckpoints','aiStageLog','aiBaselineLog',
 'aiBaselineSelectionSummary','aiRecent','openResearchMonitor'];
for(const id of obsolete)assert.ok(!ids.includes(id),'legacy AI UI still visible: '+id);
for(const text of ['function refreshAI()','function refreshAILog()',
 'startAIResearch(','startBaselineRebuild(','/api/admin/ai/log?lines=180'])
 assert.ok(!html.includes(text),'unused AI polling or actions should be removed: '+text);
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))
 new vm.Script(match[1]);
console.log('PASS: focused V083 AI tab with population selector and inert V07-V082 compatibility archive');
