"use strict";
const $ = id => document.getElementById(id);
const LOGIN = $("loginView");
const APP = $("appView");
const NAMES = ["OPENAI_API_KEY","TOSS_CLIENT_ID","TOSS_CLIENT_SECRET","TOSS_ACCOUNT_SEQ","NEWS_API_KEY","ALPHA_VANTAGE_API_KEY"];
const PAGE_TITLES = {
  research:["AI 연구","V0.8.3 연구 엔진"],
  bots:["V0.9 가상시장 관제","20개 가상 계좌 · 실제 틱 기반 paper trading"],
  agents:["봇별 상태","가상 계좌별 보유 · 잔고 · 수익률 · 최근 거래"],
  historical:["주식 데이터 수집기","과거 50종목 · 실시간 1틱 독립 제어"],
  inventory:["현재 데이터 현황","SQLite 저장 파일 · 종목별 기간 · 1틱 보존 데이터"],
  api:["API 설정","홈서버 공개 API 연결"],
  server:["서버 성능","작업 관리자 · CPU / 메모리 / 디스크 / 네트워크 / 전력"],
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
  if(page==="server")void refreshServerPerformance();
  if(page==="historical")void refreshHistory50();
  if(page==="inventory")void refreshInventory();
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
let latestHistory50=null;
function historyStateLabel(state){
  return {queued:"대기",collecting:"수집 중",checkpoint:"저장 중",error:"오류",
    collection_pass_finished_unverified:"1차 탐색 종료 · 검증 미완료",
    unknown:"상태 확인 필요"}[state]||"확인 중";
}
function historyNum(v){
  return Number.isFinite(v)&&v>=0?Math.floor(v).toLocaleString("ko-KR"):"—";
}
function historyPct(v){
  return Number.isFinite(v)&&v>=0&&v<=100?v.toFixed(2)+"%":"미확인";
}
function percentBar(v) {
  const outer=document.createElement("div");
  outer.className="history-bar";
  const fill=document.createElement("span");
  fill.style.width=Number.isFinite(v)&&v>=0&&v<=100?v+"%":"0%";
  outer.appendChild(fill);
  return outer;
}
function renderHistory50(plan,master){
  latestHistory50=plan;
  const ready=plan?.configured===true;
  const entries=ready&&Array.isArray(plan.symbols)?plan.symbols:[];
  setText("history50Count",ready?entries.length+" / 50개":"설정 대기");
  const mode=master?.collectorStatus;
  let state="미설치";
  if(ready){
    if(master?.serviceState!=="running")state="스케줄러 중지";
    else if(!plan.enabled)state="과거 수집 미허용";
    else if(master?.paused)state="전체 수집 일시정지";
    else if(master?.activeWorker==="backfill")state="과거 1분봉 수집 중";
    else if(mode==="ERROR")state="거래일정 검증 오류";
    else if(mode==="MARKET_OPEN")state="정규장 개장 · 과거 수집 예약";
    else if(mode==="TICKS_PAUSED")state="정규장 개장 · 1틱 중지 · 과거 수집 예약";
    else if(mode==="SWITCHING")state="작업 전환 중";
    else if(mode==="MARKET_CLOSED")state="장외 · 작업 시작 대기";
    else if(mode==="PAUSED")state="스케줄러 일시정지";
    else state="현재 작업 확인 필요";
  }
  setText("history50State",state);
  setText("history50Active",ready?("대기 "+historyNum(plan.counts?.queued)+"개 · 완료 검토 "+historyNum(plan.counts?.collection_pass_finished_unverified)+"개"):"50종목 구성 필요");
  setText("history50Scan",historyPct(plan?.scanPercent));
  const bar=$("history50ScanBar");
  if(bar)bar.style.width=Number.isFinite(plan?.scanPercent)?Math.max(0,Math.min(100,plan.scanPercent))+"%":"0%";
  setText("history50Rows",ready?historyNum(plan.totalStoredRows):"—");
  setText("history50Pages","API 요청 페이지 "+(ready?historyNum(plan.totalPages):"—"));
  setText("history50Errors","오류 "+(ready?historyNum(plan.counts?.error):"—")+"개 종목");
  const diagnosis=[
    "스케줄러 "+(master?.serviceState||"미확인"),
    "과거 수집 "+(plan?.enabled?"허용":"중지"),
    "전체 "+(master?.paused?"일시정지":"허용"),
    "모드 "+(mode||"확인 중"),
    "현재 작업 "+(master?.activeWorker||"없음")
  ];
  if(master?.reason)diagnosis.push("사유 "+master.reason);
  if(Number.isInteger(master?.workerFailures)&&master.workerFailures>0)
    diagnosis.push("작업 실패 "+master.workerFailures+"회");
  if(master?.lastExitCode!==null&&master?.lastExitCode!==undefined&&master.lastExitCode!==0)
    diagnosis.push("직전 종료 코드 "+master.lastExitCode);
  setText("history50MasterState",diagnosis.join(" · "));
  const lastAction=plan?.controlReason||"미확인";
  setText("history50Persisted","서버 저장값: "+(plan?.enabled?"과거 수집 허용됨":"과거 수집 중지")+
    " · 마지막 설정 "+lastAction+" · "+prettyDate(plan?.controlUpdatedAt));
  for(const button of document.querySelectorAll("[data-history-50]")){
    button.disabled=historyControlPending||!ready||
      (button.getAttribute("data-history-50")==="resume"?plan.enabled:!plan.enabled);
  }
  const filter=$("history50Filter")?.value||"all";
  const sort=$("history50Sort")?.value||"progress";
  let filtered=entries.filter(x=>{
    if(filter==="active")return x.state==="collecting"||x.state==="checkpoint";
    if(filter==="error")return x.state==="error";
    if(filter==="queued")return x.state==="queued";
    if(filter==="finished")return x.state==="collection_pass_finished_unverified";
    return true;
  });
  filtered=[...filtered];
  if(sort==="symbol")filtered.sort((a,b)=>a.symbol.localeCompare(b.symbol));
  else if(sort==="error")filtered.sort((a,b)=>(b.state==="error")-(a.state==="error")||a.symbol.localeCompare(b.symbol));
  else filtered.sort((a,b)=>(Number.isFinite(b.scanPercent)?b.scanPercent:-1)-
    (Number.isFinite(a.scanPercent)?a.scanPercent:-1)||a.symbol.localeCompare(b.symbol));
  setText("history50Visible",filtered.length+" / "+entries.length+"개 표시");
  const root=$("history50Table");if(!root)return;
  root.replaceChildren();
  if(!filtered.length){
    root.appendChild(node("p","meta",ready?"해당 조건의 종목이 없습니다.":"먼저 50종목 수집기를 설치해 주세요."));
    return;
  }
  const table=node("table","history-table");
  const head=node("thead");
  const tr=node("tr");
  for(const name of ["종목","탐색 진행률","거래일 검증","저장 봉 수","API 페이지","상태"]){
    tr.appendChild(node("th","",name));
  }
  head.appendChild(tr);table.appendChild(head);
  const body=node("tbody");
  for(const item of filtered){
    const row=node("tr");
    row.appendChild(node("td","history-name",item.symbol));
    const progress=node("td","history-progress-cell");
    const caption=node("div","history-progress-line");
    caption.append(node("span","meta","기간 탐색"),node("strong","",historyPct(item.scanPercent)));
    progress.append(caption,percentBar(item.scanPercent));row.appendChild(progress);
    const audit=node("td");
    audit.appendChild(node("strong","",historyPct(item.verifiedDaysPercent)));
    audit.appendChild(node("div","meta",
      Number.isFinite(item.verifiedDaysPercent)?"거래일 기준 · 1차 탐색 후 계산":"검증 중 · 완료율 아님"));
    row.appendChild(audit);
    row.appendChild(node("td","",historyNum(item.storedRows)));
    row.appendChild(node("td","",historyNum(item.pages)));
    const status=node("td",item.state==="error"?"history-error":"",historyStateLabel(item.state));
    if(item.errorCode)status.append(node("small","history-error"," · "+item.errorCode));
    row.appendChild(status);
    body.appendChild(row);
  }
  table.appendChild(body);root.appendChild(table);
}
let fetchingHistory50=false;
async function refreshHistory50(){
  if(fetchingHistory50||APP.hidden||$("page-historical").hidden)return;
  fetchingHistory50=true;
  try{
    const data=await get("/api/history/status");
    if(!APP.hidden&&!$("page-historical").hidden)
      renderHistory50(data,data.scheduler||overview?.historical);
  }catch(err){
    if(!APP.hidden&&!$("page-historical").hidden)
      setText("history50Message","상태 조회 실패: "+err.message);
  }finally{fetchingHistory50=false;}
}
let lastInventory=null;
function formatInventoryBytes(value){
  if(!Number.isFinite(value)||value<0)return "미확인";
  if(value===0)return "0 B";
  const units=["B","KB","MB","GB","TB"];
  const k=Math.min(units.length-1,Math.floor(Math.log(value)/Math.log(1024)));
  return (value/(1024**k)).toLocaleString("ko-KR",{maximumFractionDigits:k===0?0:1})+" "+units[k];
}
function inventoryDate(value){
  return typeof value==="string"&&value.length>=10?value.slice(0,10):"—";
}
function renderInventory(data){
  lastInventory=data;
  const historical=data?.history||{},tick=data?.ticks||{};
  setText("inventoryFiles",Number.isFinite(historical.fileCount)?historical.fileCount+" / 50":"—");
  setText("inventoryRows",Number.isFinite(historical.checkpointRows)?historyNum(historical.checkpointRows):"—");
  setText("inventoryBytes",formatInventoryBytes(historical.storedBytes));
  setText("inventoryTickSessions",Number.isFinite(tick.sessionFolderCount)?historyNum(tick.sessionFolderCount):"—");
  setText("inventoryTickLatest","마지막 세션 "+(tick.lastSession||"없음"));
  setText("inventoryTickStatus","마지막 기록: "+(tick.lastSession||"없음")+
    " · 저장 CSV "+(tick.lastSessionFiles?.length||0)+"개 · 크기 "+formatInventoryBytes(tick.lastSessionBytes)+
    " · 수집기 마지막 상태 "+(tick.status||"미확인"));
  const filter=$("inventoryFilter")?.value||"all";
  let records=Array.isArray(historical.symbols)?historical.symbols:[];
  records=records.filter(x=>filter==="saved"?x.dbExists===true:
    filter==="missing"?x.dbExists===false:
    filter==="gaps"?(Number(x.missingDays)>0):true);
  setText("inventoryUpdated",records.length+" / "+(historical.symbols?.length||0)+
    "종목 표시 · 실제 파일 크기 확인 · 행 수/기간은 마지막 수집 체크포인트 기준");
  const root=$("inventoryTable");if(root){
    root.replaceChildren();
    if(!records.length)root.appendChild(node("p","meta","표시할 데이터가 없습니다."));
    else{
      const table=node("table","history-table");
      const head=node("thead"),titles=node("tr");
      for(const name of ["종목","저장 파일","마지막 기록 봉 수","시작 날짜","마지막 날짜","파일 크기","결측 거래일","탐색 진행"])titles.append(node("th","",name));
      head.append(titles);table.append(head);
      const body=node("tbody");
      for(const item of records){
        const row=node("tr");
        row.append(node("td","history-name",item.symbol||"—"));
        row.append(node("td",item.dbExists?"":"history-file-missing",item.dbExists?"SQLite 있음":"파일 없음"));
        row.append(node("td","",item.dbExists?historyNum(item.checkpointRows):"—"));
        row.append(node("td","inventory-date",inventoryDate(item.oldest)));
        row.append(node("td","inventory-date",inventoryDate(item.newest)));
        row.append(node("td","",formatInventoryBytes(item.bytes)));
        row.append(node("td","",Number.isFinite(item.missingDays)?historyNum(item.missingDays):"검증 대기"));
        const percent=node("td","history-progress-cell");
        percent.append(node("span","",historyPct(item.scanPercent)),percentBar(item.scanPercent));
        row.append(percent);body.append(row);
      }
      table.append(body);root.append(table);
    }
  }
  const tickRoot=$("inventoryTickTable");
  if(tickRoot){
    tickRoot.replaceChildren();
    const stored=Array.isArray(tick.lastSessionFiles)?tick.lastSessionFiles:[];
    if(!stored.length)tickRoot.append(node("p","meta","최근 세션의 1틱 CSV 파일이 없습니다."));
    else{
      const table=node("table","history-table"),thead=node("thead"),titles=node("tr");
      for(const name of ["종목","저장 파일","용량"])titles.append(node("th","",name));
      thead.append(titles);table.append(thead);
      const body=node("tbody");
      for(const item of stored){
        const row=node("tr");
        row.append(node("td","history-name",item.symbol||"—"));
        row.append(node("td","",tick.lastSession+"/"+item.symbol+".csv"));
        row.append(node("td","",formatInventoryBytes(item.bytes)));
        body.append(row);
      }
      table.append(body);tickRoot.append(table);
    }
  }
}
let inventoryLoading=false;
async function refreshInventory(){
  if(inventoryLoading||APP.hidden||$("page-inventory").hidden)return;
  inventoryLoading=true;
  try{
    const data=await get("/api/data/inventory");
    if(!APP.hidden&&!$("page-inventory").hidden)renderInventory(data);
  }catch(error){
    if(!APP.hidden&&!$("page-inventory").hidden)setText("inventoryUpdated","조회 오류: "+error.message);
  }finally{inventoryLoading=false;}
}
function render(data) {
  overview=data;
  if(window.ServerMetrics && !$("page-server").hidden)
    window.ServerMetrics.render($("privateServerMetrics"),data.telemetry);
  setText("researchStatus",data.research?.status==="NOT_DEPLOYED"?"미배포":data.research?.status);
  setText("researchOutput",jsonSafe(data.research));
  renderPaper(data.paper || {});
  renderAgents(data.paper || {});
  renderHistory50(data.historical50||{},data.historical||{});
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
  const tick=data.ticks||{};
  setText("tickStatus",tick.enabled===false?"중지":tick.active?"허용 · 수집 중":"허용 · 대기");
  setText("tickWorkerState",tick.active?(tick.enabled?"실시간 수신":"중지 전환 중"):
    tick.serviceState==="running"?"작업 중지":"서비스 중지");
  setText("tickSession",tick.sessionDate||"기록 없음");
  setText("ticksOutput",jsonSafe(tick));
  for(const button of document.querySelectorAll("button[data-tick-action]")){
    button.disabled = button.dataset.tickAction==="resume"?tick.enabled===true:tick.enabled===false;
  }
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
let serverPerformanceRefreshing=false;
async function refreshServerPerformance(){
  if(serverPerformanceRefreshing || APP.hidden || $("page-server").hidden)return;
  serverPerformanceRefreshing=true;
  setText("serverPerfState","측정값 확인 중");
  try{
    const telemetry=await get("/api/telemetry");
    if(APP.hidden || $("page-server").hidden)return;
    if(window.ServerMetrics)window.ServerMetrics.render($("privateServerMetrics"),telemetry);
    setText("serverPerfState",!telemetry.ok?"측정 데이터 없음":
      telemetry.stale?"갱신 지연":"정상 수집");
  }catch(err){
    if(!APP.hidden && !$("page-server").hidden){
      setText("serverPerfState","연결 오류");
      if(window.ServerMetrics)window.ServerMetrics.render($("privateServerMetrics"),null);
    }
  }finally{
    serverPerformanceRefreshing=false;
  }
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
$("serverPerfRefresh").addEventListener("click",refreshServerPerformance);
$("inventoryRefresh").addEventListener("click",refreshInventory);
$("inventoryFilter").addEventListener("change",()=>renderInventory(lastInventory));
setInterval(()=>{if(!document.hidden&&!APP.hidden&&!$("page-inventory").hidden)void refreshInventory();},15000);
for(const button of document.querySelectorAll("button[data-tick-action]")){
  button.addEventListener("click",async()=>{
    const enabled=button.dataset.tickAction==="resume";
    if(!confirm(enabled?
      "실시간 1틱 수집을 다시 허용할까요? 미국 정규장에서는 1틱 작업이 우선 실행될 수 있습니다.":
      "실시간 1틱 수집을 중지할까요? 과거 50종목 1분봉 수집과 저장 데이터는 유지됩니다."))return;
    button.disabled=true;
    setText("tickActionMessage","1틱 설정 변경 중...");
    try{
      const result=await post("/api/ticks/control",{enabled});
      setText("tickActionMessage",result.enabled?
        "1틱 수집 허용됨 · 미국장 일정과 스케줄러 상태에 따라 시작":
        "1틱 중지 요청 완료 · 이미 실행 중이면 스케줄러가 종료합니다");
      await refresh();
    }catch(e){
      setText("tickActionMessage","변경 실패: "+e.message);
      await refresh();
    }
  });
}
$("history50Refresh").addEventListener("click",refreshHistory50);
$("history50Filter").addEventListener("change",()=>renderHistory50(latestHistory50,overview?.historical));
$("history50Sort").addEventListener("change",()=>renderHistory50(latestHistory50,overview?.historical));
setInterval(()=>{if(!document.hidden&&!APP.hidden&&!$("page-historical").hidden)void refreshHistory50();},10000);
let historyControlPending=false;
for(const button of document.querySelectorAll("button[data-history-50]")){
  button.addEventListener("click",async()=>{
    // A second click, in-flight poll or a stale card must not cause the
    // opposite action. Start is a distinct API, not a toggling endpoint.
    if(historyControlPending)return;
    const enabled=button.getAttribute("data-history-50")==="resume";
    if(!confirm(enabled?
      "50종목 과거 1분봉 수집을 시작/예약할까요? 전체 스케줄러도 재개됩니다. 미국 정규장 중에는 종료 후 시작되며, API 호출량이 많을 수 있습니다.":
      "과거 50종목 수집만 중지할까요? 저장된 SQLite 데이터는 유지됩니다."))return;
    historyControlPending=true;
    for(const action of document.querySelectorAll("button[data-history-50]"))action.disabled=true;
    setText("history50Message",enabled?"수집 시작 설정을 서버에 저장하고 검증하는 중...":"과거 수집 중지 설정 확인 중...");
    try{
      const result=enabled?await post("/api/history/start",{}):
        await post("/api/history/control",{enabled:false});
      // Verify the disk state through a separate authenticated read, rather
      // than treating the POST acknowledgement as collection progress.
      const status=await get("/api/history/status");
      if(status.enabled!==enabled)
        throw new Error("설정 검증 불일치: 서버 값이 다른 요청으로 변경됨 ("+(status.controlReason||"원인 미확인")+")");
      if(enabled && status.scheduler?.paused!==false)
        throw new Error("과거 수집은 허용됐지만 전체 스케줄러는 여전히 일시정지 상태입니다.");
      renderHistory50(status,status.scheduler||overview?.historical);
      if(enabled){
        const active=status.scheduler?.activeWorker==="backfill";
        setText("history50Message",active?"과거 1분봉 다운로드 실행 확인됨.":
          "수집 허용·스케줄러 재개 확인됨. 미국 정규장 중이면 장 종료 후 수집이 시작됩니다. ("+
          (status.scheduler?.collectorStatus||"작업 대기")+")");
      }else{
        setText("history50Message","과거 1분봉 수집 중지를 서버 저장값으로 확인했습니다.");
      }
      await refresh();
    }catch(err){
      setText("history50Message","설정 확인 실패: "+err.message);
      await refresh();
    }finally{
      historyControlPending=false;
      await refreshHistory50();
      if(latestHistory50)renderHistory50(latestHistory50,latestHistory50.scheduler||overview?.historical);
    }
  });
}

setInterval(()=>{
  if(!document.hidden && !APP.hidden && !$("page-server").hidden)
    void refreshServerPerformance();
},30000);
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
