/* V0.8 research-only admin controller. Legacy V0.7 remains available for audit compatibility. */
(()=>{
"use strict";
const get=id=>document.getElementById(id);
const write=(id,value)=>{const node=get(id);if(node)node.textContent=value};
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const numeric=v=>typeof v==="number"&&Number.isFinite(v)?v:null;
const pct=v=>numeric(v)==null?"데이터 없음":(v*100).toFixed(2)+"%";
const number=v=>numeric(v)==null?"—":String(v);
const phases={not_started:"대기",starting:"시작 중",running:"실행 중",completed:"완료",failed:"실패",interrupted:"중단"};
let busy=false,active=false;
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
  const stages=d.stages||{},runs=d.runs||{},run=runs.train||runs.smoke||{},gens=Array.isArray(d.generations)?d.generations:[];
  const activeStage=Object.entries(stages).find(([k,v])=>["running","starting"].includes(v?.phase));
  const stageName=activeStage?.[0]||(["validation","holdout","train","smoke"].find(k=>stages[k]?.phase==="completed")||"대기");
  const processed=numeric(run.completedSessions),requested=numeric(run.requestedSessions);
  const latest=gens.length?gens[gens.length-1]:null;
  const cells=[["현재 단계",stageName,"실행: "+(activeStage?.[1]?.phase||"대기")],
    ["진행 거래일",processed==null?"—":processed+" / "+(requested??"?"),"서버 보고 기준"],
    ["완료 세대",number(run.completedTrainGenerations??gens.length),"연속 유효 5거래일"],
    ["수익 부모",number(latest?.parents),"최신 완료 창"],
    ["RANDOM_RECOVERY",latest?.recovery==null?"—":latest.recovery?"발생":"미발생","최신 완료 창"],
    ["유전자 다양성",number(latest?.uniqueGenomes),"서로 다른 유전자 수"],
    ["최신 평균 수익",latest?pct(latest.meanReturn):"—","비용 차감 기준"],
    ["2026 Final OOS","CLOSED","학습·검증 불가"]];
  get("v08WebStats").innerHTML=cells.map(([a,b,c])=>'<div class="v08-tile"><div class="label">'+esc(a)+'</div><div class="v08-value">'+esc(b)+'</div><div class="v08-muted">'+esc(c)+'</div></div>').join("");
  get("v08WebStages").innerHTML=["smoke","train","holdout","validation","export"].map(k=>{
    const st=stages[k]||{},phase=st.phase||"not_started";
    return '<div class="v08-tile"><strong>'+esc(k)+'</strong><div>'+esc(phases[phase]||phase)+'</div><div class="v08-muted">'+esc(st.error||"")+'</div></div>';
  }).join("");
  chart("v08ReturnChart","v08ReturnSummary",gens,"meanReturn",pct);
  chart("v08MddChart","v08MddSummary",gens,"meanMdd",pct);
  chart("v08FitnessChart","v08FitnessSummary",gens,"meanFitness",v=>v.toFixed(4));
  write("v08WebLineage",gens.map(g=>"세대 "+g.generation+" · "+(g.dates||[]).join(" ~ ")+" · 평균 수익 "+pct(g.meanReturn)+" · MDD "+pct(g.meanMdd)+" · fitness "+(numeric(g.meanFitness)==null?"—":g.meanFitness.toFixed(4))+" · 부모 "+number(g.parents)+" · 유전자 "+number(g.uniqueGenomes)+" · 무작위 회복 "+(g.recovery?"예":"아니오")).join("\n")||"완료된 세대 없음");
  const downloading=Boolean(d.downloadAvailable);
  get("v08WebDownload").disabled=!downloading;
  document.querySelectorAll("[data-v08-retry]").forEach(b=>b.disabled=busy||!["failed","interrupted"].includes(stages[b.dataset.v08Retry]?.phase));
  document.querySelectorAll("[data-v08]").forEach(b=>{const k=b.dataset.v08; const prior={train:"smoke",holdout:"train",validation:"holdout",export:"validation"}[k];
    b.disabled=busy||Boolean(activeStage)||(stages[k]?.phase&&stages[k].phase!=="not_started")||(prior&&stages[prior]?.phase!=="completed");
  });
  write("v08WebRaw",JSON.stringify(d,null,2));
}
async function call(command){const d=await req("/api/admin/console",{method:"POST",body:JSON.stringify({command})});if(d?.ok===false||d?.exitCode)throw Error(d?.output||d?.error||"API 요청 실패");return d?.output||""}
async function refresh(){
  if(busy||active||!token())return;
  active=true;
  try{
    const [report,log]=await Promise.all([call("v08 report"),call("v08 log")]);
    let data;try{data=JSON.parse(report)}catch{throw Error("V0.8 보고서 JSON 형식 오류")}
    render(data);write("v08WebLog",log.split("\n").slice(-35).join("\n")||"로그 없음");
  }catch(e){
    write("v08WebFeedback","V0.8 상태 조회 실패: "+e.message+"\n운영 EC2에 V0.8 백엔드가 적용됐는지 확인하세요.");
    write("v08WebStats","서버 연동 전 · "+e.message);
  }finally{active=false}
}
async function action(stage,retry=false){
 if(busy)return;
 if(!confirm("V0.8 "+(retry?"재실행: ":"실행: ")+stage+"\n연구 전용 · 기존 V0.7 데이터 보존 · 2026 OOS CLOSED\n서버에서 실제 작업을 시작할까요?"))return;
 busy=true;
 document.querySelectorAll("[data-v08],[data-v08-retry]").forEach(b=>b.disabled=true);
 const command="v08 "+(retry?"retry ":"")+stage;
 write("v08WebFeedback",command+" · 서버 응답 확인 중");
 try{const output=await call(command);write("v08WebFeedback",command+" · 서버 응답:\n"+output)}
 catch(e){write("v08WebFeedback",command+" · 실행 차단/실패:\n"+e.message+"\n기존 연구 작업과 데이터는 유지됩니다.")}
 finally{busy=false;await refresh()}
}
async function download(){
 const b=get("v08WebDownload");if(!b||b.disabled)return;
 b.disabled=true;
 try{
  const response=await fetch(API+"/api/research/v08/download",{headers:{Authorization:"Bearer "+token()}});
  if(!response.ok)throw Error("다운로드 실패: HTTP "+response.status);
  const url=URL.createObjectURL(await response.blob());
  const a=document.createElement("a");a.href=url;a.download="v08-research-results.zip";document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
 }catch(e){write("v08WebFeedback",e.message)}
 finally{await refresh()}
}
get("v08WebRefresh")?.addEventListener("click",refresh);
get("v08WebDownload")?.addEventListener("click",download);
document.querySelectorAll("[data-v08]").forEach(b=>b.addEventListener("click",()=>action(b.dataset.v08)));
document.querySelectorAll("[data-v08-retry]").forEach(b=>b.addEventListener("click",()=>action(b.dataset.v08Retry,true)));
document.querySelectorAll('[data-tab="ai"],[data-group="ai"]').forEach(b=>b.addEventListener("click",()=>{Promise.resolve().then(refresh)}));
if(location.hash==="#ai")refresh();
setInterval(()=>{if(get("ai")?.classList.contains("active"))refresh()},15000);
})();
