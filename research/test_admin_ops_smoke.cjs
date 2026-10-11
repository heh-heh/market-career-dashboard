// Offline/static smoke for collector controls and safe deploy/merge admin surface.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'admin.html'),'utf8');
const server=fs.readFileSync(path.join(root,'server.py'),'utf8');
const index=fs.readFileSync(path.join(root,'index.html'),'utf8');

const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
assert.equal(ids.length,new Set(ids).size,'duplicate admin DOM IDs');
for(const id of [
  'collectors','collectorRawTickState','collectorOneMinuteState','collectorRefreshBtn',
  'gitMergeRef','gitMergeRefBtn','gitOut'
]) assert.ok(ids.includes(id),'missing '+id);

assert.match(html,/data-tab="collectors">수집 관리/);
assert.match(html,/\/api\/admin\/collectors/);
assert.match(html,/\/api\/admin\/collectors\/control/);
assert.match(html,/data-action="start"/);
assert.match(html,/data-action="stop"/);
assert.match(html,/data-action="enable"/);
assert.match(html,/data-action="disable"/);
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
assert.match(server,/strategy-engine-v3/);
assert.ok(!server.includes('["reset","--hard"]'),'hard reset must not be exposed');
assert.ok(!server.includes('["clean","-fd"]'),'git clean must not be exposed');
assert.ok(!server.includes('["switch","-C","strategy-engine-v3"'),'sync must not rewrite local branch history');

assert.match(index,/data-view="v9">연구·데이터/);
assert.match(index,/data-view="v7">관리자/);
assert.match(index,/메인 화면은 조회·다운로드 중심/);
assert.ok(!index.includes('let adminConsoleHistory=[];'),'dead main admin console JS should be removed');

console.log('PASS: collector controls, safe merge/deploy, and streamlined read-only main navigation');
