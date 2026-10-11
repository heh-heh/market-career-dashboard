/* Read-only research export UI. No trading, training or order endpoints. */
(() => {
 'use strict';
 const root = document.getElementById('researchExportCenter');
 if (!root) return;
 let catalog = null, timer = null;
 const esc = v => String(v ?? '—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const bytes = n => n == null ? '—' : n < 1048576 ? (n/1024).toFixed(1)+' KB' : n < 1073741824 ? (n/1048576).toFixed(1)+' MB' : (n/1073741824).toFixed(2)+' GB';
 const show = msg => { document.getElementById('exportMessage').textContent = msg; };
 const base = () => AWS_API_BASE.replace(/\/$/,'');
 async function request(path, options={}) {
  const headers = settingsHeaders();
  if (!headers.Authorization) throw new Error('설정에서 관리자 로그인 후 이용하세요.');
  const res = await fetch(base()+path,{...options,headers:{...headers,'Content-Type':'application/json'}});
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || 'Export 요청 실패');
  return data;
 }
 root.innerHTML = `<div class="label">연구 데이터 다운로드</div><h3>Research Data Export</h3>
 <p>원본은 읽기 전용입니다. 관리자 로그인 필요 · Archive 7일 보관 · 실제 학습/백테스트를 실행하지 않습니다.</p>
 <div class="export-actions"><button data-export="refresh">목록 갱신</button><button data-export="summary-json">요약 JSON</button><button data-export="summary-csv">요약 CSV</button></div>
 <div id="exportMessage" role="status" aria-live="polite">목록 갱신을 눌러 서버의 연구 결과를 확인하세요.</div>
 <div id="exportSources"></div><h4>Evolution 최근 실행</h4><p>선택하지 않으면 모든 실행을 포함합니다. 수치가 없는 항목은 — 로 표시합니다.</p>
 <div id="exportRuns"></div><div class="export-actions">
 <button data-export="evolution_full">Evolution 전체 다운로드</button><button data-export="evolution_summary">요약만 다운로드</button>
 <button data-export="evolution_population">Population 다운로드</button><button data-export="evolution_trades">Trades 다운로드</button>
 <button data-export="V07_FULL_RESEARCH_BUNDLE">V0.7 전체 연구 Bundle</button><button data-export="v4_full">V4 연구 결과</button><button data-export="research_metadata">전체 연구 Metadata</button></div>
 <h4>버전별 Artifact 비교</h4><p>파일에 기록된 단위 그대로 표시합니다. 알려지지 않은 수익률 단위를 추정하지 않습니다.</p><div id="exportComparison" class="export-table"></div>
 <h4>Toss 데이터 선택 Export</h4><p>전체 데이터 압축은 기본 선택되지 않습니다. Tick은 최대 31일, Archive는 최대 2 GB입니다. 여러 번 나누어 받으세요.</p>
 <label>Data type <select id="exportType"><option value="toss_1m">1 minute</option><option value="toss_ticks">Raw Tick (LOSSY)</option></select></label>
 <div id="exportSymbols" class="export-symbols"></div><div class="export-dates"><label>From <input id="exportFrom" type="date"></label><label>To <input id="exportTo" type="date"></label></div>
 <button data-export="raw">선택 범위 Export 생성</button><h4>Export 작업</h4><div id="exportJobs"></div>`;
 function renderCatalog(data) {
  catalog=data;
  const labels={evolution:'Evolution 결과',v4:'V4 연구 결과',results:'저장소 연구 결과',minutes:'Toss 1분봉',ticks:'Toss Raw Tick',metadata:'연구 Metadata'};
  document.getElementById('exportSources').innerHTML=(data.sources||[]).map(s=>`<div class="export-source"><b>${esc(labels[s.id]||s.id)}</b><span>${s.available?'가능':'자료 없음'} · ${esc(s.files)} 파일 · ${bytes(s.estimatedBytes)}</span><small>업데이트 ${esc(s.updatedAt)}</small></div>`).join('');
  const runs=data.runs||[];
  document.getElementById('exportRuns').innerHTML=runs.slice(0,30).map(r=>`<label class="export-run"><input type="checkbox" name="exportRun" value="${esc(r.id)}"><span><b>${esc(r.label)}</b> · ${esc(r.runId)}<small>Train ${esc((r.trainPeriod||[]).join(' ~ '))} · Validation ${esc((r.validationPeriod||r.evaluationPeriod||[]).join(' ~ '))}<br>Population ${esc(r.population)} · Generation ${esc(r.generation)} · Shortlist ${esc(r.shortlistCount)} · ${bytes(r.estimatedBytes)}</small></span></label>`).join('')||'<p>확인된 실행 Metadata가 없습니다.</p>';
  document.getElementById('exportSymbols').innerHTML=(data.symbols||[]).map(s=>`<label><input type="checkbox" name="exportSymbol" value="${esc(s)}">${esc(s)}</label>`).join('');
  document.getElementById('exportComparison').innerHTML='<table><thead><tr><th>Version / Run</th><th>Mean</th><th>Median</th><th>Positive</th><th>Population</th><th>Trades/Bot</th><th>Trades↔Val</th><th>Train↔Val</th><th>Best</th><th>Verdict</th></tr></thead><tbody>'+runs.map(r=>'<tr>'+[r.label+' / '+r.runId,r.meanValidation,r.medianValidation,r.positiveBots,r.population,r.avgTradesPerBot,r.tradesValidationCorrelation,r.trainValidationCorrelation,r.bestValidation,r.verdict].map(x=>'<td>'+esc(x)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
  renderJobs(data.jobs||[]);
  show((data.warnings||[]).length ? '목록 주의: '+data.warnings.join(' · ') : '목록 갱신 완료. 실행/기간을 선택하고 Export를 생성하세요.');
 }
 function renderJobs(jobs) {
  document.getElementById('exportJobs').innerHTML=jobs.map(j=>`<div class="export-job"><b>${esc(j.type)} · ${esc(j.state)}</b><span>${esc(j.progressPct)}% · ${esc(j.filesProcessed)}/${esc(j.totalFiles)} 파일 · ${bytes(j.bytesProcessed)} 처리 · 예상 ${bytes(j.estimatedBytes)} · Archive ${bytes(j.archiveBytes)}</span>${j.error?'<strong>'+esc(j.error)+'</strong>':''}${j.bundleEvidence&&j.bundleEvidence.completeEvidence===false?'<strong>부분 Bundle: 2022~2025 전체 단계/연결 증거를 확인하지 못했습니다.</strong>':''}${j.state==='completed'?'<button data-export="download" data-job="'+esc(j.jobId)+'">다운로드</button>':''}</div>`).join('')||'<p>Export 작업이 없습니다.</p>';
 }
 const checked = name => Array.from(root.querySelectorAll('input[name="'+name+'"]:checked')).map(x=>x.value);
 async function create(type) {
  const body={type};
  if (type==='raw') {
   body.type=document.getElementById('exportType').value; body.symbols=checked('exportSymbol');
   body.from=document.getElementById('exportFrom').value; body.to=document.getElementById('exportTo').value;
   if (!body.symbols.length||!body.from||!body.to) throw new Error('종목과 From/To 날짜를 명시적으로 선택하세요.');
   if (body.to<body.from) throw new Error('날짜 범위가 잘못되었습니다.');
   if (body.type==='toss_ticks'&&(Date.parse(body.to)-Date.parse(body.from))/86400000>30) throw new Error('Tick은 31일 이내로 나누어 Export하세요.');
  } else if (type.startsWith('evolution')||type==='V07_FULL_RESEARCH_BUNDLE') {
   const ids=checked('exportRun'); if(ids.length) body.runIds=ids;
  }
  const job=await request('/api/research/export/jobs',{method:'POST',body:JSON.stringify(body)});
  if(!catalog) catalog={jobs:[]}; catalog.jobs=[job,...(catalog.jobs||[])]; renderJobs(catalog.jobs);
  show('Export 생성 요청 완료. 원본은 변경되지 않습니다.'); poll();
 }
 async function poll() {
  clearTimeout(timer);
  try {
   const active=(catalog?.jobs||[]).filter(j=>['queued','running'].includes(j.state));
   if(!active.length) return;
   const updates=await Promise.all(active.map(j=>request('/api/research/export/jobs/'+j.jobId)));
   catalog.jobs=catalog.jobs.map(j=>updates.find(u=>u.jobId===j.jobId)||j);renderJobs(catalog.jobs);
   if(catalog.jobs.some(j=>['queued','running'].includes(j.state))) timer=setTimeout(poll,2000);
  } catch(e) { show(e.message+' · 목록 갱신으로 다시 확인하세요.'); }
 }
 function link(url,name) { const a=document.createElement('a');a.href=url;a.download=name||'';a.rel='noreferrer';document.body.appendChild(a);a.click();a.remove(); }
 async function summary(format) {
  const headers=settingsHeaders(); if(!headers.Authorization) throw new Error('관리자 로그인 필요');
  const res=await fetch(base()+'/api/research/export/summary?format='+format,{headers});
  if(!res.ok) throw new Error((await res.json()).error||'요약 다운로드 실패');
  const url=URL.createObjectURL(await res.blob());link(url,'evolution_research_summary.'+format);setTimeout(()=>URL.revokeObjectURL(url),30000);
 }
 root.addEventListener('click',async event=>{
  const button=event.target.closest('[data-export]'); if(!button) return;
  const action=button.dataset.export;button.disabled=true;
  try {
   if(action==='refresh') {renderCatalog(await request('/api/research/export/catalog'));poll();}
   else if(action.startsWith('summary-')) await summary(action.split('-')[1]);
   else if(action==='download') { const t=await request('/api/research/export/jobs/'+button.dataset.job+'/ticket',{method:'POST',body:'{}'});link(base()+t.downloadUrl); }
   else await create(action);
  } catch(e) {show(e.message);} finally {button.disabled=false;}
 });
})();
