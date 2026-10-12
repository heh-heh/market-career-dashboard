// Offline smoke for the read-only authenticated server status page and safe rendering.
const fs=require('node:fs'), assert=require('node:assert/strict'),vm=require('node:vm'),path=require('node:path');
const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'admin.html'),'utf8');
const server=fs.readFileSync(path.join(root,'server.py'),'utf8');
assert.match(html,/data-tab="status" data-parent="manage"[^>]*>서버 상태/);
assert.match(html,/<section id="status" class="panel">/);
assert.match(html,/id="healthRefresh"/);
assert.match(html,/id="healthDisks"/);
assert.match(html,/id="healthDevices"/);
assert.match(html,/id="healthServices"/);
assert.match(html,/data-cmd="df -h">전체 디스크/);
assert.match(html,/data-cmd="lsblk">블록 디스크/);
assert.match(server,/if path=="\/api\/admin\/server-health":\s*\n\s*if not authorized\(self\):/);
assert.match(server,/from server_health import snapshot/);
assert.match(server,/if parts==\["df","-h"\]:/);
assert.match(server,/if parts==\["lsblk"\]:/);
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
assert.equal(new Set(ids).size,ids.length);
const nodes=Object.fromEntries(ids.map(id=>[id,{innerHTML:'',textContent:'',disabled:false,onclick:null}]));
const calls=[];
const fixture={ok:true,sampledAt:'2026-10-12T09:00:00Z',cpu:{usedPct:75.5,cores:2,loadAverage:[1.2,0.6,0.3]},
 memory:{usedPct:62,totalBytes:10000000000,usedBytes:6200000000,availableBytes:3800000000,
 swapTotalBytes:1000000000,swapUsedBytes:80000000,swapUsedPct:8},
 uptimeSeconds:100000,mounts:[
 {mount:'/',device:'/dev/nvme0n1p1',totalBytes:100000000000,usedBytes:50000000000,availableBytes:50000000000,usedPct:50},
 {mount:'/mnt/research',device:'/dev/nvme1n1',totalBytes:500000000000,usedBytes:100000000000,availableBytes:400000000000,usedPct:20}],
 devices:[{name:'nvme1n1',type:'disk',filesystem:'',mount:'',sizeBytes:500000000000}],
 services:[{name:'market-career-dashboard.service',state:'active'},
           {name:'research-collection-scheduler.service',state:'inactive'}],
 network:{receivedBytes:15000,sentBytes:7000},warnings:[],unavailable:[]};
const start=html.indexOf('// Authenticated read-only server health monitor.');
const end=html.indexOf('// Settings: only explicitly entered secrets',start);
assert.ok(start>=0&&end>start,'health logic section');
const ctx=vm.createContext({
 console, document:{getElementById(id){assert.ok(nodes[id],id);return nodes[id]}},
 token(){return 'dummy'}, async req(url,options){calls.push([url,options]);return fixture},
 setText(id,v){nodes[id].textContent=v},esc(value){return String(value??'').replaceAll('<','&lt;').replaceAll('>','&gt;')},
 setInterval(){},currentAdminGroup:'manage',navigateAdmin(){},
});
new vm.Script(html.slice(start,end)).runInContext(ctx);
(async()=>{
 await nodes.healthRefresh.onclick();
 assert.equal(calls.length,1);
 assert.equal(calls[0][0],'/api/admin/server-health');
 assert.equal(calls[0][1],undefined,'read-only GET');
 assert.match(nodes.healthStats.innerHTML,/75\.5%/);
 assert.match(nodes.healthStats.innerHTML,/62\.0%/);
 assert.match(nodes.healthStats.innerHTML,/100000/);
 assert.match(nodes.healthDisks.innerHTML,/\/mnt\/research/);
 assert.match(nodes.healthDisks.innerHTML,/\/dev\/nvme1n1/);
 assert.match(nodes.healthDevices.innerHTML,/nvme1n1/);
 assert.match(nodes.healthServices.innerHTML,/대시보드 API/);
 assert.match(nodes.healthServices.innerHTML,/inactive/);
 assert.equal(nodes.healthRefresh.disabled,false);
 fixture.mounts[0].mount='<img src=x onerror=alert(1)>';
 await nodes.healthRefresh.onclick();
 assert.ok(!nodes.healthDisks.innerHTML.includes('<img'), 'untrusted mount escaped');
 assert.match(nodes.healthDisks.innerHTML,/&lt;img/);
 console.log('PASS: read-only admin server health, mounted/unmounted volumes and escaped UI');
})().catch(err=>{console.error(err);process.exitCode=1});
