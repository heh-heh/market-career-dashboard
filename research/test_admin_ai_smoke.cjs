// Static/offline smoke for authenticated one-click Autonomous AI admin controls.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'admin.html'),'utf8');
const server=fs.readFileSync(path.join(root,'server.py'),'utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
assert.equal(ids.length,new Set(ids).size,'duplicate admin DOM IDs');
for(const id of ['ai','aiGate','aiDryRunBtn','aiRunBtn','aiRefreshBtn','aiState','aiIteration',
 'aiCandidate','aiOos','aiHypothesis','aiEnvironment','aiTraining','aiHoldout','aiValidation',
 'aiSelected','aiMetrics','aiDecision','aiRecent']) assert.ok(ids.includes(id),'missing '+id);
assert.match(html,/data-tab="ai">AI 연구</);
assert.match(html,/req\("\/api\/admin\/ai\/status"\)/);
assert.match(html,/req\("\/api\/admin\/ai\/start"/);
assert.match(html,/mode==="run"/);
assert.match(html,/PROVISIONAL_UNREVIEWED_DATA/);
assert.match(html,/Final OOS/);
assert.ok(!html.includes('RESEARCH_EXPORT_TOKEN'),'browser must never receive internal research token');
assert.match(server,/if path=="\/api\/admin\/ai\/status":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/if path=="\/api\/admin\/ai\/start":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/RESEARCH_EXPORT_BASE_URL":"http:\/\/127\.0\.0\.1:8080"/);
assert.match(server,/RESEARCH_EXPORT_TOKEN":token/);
assert.match(server,/finalOosGate":"CLOSED"/);
assert.match(server,/--max-iterations","1"/);
console.log('PASS: authenticated AI tab, hidden internal token, bounded one-click dry-run/research, Final OOS closed');
