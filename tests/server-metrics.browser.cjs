/* Offline fixtures only: never contacts the home server or sends a control request. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const adminSource=fs.readFileSync(path.resolve(root,'deploy/admin-console/console.py'),'utf8');
const adminCsp=(adminSource.match(/"default-src 'none'; script-src 'self'; style-src [^"]+"/)||[])[0]?.slice(1,-1);
assert(adminCsp&&adminCsp.includes("style-src 'self' 'unsafe-inline'"),'admin CSP must allow its own task manager stylesheet');

const fixture={ok:true,mode:'PUBLIC_READ_ONLY_AGGREGATED',samplingSeconds:60,updatedAt:'2026-10-29T10:04:00Z',
  metrics:{cpuPct:60,memoryPct:52,memoryUsedGiB:4.16,memoryTotalGiB:8,diskPct:40,diskUsedGiB:40,diskTotalGiB:100,dataDiskPct:20,dataDiskUsedGiB:40,dataDiskTotalGiB:200,networkRxMbps:2,networkTxMbps:.5,cpuPackageWatts:null},
  history:[0,20,40,null,60].map((v,i)=>({at:`2026-10-29T10:0${i}:00Z`,cpuPct:v,memoryPct:48+i,diskPct:40,dataDiskPct:20,networkRxMbps:i*.5,networkTxMbps:i*.125,cpuPackageWatts:null}))};
const inventoryFixture={kind:'PRIVATE_COLLECTOR_STORAGE_INVENTORY',history:{
  targetCount:50,fileCount:1,storedBytes:1024,checkpointRows:500,
  symbols:Array.from({length:50},(_,i)=>({
    symbol:i===0?'NVDA':'TEST'+i,dbExists:i===0,bytes:i===0?1024:null,
    checkpointRows:i===0?500:null,oldest:i===0?'2015-01-01T00:00:00Z':null,
    newest:i===0?'2026-10-01T00:00:00Z':null,scanPercent:i===0?40:0,
    missingDays:i===0?3:null,state:i===0?'collecting':'queued'
  }))
},ticks:{sessionFolderCount:1,lastSession:'2026-10-28',
lastSessionFiles:[{symbol:'NVDA',bytes:500}],lastSessionBytes:500,
status:'stopped',lastUpdate:'2026-10-29T00:00:00Z'}};
const publicRequests=[];
let historyEnabled=false;
let tickEnabled=false;
let historyRequests=[];
const historyFixture=()=>({
  configured:true,enabled:historyEnabled,tickEnabled,symbolCount:50,scanPercent:0.47,
  totalStoredRows:722500,totalPages:7225,
  controlReason:historyEnabled?'ADMIN_HISTORY_START':'ADMIN_HISTORY_PAUSE',
  controlUpdatedAt:'2026-10-29T14:00:00+00:00',
  counts:{queued:0,error:0,collection_pass_finished_unverified:0},
  symbols:Array.from({length:50},(_,i)=>({
    symbol:i===0?'NVDA':'TEST'+i,status:'collecting',state:'collecting',
    scanPercent:0.47,storedRows:i===0?722500:0,pages:i===0?7225:0
  })),
  scheduler:{serviceState:'running',paused:false,
    activeWorker:historyEnabled?(tickEnabled?'tick':'backfill'):(tickEnabled?'tick':null),
    collectorStatus:tickEnabled?'MARKET_OPEN':historyEnabled?'HISTORY_ONLY_RUNNING':'ALL_COLLECTORS_PAUSED',
    reason:tickEnabled?null:historyEnabled?'HISTORY_ONLY_DURING_MARKET_OPEN':'TICK_AND_HISTORY_DISABLED',
    calendarConfirmed:true}
});
const server=http.createServer((req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  if(pathname==='/api/history/status'&&req.method==='GET'){
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify(historyFixture()));return;
  }
  if(pathname==='/api/history/start'&&req.method==='POST'){
    historyRequests.push('start');
    historyEnabled=true;
    res.setHeader('Content-Type','application/json');
    res.end(JSON.stringify({ok:true,enabled:true,masterPaused:false,mode:'TICKS_PAUSED',collector:null,queued:true}));
    return;
  }
  if(pathname==='/api/ticks/control'&&req.method==='POST'){
    let json='';
    req.on('data',part=>json+=part);
    req.on('end',()=>{
      const reqBody=JSON.parse(json);
      tickEnabled=reqBody.enabled===true;
      historyRequests.push(tickEnabled?'tick-on':'tick-off');
      res.setHeader('Content-Type','application/json');
      res.end(JSON.stringify({ok:true,enabled:tickEnabled,masterPaused:false}));
    });
    return;
  }
  if(pathname==='/api/history/control'&&req.method==='POST'){
    historyRequests.push('pause');
    historyEnabled=false;
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:true,enabled:false}));
    return;
  }
  if(pathname==='/api/data/inventory'){
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify(inventoryFixture));return;
  }
  if(pathname==='/api/me'||pathname==='/api/overview'||pathname==='/api/telemetry'){
    res.setHeader('Content-Type','application/json');
    res.end(JSON.stringify(pathname==='/api/me'?{csrf:'fixture'}:
      pathname==='/api/overview'?{telemetry:fixture,historical50:historyFixture(),
        historical:historyFixture().scheduler,ticks:{enabled:tickEnabled,active:tickEnabled}}:fixture));
    return;
  }
  const files={'/':'server-status.html','/console':'deploy/admin-console/index.html','/app.js':'deploy/admin-console/app.js'};
  const rel=files[pathname]||pathname.slice(1),file=path.resolve(root,rel);
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
  if(pathname==='/console'||pathname==='/app.js'||pathname==='/server-metrics.css'||pathname==='/server-metrics.js')
    res.setHeader('Content-Security-Policy',adminCsp);
  res.setHeader('Content-Type',file.endsWith('.css')?'text/css':file.endsWith('.js')?'application/javascript':'text/html');res.end(fs.readFileSync(file));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
  let cases=0;
  try{
    for(const width of [320,390,768,1440])for(const admin of [false,true]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      let cssLoaded=false;
      page.on('response',response=>{
        if(admin&&new URL(response.url()).pathname==='/server-metrics.css'&&response.status()===200)
          cssLoaded=true;
      });
      page.on('pageerror',error=>errors.push(error.message));
      page.on('console',m=>{if(m.type()==='error'&&m.text().includes('Content Security Policy'))errors.push(m.text());});
      await page.route('https://heh-heh.tail39e546.ts.net/**',route=>{
        publicRequests.push({url:route.request().url(),method:route.request().method(),headers:route.request().headers()});
        return route.fulfill({json:fixture,headers:{'Access-Control-Allow-Origin':'*'}});
      });
      await page.goto(`http://127.0.0.1:${server.address().port}/${admin?'console':''}`);
      if(admin){
        await page.click('[data-page="server"]');
        assert.equal(await page.locator('#serverDiagnostics').getAttribute('open'),null,'diagnostics start collapsed');
        await page.locator('#serverPerfRefresh').click();
        await page.waitForFunction(()=>document.querySelector('#serverPerfState')?.textContent==='정상 수집');
      }
      const selector=admin?'#privateServerMetrics':'#serverMetrics';
      const target=page.locator(selector);
      await target.locator('.metric-resource').first().waitFor();
      // Regression: the private page used to block /server-metrics.css via
      // style-src 'unsafe-inline' (without 'self'), leaving all cards in one row.
      const styled=await target.evaluate(t=>({
        layout:getComputedStyle(t.querySelector('.metric-workbench')).display,
        list:getComputedStyle(t.querySelector('.metric-resource-list')).display,
        detail:getComputedStyle(t.querySelector('.metric-detail')).backgroundColor,
        graphHeight:t.querySelector('.metric-graph').getBoundingClientRect().height
      }));
      assert.equal(styled.layout,'grid',`${width} ${admin?'admin':'public'}: stylesheet not loaded`);
      assert.equal(styled.list,'grid','resource cards must form a vertical list or mobile grid');
      assert(styled.graphHeight>=190,'detail chart is missing Task Manager sizing');
      if(admin)assert(cssLoaded,'private administrator did not load /server-metrics.css');

      assert.equal(await target.locator('.metric-resource').count(),6,'GPU absent without real telemetry');
      assert.equal(await target.locator('.metric-spark').count(),6);
      assert.equal(await target.locator('.metric-detail h2').textContent(),'CPU');
      const dd=target.locator('.metric-stat-values dd');
      assert.equal(await dd.nth(1).textContent(),'30%');assert.equal(await dd.nth(2).textContent(),'60%');assert.equal(await dd.nth(3).textContent(),'0%');
      assert.equal(await target.locator('.metric-graph polyline').count(),1,'null breaks the line; isolated sample is a dot');
      await target.locator('[data-resource="memory"]').click();
      assert.equal(await target.locator('.metric-detail h2').textContent(),'메모리');
      await page.evaluate(({selector,data})=>window.ServerMetrics.render(document.querySelector(selector),data),{selector,data:fixture});
      assert.equal(await target.locator('[aria-pressed="true"]').getAttribute('data-resource'),'memory','selection survives polling');
      assert.equal(await page.locator(':focus').getAttribute('data-resource'),'memory','polling preserves keyboard focus');
      await target.locator('[data-resource="memory"]').press('ArrowDown');
      assert.equal(await target.locator('.metric-detail h2').textContent(),'루트 디스크');
      await target.locator('[data-resource="network"]').click();
      assert.equal(await target.locator('.metric-stat-group').count(),2,'RX and TX have independent stats');
      await target.locator('[data-resource="power"]').click();
      assert.equal(await target.locator('.metric-current').textContent(),'측정 불가');
      assert((await target.locator('.metric-detail').textContent()).includes('서버 전체 소비전력이 아니며'));
      const geometry=await target.evaluate(t=>{
        const a=t.querySelector('.metric-resource-list').getBoundingClientRect(),b=t.querySelector('.metric-detail').getBoundingClientRect();
        return {overflow:document.documentElement.scrollWidth>innerWidth,listBottom:a.bottom,detailTop:b.top,listRight:a.right,detailLeft:b.left};
      });
      assert.equal(geometry.overflow,false,`${width} ${admin?'admin':'public'} horizontal overflow`);
      if(width<=690)assert(geometry.detailTop>=geometry.listBottom,'mobile: list above detail');
      else assert(geometry.detailLeft>=geometry.listRight,'desktop: list left of detail');
      // Optional telemetry only appears when supplied; unknown/private keys are never rendered.
      await page.evaluate(({selector,data})=>window.ServerMetrics.render(document.querySelector(selector),{...data,privateToken:'NEVER_RENDER_THIS',metrics:{...data.metrics,gpuPct:0}}),{selector,data:fixture});
      assert.equal(await target.locator('[data-resource="gpu"]').count(),1);
      await target.locator('[data-resource="gpu"]').click();
      assert.equal(await target.locator('.metric-current').textContent(),'0%');
      assert(!(await target.textContent()).includes('NEVER_RENDER_THIS'));
      // Real GPU sensor payload creates four independent charts, without showing unknown keys.
      const gpuFixture={...fixture,metrics:{...fixture.metrics,
        gpuPct:12,gpuMemoryUsedMiB:512,gpuMemoryTotalMiB:6144,gpuTempC:43,gpuWatts:29.75},
        history:fixture.history.map((row,i)=>({...row,gpuPct:i*3,
          gpuMemoryUsedMiB:i*128,gpuTempC:40+i,gpuWatts:25+i}))};
      await page.evaluate(({selector,data})=>window.ServerMetrics.render(document.querySelector(selector),data),{selector,data:gpuFixture});
      for(const id of ['gpu','gpu-memory','gpu-temp','gpu-power'])
        assert.equal(await target.locator('[data-resource="'+id+'"]').count(),1,'missing '+id+' chart');
      await target.locator('[data-resource="gpu-memory"]').click();
      assert.equal(await target.locator('.metric-current').textContent(),'512 MiB');
      assert((await target.locator('.metric-detail').textContent()).includes('6,144 MiB'),'VRAM total must appear');
      await target.locator('[data-resource="gpu-temp"]').click();
      assert.equal(await target.locator('.metric-current').textContent(),'43°C');
      await target.locator('[data-resource="gpu-power"]').click();
      assert.equal(await target.locator('.metric-current').textContent(),'29.8 W');
      assert(!(await target.textContent()).includes('NEVER_RENDER_THIS'));
      // Missing timestamps are not invented; an unavailable payload never becomes zero usage.
      await page.evaluate(({selector,data})=>window.ServerMetrics.render(document.querySelector(selector),{...data,history:[{cpuPct:99}]}),{selector,data:fixture});
      await target.locator('[data-resource="cpu"]').click();
      assert.equal(await target.locator('.metric-stat-values dd').nth(1).textContent(),'측정 불가');
      await page.evaluate(selector=>window.ServerMetrics.render(document.querySelector(selector),null),selector);
      assert.equal(await target.locator('.metric-current').textContent(),'측정 불가');
      assert.equal(await target.locator('.metric-graph polyline').count(),0);
      await page.evaluate(({selector,data})=>window.ServerMetrics.render(document.querySelector(selector),data),{selector,data:fixture});
      // Irregular observations keep actual time spacing, never bridge a collection outage.
      const gap={...fixture,history:[fixture.history[0],fixture.history[1],{...fixture.history[2],at:'2026-10-29T10:20:00Z'}]};
      await page.evaluate(({selector,data})=>window.ServerMetrics.render(document.querySelector(selector),data),{selector,data:gap});
      assert.equal(await target.locator('.metric-graph polyline').count(),1);
      assert.equal(await target.locator('.metric-graph circle').count(),1);
      assert.deepEqual(errors,[],errors.join('\n'));
      if(process.env.METRIC_SCREENSHOTS&&[390,1440].includes(width)){
        await page.evaluate(({selector,data})=>window.ServerMetrics.render(document.querySelector(selector),data),{selector,data:fixture});
        await page.screenshot({path:`/tmp/server-performance-${admin?'admin':'public'}-${width}.png`,fullPage:true});
      }
      if(admin){
        historyEnabled=false;
        tickEnabled=false;
        historyRequests=[];
        page.on('dialog',async dialog=>dialog.accept());
        await page.locator('[data-page="historical"]').click();
        const historyToggle=page.locator('#historyCollectorToggle');
        const tickToggle=page.locator('#tickCollectorToggle');
        await page.waitForFunction(()=>!document.querySelector('#historyCollectorToggle')?.disabled);
        assert.equal(await historyToggle.isChecked(),false);
        assert.equal(await tickToggle.isChecked(),false);
        await historyToggle.check();
        await page.waitForFunction(()=>document.querySelector('#history50Persisted')?.textContent?.includes('수집 허용됨'));
        assert.deepEqual(historyRequests,['start'],'1-minute ON must never dispatch pause');
        assert.equal(historyEnabled,true);
        assert((await page.locator('#history50State').textContent()).includes('1분봉'),
          '1-minute only must run during US market open');
        await tickToggle.check();
        await page.waitForFunction(()=>document.querySelector('#tickToggleStatus')?.textContent?.includes('ON'));
        assert.equal(tickEnabled,true);
        assert.deepEqual(historyRequests,['start','tick-on']);
        await historyToggle.uncheck();
        await page.waitForFunction(()=>document.querySelector('#history50Persisted')?.textContent?.includes('과거 수집 중지'));
        assert.deepEqual(historyRequests,['start','tick-on','pause'],'1-minute OFF must not turn off tick');
        assert.equal(tickEnabled,true);
        await tickToggle.uncheck();
        await page.waitForFunction(()=>document.querySelector('#tickToggleStatus')?.textContent?.includes('OFF'));
        assert.deepEqual(historyRequests,['start','tick-on','pause','tick-off']);
        assert.equal(historyEnabled,false);
        assert.equal(tickEnabled,false);
        await page.locator('[data-page="inventory"]').click();
        await page.locator('#inventoryTable tbody tr').first().waitFor();
        assert.equal(await page.locator('#inventoryTable tbody tr').count(),50);
        assert.equal(await page.locator('#inventoryFiles').textContent(),'1 / 50');
        assert.equal(await page.locator('#inventoryTickSessions').textContent(),'1');
        assert((await page.locator('#inventoryTable tbody tr').first().textContent()).includes('NVDA'));
        await page.locator('#inventoryFilter').selectOption('missing');
        assert.equal(await page.locator('#inventoryTable tbody tr').count(),49);
        await page.locator('[data-page="historical"]').click();
        assert.equal(await page.locator('[data-page="ticks"]').count(),0);
        assert.equal(await page.locator('#page-ticks').count(),0);
        assert.equal(await page.locator('#historyCollectorToggle').count(),1);
        assert.equal(await page.locator('#tickCollectorToggle').count(),1);
        const actual=await page.locator('#page-historical').isVisible();
        assert(actual,'unified collector page should be visible');
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      }
      await page.close();cases++;
    }
    assert(publicRequests.length>0);
    assert(publicRequests.every(r=>r.method==='GET'&&r.url.endsWith('/api/server-telemetry')&&!r.headers.authorization&&!r.headers.cookie));
    console.log(`PASS: ${cases} public/admin viewport cases; stats, selection, focus, gaps, missing data, CSP and read-only requests`);
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
