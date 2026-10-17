const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
const root=require('node:path').join(__dirname,'..');
const html=fs.readFileSync(root+'/admin.html','utf8').replace(/\nauthGate\(\);/,'\n// Browser fixture: no real authentication/network');
const dates=['2023-01-03','2023-01-04','2023-01-05','2023-01-06','2023-01-09'];
let data={jobId:'synthetic-browser-only',stages:{train:{phase:'running',startedEpoch:1},holdout:{phase:'not_started'},validation:{phase:'not_started'}},runs:{train:{completedSessions:37,requestedSessions:100,selectedSessions:5,windowSessionsCompleted:2,evaluationWindowSessions:5,completedTrainGenerations:1,skippedGenerationAttempts:3,currentHistoricalDate:'2023-02-23',latestLog:'SYNTHETIC_FIXTURE'}},diskFreeBytes:100*1024**3,generations:[{generation:1,dates,returns:{mean:.02},mdd:{mean:.03},fitness:{mean:.01},selection:{parentPoolSize:4,eliteCount:2,mutationCount:8,crossoverCount:3}},{generation:2,dates,returns:{mean:9}}],downloadAvailable:false};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.BROWSER_BIN||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
 const page=await browser.newPage();const errors=[],calls=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(url.hostname==='fixture.test'){
   if(url.pathname==='/admin.html')return route.fulfill({contentType:'text/html',body:html});
   const file=url.pathname.slice(1);if(['research-export.js','research-export.css'].includes(file))return route.fulfill({contentType:file.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(root+'/'+file)});
   return route.fulfill({status:404,body:''});
  }
  assert.equal(url.hostname,'35.79.235.14');calls.push({path:url.pathname,method:route.request().method(),body:route.request().postData()});
  if(url.pathname==='/api/admin/console'){
   const {command}=JSON.parse(route.request().postData());
   const output=command==='v07 report'?JSON.stringify(data):command==='v07 log'?Array.from({length:40},(_,i)=>'SYNTHETIC_LOG_'+i).join('\n'):'SYNTHETIC_RESPONSE';
   return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,output})});
  }
  if(url.pathname==='/api/admin/server-health')return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,cpu:{usedPct:22.5},memory:{usedPct:50,swapUsedPct:0}})});
  throw new Error('Unexpected remote API request: '+url.pathname);
 });
 await page.goto('https://fixture.test/admin.html#ai');
 await page.evaluate(()=>{localStorage.setItem('market_dashboard_admin_token','synthetic');document.getElementById('gate').classList.add('hidden');document.getElementById('app').classList.remove('hidden');navigateAdmin('ai')});
 await page.waitForFunction(()=>document.getElementById('v07Overview').innerText.includes('37%'));
 for(const width of [320,390,768,1280]){
  await page.setViewportSize({width,height:900});
  const dims=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,inner:innerWidth}));assert.ok(dims.scroll<=dims.inner+1,JSON.stringify({width,...dims}));
  await page.screenshot({path:'/tmp/v07-dashboard-'+width+'.png',fullPage:true});
 }
 assert.equal((await page.locator('#v07AiLog').innerText()).split('\n').length,25);
 assert.ok(!(await page.locator('#v07Lineage').innerText()).includes('900%'));
 assert.ok(await page.locator('[data-v07-retry="train"]').isDisabled());
 await page.locator('#v07ManagedPanel [data-v07="train"]').click();
 await page.waitForFunction(()=>!document.querySelector('#v07ManagedPanel [data-v07="train"]').disabled);
 assert.ok(calls.some(c=>JSON.parse(c.body||'{}').command==='v07 train'));
 await page.waitForFunction(()=>!v07WebPending);data={};await page.evaluate(()=>refreshV07Web());assert.ok((await page.locator('#v07Overview').innerText()).includes('집계 중'));assert.equal(await page.locator('#v07ManagedGraph polyline').count(),0);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({syntheticOnly:true,widths:[320,390,768,1280],noPageOverflow:true,logLines:25,noFutureGeneration:true,controlsPreserved:true,pageErrors:errors.length,apiPaths:[...new Set(calls.map(c=>c.path))]}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
