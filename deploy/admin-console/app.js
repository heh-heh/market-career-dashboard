"use strict";
const $ = id => document.getElementById(id);
const LOGIN = $("loginView");
const APP = $("appView");
const NAMES = ["OPENAI_API_KEY","TOSS_CLIENT_ID","TOSS_CLIENT_SECRET","TOSS_ACCOUNT_SEQ","NEWS_API_KEY","ALPHA_VANTAGE_API_KEY"];
const PAGE_TITLES = {
  research:["AI 연구","V0.8.3 연구 엔진"],
  bots:["봇 상태","개체 수와 학습 세대"],
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
  if(page==="server") {
    runAction("server.containers");
    runAction("git.status");
  }
}
function jsonSafe(data) {
  return JSON.stringify(data,null,2) || "{}";
}
function render(data) {
  overview=data;
  setText("researchStatus",data.research?.status==="NOT_DEPLOYED"?"미배포":data.research?.status);
  setText("researchOutput",jsonSafe(data.research));
  setText("botCount",data.bots?.count==null?"미가동":String(data.bots.count));
  setText("botsOutput",jsonSafe(data.bots));
  setText("historyStatus",data.historical?.collectorStatus==="NOT_DEPLOYED"?"미배포":data.historical?.collectorStatus);
  setText("historyStorage",data.historical?.storageExists?"저장소 있음":"준비 중");
  setText("publicCollector",data.collection?.phase==="COMPLETED"?"수집 정상":data.collection?.phase||"미확인");
  setText("collectionOutput",jsonSafe({
    historical:data.historical,
    dashboardCollector:data.collection
  }));
  setText("tickStatus",data.ticks?.mode==="NOT_MIGRATED"?"이전 대기":data.ticks?.state);
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
  try{
    const s=await post("/api/login",{password},false);
    csrf=s.csrf;
    $("password").value="";
    showLoggedIn(true);
    await refresh();
  }catch(err){setText("loginError",err.message);}
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
