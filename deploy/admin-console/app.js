"use strict";
const $ = id => document.getElementById(id);
const LOGIN = $("loginView");
const APP = $("appView");
const NAMES = ["OPENAI_API_KEY","TOSS_CLIENT_ID","TOSS_CLIENT_SECRET","TOSS_ACCOUNT_SEQ","NEWS_API_KEY","ALPHA_VANTAGE_API_KEY"];
const PAGE_TITLES = {
  research:["AI 연구","V0.8.3 연구 엔진"],
  bots:["V0.9 가상시장 관제","20개 가상 계좌 · 실제 틱 기반 paper trading"],
  agents:["봇별 상태","가상 계좌별 보유 · 잔고 · 수익률 · 최근 거래"],
  historical:["주식 과거 데이터","과거 시세와 정기 자료 수집"],
  ticks:["1틱 단위 수집기","Tick 데이터 수집 상태"],
  api:["API 설정","홈서버 공개 API 연결"],
  server:["현재 서버 상태","Ubuntu Docker 및 Git"],
  keys:["API Key 관리","홈서버 비밀키 저장소"]
};
let csrf = "";
let overview = null;

function showLoggedIn(loggedIn) {
  LOGIN.hidden = loggedIn;
  APP.hidden = !loggedIn;
  if (loggedIn) showPage("research");
}
async function get(path) {
  const r = await fetch(path, {credentials:"same-origin",cache:"no-store"});
  if (r.status === 401) {
    showLoggedIn(false);
    throw new Error("관리자 인증이 필요합니다.");
  }
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || "HTTP " + r.status);
  return body;
}
async function post(path, data, authenticated = true) {
  const headers = {"Content-Type":"application/json"};
  if (authenticated) headers["X-CSRF-Token"] = csrf;
  const r = await fetch(path, {
    method:"POST",credentials:"same-origin",cache:"no-store",
    headers,body:JSON.stringify(data)
  });
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || "HTTP " + r.status);
  return body;
}
function setText(id, value) {
  if ($(id)) $(id).textContent = value ?? "—";
}
function showPage(page) {
  if (!PAGE_TITLES[page]) return;
  for (const element of document.querySelectorAll("section.page")) {
    element.hidden = element.id !== "page-" + page;
  }
  for (const button of document.querySelectorAll("button[data-page]")) {
    button.classList.toggle("active",button.dataset.page===page);
  }
  setText("pageTitle",PAGE_TITLES[page][0]);
  setText("pageSub",PAGE_TITLES[page][1]);
  // Diagnostics are folded by default; only load logs on demand.
}
function jsonSafe(data) {
  return JSON.stringify(data,null,2) || "{}";
}
function money(amount) {
  const n=Number(amount);
  return Number.isFinite(n)?"$"+n.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2}):"—";
}
function amount(value) {
  const n=Number(value);
  return Number.isFinite(n)?n.toLocaleString("ko-KR"):"—";
}
function prettyDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())?"—":date.toLocaleString("ko-KR",{hour12:false});
}
function tableTo(id, columns, records, fallback) {
  const node=$(id);
  if(!node)return;
  node.replaceChildren();
  if(!records?.length) { node.textContent=fallback; return; }
  const table=document.createElement("table");
  table.className="status-table";
  const thead=document.createElement("thead");
  const thr=document.createElement("tr");
  for (const column of columns) {
    const th=document.createElement("th");
    th.textContent=column.title;
    thr.appendChild(th);
  }
  thead.appendChild(thr);
  table.appendChild(thead);
  const tbody=document.createElement("tbody");
  for (const record of records) {
    const tr=document.createElement("tr");
    for (const column of columns) {
      const td=document.createElement("td");
      td.textContent=column.render(record);
      if(column.numeric)td.className="num";
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  node.appendChild(table);
}
function renderPaper(paper) {
  const installed=paper.service==="running";
  const paused=paper.paused!==false;
  const available=paper.mode==="RECEIVED_TICK_PAPER_PILOT";
  const state=!installed?"미배포":paused?"일시정지":paper.state==="MARKET_ACTIVE"?"가상매매 활성":"시장 / 데이터 대기";
  setText("paperState",state);
  setText("paperUpdated","마지막 갱신: "+prettyDate(paper.updatedAt));
  const m=available?money:()=> "—";
  setText("paperEquity",m(paper.equityUsd));
  setText("paperInitial","초기 가상 자본 "+m(paper.initialCapitalUsd));
  setText("paperPnl",m(paper.pnlUsd));
  const ret=Number(paper.returnPct);
  setText("paperReturn",available&&Number.isFinite(ret)?"가상 수익률 "+ret.toFixed(3)+"%":"수익률 —");
  setText("paperCash",m(paper.cashUsd));
  setText("paperInvested","보유 평가액 "+m(paper.investedUsd));
  setText("paperBots",available?amount(paper.bots)+"개":"—");
  setText("paperActiveBots","포지션 보유 계좌 "+(available?amount(paper.botsWithPositions):"—"));
  setText("paperHoldings",available?amount(paper.uniqueHoldings)+"종목":"—");
  setText("paperPositions","계좌별 전체 포지션 "+(available?amount(paper.positionsCount):"—"));
  setText("paperTrades",available?amount(paper.tradeCount)+"건":"—");
  setText("paperSession","거래 세션 "+(paper.sessionDate||"대기")+" · "+(paper.feedReason||"정규장 정보 확인"));
  tableTo("paperHoldingsTable",[
    {title:"종목",render:r=>r.symbol||"—"},
    {title:"총 수량",render:r=>amount(r.shares),numeric:true},
    {title:"보유 계좌",render:r=>amount(r.accounts),numeric:true},
    {title:"평가액",render:r=>money(r.marketValueUsd),numeric:true},
    {title:"미실현 손익",render:r=>money(r.unrealizedPnlUsd),numeric:true}
  ],paper.holdings||[],"아직 보유 종목이 없습니다.");
  tableTo("paperQuotesTable",[
    {title:"종목",render:r=>r.symbol||"—"},
    {title:"관측가",render:r=>money(r.priceUsd),numeric:true},
    {title:"최근 틱 시각",render:r=>prettyDate(r.receivedAt)}
  ],paper.quotes||[],"시세 대기 중...");
  tableTo("paperTradesTable",[
    {title:"가상 시각",render:r=>prettyDate(r.at)},
    {title:"가상 계좌",render:r=>r.bot||"—"},
    {title:"구분",render:r=>r.side==="BUY"?"매수":r.side==="SELL"?"매도":"—"},
    {title:"종목",render:r=>r.symbol||"—"},
    {title:"수량",render:r=>amount(r.qty),numeric:true},
    {title:"가정 체결가",render:r=>money(r.fillUsd),numeric:true},
    {title:"가정 수수료",render:r=>money(r.feeUsd),numeric:true}
  ],paper.recentTrades||[],"가상 체결이 아직 없습니다.");
  for(const button of document.querySelectorAll("button[data-paper-action]")) {
    button.disabled=!installed||(button.dataset.paperAction==="resume"?!paused:paused);
  }
  setText("paperAssumption","실제 주문 미사용 · 수신 틱 가격에 불리한 10bp 체결 보정 + 5bp 가정 수수료 · 실제 체결/투자수익 아님");
}
function botStateLabel(value) {
  return ({
    PAUSED:"일시정지",
    WAITING_FOR_MARKET:"시장 대기",
    HOLDING:"포지션 보유",
    READY:"거래 신호 대기",
    DATA_WARMUP:"시세 학습 구간"
  })[value] || "상태 미확인";
}
function node(tag,klass,textValue) {
  const e=document.createElement(tag);
  if(klass)e.className=klass;
  if(textValue!==undefined)e.textContent=String(textValue);
  return e;
}
function metric(label,value) {
  const wrap=node("div","agent-metric");
  wrap.append(node("div","label",label),node("strong","",value));
  return wrap;
}
function renderAgents(paper) {
  const available=paper?.mode==="RECEIVED_TICK_PAPER_PILOT";
  const agents=available&&Array.isArray(paper.agents)?paper.agents:[];
  setText("agentsSession",!available?"서비스 미설치 / 데이터 없음":paper.paused?"시뮬레이션 일시정지":paper.state==="MARKET_ACTIVE"?"미국장 시세 관측 중":"시장 대기");
  setText("agentsUpdated","최종 상태: "+prettyDate(paper?.updatedAt));
  setText("agentsTotal",available?amount(agents.length)+"개":"—");
  setText("agentsHolding",available?amount(agents.filter(x=>x.positionCount>0).length)+"개":"—");
  const ret=Number(paper?.returnPct);
  setText("agentsAggregateRoi",available&&Number.isFinite(ret)?ret.toFixed(3)+"%":"—");
  const cardRoot=$("agentsCards");
  if(!cardRoot)return;
  const expanded=new Set([...cardRoot.querySelectorAll("details[data-agent-id][open]")].map(x=>x.dataset.agentId));
  cardRoot.replaceChildren();
  if(!agents.length){
    cardRoot.appendChild(node("div","panel muted",available?"계좌별 상태가 아직 생성되지 않았습니다. 연구 서비스 업데이트가 필요합니다.":"가상 시뮬레이션을 설치하면 계좌 상태가 표시됩니다."));
    setText("agentsVisible","0개 표시");
    return;
  }
  const filter=$("agentsFilter").value;
  const order=$("agentsSort").value;
  const selected=agents.filter(agent=>{
    const ret=Number(agent.returnPct);
    if(filter==="holding")return Number(agent.positionCount)>0;
    if(filter==="cash")return Number(agent.positionCount)===0;
    if(filter==="profit")return Number.isFinite(ret)&&ret>0;
    if(filter==="loss")return Number.isFinite(ret)&&ret<0;
    return true;
  });
  if(order==="return-desc")selected.sort((a,b)=>Number(b.returnPct)-Number(a.returnPct));
  else if(order==="return-asc")selected.sort((a,b)=>Number(a.returnPct)-Number(b.returnPct));
  else if(order==="equity-desc")selected.sort((a,b)=>Number(b.equityUsd)-Number(a.equityUsd));
  else selected.sort((a,b)=>String(a.id).localeCompare(String(b.id)));
  setText("agentsVisible",selected.length+" / "+agents.length+"개 표시");
  if(!selected.length){
    cardRoot.appendChild(node("div","panel muted","해당 조건에 맞는 가상 계좌가 없습니다."));
    return;
  }
  for (const agent of selected) {
    const card=node("article","panel agent-card");
    const header=node("div","agent-card-head");
    const idGroup=node("div");
    idGroup.append(
      node("h3","",agent.id||"—"),
      node("div","agent-meta",agent.strategy==="MOMENTUM"?"모멘텀 추종 · 규칙 기반":"역추세 · 규칙 기반")
    );
    const chip=node("span","agent-state"+(agent.state==="HOLDING"||agent.state==="READY"?" active":""),botStateLabel(agent.state));
    header.append(idGroup,chip);
    const metrics=node("div","agent-metrics");
    metrics.append(
      metric("평가자산",money(agent.equityUsd)),
      metric("남은 현금",money(agent.cashUsd)),
      metric("매수 평가액",money(agent.investedUsd)),
      metric("누적 손익",money(agent.pnlUsd)),
      metric("수익률",Number(agent.returnPct).toFixed(3)+"%"),
      metric("보유 포지션",amount(agent.positionCount)+"개")
    );
    const note=node("div","agent-meta",
      "실현 "+money(agent.realizedPnlUsd)+" · 미실현 "+money(agent.unrealizedPnlUsd)+" · 가상 거래 "+amount(agent.tradeCount)+"회");
    card.append(header,metrics,note);
    const holdings=node("div","agent-positions");
    const hTitle=node("div","label","보유 종목");
    holdings.append(hTitle);
    const positions=Array.isArray(agent.holdings)?agent.holdings:[];
    if(!positions.length){
      holdings.appendChild(node("p","agent-meta","현재 보유 없음 · 가상 현금 대기"));
    } else {
      const details=node("details");
      details.dataset.agentId=agent.id;
      details.open=expanded.has(agent.id);
      details.appendChild(node("summary","",positions.map(p=>p.symbol+" "+amount(p.qty)+"주").join(" · ")));
      const table=node("div","table-scroll");
      const t=node("table","status-table");
      const head=node("thead");
      const hdr=node("tr");
      for(const label of ["종목","수량","진입가","관측가","평가액","미실현 손익"])hdr.appendChild(node("th","",label));
      head.appendChild(hdr);
      t.appendChild(head);
      const tbody=node("tbody");
      for(const p of positions){
        const tr=node("tr");
        for(const item of [p.symbol,amount(p.qty),money(p.entryUsd),money(p.markUsd),money(p.marketValueUsd),money(p.unrealizedPnlUsd)]){
          tr.appendChild(node("td","",item));
        }
        tbody.appendChild(tr);
      }
      t.appendChild(tbody);
      table.appendChild(t);
      details.appendChild(table);
      holdings.appendChild(details);
    }
    if(agent.markStale){
      holdings.appendChild(node("div","agent-meta bad","시세 지연: 마지막 관측가 기준 평가"));
    }
    card.appendChild(holdings);
    const last=agent.lastTrade;
    const tradeLabel=last?
      "마지막 가상 거래: "+(last.side==="BUY"?"매수":"매도")+" "+last.symbol+" "+amount(last.qty)+"주 · "+money(last.fillUsd)+" · "+prettyDate(last.at):
      "마지막 가상 거래: 없음";
    card.append(
      node("p","agent-meta",tradeLabel),
      node("p","agent-meta","관심 종목: "+(agent.watchlist||[]).join(", "))
    );
    cardRoot.appendChild(card);
  }
}
function render(data) {
  overview=data;
  if(window.ServerMetrics)window.ServerMetrics.render($("privateServerMetrics"),data.telemetry);
  setText("researchStatus",data.research?.status==="NOT_DEPLOYED"?"미배포":data.research?.status);
  setText("researchOutput",jsonSafe(data.research));
  renderPaper(data.paper || {});
  renderAgents(data.paper || {});
  setText("historyStatus",data.historical?.serviceState==="running" && data.historical?.paused?"일시정지":
    data.historical?.collectorStatus==="NOT_DEPLOYED"?"미배포":data.historical?.collectorStatus);
  setText("historyStorage",data.historical?.storageExists?"저장소 있음":"준비 중");
  setText("publicCollector",data.collection?.phase==="COMPLETED"?"수집 정상":data.collection?.phase||"미확인");
  setText("collectionOutput",jsonSafe({
    historical:data.historical,
    dashboardCollector:data.collection
  }));
  for (const b of document.querySelectorAll("button[data-collection-action]")) {
    const installed = data.historical?.serviceState === "running";
    const paused = data.historical?.paused !== false;
    b.disabled = !installed || (b.dataset.collectionAction==="resume" ? !paused : paused);
  }
  setText("tickStatus",data.ticks?.serviceState==="not-running"?"서비스 중지":
    data.ticks?.paused?"일시정지":data.ticks?.mode==="NOT_MIGRATED"?"이전 대기":data.ticks?.mode);
  setText("ticksOutput",jsonSafe(data.ticks));
  setText("apiStatus",data.api?.ok?"정상":"연결 오류");
  setText("apiMode",data.api?.mode||"미확인");
  setText("apiUpdated",data.api?.dataUpdatedAt?
    new Date(data.api.dataUpdatedAt).toLocaleString("ko-KR"):"미확인");
  setText("apiOutput",jsonSafe({...data.api,homeServer:"127.0.0.1:18084",
    publicHttps:"https://heh-heh.tail39e546.ts.net",
    administratorScope:data.meta?.adminAccess}));
  const status={};
  for(const key of NAMES)status[key]=data.keys?.[key]?"설정됨":"미설정";
  setText("keysOutput",jsonSafe(status));
}
async function refresh() {
  setText("globalError","");
  try {render(await get("/api/overview"));}
  catch(e){setText("globalError",e.message);}
}
async function runAction(action) {
  const scope=action.split(".")[0];
  if(!["server","git"].includes(scope)) return;
  const output=$(scope+"-output");
  if (!output) return;
  output.textContent="조회 중: "+action;
  try{
    const r=await get("/api/run?action="+encodeURIComponent(action));
    output.textContent=r.text||"(출력 없음)";
  }catch(e){output.textContent="조회 실패: "+e.message;}
}
async function init() {
  NAMES.forEach(name=>{
    const option=document.createElement("option");
    option.value=name;option.textContent=name;
    $("keyName").append(option);
  });
  try{
    const session=await get("/api/me");
    csrf=session.csrf;
    showLoggedIn(true);
    await refresh();
  }catch(e){showLoggedIn(false);}
}
$("loginForm").addEventListener("submit",async e=>{
  e.preventDefault();
  setText("loginError","");
  const password=$("password").value;
  const username=$("adminUsername").value.trim();
  const otp=$("adminOtp").value.trim();
  try{
    const s=await post("/api/login",{username,password,otp},false);
    csrf=s.csrf;
    $("password").value="";
    $("adminOtp").value="";
    showLoggedIn(true);
    await refresh();
  }catch(err){
    $("password").value="";
    $("adminOtp").value="";
    setText("loginError",err.message);
  }
});
$("logout").addEventListener("click",async()=>{
  try{await post("/api/logout",{});}catch(e){}
  csrf="";overview=null;showLoggedIn(false);
});
$("refresh").addEventListener("click",refresh);
for(const button of document.querySelectorAll("button[data-page]")){
  button.addEventListener("click",()=>showPage(button.dataset.page));
}
for(const button of document.querySelectorAll("button[data-action]")){
  button.addEventListener("click",()=>runAction(button.dataset.action));
}
for (const button of document.querySelectorAll("button[data-collection-action]")) {
  button.addEventListener("click", async () => {
    const action=button.dataset.collectionAction;
    if(!["pause","resume"].includes(action))return;
    const paused=action==="pause";
    if(!confirm(paused?"주식 데이터 자동 수집을 일시정지할까요?":"주식 데이터 자동 수집을 활성화할까요? 정규장에는 1틱, 장외에는 과거 1분봉이 수집됩니다."))return;
    setText("collectionMessage","제어 요청 전송 중...");
    button.disabled=true;
    try {
      const result=await post("/api/collection/control",{paused});
      setText("collectionMessage",result.paused?"일시정지 요청됨":"자동 수집 재개 요청됨");
      await refresh();
    } catch(error){
      setText("collectionMessage","실패: "+error.message);
      await refresh();
    }
  });
}
for(const button of document.querySelectorAll("button[data-paper-action]")) {
  button.addEventListener("click",async()=>{
    const action=button.dataset.paperAction;
    if(!["pause","resume"].includes(action))return;
    if(!confirm(action==="resume"?
      "실제 주문 없이 20개 가상 계좌의 모의매매를 시작할까요? 수집된 실시간 시세를 읽어 가상 체결합니다.":
      "가상 시뮬레이션을 일시정지할까요? 기존 가상 원장은 보존됩니다."))return;
    button.disabled=true;
    setText("paperMessage","요청 처리 중...");
    try {
      const result=await post("/api/paper/control",{paused:action==="pause"});
      setText("paperMessage",result.paused?"가상매매 일시정지 요청 완료":"가상매매 재개 요청 완료");
      await refresh();
    }catch(e) {
      setText("paperMessage","오류: "+e.message);
      await refresh();
    }
  });
}
setInterval(()=>{
  if(!APP.hidden&&(!$("page-bots").hidden||!$("page-agents").hidden))refresh();
},7000);
$("serverDiagnostics").addEventListener("toggle",function(){
  if(this.open){
    runAction("server.containers");
    runAction("git.status");
  }
});
$("agentsManualRefresh").addEventListener("click",refresh);
$("agentsFilter").addEventListener("change",()=>renderAgents(overview?.paper||{}));
$("agentsSort").addEventListener("change",()=>renderAgents(overview?.paper||{}));
$("keyForm").addEventListener("submit",async e=>{
  e.preventDefault();
  setText("keyMessage","");
  const name=$("keyName").value;
  const value=$("keyValue").value;
  if(!NAMES.includes(name)){setText("keyMessage","허용되지 않은 항목입니다.");return;}
  if(value===""&&!confirm(name+" 키를 삭제할까요?"))return;
  try {
    const r=await post("/api/keys",{name,value});
    $("keyValue").value="";
    setText("keyMessage",name+(r.configured?" 저장 완료":" 삭제 완료"));
    await refresh();
  }catch(err){setText("keyMessage","저장 실패: "+err.message);}
});
init();
