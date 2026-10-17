// Optional local Playwright check; all network intercepted, no real API actions.
const {chromium}=require('playwright'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.join(__dirname,'..'),html=fs.readFileSync(root+'/admin.html','utf8').replace(/\nauthGate\(\);/,'\n// synthetic fixture');
let data={protocol:'V081_WEB_1',initialPopulation:100,maxPopulation:1000,stages:{smoke:{phase:'completed',startedEpoch:1},calibrate:{phase:'completed',startedEpoch:2},train:{phase:'not_started'}},runs:{smoke:{population:100,completedSessions:5,requestedSessions:5,completedTrainGenerations:1}},resources:{cpuPct:20,memoryPct:35,swapPct:0},capacity:{recommendation:{recommendedPopulation:225}},generations:[{generation:1,dates:['2023-06-01','2023-06-02','2023-06-05','2023-06-06','2023-06-07'],meanReturn:-.01,meanMdd:.02,meanFitness:-.03,parents:2,preservationParents:true,offspringCount:100,parentSelection:[{botId:'a',netPnl:-.5},{botId:'b',netPnl:-.8}]}]};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.BROWSER_BIN||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
 const page=await browser.newPage(),errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
 await page.route('**/*',async route=>{
  const p=new URL(route.request().url()).pathname;
  if(p==='/admin.html')return route.fulfill({contentType:'text/html',body:html});
  if(['/v08-web.js','/v081-web.js','/v082-web.js','/research-export.js','/research-export.css'].includes(p))return route.fulfill({contentType:p.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(root+p)});
  if(p==='/api/admin/console'){
   const c=JSON.parse(route.request().postData()).command;calls.push(c);
   return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,output:c==='v081 report'?JSON.stringify(data):c.endsWith(' report')?'{}':'SYNTHETIC_ONLY'})});
  }
  return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:false,error:'OFFLINE_FIXTURE'})});
 });
 await page.goto('https://fixture.test/admin.html#ai');
 await page.evaluate(()=>{localStorage.setItem('market_dashboard_admin_token','synthetic');document.getElementById('gate').classList.add('hidden');document.getElementById('app').classList.remove('hidden');navigateAdmin('ai');document.querySelector('[data-group="ai"]').click()});
 await page.waitForFunction(()=>document.getElementById('v081WebStats').innerText.includes('225'));
 for(const width of [320,390,768,1280]){
  await page.setViewportSize({width,height:900});const size=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,inner:innerWidth}));assert.ok(size.scroll<=size.inner+1,JSON.stringify({width,...size}));
  await page.screenshot({path:'/tmp/v081-dashboard-'+width+'.png',fullPage:true});
 }
 assert.match(await page.locator('#v081WebLineage').innerText(),/a:-0.5/);
 await page.locator('#v081WebPanel [data-v081="train"]').click();
 await page.waitForFunction(()=>!document.querySelector('#v081WebPanel [data-v081="train"]').disabled);
 assert.ok(calls.includes('v081 train'));
 data={protocol:'OLD'};await page.locator('#v081WebRefresh').click();
 await page.waitForFunction(()=>document.getElementById('v081WebFeedback').innerText.includes('V081_BACKEND_UNSUPPORTED'));
 assert.ok(await page.locator('#v081WebPanel [data-v081="train"]').isDisabled());
 assert.deepEqual(errors,[]);console.log(JSON.stringify({syntheticOnly:true,widths:[320,390,768,1280],noOverflow:true,unsupportedBlocked:true,actualParentPnl:true,pageErrors:0}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
