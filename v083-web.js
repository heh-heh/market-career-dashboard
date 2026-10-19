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
let spotPlan=null,spotRequest=null,spotBusy=false;
async function spotApi(action,body){
 const d=await req("/api/research/v083/spot/"+action,body?{method:"POST",body:JSON.stringify(body)}:undefined);
 if(d?.ok===false)throw Error(d.error||"Spot 요청 차단");
 if(d?.protocol&&d.protocol!=="V083_SPOT_CONTROL_2")throw Error("SPOT_BACKEND_UNSUPPORTED");
 return d;
}
function spotButtons(){
 const ready=Boolean(spotPlan?.runnable&&supported&&!busy&&!spotBusy);
 const blocked=["STARTING","REQUESTED_ONE_TIME_SPOT","CREATE_UNKNOWN","RUNNING","STOP_REQUESTED"].includes(spotRequest?.phase);
 if(get("v083SpotPrepare"))get("v083SpotPrepare").disabled=!ready||blocked;
 if(get("v083SpotApprove"))get("v083SpotApprove").disabled=!ready||spotRequest?.phase!=="PREPARED";
 if(get("v083SpotLaunch"))get("v083SpotLaunch").disabled=!ready||spotRequest?.phase!=="APPROVED";
 if(get("v083SpotStop"))get("v083SpotStop").disabled=spotBusy||!blocked;
}
const spotPhaseLabel=p=>({PREPARED:"준비 완료",APPROVED:"비용 승인됨",STARTING:"서버 준비 중",REQUESTED_ONE_TIME_SPOT:"서버 생성 접수",RUNNING:"학습 서버 실행 중",COMPLETED:"학습 완료",FAILED:"실패",INTERRUPTED:"학습 중단",CREATE_UNKNOWN:"AWS 생성 상태 확인 필요",STOP_REQUESTED:"종료 요청 중"}[p]||"대기");
const spotErrorLabel=e=>({SPOT_JOB_ALREADY_ATTEMPTED_NEW_APPROVAL_AND_JOB_REQUIRED:"이 작업 ID는 이미 사용됐습니다. 과거 결과를 보존하고 새 캠페인·새 작업 ID로 승인하세요.",SPOT_TERMINATED_WITHOUT_DURABLE_FINAL_RESULT:"서버 종료는 확인됐지만 정상 결과 저장은 확인되지 않았습니다.",VcpuLimitExceeded:"Spot vCPU 할당량이 부족합니다.",UnauthorizedOperation:"EC2 생성 권한이 거절됐습니다.",AuthFailure:"AWS 인증이 거절됐습니다.",InsufficientInstanceCapacity:"해당 Spot 사양의 가용 용량이 부족합니다. 자동 재시도하지 않습니다.",Unsupported:"요청한 인스턴스 또는 계정 설정을 지원하지 않습니다.",V083_TRIAL_UNSAFE:"자원 보정이 CPU·RAM·Swap 또는 실행시간 안전조건을 통과하지 못했습니다.",SPOT_CAPACITY_CERTIFICATE_NOT_SAFE:"자원 보정 인증이 안전 조건을 충족하지 못했습니다.",PACKAGE_NOT_AVAILABLE:"Python/AWS 패키지를 설치할 수 없습니다.",ACCESS_DENIED:"S3 또는 IAM 권한이 거절됐습니다.",SHA256_MISMATCH:"다운로드한 번들의 SHA256이 다릅니다.",PYTHON_MODULE_MISSING:"필수 Python 라이브러리가 없습니다.",SPOT_SINGLE_CAPACITY_CERTIFICATE_REQUIRED:"자원 보정 결과 경로를 확인할 수 없습니다."}[String(e||"").replace(/^SPOT_BOOTSTRAP_/,"")]||e||"없음");
const spotStageLabel=s=>({python_packages:"Python 설치",virtualenv:"가상환경 준비",dependencies:"라이브러리 설치",import_check:"라이브러리 검증",bundle_download:"학습 번들 다운로드",bundle_hash:"학습 번들 SHA256 검증",bundle_extract:"학습 번들 압축 해제",runtime_download:"실행 코드 다운로드",runtime_hash:"실행 코드 SHA256 검증",runtime_extract:"실행 코드 압축 해제",calibrate:"반복 자원 보정",train:"학습",worker:"학습 워커 실행"}[s]||s||"미관측");
function renderSpot(d){
 spotPlan=d;if(Object.prototype.hasOwnProperty.call(d,"request"))spotRequest=d.request;
 const reasons=d.blockedReasons||[];
 write("v083SpotStatus",d.configured?("Spot "+(d.runnable?"로컬 설정 확인":"차단")+" · "+spotPhaseLabel(spotRequest?.phase)+" ("+(spotRequest?.phase||"대기")+")"+" · "+(d.instances||[]).map(x=>x.id+" "+x.state).join(", ")+" · "+reasons.join(" / ")):("Spot 미설정 · 시작 차단 · "+reasons.join(" / ")));
 const costs=spotRequest?.costBreakdown||d.costBreakdown;
 write("v083SpotEstimate",costs?"검토 추정: compute $"+costs.compute_usd+" / EBS $"+costs.ebs_usd+" / S3 $"+costs.s3_usd+" / 네트워크 $"+costs.network_usd+" / IPv4 $"+costs.ipv4_usd+" / 기타 $"+costs.other_usd+" · 합계 $"+costs.estimated_total_usd+" · 승인 예산 $"+costs.budget_usd+"\n청구액 하드캡이 아닙니다. 종료 지연·저장·요청·세금 등으로 초과 가능":"검토된 비용 추정 없음 · 유료 시작 비활성");
 const remote=d.remote?.data,train=remote?.training||{};
 const instance=spotRequest?.instanceObservation||spotRequest?.reconciliation?.instances?.[0]||d.instances?.[0];
 const details="\n작업 "+(spotRequest?.jobId||"미준비")+" · 인스턴스 "+(instance?.id||spotRequest?.launch?.instance_id||"미확인")+" · 서버 상태 "+(instance?.state||"미확인")+" · 봇 "+number(spotRequest?.population)+" · 종류 "+(spotRequest?.instanceType||"미확인")+" · 요청 이후 "+(numeric(spotRequest?.elapsedSeconds)==null?"미측정":Math.floor(spotRequest.elapsedSeconds)+"초")+"\n종료 사유 "+(instance?.stateReasonCode||"미확인")+" · 연구 결과 저장 "+(spotRequest?.resultStored?"확인":"미확인")+" · 진단 저장 "+(spotRequest?.diagnosticStored?"확인":"미확인")+"\n마지막 오류: "+spotErrorLabel(spotRequest?.awsErrorCode||spotRequest?.lastError||remote?.errorCode||remote?.errorType);
 get("v083SpotStatus").textContent+=details;
 const calibration=remote?.calibration;

 write("v083SpotProgress",remote?"상태 "+(remote.phase||"—")+" · 단계 "+spotStageLabel(remote.stage||remote.phase)+" · 거래일 "+number(train.completedSessions)+" / "+number(train.requestedSessions)+" · 세대 "+number(train.completedTrainGenerations)+" · 평균 수익 "+pct(train.meanWindowReturn)+" · CPU 최고 "+number(train.resourceReport?.peak?.cpuPct)+"% · RAM 최고 "+number(train.resourceReport?.peak?.memoryPct)+"% · Swap 최고 "+number(train.resourceReport?.peak?.swapPct)+"% · 체크포인트 "+(remote.checkpoint?.manifestKey||remote.checkpoint?.key||"아직 없음")+"\n"+spotErrorLabel(remote.errorCode||remote.errorType||remote.error)+"\n자원 보정 "+(calibration?.status||"관측 없음")+" · 측정 "+number(calibration?.completedTrials)+"회 · "+spotErrorLabel(calibration?.errorCode):(d.remote?.reason||"실제 원격 결과 없음"));
 spotButtons();
}
async function spotInspect(){
 if(spotBusy||!token())return;
 try{const d=await spotApi("status");if(d.protocol!=="V083_SPOT_CONTROL_2")throw Error("SPOT_BACKEND_UNSUPPORTED");renderSpot(d)}
 catch(e){spotPlan=null;spotRequest=null;spotButtons();write("v083SpotStatus","Spot 조회 실패/차단: "+e.message)}
}
async function spotAction(verb){
 if(spotBusy||busy)return;
 if(!spotPlan?.runnable&&verb!=="stop")return;
 if(verb==="approve"&&!confirm("검토된 비용 추정과 초과 가능성을 확인했으며 실제 AWS 과금을 승인합니까?\n이 단계는 서버를 생성하지 않습니다."))return;
 if(verb==="start"&&!confirm("별도 EC2 Spot 1대를 지금 생성하고 학습할까요? 실제 과금됩니다. 기존 서버는 유지됩니다."))return;
 if(verb==="stop"&&!confirm("이 승인 작업의 Spot만 종료할까요? 마지막 완료 체크포인트 이후 진행분은 재평가됩니다."))return;
 spotBusy=true;spotButtons();
 try{
  let body;
  if(verb==="prepare")body={population:selectedPopulation(),instanceType:get("v083SpotInstance")?.value,maxHours:Number(get("v083SpotHours")?.value)};
  else if(verb==="approve")body={requestId:spotRequest.requestId,configHash:spotRequest.configHash,acknowledgeCost:true,confirmation:"APPROVE_PAID_SPOT"};
  else body={requestId:spotRequest.requestId,confirmation:verb==="start"?"CREATE_ONE_PAID_SPOT":"STOP_OWN_SPOT"};
  const response=await spotApi(verb,body);spotRequest=response;
  write("v083SpotStatus","Spot 요청 상태: "+response.phase);
 }catch(e){write("v083SpotStatus","Spot 요청 차단/실패: "+e.message)}
 finally{spotBusy=false;spotButtons();await spotInspect()}
}
async function spotStartOneClick(){
 if(spotBusy||busy)return;
 spotBusy=true;spotButtons();
 try{
  const previous=await spotApi("status");
  renderSpot(previous);
  if(!previous.configured||!previous.runnable)throw Error("서버의 전용 IAM/데이터/자동 종료 점검을 먼저 통과해야 합니다: "+(previous.blockedReasons||[]).join(", "));
  const instanceType=get("v083SpotInstance")?.value;
  const population=selectedPopulation();
  const maxHours=Number(get("v083SpotHours")?.value);
  const ready=await spotApi("prepare",{population,instanceType,maxHours});
  spotRequest=ready;
  const cost=ready.costBreakdown||{};
  write("v083SpotEstimate","작업 추정 총액 $"+cost.estimated_total_usd+" / 최대 허용 $"+cost.budget_usd+" · AWS 청구 하드캡 아님");
  if(!confirm("Spot 연구 학습 요금 승인\n봇 "+population+"개 · "+instanceType+" · "+maxHours+"시간 이내\n예상 총액 $"+cost.estimated_total_usd+" / 목표 최대 $"+cost.budget_usd+"\nAWS 청구액은 예상과 다를 수 있습니다. 계속할까요?"))return;
  const approved=await spotApi("approve",{requestId:ready.requestId,configHash:ready.configHash,acknowledgeCost:true,confirmation:"APPROVE_PAID_SPOT"});
  spotRequest=approved;
  if(!confirm("AWS Spot 서버를 한 대 생성하고 지금 학습할까요? 생성 즉시 과금되며 완료/실패 시 자동 종료됩니다."))return;
  const started=await spotApi("start",{requestId:approved.requestId,confirmation:"CREATE_ONE_PAID_SPOT"});
  spotRequest=started;
  write("v083SpotStatus","Spot 서버 생성 요청: "+started.phase);
 }catch(e){write("v083SpotStatus","Spot 학습 시작 실패/차단: "+e.message)}
 finally{spotBusy=false;spotButtons();await spotInspect()}
}
get("v083SpotStartAll")?.addEventListener("click",spotStartOneClick);
get("v083SpotRefresh")?.addEventListener("click",spotInspect);
get("v083SpotPrepare")?.addEventListener("click",()=>spotAction("prepare"));
get("v083SpotApprove")?.addEventListener("click",()=>spotAction("approve"));
get("v083SpotLaunch")?.addEventListener("click",()=>spotAction("start"));
get("v083SpotStop")?.addEventListener("click",()=>spotAction("stop"));
setInterval(()=>{if(["STARTING","REQUESTED_ONE_TIME_SPOT","CREATE_UNKNOWN","RUNNING","STOP_REQUESTED"].includes(spotRequest?.phase)&&get("ai")?.classList.contains("active"))spotInspect()},15000);
get("v083WebRefresh")?.addEventListener("click",refresh);
get("v083WebDownload")?.addEventListener("click",download);
document.querySelectorAll("[data-v083]").forEach(b=>b.addEventListener("click",()=>action(b.dataset.v083)));
document.querySelectorAll("[data-v083-retry]").forEach(b=>b.addEventListener("click",()=>action(b.dataset.v083Retry,true)));
document.querySelectorAll('[data-tab="ai"],[data-group="ai"]').forEach(b=>b.addEventListener("click",()=>{Promise.resolve().then(refresh)}));
if(location.hash==="#ai")refresh();
setInterval(()=>{if(get("ai")?.classList.contains("active"))refresh()},15000);
})();
