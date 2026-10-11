// Static/offline smoke for authenticated one-click Autonomous AI admin controls.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'admin.html'),'utf8');
const server=fs.readFileSync(path.join(root,'server.py'),'utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
assert.equal(ids.length,new Set(ids).size,'duplicate admin DOM IDs');
for(const id of ['ai','aiGate','aiDryRunBtn','aiRunBtn','aiRefreshBtn','aiState','aiIteration',
 'aiCandidate','aiOos','aiHypothesis','aiEnvironment','aiTraining','aiHoldout','aiValidation',
 'aiSelected','aiMetrics','aiDecision','aiRecent','aiProcessPid','aiProcessState','aiProcessRam',
 'aiProcessUptime','aiStartedAt','aiUpdatedAt','aiProcessMode','aiServerTime','aiPipeline',
 'aiCheckpoints','aiLog','aiLogMeta','aiStageLog','aiStageLogMeta','aiLogRefreshBtn',
 'aiBaselineRebuildBtn','aiBaselineRebuildState','aiBaselinePhase','aiBaselineProgress',
 'aiBaselinePid','aiBaselineCampaign','aiBaselineLog','aiBaselineLogMeta',
 'aiResourceGuard','aiGuardCpu','aiGuardRam','aiGuardSwap','aiGuardDisk',
 'apiFeatureSummary','apiFeatureRefresh','apiFeatureCards']) assert.ok(ids.includes(id),'missing '+id);
assert.match(html,/data-tab="ai">AI 연구</);
assert.match(html,/req\("\/api\/admin\/ai\/status"\)/);
assert.match(html,/req\("\/api\/admin\/ai\/log\?lines=180"\)/);
assert.match(html,/req\("\/api\/admin\/ai\/start"/);
assert.match(html,/req\("\/api\/admin\/ai\/rebuild-baseline"/);
assert.match(html,/V0\.7 기준선 재생성/);
assert.match(html,/mode==="run"/);
assert.match(html,/dryRunReady/);
assert.match(html,/Dry Run 완료 필요/);
assert.match(server,/DRY_RUN_REVIEW_REQUIRED/);
assert.match(server,/reviewedDryRun/);
assert.match(html,/PROVISIONAL_UNREVIEWED_DATA/);
assert.match(html,/Final OOS/);
assert.ok(!html.includes('RESEARCH_EXPORT_TOKEN'),'browser must never receive internal research token');
assert.match(server,/if path=="\/api\/admin\/ai\/status":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/if path=="\/api\/admin\/ai\/log":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/def _safe_log_tail\(/);
assert.match(server,/AUTONOMOUS_WEB_LOG\.open\("wb"/);
assert.match(server,/\bimport\s+[^\n]*\buuid\b/,'uuid import required for per-launch log archival');
assert.match(server,/web-launch-/);
assert.match(server,/from research_export import sanitize/);
assert.match(server,/def _autonomous_pipeline\(/);
assert.match(server,/def _autonomous_checkpoints\(/);
assert.match(server,/rssMb/);
assert.match(server,/uptimeSeconds/);
assert.match(server,/resourceGuard/);
assert.match(html,/Resource Guard/);
const registry=fs.readFileSync(path.join(root,'research','autonomous','experiment_registry.py'),'utf8');
const exportClient=fs.readFileSync(path.join(root,'research','autonomous','export_client.py'),'utf8');
assert.match(registry,/autonomous_state/);
assert.match(exportClient,/export_job/);
assert.match(exportClient,/export_download/);
assert.match(server,/if path=="\/api\/admin\/ai\/start":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/if path=="\/api\/admin\/ai\/rebuild-baseline":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/if path=="\/api\/admin\/api-features":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/API_FEATURE_DEFS/);
assert.match(server,/disabled_api_feature\(path,"GET"\)/);
assert.match(server,/disabled_api_feature\(path,"POST"\)/);
assert.match(server,/LIVE_TRADER\.disarm\(\)/);
assert.match(server,/API_DISABLED/);
assert.match(html,/data-tab="api"/);
assert.match(html,/\/api\/admin\/api-features/);
assert.match(html,/API 설정/);
assert.match(server,/run_v07_campaign\.py/);
assert.match(server,/--symbols","TQQQ,AAPL"/);
assert.match(server,/--population","20"/);
assert.match(server,/--max-population","300"/);
assert.match(server,/finalOosGate":"CLOSED"/);
assert.match(server,/V07_BASELINE_REBUILD_ALREADY_RUNNING/);
assert.match(server,/RESEARCH_EXPORT_BASE_URL":"http:\/\/127\.0\.0\.1:8080"/);
assert.match(server,/RESEARCH_EXPORT_TOKEN":token/);
assert.match(server,/finalOosGate":"CLOSED"/);
assert.match(server,/--max-iterations","1"/);
assert.match(html,/aiResearchStorage/);
assert.match(html,/aiApiDependencies/);
assert.match(server,/research_storage\(AUTONOMOUS_ROOT,ROOT\)/);
assert.match(server,/DEPENDENCY_DISABLED/);
assert.match(server,/"failureFacts","lastSuccessfulState"/);
console.log('PASS: authenticated AI tab, detailed process/pipeline/checkpoint status, sanitized logs, bounded research, Final OOS closed');
