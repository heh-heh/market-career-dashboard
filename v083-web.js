/* V0.8.3 research-only admin controller. Legacy V0.7 remains available for audit compatibility. */
(()=>{
"use strict";
const get=id=>document.getElementById(id);
const write=(id,value)=>{const node=get(id);if(node)node.textContent=value};
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const numeric=v=>typeof v==="number"&&Number.isFinite(v)?v:null;
const pct=v=>numeric(v)==null?"데이터 없음":(v*100).toFixed(2)+"%";
const number=v=>numeric(v)==null?"—":String(v);
const phases={not_started:"대기",starting:"시작 중",running:"실행 중",completed:"완료",failed:"실패",interrupted:"중단"};
let busy=false,active=false,supported=false, userEditedPopulation=false;
const populationInput=get("v083PopulationInput");
function selectedPopulation(){
  const raw=populationInput?.value?.trim()||"";
  const n=Number(raw);
  if(!/^[0-9]{1,2}$/.test(raw)||!Number.isInteger(n)||n<2||n>50)throw Error("봇 수는 2~50 사이 정수로 입력해 주세요.");
  return n;
}
populationInput?.addEventListener("input",()=>{userEditedPopulation=true});
function chart(svgId,summaryId,rows,key,fmt){
  const svg=get(svgId),values=rows.map(x=>numeric(x[key]));
  if(!svg)return;
  const good=values.filter(v=>v!=null);
  if(!good.length){svg.innerHTML='<text x="24" y="78" fill="currentColor">완료 세대 데이터 없음</text>';write(summaryId,"데이터 없음");return;}
  const lo=Math.min(0,...good),hi=Math.max(0,...good),span=(hi-lo)||1;
  const x=i=>rows.length===1?300:28+(i/(rows.length-1))*544;
  const y=v=>135-(v-lo)/span*110;
  let parts=['<line x1="28" y1="'+y(0).toFixed(1)+'" x2="572" y2="'+y(0).toFixed(1)+'" stroke="currentColor" opacity=".25"/>'];
  let segment=[];
  const flush=()=>{if(segment.length>1)parts.push('<polyline fill="none" stroke="#42c9ad" stroke-width="2" points="'+segment.join(" ")+'"/>');else if(segment.length)parts.push('<circle cx="'+segment[0].split(",")[0]+'" cy="'+segment[0].split(",")[1]+'" r="3" fill="#42c9ad"/>');segment=[]};
  values.forEach((v,i)=>{if(v==null){flush();return;}segment.push(x(i).toFixed(1)+","+y(v).toFixed(1));});
  flush();svg.innerHTML=parts.join("");
  write(summaryId,good.length+"개 완료 세대 · 마지막 "+fmt(good[good.length-1]));
}
function render(d){
  const stages=d.stages||{},runs=d.runs||{},gens=Array.isArray(d.generations)?d.generations:[];
  if(populationInput&&!userEditedPopulation)populationInput.value=String(d.selectedPopulation??50);
  write("v083PopulationHint","현재 실험: "+(d.jobId||"없음")+" · 저장된 개체 수 "+number(d.selectedPopulation??50)+"개 · 변경한 수는 새 실험에서만 적용됩니다. 신규 실험은 무수익 세대에서도 순방향 진행합니다. CPU 75% 보호 유지.");
  const activeStage=Object.entries(stages).find(([k,v])=>["running","starting"].includes(v?.phase));
  const lastStage=Object.entries(stages).filter(([k,v])=>v?.phase&&v.phase!=="not_started").sort((a,b)=>(a[1].startedEpoch||0)-(b[1].startedEpoch||0)).at(-1);
  const stageName=activeStage?.[0]||lastStage?.[0]||"대기";
  const run=runs[stageName]||{};
  const measured=(d.capacity?.trials||[]).filter(t=>t.label!=="warmup").map(t=>t.resourceReport);
  const measuredTime=measured.length===2&&measured.every(r=>numeric(r.meanSessionSeconds)!=null)?measured.reduce((s,r)=>s+r.meanSessionSeconds,0)/2:null;
  const processed=numeric(run.completedSessions),requested=numeric(run.requestedSessions);
  const latest=gens.length?gens[gens.length-1]:null;
  const cells=[["현재 단계",stageName,"실행: "+(activeStage?.[1]?.phase||lastStage?.[1]?.phase||"대기")],
    ["진행 거래일",processed==null?"—":processed+" / "+(requested??"?"),"현재 역사적 날짜의 진행도 · 재시작 시 다시 1일부터"],
    ["실제 처리 거래일",number(run.totalProcessedSessions),"최초 날짜 재탐색으로 다시 처리한 거래일 포함"],
    ["완료 세대",number(run.completedTrainGenerations),"연속 유효 5거래일"],
    ["선발 부모",number(latest?.parents),"항상 상위 2개 · 유효 5거래일"],
    ["수익 봇 비율",latest?pct(latest.profitableBotRatio):"—","최신 5거래일 평가 창"],
    ["자식 생성",number(latest?.offspringCount),"최신 완료 창"],
    ["무수익 세대",number(run.noProfitGenerations),run.evolutionPolicy==="V083_FORWARD_ONLY_NO_PROFIT_TOP_TWO"?"상위 2개 번식 후 다음 거래일 진행":"과거 실험은 재시작 정책 사용"],
    ["학습 날짜 정책",run.evolutionPolicy==="V083_FORWARD_ONLY_NO_PROFIT_TOP_TWO"?"순방향 진행":"과거 실험 / 초기 날짜 재시작 기록","새 실험에서는 처리한 거래일을 되돌리지 않음"],
    ["유전자 다양성",number(latest?.uniqueGenomes),"서로 다른 유전자 수"],
    ["최신 평균 수익",latest?pct(latest.meanReturn):"—","비용 차감 기준"],
    ["초기 시험 봇",number(d.initialPopulation),"캠페인별 고정 개체 수"],
    ["현재 학습 봇",number(run.population),"실행 중 조용한 증감 없음"],
    ["측정 권장 봇",number(d.capacity?.population),"통과한 반복 시험의 최대값"],
    ["설정 최대 봇",number(d.maxPopulation),"수동 입력 상한 · 최저 2개"],
    ["CPU / RAM / Swap",[d.resources?.cpuPct,d.resources?.memoryPct,d.resources?.swapPct].map(v=>numeric(v)==null?"—":v.toFixed(1)+"%").join(" / "),"서버 관측값 · 목표 75 / 65 / 3% 이하"],
    ["거래일 시험 추정",measuredTime==null?"—":measuredTime.toFixed(2)+"s","권장 개체 수·동일 시험 구간 기준"],
    ["거래일 실제 시간",run.resourceReport?.meanSessionSeconds==null?"—":run.resourceReport.meanSessionSeconds.toFixed(2)+"s","동일 데이터 관측 평균; 완료 시각 예측 아님"],
    ["원금 보존 부모",latest?.preservationParents==null?"—":latest.preservationParents?"사용":"미사용","유효 평가 결과에서만 선발"],
    ["2026 Final OOS","CLOSED","학습·검증 불가"]];
  get("v083WebStats").innerHTML=cells.map(([a,b,c])=>'<div class="v083-tile"><div class="label">'+esc(a)+'</div><div class="v083-value">'+esc(b)+'</div><div class="v083-muted">'+esc(c)+'</div></div>').join("");
  get("v083WebStages").innerHTML=["smoke","calibrate","train","holdout","validation","export"].map(k=>{
    const st=stages[k]||{},phase=st.phase||"not_started";
    return '<div class="v083-tile"><strong>'+esc(k)+'</strong><div>'+esc(phases[phase]||phase)+'</div><div class="v083-muted">'+esc(st.error||"")+'</div></div>';
  }).join("");
  chart("v083ReturnChart","v083ReturnSummary",gens,"meanReturn",pct);
  chart("v083MddChart","v083MddSummary",gens,"meanMdd",pct);
  chart("v083FitnessChart","v083FitnessSummary",gens,"meanFitness",v=>v.toFixed(4));
  write("v083WebLineage",gens.map(g=>"세대 "+g.generation+" · "+(g.dates||[]).join(" ~ ")+" · 평균 수익 "+pct(g.meanReturn)+" · MDD "+pct(g.meanMdd)+" · fitness "+(numeric(g.meanFitness)==null?"—":g.meanFitness.toFixed(4))+" · 부모 "+number(g.parents)+" · 자식 "+number(g.offspringCount)+" · 엘리트 "+number(g.eliteCount)+" · 변이 유전자 "+number(g.mutationCount)+" · 유전자 "+number(g.uniqueGenomes)+" · 최대 부모 기여 "+pct(g.maxParentContribution)+" · 무수익 재탐색 "+(g.preservationParents?"예":"아니오")+" · 부모 순손익 "+(g.parentSelection||[]).map(p=>p.botId+":"+p.netPnl).join(", ")+" · crossover "+number(g.crossoverCount)).join("\n")||"완료된 세대 없음");
  const downloading=Boolean(d.downloadAvailable);
  get("v083WebDownload").disabled=!downloading;
  document.querySelectorAll("[data-v083-retry]").forEach(b=>b.disabled=busy||!["failed","interrupted"].includes(stages[b.dataset.v083Retry]?.phase));
  document.querySelectorAll("[data-v083]").forEach(b=>{const k=b.dataset.v083; const prior={calibrate:"smoke",train:"calibrate",holdout:"train",validation:"holdout",export:"validation"}[k];
    b.disabled=Boolean(busy||Boolean(activeStage)||(stages[k]?.phase&&stages[k].phase!=="not_started")||(prior&&stages[prior]?.phase!=="completed"));
  });
  write("v083WebPriorDiagnosis",JSON.stringify(d.priorZeroGeneration||{available:false,reason:"서버 제공 자료 없음"},null,2));
  write("v083WebCapacity",JSON.stringify({recommendation:d.capacity?.recommendation,currentTrial:d.capacity?.currentTrial,trials:d.capacity?.trials,resourceReport:run.resourceReport},null,2)||"아직 측정하지 않음");
  // A stage's exit code alone hides the measured guard failure.
  // Surface the actual failed sample; never label an unsafe trial as successful.
  if(stages.calibrate?.phase==="failed"){
    const trials=Array.isArray(d.capacity?.trials)?d.capacity.trials:[];
    const readings=trials.map(t=>t.resourceReport).filter(Boolean);
    const failure=readings.map(t=>t.failure||t.error).filter(Boolean).at(-1);
    const decision=d.capacity?.status||"NO_RECOMMENDATION";
    write("v083WebFeedback","자원 산정 실패 · "+(failure||stages.calibrate.error||"상세 측정값 없음")+
      "\n산정 상태: "+decision+
      "\n허용 CPU 75% / RAM 65% / Swap 3% — 기준을 완화하지 않았습니다."+
      "\n실패한 자원 산정만 재실행할 수 있으며, 실행 전 서버 부하를 확인하세요. 기존 결과는 보존됩니다.");
  }
  // V083_RUN_EXIT_2 is a wrapper from the worker. The research run's
  // recorded error explains whether the guard or the restart policy stopped it.
  if(stages.train?.phase==="failed"||stages.train?.phase==="interrupted"){
    const failed=runs.train||{}, reason=String(failed.error||stages.train.error||"상세 원인 미확인");
    const cpu=numeric(failed.resourceReport?.peak?.cpuPct);
    const observedCpu=cpu==null?"CPU 최고값 확인 불가":"학습 CPU 최고 "+cpu.toFixed(1)+"% / 제한 75%";
    if(reason.includes("V083_RESTART_LIMIT_REACHED")){
      write("v083WebFeedback","학습 중단: 수익 봇이 없는 5거래일 평가 구간이 반복되어 최초 날짜 재시작 한도에 도달했습니다."+
        "\n재시작 "+number(failed.restarts)+" / "+number(failed.restartLimit??2)+"회 · 실제 처리 "+number(failed.totalProcessedSessions)+"거래일 · 완료 "+number(failed.completedTrainGenerations)+"세대."+
        "\n"+observedCpu+" · 이번 중단 원인은 CPU 초과가 아닙니다."+
        "\n기존 체크포인트와 평가 기록은 보존됐습니다. 반복 학습을 자동 실행하지 않으며 진화 정책을 검토해야 합니다.");
    }else{
      write("v083WebFeedback","V0.8.3 학습 중단 · "+reason+"\n"+observedCpu+
        "\n출구 코드 "+String(stages.train.error||"없음")+" · 체크포인트/기록은 보존됩니다. 상세 로그에서 원인을 확인하세요.");
    }
  }
  const analysis=run.indicatorAnalysis||{};
  const items=Object.values(analysis);
  const value=v=>numeric(v)==null?"—":v.toFixed(3);
  get("v083SymbolAnalysis").innerHTML=items.length?items.map(x=>{
    const f=x.indicators||{},p=x.portfolio||{};
    const contributions=Object.entries(x.contributions||{}).filter(([,v])=>numeric(v)!=null).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,5);
    return '<details class="v083-tile"><summary>'+esc(x.symbol)+' · '+esc(x.decision)+' · '+esc(x.reason)+'</summary><div class="v083-muted">역사 데이터 · '+esc(x.decisionTimestamp)+' · bot '+esc(x.botId)+' · 승률 추정 아님</div><p>RSI '+value(f.rsi14)+' · MACD '+value(f.macd)+' · VWAP '+value(f.vwap)+' · ATR '+value(f.atr14)+'</p><p>추세 '+esc(f.trend||"데이터 없음")+' · 충돌 '+(f.conflict===true?"있음":f.conflict===false?"없음":"집계 중")+' · 지표 준비 '+(f.ready===true?"완료":"부족")+'</p><p>보유 '+number(p.held)+' / 현재 후보 기준 허용 '+number(p.allowedPositions)+' · 신규 '+number(p.newEntriesAllowed)+' · 현금 $'+value(p.cash)+' · 권장 금액 $'+value(p.proposedBudget)+' · 손절 위험 $'+value(p.stopRisk)+' · 실질 노출 $'+value(p.effectiveGrossExposure)+'</p><p>주요 지표 기여: '+contributions.map(([k,v])=>esc(k)+" "+value(v)).join(" / ")+'</p><pre>'+esc(JSON.stringify({indicators:f,contributions:x.contributions,portfolio:p,expectedRoundTripCostBps:x.expectedRoundTripCostBps},null,2))+'</pre></details>';
  }).join(""):"집계 중 / 데이터 없음";
  write("v083WebRaw",JSON.stringify(d,null,2));
}
async function call(command){const d=await req("/api/admin/console",{method:"POST",body:JSON.stringify({command})});if(d?.ok===false||d?.exitCode)throw Error(d?.output||d?.error||"API 요청 실패");return d?.output||""}
async function refresh(){
  if(busy||active||!token())return;
  active=true;
  try{
    const [report,log]=await Promise.all([call("v083 report"),call("v083 log")]);
    let data;try{data=JSON.parse(report)}catch{throw Error("V0.8.3 보고서 JSON 형식 오류")}
    if(data.protocol!=="V083_WEB_1")throw Error("V083_BACKEND_UNSUPPORTED: 서버 버전 불일치");
    supported=true;render(data);write("v083WebLog",log.split("\n").slice(-35).join("\n")||"로그 없음");
  }catch(e){
    supported=false;document.querySelectorAll("[data-v083],[data-v083-retry]").forEach(b=>b.disabled=true);get("v083WebDownload").disabled=true;
    write("v083WebFeedback","V0.8.3 상태 조회 실패: "+e.message+"\n운영 EC2에 V0.8.3 백엔드가 적용됐는지 확인하세요. 기존 V0.7/V0.8 결과는 보존됩니다.");
    write("v083WebStats","서버 연동 전 · "+e.message);
  }finally{active=false}
}
async function action(stage,retry=false){
 if(busy)return;
 if(!supported){write("v083WebFeedback","V083_BACKEND_UNSUPPORTED: 서버 지원 확인 전 실행할 수 없습니다.");return;}
 let chosen=null;
 try{if(!retry&&(stage==="smoke"||stage==="new"))chosen=selectedPopulation()}catch(e){write("v083WebFeedback",e.message);populationInput?.focus?.();return;}
 if(!confirm("V0.8.3 "+(retry?"재실행: ":"실행: ")+stage+(chosen==null?"":" · "+chosen+"개 봇")+"\n연구 전용 · 기존 결과 보존 · 2026 OOS CLOSED\n서버에서 실제 작업을 시작할까요?"))return;
 busy=true;
 document.querySelectorAll("[data-v083],[data-v083-retry]").forEach(b=>b.disabled=true);
 const command="v083 "+(retry?"retry ":"")+stage+(chosen==null?"":" "+chosen);
 write("v083WebFeedback",command+" · 서버 응답 확인 중");
 try{const output=await call(command);write("v083WebFeedback",command+" · 서버 응답:\n"+output)}
 catch(e){write("v083WebFeedback",command+" · 실행 차단/실패:\n"+e.message+"\n기존 연구 작업과 데이터는 유지됩니다.")}
 finally{busy=false;await refresh()}
}
async function download(){
 const b=get("v083WebDownload");if(!b||b.disabled)return;
 b.disabled=true;
 try{
  const response=await fetch(API+"/api/research/v083/download",{headers:{Authorization:"Bearer "+token()}});
  if(!response.ok)throw Error("다운로드 실패: HTTP "+response.status);
  const url=URL.createObjectURL(await response.blob());
  const a=document.createElement("a");a.href=url;a.download="v083-research-results.zip";document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }catch(e){write("v083WebFeedback",e.message)}
 finally{await refresh()}
}
get("v083WebRefresh")?.addEventListener("click",refresh);
get("v083WebDownload")?.addEventListener("click",download);
document.querySelectorAll("[data-v083]").forEach(b=>b.addEventListener("click",()=>action(b.dataset.v083)));
document.querySelectorAll("[data-v083-retry]").forEach(b=>b.addEventListener("click",()=>action(b.dataset.v083Retry,true)));
document.querySelectorAll('[data-tab="ai"],[data-group="ai"]').forEach(b=>b.addEventListener("click",()=>{Promise.resolve().then(refresh)}));
if(location.hash==="#ai")refresh();
setInterval(()=>{if(get("ai")?.classList.contains("active"))refresh()},15000);
})();
