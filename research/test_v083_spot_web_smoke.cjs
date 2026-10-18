const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const js=fs.readFileSync(path.join(__dirname,'../v083-web.js'),'utf8');
const nodes=new Map();function get(id){if(!nodes.has(id))nodes.set(id,{textContent:'',innerHTML:'',disabled:true,value:id==='v083SpotInstance'?'c6i.2xlarge':id==='v083SpotHours'?'8':'10',addEventListener(){}});return nodes.get(id)}
const calls=[];let phase=null,backend=true,allow=true;
const ctx=vm.createContext({document:{getElementById:get,querySelectorAll:()=>[]},location:{hash:''},token:()=>true,confirm:()=>allow,setInterval(){},console,API:'',req:async(url,opts)=>{
 const action=url.split('/').pop(),body=opts?.body?JSON.parse(opts.body):null;calls.push({action,body});
 if(action==='status')return {ok:true,protocol:backend?'V083_SPOT_CONTROL_2':'OLD',configured:true,runnable:true,request:phase?{requestId:'id',configHash:'hash',phase}:null};
 if(action==='prepare')phase='PREPARED';if(action==='approve')phase='APPROVED';if(action==='start')phase='REQUESTED_ONE_TIME_SPOT';if(action==='stop')phase='STOP_REQUESTED';
 return {ok:true,requestId:'id',configHash:'hash',phase};
}});
vm.runInContext(js.replace('})();','supported=true;globalThis.test={render,spotInspect,spotAction,renderSpot};})();'),ctx);
(async()=>{
 assert.equal(calls.length,0);assert.equal(get('v083SpotLaunch').disabled,true);
 ctx.test.render({protocol:'V083_WEB_1',stages:{},runs:{},generations:[]});
 await ctx.test.spotInspect();assert.equal(get('v083SpotPrepare').disabled,false);assert.equal(get('v083SpotLaunch').disabled,true);
 await ctx.test.spotAction('prepare');assert.equal(get('v083SpotApprove').disabled,false);assert.equal(get('v083SpotLaunch').disabled,true);
 allow=false;await ctx.test.spotAction('approve');assert.ok(!calls.some(c=>c.action==='approve'));
 allow=true;await ctx.test.spotAction('approve');assert.equal(get('v083SpotLaunch').disabled,false);
 assert.equal(calls.find(c=>c.action==='approve').body.confirmation,'APPROVE_PAID_SPOT');assert.ok(!calls.some(c=>c.action==='start'));
 await ctx.test.spotAction('start');assert.equal(calls.find(c=>c.action==='start').body.confirmation,'CREATE_ONE_PAID_SPOT');assert.equal(get('v083SpotPrepare').disabled,true);
 await ctx.test.spotAction('stop');assert.equal(get('v083SpotPrepare').disabled,true,'stop request does not prove termination');
 ctx.test.renderSpot({configured:true,runnable:true,request:{phase:'RUNNING'},remote:{data:{phase:'train',training:{completedSessions:5,requestedSessions:10,completedTrainGenerations:1},checkpoint:{key:'committed/one'}}}});
 assert.match(get('v083SpotProgress').textContent,/committed\/one/);
 backend=false;await ctx.test.spotInspect();assert.equal(get('v083SpotLaunch').disabled,true);assert.match(get('v083SpotStatus').textContent,/UNSUPPORTED/);
 console.log('PASS: independent cost/creation consent, no initial AWS, unsupported backend disabled, real checkpoint progress');
})().catch(e=>{console.error(e);process.exit(1)});
