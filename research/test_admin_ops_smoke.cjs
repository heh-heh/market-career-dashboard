// Offline/static smoke for collector controls and safe deploy/merge admin surface.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'admin.html'),'utf8');
const server=fs.readFileSync(path.join(root,'server.py'),'utf8');
const index=fs.readFileSync(path.join(root,'index.html'),'utf8');

const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
assert.equal(ids.length,new Set(ids).size,'duplicate admin DOM IDs');
for(const id of [
  'collectors','collectionAutoState','collectionResume','collectionPause','collectionPage','collectorRefreshBtn',
  'gitMergeRef','gitMergeRefBtn','gitOut'
]) assert.ok(ids.includes(id),'missing '+id);

assert.match(html,/data-tab="collectors" data-parent="data"[^>]*>수집 관리/);
assert.match(html,/\/api\/admin\/collectors/);
assert.match(html,/\/api\/research\/collection50\/control/);
assert.ok(!html.includes('data-collector="rawTick"'),'old individual Tick controls must be hidden');
assert.ok(!html.includes('data-collector="oneMinute"'),'old individual candle controls must be hidden');
assert.ok(!html.includes('data-cmd="systemctl start collect-toss-ticks.service"'),'no direct legacy Tick start');
assert.ok(!html.includes('data-cmd="systemctl start collect-expanded-universe.service"'),'no direct legacy candle start');
assert.match(html,/data-git="deploy-status"/);
assert.match(html,/data-git="deploy-apply"/);
assert.match(html,/data-git="merge-abort"/);
assert.match(html,/merge-ref/);

assert.match(server,/if path=="\/api\/admin\/collectors":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/if path=="\/api\/admin\/collectors\/control":[\s\S]{0,220}if not authorized\(self\)/);
assert.match(server,/ADMIN_COLLECTORS=/);
assert.match(server,/collect-expanded-universe\.service/);
assert.match(server,/collect-toss-ticks\.service/);
assert.match(server,/def merge_git_ref\(ref\):/);
assert.match(server,/\["merge","--no-ff","--no-edit",sha\]/);
assert.match(server,/def deploy_apply\(\):/);
assert.match(server,/AUTONOMOUS_RESEARCH_RUNNING/);
assert.match(server,/strategy-engine-v3/);
assert.ok(!server.includes('["reset","--hard"]'),'hard reset must not be exposed');
assert.ok(!server.includes('["clean","-fd"]'),'git clean must not be exposed');
assert.ok(!server.includes('["switch","-C","strategy-engine-v3"'),'sync must not rewrite local branch history');

assert.ok(!index.includes('data-view="v9"'),'research-only content is removed from main navigation');
assert.match(index,/data-view="v4">동향/);
assert.match(html,/id="console" class="panel"/);
assert.match(html,/class="console-layout"/);
assert.ok(html.indexOf('id="consoleOut"')<html.indexOf('id="gitOut"'),'server left, Git right');
assert.match(index,/data-view="v7">관리자/);
assert.match(index,/자동 수집 현황은 새 페이지로 통합/);
assert.ok(!index.includes('id="tickCollectorPhase"'),'duplicate legacy Tick panel removed');
assert.ok(!index.includes('id="btUniverseCards"'),'duplicate expanded universe panel removed');
assert.match(index,/새 자동 수집 현황 보기/);
assert.ok(!index.includes('let adminConsoleHistory=[];'),'dead main admin console JS should be removed');

console.log('PASS: grouped admin navigation, combined console, central collector controls, safe deploy, read-only main');
