/* Read-only Autonomous Research monitor; authenticated artifact export only. */
(() => {
 'use strict';
 const root=document.getElementById('autonomousResearchCard');
 if(!root) return;
 const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const base=()=>AWS_API_BASE.replace(/\/$/,'');
 let timer=null;
 root.innerHTML='<div class="label">RESEARCH ONLY · 2026 접근 차단</div><h2>Autonomous AI Research</h2><div id="autonomousCurrent">실행 대기</div><p id="autonomousMessage" role="status"></p><div id="autonomousRecent"></div>';
 async function api(path,options={},auth=false){
  const headers=auth?settingsHeaders():{};
  if(auth&&!headers.Authorization) throw new Error('설정에서 관리자 로그인 후 다운로드하세요.');
  const r=await fetch(base()+path,{...options,headers:{...headers,'Content-Type':'application/json'}});
  const d=await r.json(); if(!r.ok||d.ok===false) throw new Error(d.error||'연구 API 응답 오류');return d;
 }
 function render(d){
  const c=d.current||{};
  const pairs=[['Iteration',c.iteration],['Candidate',c.candidateVersion],['State',c.state],['Baseline',c.parentVersion],
   ['Training',c.trainingProgress==null?'—':c.trainingProgress+'%'],['Holdout',c.holdoutProgress==null?'—':c.holdoutProgress+'%'],
   ['Validation',c.validationProgress==null?'—':c.validationProgress+'%'],['Positive bots',c.positiveBots],
   ['Validation mean',c.validationMean],['Validation median',c.validationMedian],['Avg trades',c.avgTrades],
   ['Decision',c.decision],['Failure',c.failureCode],['Final OOS Gate',c.finalOosGate||'CLOSED']];
  document.getElementById('autonomousCurrent').innerHTML='<p>'+esc(c.hypothesis)+'</p><div class="bt-grid">'+pairs.map(([k,v])=>'<div class="bt-stat"><div class="label">'+esc(k)+'</div><div class="v">'+esc(v)+'</div></div>').join('')+'</div>';
  document.getElementById('autonomousRecent').innerHTML=(d.recent||[]).map(i=>'<p>Iteration '+esc(i.iteration)+' · '+esc(i.candidateVersion)+' · '+esc(i.state)+' <button data-iteration="'+esc(i.id)+'">Download Experiment</button></p>').join('');
 }
 async function update(){
  clearTimeout(timer);
  try{render(await api('/api/research/autonomous/status'));}
  catch(e){document.getElementById('autonomousMessage').textContent='모니터: '+e.message;}
  timer=setTimeout(update,10000);
 }
 root.addEventListener('click',async e=>{
  const b=e.target.closest('[data-iteration]'); if(!b) return;
  const msg=document.getElementById('autonomousMessage');b.disabled=true;
  try{
   if(!/^[0-9a-f]{24}$/.test(b.dataset.iteration))throw new Error('Invalid iteration ID');
   let j=await api('/api/research/export/jobs',{method:'POST',body:JSON.stringify({type:'autonomous_iteration',runIds:[b.dataset.iteration]})},true);
   if(!/^[0-9a-f]{32}$/.test(j.jobId))throw new Error('Invalid export ID');
   const deadline=Date.now()+3600000;
   while(['queued','running'].includes(j.state)){
    if(Date.now()>deadline)throw new Error('Export 시간 초과; 다운로드 센터에서 확인하세요.');
    msg.textContent='Export '+j.state+' '+(j.progressPct||0)+'%';
    await new Promise(resolve=>setTimeout(resolve,2000));j=await api('/api/research/export/jobs/'+j.jobId,{},true);
   }
   if(j.state!=='completed')throw new Error(j.error||'Export 실패');
   const t=await api('/api/research/export/jobs/'+j.jobId+'/ticket',{method:'POST',body:'{}'},true);
   if(!t.downloadUrl.startsWith('/api/research/export/jobs/'+j.jobId+'/download?ticket='))throw new Error('Invalid download ticket URL');
   const a=document.createElement('a');a.href=base()+t.downloadUrl;a.rel='noreferrer';document.body.appendChild(a);a.click();a.remove();
   msg.textContent='다운로드 준비 완료. 원본 연구 데이터는 변경되지 않습니다.';
  }catch(err){msg.textContent=err.message;}finally{b.disabled=false;}
 });
 update();
})();
