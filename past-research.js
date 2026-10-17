/* Archived evolution results only: never submits research start/retry commands. */
(()=>{
"use strict";
const $=id=>document.getElementById(id);
const versions={v082:"V0.8.2",v081:"V0.8.1",v08:"V0.8",v07:"V0.7"};
const finite=v=>typeof v==="number"&&Number.isFinite(v)?v:null;
const pct=v=>finite(v)===null?"데이터 없음":(v*100).toFixed(2)+"%";
const escape=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let busy=0;
const results=Object.create(null);
function rowsFor(version,d){
  const raw=Array.isArray(d.generations)?d.generations:[];
  const denominator=d.runs?.train?.population;
  return raw.slice(-100).map((g,i)=>{
    const mean=version==="v07"?g.returns?.mean:g.meanReturn;
    const parentCount=version==="v07"?g.selection?.parentPoolSize:g.parents;
    const population=version==="v07"||version==="v08"?denominator:g.population;
    const validSize=Number.isInteger(population)&&population>0&&population<=10000;
    const validParents=Number.isInteger(parentCount)&&parentCount>=0;
    return {
      generation:Number.isInteger(g.generation)&&g.generation>0?g.generation:i+1,
      return:finite(mean),
      survival:validSize&&validParents&&parentCount<=population?parentCount/population:null,
      parents:validSize&&validParents&&parentCount<=population?parentCount:null,
      population:validSize?population:null,
      date:Array.isArray(g.dates)&&g.dates.length?String(g.dates[g.dates.length-1]):null
    };
  });
}
function graph(svgId,legendId,data,key,unit,color,alwaysPercentageRange){
  const svg=$(svgId),legend=$(legendId);
  if(!svg||!legend)return;
  const measured=data.map((r,i)=>({i,val:finite(r[key])})).filter(x=>x.val!==null);
  if(!measured.length){
    svg.innerHTML='<text x="30" y="100" fill="currentColor" opacity=".7">해당 세대별 데이터 없음</text>';
    legend.textContent="그래프를 만들 수 있는 검증된 값이 없습니다.";return;
  }
  const width=660,height=215,left=62,right=24,top=20,bottom=36;
  const min=alwaysPercentageRange?0:Math.min(0,...measured.map(p=>p.val));
  const max=alwaysPercentageRange?1:Math.max(0,...measured.map(p=>p.val));
  const gap=max-min||.01;
  const scaleX=i=>left+(data.length<=1?.5:i/(data.length-1))*(width-left-right);
  const scaleY=v=>top+(max-v)/gap*(height-top-bottom);
  const px=v=>v.toFixed(1);
  const valueString=v=>pct(v);
  let svgParts=[];
  for(let step=0;step<=4;step++){
    const v=min+(max-min)*step/4;
    const y=px(scaleY(v));
    svgParts.push('<line x1="'+left+'" y1="'+y+'" x2="'+(width-right)+'" y2="'+y+'" stroke="currentColor" opacity=".12"/>');
    svgParts.push('<text x="'+(left-9)+'" y="'+(+y+4)+'" text-anchor="end" font-size="11" fill="currentColor" opacity=".8">'+escape((v*100).toFixed(1)+'%')+'</text>');
  }
  const bottomY=height-bottom;
  const indexes=data.length<=1?[0]:[0,Math.floor((data.length-1)/2),data.length-1].filter((v,i,a)=>a.indexOf(v)===i);
  for(const idx of indexes){
    svgParts.push('<text x="'+px(scaleX(idx))+'" y="'+(height-11)+'" text-anchor="middle" font-size="11" fill="currentColor" opacity=".8">'+escape('세대 '+data[idx].generation)+'</text>');
  }
  const coords=data.map((r,i)=>({x:scaleX(i),y:finite(r[key])===null?null:scaleY(r[key]),row:r}));
  let chunk=[];
  const drawChunk=()=>{
    if(chunk.length>1)svgParts.push('<polyline fill="none" stroke="'+color+'" stroke-width="2.5" points="'+chunk.map(p=>px(p.x)+','+px(p.y)).join(' ')+'"/>');
    chunk=[];
  };
  for(const point of coords){
    if(point.y===null){drawChunk();continue}
    chunk.push(point);
  }
  drawChunk();
  for(const p of coords){
    if(p.y===null)continue;
    const detail=p.row.parents!==null?' · 부모 '+p.row.parents+'/'+p.row.population:'';
    const label='세대 '+p.row.generation+(p.row.date?' · '+p.row.date:'')+' · '+unit+' '+valueString(p.row[key])+detail;
    svgParts.push('<circle cx="'+px(p.x)+'" cy="'+px(p.y)+'" r="4" fill="'+color+'" stroke="#101b2f" stroke-width="1.5"><title>'+escape(label)+'</title></circle>');
  }
  svg.innerHTML=svgParts.join('');
  const last=measured[measured.length-1];
  legend.textContent='표시 '+measured.length+'/'+data.length+'세대 · 최근 확인 '+pct(last.val)+(last.i<data.length-1?' (이전 유효 세대)':'');
}
function render(version,d){
  const rows=rowsFor(version,d);
  const validReturns=rows.filter(r=>r.return!==null).length;
  const validSurvival=rows.filter(r=>r.survival!==null).length;
  const last=rows.at(-1);
  const train=d.stages?.train||{},smoke=d.stages?.smoke||{};
  const stage=train.phase&&train.phase!=="not_started"?train:smoke;
  const state=({completed:"완료",running:"진행 중",starting:"시작 중",failed:"실패 기록",interrupted:"중단 기록",not_started:"실행 전"})[stage.phase]||"기록 대기";
  $("pastResearchStatus").textContent=versions[version]+' · '+state+' · '+(d.jobId||"작업 ID 없음")+(rows.length?' · 완료된 세대 기록 '+rows.length+'개':' · 세대별 기록 없음')+' · Final OOS CLOSED';
  const tiles=[
    ["표시 세대",rows.length+"개"],
    ["유효 수익률",validReturns+"개 세대"],
    ["선발률 집계",validSurvival+"개 세대"],
    ["마지막 평가",last?.date||"데이터 없음"]
  ];
  $("pastResearchOverview").innerHTML=tiles.map(([title,value])=>'<div class="past-stat"><span class="label">'+escape(title)+'</span><strong>'+escape(value)+'</strong></div>').join("");
  graph("pastReturnChart","pastReturnLegend",rows,"return","평균 수익률","#42c9ad",false);
  graph("pastSurvivalChart","pastSurvivalLegend",rows,"survival","부모 선발률","#a8a4ff",true);
}
async function refresh(force=false){
  const version=$("pastResearchVersion")?.value;
  if(!Object.hasOwn(versions,version))return;
  if(!token()){$("pastResearchStatus").textContent="관리자 로그인이 필요합니다.";return}
  const seq=++busy;
  if(!force&&results[version]){render(version,results[version]);return}
  $("pastResearchRefresh").disabled=true;
  $("pastResearchStatus").textContent=versions[version]+" 연구 결과 조회 중...";
  try{
    const res=await req("/api/admin/console",{method:"POST",body:JSON.stringify({command:version+" report"})});
    if(res?.ok===false||res?.exitCode)throw Error(res?.output||res?.error||"보고서 조회 실패");
    const data=JSON.parse(res.output||"{}");
    if(data.finalOosGate!=="CLOSED"||!Array.isArray(data.generations)&&data.jobId){
      throw Error("과거 연구 보고서 형식 또는 OOS 정책이 일치하지 않습니다.");
    }
    if(seq!==busy)return;
    results[version]=data;
    render(version,data);
  }catch(e){
    if(seq!==busy)return;
    $("pastResearchStatus").textContent=versions[version]+" 결과 조회 실패: "+e.message;
    $("pastResearchOverview").replaceChildren();
    graph("pastReturnChart","pastReturnLegend",[],"return","평균 수익률","#42c9ad",false);
    graph("pastSurvivalChart","pastSurvivalLegend",[],"survival","부모 선발률","#a8a4ff",true);
  }finally{
    if(seq===busy)$("pastResearchRefresh").disabled=false;
  }
}
$("pastResearchVersion")?.addEventListener("change",()=>refresh());
$("pastResearchRefresh")?.addEventListener("click",()=>refresh(true));
window.refreshPastResearch=()=>refresh();
if(location.hash==="#past")refresh();
})();
