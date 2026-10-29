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
const publicRequests=[];
const server=http.createServer((req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  if(pathname==='/api/me'||pathname==='/api/overview'||pathname==='/api/telemetry'){
    res.setHeader('Content-Type','application/json');
    res.end(JSON.stringify(pathname==='/api/me'?{csrf:'fixture'}:pathname==='/api/overview'?{telemetry:fixture}:fixture));
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
      await page.close();cases++;
    }
    assert(publicRequests.length>0);
    assert(publicRequests.every(r=>r.method==='GET'&&r.url.endsWith('/api/server-telemetry')&&!r.headers.authorization&&!r.headers.cookie));
    console.log(`PASS: ${cases} public/admin viewport cases; stats, selection, focus, gaps, missing data, CSP and read-only requests`);
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
