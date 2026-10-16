const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'..','admin.html'),'utf8');
const backend=fs.readFileSync(path.join(__dirname,'..','server.py'),'utf8');
const worker=fs.readFileSync(path.join(__dirname,'..','v07_web_jobs.py'),'utf8');
for(const stage of ['prepare','audit','select','smoke']){
 assert.match(html,new RegExp('data-v07="'+stage+'"'));
}
for(const id of ['v07WebRefresh','v07WebStatus','v07WebLog'])assert.match(html,new RegExp('id="'+id+'"'));
assert.match(html,/function refreshV07Web\(\)/);
assert.match(html,/req\("\/api\/admin\/console"/);
assert.match(html,/if\(name==='console'\)\{refreshGit\(\);refreshV07Web\(\);\}/);
assert.match(backend,/if parts and parts\[0\]=="v07":/);
assert.match(worker,/start_new_session=True/);
assert.match(worker,/stdin=subprocess\.DEVNULL/);
assert.match(worker,/RESEARCH_EBS_MOUNT_REQUIRED/);
assert.match(worker,/2022-01-01/);
assert.match(worker,/2025-12-31/);
assert.ok(!worker.includes('shell=True'), 'never provide arbitrary shell execution');
console.log('PASS: admin web shell has bounded detached V0.7 stages, state/log polling and auth reuse');
