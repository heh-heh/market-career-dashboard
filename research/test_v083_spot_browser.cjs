// Optional Playwright integration; ALL network intercepted, no real AWS/API.
const {chromium}=require('playwright'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.join(__dirname,'..'),html=fs.readFileSync(root+'/admin.html','utf8').replace(/\nauthGate\(\);/,'\n// offline auth fixture');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.BROWSER_BIN||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage(),errors=[],calls=[];let phase=null;
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.route('**/*',async route=>{
   const p=new URL(route.request().url()).pathname;
   if(p==='/admin.html')return route.fulfill({contentType:'text/html',body:html});
   if(/^\/[a-z0-9-]+\.(js|css)$/.test(p)&&fs.existsSync(root+p))return route.fulfill({contentType:p.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(root+p)});
   if(p==='/api/admin/console'){
    const command=JSON.parse(route.request().postData()).command;
    const d=command==='v083 report'?{protocol:'V083_WEB_1',stages:{},runs:{},generations:[]}:{};
    return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,output:JSON.stringify(d)})});
   }
   if(p.startsWith('/api/research/v083/spot/')){
    const action=p.split('/').pop();calls.push(action);
    if(action==='prepare')phase='PREPARED';if(action==='approve')phase='APPROVED';if(action==='start')phase='REQUESTED_ONE_TIME_SPOT';
    const request=phase?{phase,requestId:'synthetic',configHash:'synthetic'}:null;
    return route.fulfill({contentType:'application/json',body:JSON.stringify(action==='status'?{ok:true,protocol:'V083_SPOT_CONTROL_2',configured:true,runnable:true,request}:{ok:true,...request})});
   }
   return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:false,error:'OFFLINE_ONLY'})});
  });
  await page.goto('https://fixture.test/admin.html#ai');
  await page.evaluate(()=>{localStorage.setItem('market_dashboard_admin_token','synthetic');document.getElementById('gate').classList.add('hidden');document.getElementById('app').classList.remove('hidden');navigateAdmin('ai');document.querySelector('[data-group="ai"]').click()});
  await page.waitForFunction(()=>!document.querySelector('#v083WebPanel [data-v083="smoke"]').disabled);
  assert.equal(calls.length,0,'page load does not query or launch Spot');
  assert.ok(await page.locator('#v083SpotLaunch').isDisabled());
  await page.locator('#v083SpotRefresh').click();
  await page.waitForFunction(()=>!document.getElementById('v083SpotPrepare').disabled);
  for(const width of [320,390,768,1280]){
   await page.setViewportSize({width,height:900});
   const size=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,inner:innerWidth}));
   assert.ok(size.scroll<=size.inner+1,JSON.stringify({width,...size}));
  }
  await page.locator('#v083SpotPrepare').click();await page.waitForFunction(()=>!document.getElementById('v083SpotApprove').disabled);
  assert.ok(!calls.includes('start'));
  await page.locator('#v083SpotApprove').click();await page.waitForFunction(()=>!document.getElementById('v083SpotLaunch').disabled);
  assert.ok(!calls.includes('start'),'approval alone does not create an instance');
  await page.locator('#v083SpotLaunch').click();await page.waitForFunction(()=>document.getElementById('v083SpotStatus').innerText.includes('REQUESTED_ONE_TIME_SPOT'));
  assert.equal(calls.filter(x=>x==='start').length,1);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({syntheticOnly:true,widths:[320,390,768,1280],noOverflow:true,pageErrors:0,separateConsent:true}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
