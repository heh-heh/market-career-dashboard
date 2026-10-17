/* Read-only V0.8.2 bot roster. No trade, training or file mutation actions. */
(()=>{
"use strict";
const node=id=>document.getElementById(id);
const html=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const num=x=>typeof x==="number"&&Number.isFinite(x)?x:null;
const pct=x=>num(x)==null?"—":(x*100).toFixed(2)+"%";
const money=x=>num(x)==null?"—":"$"+x.toFixed(2);
const viewButtons=()=>[...document.querySelectorAll("[data-roster-view]")];
let snapshot=null,view="current",selectedId=null,loading=false;
function allBots(){return snapshot?(view==="current"?snapshot.currentBots:snapshot.evaluatedBots)||[]:[]}
function metric(b,key){
  if(view==="evaluated")return b[key];
  return b.latestEvaluation?.[key]??null;
}
function detail(bot){
  if(!bot){node("botRosterDetailTitle").textContent="봇을 선택하세요";node("botRosterDetail").textContent="";return}
  node("botRosterDetailTitle").textContent=bot.id;
  const isCurrent=view==="current";
  node("botRosterDetailHint").textContent=isCurrent
    ?"현재 생성된 개체 · 상속된 누적 이력은 이 자식의 검증된 수익이 아닙니다."
    :"실제 유효 5거래일 평가 기록 · 미래 성과나 실거래 수익이 아닙니다.";
  node("botRosterDetail").textContent=JSON.stringify(isCurrent?{
    botId:bot.id,generation:bot.generation,status:bot.status,origin:bot.origin,
    parentIds:bot.parents,birthDate:bot.birthDate,mutationCount:bot.mutationCount,
    lastEvaluationSameBot:bot.latestEvaluation,
    inheritedLifetimeNotOwnPerformance:bot.inheritedLifetime,
    genomeHash:bot.genomeHash,genome:bot.genome
  }:bot,null,2);
}
function render(){
  if(!snapshot)return;
  const current=snapshot.currentBots||[],evaluated=snapshot.evaluatedBots||[];
  const parentCount=evaluated.filter(b=>b.selectedParent).length;
  const cards=[
    ["현재 개체",current.length+" / "+snapshot.expectedBots],
    ["최근 평가",evaluated.length+" / "+snapshot.expectedBots],
    ["선발된 부모",evaluated.length?String(parentCount):"—"],
    ["완료 세대",snapshot.completedGenerations??"—"]
  ];
  node("botRosterSummary").innerHTML=cards.map(([k,v])=>'<div class="bots-stat"><span class="label">'+html(k)+'</span><strong>'+html(v)+'</strong></div>').join("");
  const timestamp=(snapshot.evaluatedDates||[]).join(" ~ ");
  const phase={not_started:"실행 전",starting:"시작 중",running:"진행 중",completed:"완료",failed:"실패",interrupted:"중단"}[snapshot.phase]||snapshot.phase||"대기";
  node("botRosterStatus").textContent="V0.8.2 · "+(snapshot.stage==="train"?"전체 학습":"50개체 시험")+" "+phase
    +" · 완료 세대 "+(snapshot.completedGenerations??"—")
    +(timestamp?" · 최근 평가 "+timestamp:"")
    +"\n"+(snapshot.message||"연구 스냅샷")+" · 2026 Final OOS CLOSED";
  node("botRosterLegend").textContent=view==="current"
    ?"현재 자식 개체군: 아직 평가되지 않은 자식의 수익률은 — 로 표시합니다. 상속된 누적 손익은 봇 상세에만 별도 표시합니다."
    :"최근 5거래일 평가 개체군: 평가 완료된 과거 개체 수익률·MDD·거래 횟수와 부모 선발 여부를 표시합니다.";
  viewButtons().forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.rosterView===view)));
  const filter=node("botRosterSearch").value.trim().toLowerCase(),order=node("botRosterSort").value;
  const bots=allBots().filter(b=>(b.id+" "+(b.parents||[]).join(" ")).toLowerCase().includes(filter));
  const sortValue=b=>order==="pnl"?metric(b,"return"):order==="fitness"?metric(b,"fitness"):order==="trades"?metric(b,"trades"):null;
  if(order==="id")bots.sort((a,b)=>a.id.localeCompare(b.id));
  else bots.sort((a,b)=>(num(sortValue(b))??-Infinity)-(num(sortValue(a))??-Infinity)||a.id.localeCompare(b.id));
  const tbody=node("botRosterRows");
  if(!bots.length){
    tbody.innerHTML='<tr><td colspan="6">'+html(allBots().length?"검색 결과가 없습니다.":"저장된 봇 스냅샷이 없습니다.")+'</td></tr>';
    detail(null);return;
  }
  if(!bots.some(b=>b.id===selectedId))selectedId=bots[0].id;
  tbody.innerHTML=bots.map(b=>{
    const evaluatedView=view==="evaluated",e=evaluatedView?b:b.latestEvaluation;
    const status=evaluatedView?(b.survival||"미확인")+(b.selectedParent?" · 부모":""):
      b.latestEvaluation?"평가됨":"신규 · 평가 전";
    const parent=evaluatedView?(b.selectedParent?"선발":"미선발"):(b.parents||[]).join(", ")||"최초 개체";
    return '<tr'+(b.id===selectedId?' class="selected"':"")+'><td><button type="button" class="bots-select" data-bot-id="'+html(b.id)+'">'+html(b.id)+'</button></td>'+
    "<td>"+html(b.generation??"—")+"</td><td>"+html(status)+"</td><td>"+html(pct(e?.return))+"</td><td>"+html(e?.trades??"—")+"</td><td>"+html(parent)+"</td></tr>";
  }).join("");
  tbody.querySelectorAll("[data-bot-id]").forEach(b=>b.onclick=()=>{
    selectedId=b.dataset.botId;render();
  });
  detail(bots.find(b=>b.id===selectedId));
}
async function refreshBotRoster(){
  if(loading||!token())return;
  loading=true;node("botRosterRefresh").disabled=true;
  try{
    const response=await req("/api/admin/console",{method:"POST",body:JSON.stringify({command:"bots report"})});
    if(response.ok===false||response.exitCode)throw Error(response.output||response.error||"봇 조회 실패");
    const data=JSON.parse(response.output||"{}");
    if(data.protocol!=="V082_BOTS_1")throw Error("봇 조회 API 버전 불일치. EC2 읽기 전용 모듈 설치가 필요합니다.");
    if(!Array.isArray(data.currentBots)||!Array.isArray(data.evaluatedBots))throw Error("봇 데이터 형식 오류");
    snapshot=data;render();
  }catch(e){
    node("botRosterStatus").textContent="봇 조회 실패: "+e.message;
  }finally{loading=false;node("botRosterRefresh").disabled=false}
}
window.refreshBotRoster=refreshBotRoster;
node("botRosterRefresh")?.addEventListener("click",refreshBotRoster);
node("botRosterSearch")?.addEventListener("input",render);
node("botRosterSort")?.addEventListener("change",render);
viewButtons().forEach(b=>b.addEventListener("click",()=>{
  view=b.dataset.rosterView;selectedId=null;render();
}));
if(location.hash==="#bots")refreshBotRoster();
setInterval(()=>{if(node("bots")?.classList.contains("active"))refreshBotRoster()},20000);
})();