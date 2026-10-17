/* V0.8.2 research-only admin controller. Legacy V0.7 remains available for audit compatibility. */
(()=>{
"use strict";
const get=id=>document.getElementById(id);
const write=(id,value)=>{const node=get(id);if(node)node.textContent=value};
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const numeric=v=>typeof v==="number"&&Number.isFinite(v)?v:null;
const pct=v=>numeric(v)==null?"데이터 없음":(v*100).toFixed(2)+"%";
const number=v=>numeric(v)==null?"—":String(v);
const phases={not_started:"대기",starting:"시작 중",running:"실행 중",completed:"완료",failed:"실패",interrupted:"중단"};
let busy=false,active=false,supported=false;
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
  const activeStage=Object.entries(stages).find(([k,v])=>["running","starting"].includes(v?.phase));
  const lastStage=Object.entries(stages).filter(([k,v])=>v?.phase&&v.phase!=="not_started").sort((a,b)=>(a[1].startedEpoch||0)-(b[1].startedEpoch||0)).at(-1);
  const stageName=activeStage?.[0]||lastStage?.[0]||"대기";
  const run=runs[stageName]||{};
  const measured=(d.capacity?.trials||[]).filter(t=>t.label!=="warmup").map(t=>t.resourceReport);
  const measuredTime=measured.length===2&&measured.every(r=>numeric(r.meanSessionSeconds)!=null)?measured.reduce((s,r)=>s+r.meanSessionSeconds,0)/2:null;
  const processed=numeric(run.completedSessions),requested=numeric(run.requestedSessions);
  const latest=gens.length?gens[gens.length-1]:null;
  const cells=[["현재 단계",stageName,"실행: "+(activeStage?.[1]?.phase||lastStage?.[1]?.phase||"대기")],
    ["진행 거래일",processed==null?"—":processed+" / "+(requested??"?"),"서버 보고 기준"],
    ["완료 세대",number(run.completedTrainGenerations),"연속 유효 5거래일"],
    ["수익 부모",number(latest?.parents),"최신 완료 창"],
    ["수익 봇 비율",latest?pct(latest.profitableBotRatio):"—","최신 5거래일 평가 창"],
    ["자식 생성",number(latest?.offspringCount),"최신 완료 창"],
    ["RANDOM_RECOVERY",latest?.recovery==null?"—":latest.recovery?"발생":"미발생","최신 완료 창"],
    ["유전자 다양성",number(latest?.uniqueGenomes),"서로 다른 유전자 수"],
    ["최신 평균 수익",latest?pct(latest.meanReturn):"—","비용 차감 기준"],
    ["초기 시험 봇",number(d.initialPopulation),"실제 시험은 50개체"],
    ["현재 학습 봇",number(run.population),"실행 중 조용한 증감 없음"],
    ["측정 권장 봇",number(d.capacity?.population),"통과한 반복 시험의 최대값"],
    ["설정 최대 봇",number(d.maxPopulation),"관리자 설정 상한"],
    ["CPU / RAM / Swap",[d.resources?.cpuPct,d.resources?.memoryPct,d.resources?.swapPct].map(v=>numeric(v)==null?"—":v.toFixed(1)+"%").join(" / "),"서버 관측값 · 목표 75 / 65 / 3% 이하"],
    ["거래일 시험 추정",measuredTime==null?"—":measuredTime.toFixed(2)+"s","권장 개체 수·동일 시험 구간 기준"],
    ["거래일 실제 시간",run.resourceReport?.meanSessionSeconds==null?"—":run.resourceReport.meanSessionSeconds.toFixed(2)+"s","동일 데이터 관측 평균; 완료 시각 예측 아님"],
    ["원금 보존 부모",latest?.preservationParents==null?"—":latest.preservationParents?"사용":"미사용","유효 평가 결과에서만 선발"],
    ["2026 Final OOS","CLOSED","학습·검증 불가"]];
  get("v082WebStats").innerHTML=cells.map(([a,b,c])=>'<div class="v082-tile"><div class="label">'+esc(a)+'</div><div class="v082-value">'+esc(b)+'</div><div class="v082-muted">'+esc(c)+'</div></div>').join("");
  get("v082WebStages").innerHTML=["smoke","calibrate","train","holdout","validation","export"].map(k=>{
    const st=stages[k]||{},phase=st.phase||"not_started";
    return '<div class="v082-tile"><strong>'+esc(k)+'</strong><div>'+esc(phases[phase]||phase)+'</div><div class="v082-muted">'+esc(st.error||"")+'</div></div>';
  }).join("");
  chart("v082ReturnChart","v082ReturnSummary",gens,"meanReturn",pct);
  chart("v082MddChart","v082MddSummary",gens,"meanMdd",pct);
  chart("v082FitnessChart","v082FitnessSummary",gens,"meanFitness",v=>v.toFixed(4));
  write("v082WebLineage",gens.map(g=>"세대 "+g.generation+" · "+(g.dates||[]).join(" ~ ")+" · 평균 수익 "+pct(g.meanReturn)+" · MDD "+pct(g.meanMdd)+" · fitness "+(numeric(g.meanFitness)==null?"—":g.meanFitness.toFixed(4))+" · 부모 "+number(g.parents)+" · 자식 "+number(g.offspringCount)+" · 엘리트 "+number(g.eliteCount)+" · 변이 유전자 "+number(g.mutationCount)+" · 유전자 "+number(g.uniqueGenomes)+" · 최대 부모 기여 "+pct(g.maxParentContribution)+" · 보존 부모 "+(g.preservationParents?"예":"아니오")+" · 부모 순손익 "+(g.parentSelection||[]).map(p=>p.botId+":"+p.netPnl).join(", ")+" · crossover "+number(g.crossoverCount)).join("\n")||"완료된 세대 없음");
  const downloading=Boolean(d.downloadAvailable);
  get("v082WebDownload").disabled=!downloading;
  document.querySelectorAll("[data-v082-retry]").forEach(b=>b.disabled=busy||!["failed","interrupted"].includes(stages[b.dataset.v082Retry]?.phase));
  document.querySelectorAll("[data-v082]").forEach(b=>{const k=b.dataset.v082; const prior={calibrate:"smoke",train:"calibrate",holdout:"train",validation:"holdout",export:"validation"}[k];
    b.disabled=Boolean(busy||Boolean(activeStage)||(stages[k]?.phase&&stages[k].phase!=="not_started")||(prior&&stages[prior]?.phase!=="completed"));
  });
  write("v082WebPriorDiagnosis",JSON.stringify(d.priorZeroGeneration||{available:false,reason:"서버 제공 자료 없음"},null,2));
  write("v082WebCapacity",JSON.stringify({recommendation:d.capacity?.recommendation,currentTrial:d.capacity?.currentTrial,trials:d.capacity?.trials,resourceReport:run.resourceReport},null,2)||"아직 측정하지 않음");
  // A stage's exit code alone hides the measured guard failure.
  // Surface the actual failed sample; never label an unsafe trial as successful.
  if(stages.calibrate?.phase==="failed"){
    const trials=Array.isArray(d.capacity?.trials)?d.capacity.trials:[];
    const readings=trials.map(t=>t.resourceReport).filter(Boolean);
    const failure=readings.map(t=>t.failure||t.error).filter(Boolean).at(-1);
    const decision=d.capacity?.status||"NO_RECOMMENDATION";
    write("v082WebFeedback","자원 산정 실패 · "+(failure||stages.calibrate.error||"상세 측정값 없음")+
      "\n산정 상태: "+decision+
      "\n허용 CPU 75% / RAM 65% / Swap 3% — 기준을 완화하지 않았습니다."+
      "\n실패한 자원 산정만 재실행할 수 있으며, 실행 전 서버 부하를 확인하세요. 기존 결과는 보존됩니다.");
  }
  const analysis=run.indicatorAnalysis||{};
  const items=Object.values(analysis);
  const value=v=>numeric(v)==null?"—":v.toFixed(3);
  get("v082SymbolAnalysis").innerHTML=items.length?items.map(x=>{
    const f=x.indicators||{},p=x.portfolio||{};
    const contributions=Object.entries(x.contributions||{}).filter(([,v])=>numeric(v)!=null).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,5);
    return '<details class="v082-tile"><summary>'+esc(x.symbol)+' · '+esc(x.decision)+' · '+esc(x.reason)+'</summary><div class="v082-muted">역사 데이터 · '+esc(x.decisionTimestamp)+' · bot '+esc(x.botId)+' · 승률 추정 아님</div><p>RSI '+value(f.rsi14)+' · MACD '+value(f.macd)+' · VWAP '+value(f.vwap)+' · ATR '+value(f.atr14)+'</p><p>추세 '+esc(f.trend||"데이터 없음")+' · 충돌 '+(f.conflict===true?"있음":f.conflict===false?"없음":"집계 중")+' · 지표 준비 '+(f.ready===true?"완료":"부족")+'</p><p>보유 '+number(p.held)+' / 현재 후보 기준 허용 '+number(p.allowedPositions)+' · 신규 '+number(p.newEntriesAllowed)+' · 현금 $'+value(p.cash)+' · 권장 금액 $'+value(p.proposedBudget)+' · 손절 위험 $'+value(p.stopRisk)+' · 실질 노출 $'+value(p.effectiveGrossExposure)+'</p><p>주요 지표 기여: '+contributions.map(([k,v])=>esc(k)+" "+value(v)).join(" / ")+'</p><pre>'+esc(JSON.stringify({indicators:f,contributions:x.contributions,portfolio:p,expectedRoundTripCostBps:x.expectedRoundTripCostBps},null,2))+'</pre></details>';
  }).join(""):"집계 중 / 데이터 없음";
  write("v082WebRaw",JSON.stringify(d,null,2));
}
async function call(command){const d=await req("/api/admin/console",{method:"POST",body:JSON.stringify({command})});if(d?.ok===false||d?.exitCode)throw Error(d?.output||d?.error||"API 요청 실패");return d?.output||""}
async function refresh(){
  // Archived UI is hidden: historical reports are loaded on demand by Past.
  if(get("legacyResearchRuntime")?.hidden)return;
  if(busy||active||!token())return;
  active=true;
  try{
    const [report,log]=await Promise.all([call("v082 report"),call("v082 log")]);
    let data;try{data=JSON.parse(report)}catch{throw Error("V0.8.2 보고서 JSON 형식 오류")}
    if(data.protocol!=="V082_WEB_1")throw Error("V082_BACKEND_UNSUPPORTED: 서버 버전 불일치");
    supported=true;render(data);write("v082WebLog",log.split("\n").slice(-35).join("\n")||"로그 없음");
  }catch(e){
    supported=false;document.querySelectorAll("[data-v082],[data-v082-retry]").forEach(b=>b.disabled=true);get("v082WebDownload").disabled=true;
    write("v082WebFeedback","V0.8.2 상태 조회 실패: "+e.message+"\n운영 EC2에 V0.8.2 백엔드가 적용됐는지 확인하세요. 기존 V0.7/V0.8 결과는 보존됩니다.");
    write("v082WebStats","서버 연동 전 · "+e.message);
  }finally{active=false}
}
async function action(stage,retry=false){
 if(busy)return;
 if(!supported){write("v082WebFeedback","V082_BACKEND_UNSUPPORTED: 서버 지원 확인 전 실행할 수 없습니다.");return;}
 if(!confirm("V0.8.2 "+(retry?"재실행: ":"실행: ")+stage+"\n연구 전용 · 기존 V0.7 데이터 보존 · 2026 OOS CLOSED\n서버에서 실제 작업을 시작할까요?"))return;
 busy=true;
 document.querySelectorAll("[data-v082],[data-v082-retry]").forEach(b=>b.disabled=true);
 const command="v082 "+(retry?"retry ":"")+stage;
 write("v082WebFeedback",command+" · 서버 응답 확인 중");
 try{const output=await call(command);write("v082WebFeedback",command+" · 서버 응답:\n"+output)}
 catch(e){write("v082WebFeedback",command+" · 실행 차단/실패:\n"+e.message+"\n기존 연구 작업과 데이터는 유지됩니다.")}
 finally{busy=false;await refresh()}
}
async function download(){
 const b=get("v082WebDownload");if(!b||b.disabled)return;
 b.disabled=true;
 try{
  const response=await fetch(API+"/api/research/v082/download",{headers:{Authorization:"Bearer "+token()}});
  if(!response.ok)throw Error("다운로드 실패: HTTP "+response.status);
  const url=URL.createObjectURL(await response.blob());
  const a=document.createElement("a");a.href=url;a.download="v082-research-results.zip";document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }catch(e){write("v082WebFeedback",e.message)}
 finally{await refresh()}
}
get("v082WebRefresh")?.addEventListener("click",refresh);
get("v082WebDownload")?.addEventListener("click",download);
document.querySelectorAll("[data-v082]").forEach(b=>b.addEventListener("click",()=>action(b.dataset.v082)));
document.querySelectorAll("[data-v082-retry]").forEach(b=>b.addEventListener("click",()=>action(b.dataset.v082Retry,true)));
document.querySelectorAll('[data-tab="ai"],[data-group="ai"]').forEach(b=>b.addEventListener("click",()=>{Promise.resolve().then(refresh)}));
if(location.hash==="#ai")refresh();
setInterval(()=>{if(get("ai")?.classList.contains("active"))refresh()},15000);
})();
