// Offline admin integration smoke: no browser dependency and no HTTP requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'admin.html'), 'utf8');
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
assert.equal(new Set(ids).size, ids.length, 'duplicate admin DOM IDs');
const nodes = new Map(ids.map(id => [id, {textContent:'',innerHTML:'',value:'',disabled:false,
  classList:{add(){},remove(){},toggle(){},contains(){return false;}}}]));
const calls=[];
let v3Enabled=false, simpleEnabled=false;
const v3 = () => ({engine:'strategy-engine-v3',enabled:v3Enabled,equityUsd:10000,
  closedTrades:0,winRate:0,candidates:[],recentTrades:[]});
const simple = () => ({enabled:simpleEnabled,paperEquity:9900,totalPnl:-1,realizedPnl:0,
  unrealizedPnl:-1,completedTrades:0,winRate:0,lastScan:'scan-time',timestampWarning:'TIMESTAMP_KIND_UNCONFIRMED',
  openPosition:{symbol:'ABC',quantity:1,entryFill:100,highestPriceAfterEntry:101,trailingActivated:true,
    trailingStopPrice:100.4,stopPrice:98.8,dataStatus:'DATA_STALE',staleSince:'outage-time',staleObservationCount:2,
    entryLatencySeconds:60}, candidates:[],recentTrades:[]});
const context = vm.createContext({console,location:{},alert(e){throw Error(e)},setInterval(){},
  localStorage:{getItem(){return ''},removeItem(){}},
  document:{getElementById(id){assert.ok(nodes.has(id),`missing ID ${id}`);return nodes.get(id)},querySelectorAll(){return []}},
  async fetch(url,options){
    const endpoint=new URL(url).pathname;
    calls.push({endpoint,method:options.method||'GET'});
    const data=options.body?JSON.parse(options.body):{};
    if(endpoint==='/api/trading/paper/v3/auto')v3Enabled=data.enabled;
    if(endpoint==='/api/trading/paper/simple-v1/auto')simpleEnabled=data.enabled;
    assert.match(endpoint,/^\/api\/trading\/paper\/(v3|simple-v1)\/(status|auto|scan)$/);
    return {ok:true,async json(){return endpoint.includes('/v3/')?v3():simple()}};
  }});
const code=html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(code,{filename:'admin.html'}).runInContext(context);
(async()=>{
  await vm.runInContext('refreshPaper()',context);
  assert.ok(calls.some(c=>c.endpoint==='/api/trading/paper/v3/status'));
  assert.ok(calls.some(c=>c.endpoint==='/api/trading/paper/simple-v1/status'));
  assert.match(nodes.get('simplePosition').textContent,/DATA_STALE.*outage-time/);
  assert.match(nodes.get('simpleState').textContent,/TIMESTAMP_KIND_UNCONFIRMED/);
  await nodes.get('paperAutoBtn').onclick();
  assert.equal(v3Enabled,true);assert.equal(simpleEnabled,false);
  await nodes.get('simpleStart').onclick();
  assert.equal(v3Enabled,true);assert.equal(simpleEnabled,true);
  await nodes.get('simpleStop').onclick();
  assert.equal(v3Enabled,true);assert.equal(simpleEnabled,false);
  await nodes.get('paperScanBtn').onclick();
  await nodes.get('simpleScan').onclick();
  assert.ok(calls.some(c=>c.endpoint==='/api/trading/paper/v3/scan'&&c.method==='POST'));
  assert.ok(calls.some(c=>c.endpoint==='/api/trading/paper/simple-v1/scan'&&c.method==='POST'));
  console.log('PASS: independent paper controls/rendering, stale/timestamp warnings, no live endpoints');
})().catch(e=>{console.error(e);process.exitCode=1});
