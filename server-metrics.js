"use strict";
/* Shared read-only performance view. No fetch, credentials or control operations. */
(() => {
  const NS="http://www.w3.org/2000/svg", states=new WeakMap();
  let sequence=0;
  const finite=v=>typeof v==="number"&&Number.isFinite(v);
  const number=(v,digits=1)=>finite(v)?v.toLocaleString("ko-KR",{maximumFractionDigits:digits}):"측정 불가";
  const value=(v,unit)=>finite(v)?number(v,unit===" Mbps"?3:1)+unit:"측정 불가";
  const node=(tag,cls,text)=>{
    const n=document.createElement(tag);
    if(cls)n.className=cls;
    if(text!==undefined)n.textContent=String(text);
    return n;
  };
  const svg=(tag,attrs={})=>{
    const n=document.createElementNS(NS,tag);
    for(const [key,val] of Object.entries(attrs))n.setAttribute(key,String(val));
    return n;
  };
  const series=(key,name,color)=>({key,name,color});
  const resources=[
    {id:"cpu",name:"CPU",sub:"프로세서 사용률",unit:"%",limit:100,series:[series("cpuPct","CPU","#63cbe8")]},
    {id:"memory",name:"메모리",sub:"RAM 사용률",unit:"%",limit:100,series:[series("memoryPct","RAM","#bb91ed")],capacity:["memoryUsedGiB","memoryTotalGiB"]},
    {id:"root",name:"루트 디스크",sub:"저장공간 사용률",unit:"%",limit:100,series:[series("diskPct","루트","#85ce93")],capacity:["diskUsedGiB","diskTotalGiB"],note:"사용량은 저장공간 점유율이며 디스크 I/O 활성 시간이나 읽기·쓰기 속도가 아닙니다."},
    {id:"data",name:"데이터 디스크",sub:"저장공간 사용률",unit:"%",limit:100,series:[series("dataDiskPct","데이터","#e4ac71")],capacity:["dataDiskUsedGiB","dataDiskTotalGiB"],note:"루트와 데이터 경로가 같은 물리 디스크에 있으면 사용량이 같을 수 있습니다. 디스크 I/O 사용률이 아닙니다."},
    {id:"network",name:"네트워크",sub:"물리 NIC · 수신 / 송신",unit:" Mbps",series:[series("networkRxMbps","수신","#e4cb76"),series("networkTxMbps","송신","#6aaef1")],note:"활성 물리 NIC의 관측된 전송률입니다. VPN·컨테이너 트래픽은 중복 합산하지 않습니다."},
    {id:"power",name:"CPU 전력",sub:"CPU 패키지 · RAPL",unit:" W",series:[series("cpuPackageWatts","CPU 패키지","#ec8d9f")],note:"RAPL 센서로 측정한 CPU 패키지 전력입니다. 서버 전체 소비전력이 아니며 지원되지 않는 센서는 측정 불가로 표시합니다."},
    {id:"gpu",name:"GPU",sub:"사용률 · VRAM · 온도 · 전력",unit:"%",limit:100,optional:true,series:[series("gpuPct","사용률","#929aef")]}
  ];
  function timestamp(v){
    if(typeof v!=="string"||!v.trim())return NaN;
    return Date.parse(v);
  }
  const clock=t=>Number.isFinite(t)?new Date(t).toLocaleTimeString("ko-KR",{timeZone:"Asia/Seoul",hour12:false,hour:"2-digit",minute:"2-digit"}):"—";
  const date=t=>Number.isFinite(t)?new Date(t).toLocaleString("ko-KR",{timeZone:"Asia/Seoul",hour12:false})+" KST":"측정 시각 없음";
  const current=(resource,m)=>resource.series.map(s=>value(m[s.key],resource.unit)).join(" / ");
  function capacity(resource,m){
    if(!resource.capacity)return resource.sub;
    const [used,total]=resource.capacity.map(k=>m[k]);
    return finite(used)&&finite(total)?number(used)+" / "+number(total)+" "+(resource.capacityUnit||"GiB"):resource.sub;
  }
  function history(data){
    return (Array.isArray(data?.history)?data.history:[]).slice(-720)
      .filter(r=>r&&Number.isFinite(timestamp(r.at)))
      .map(r=>({row:r,time:timestamp(r.at)})).sort((a,b)=>a.time-b.time);
  }
  function graph(resource,rows,{mini=false,sampling=60}={}){
    const w=mini?112:800,h=mini?40:300,pad=mini?2:0;
    const graphic=svg("svg",{viewBox:`0 0 ${w} ${h}`,preserveAspectRatio:"none",class:mini?"metric-spark":"metric-graph"});
    if(mini)graphic.setAttribute("aria-hidden","true");
    else{
      graphic.setAttribute("role","img");graphic.setAttribute("aria-label",resource.name+" 관측 시각 기준 시계열 그래프");
      for(let i=0;i<=4;i++){
        const y=pad+(h-pad*2)*i/4;
        graphic.append(svg("line",{x1:pad,y1:y,x2:w-pad,y2:y,class:"metric-gridline"}));
      }
      for(let i=0;i<=8;i++){
        const x=pad+(w-pad*2)*i/8;
        graphic.append(svg("line",{x1:x,y1:pad,x2:x,y2:h-pad,class:"metric-gridline"}));
      }
    }
    const values=rows.flatMap(r=>resource.series.map(s=>r.row[s.key]).filter(finite));
    const peak=resource.limit||Math.max(1,...values)*1.1;
    const start=rows[0]?.time,end=rows[rows.length-1]?.time;
    for(const s of resource.series){
      let segment=[],previous=null;
      const flush=()=>{
        if(segment.length>1)graphic.append(svg("polyline",{points:segment.map(p=>p.join(",")).join(" "),fill:"none",stroke:s.color,"stroke-width":mini?1.6:2,"vector-effect":"non-scaling-stroke"}));
        else if(segment.length===1)graphic.append(svg("circle",{cx:segment[0][0],cy:segment[0][1],r:mini?1.5:2.5,fill:s.color}));
        segment=[];
      };
      for(const r of rows){
        const v=r.row[s.key];
        if(!finite(v)){flush();previous=null;continue;}
        if(previous!==null&&r.time-previous>sampling*1500)flush();
        const x=end>start?pad+(w-pad*2)*(r.time-start)/(end-start):w/2;
        const y=h-pad-(h-pad*2)*Math.min(1,Math.max(0,v/peak));
        segment.push([x.toFixed(2),y.toFixed(2)]);previous=r.time;
      }
      flush();
    }
    if(mini)return graphic;
    // HTML labels remain readable on narrow screens; SVG scaling affects only the plot.
    const frame=node("div","metric-chart");
    frame.append(node("div","metric-chart-scale",values.length||resource.limit?value(peak,resource.unit):"범위 미정"),graphic);
    const axis=node("div","metric-time-axis");
    axis.append(node("span","",clock(start)),node("span","",clock(end)));frame.append(axis);
    if(!values.length)frame.append(node("p","metric-chart-empty","측정 데이터 없음"));
    return frame;
  }
  function gpuDetail(resource,m,rows,data,state){
    const panel=node("section","metric-detail metric-gpu");
    panel.id=state.id;panel.setAttribute("aria-labelledby",state.id+"-title");
    const header=node("header","metric-detail-head");
    const title=node("h2","","GPU");title.id=state.id+"-title";
    header.append(title,node("div","metric-current",value(m.gpuPct,"%")));
    panel.append(header,node("p","metric-muted metric-detail-sub","그래픽 프로세서 · 4개 항목 통합 보기"));
    const info=node("div","metric-detail-meta");
    info.append(node("span","",data?.stale?"갱신 지연 · 마지막 관측값":"관측된 사용량"),
      node("span","","최근 갱신: "+date(timestamp(data?.updatedAt))));
    panel.append(info);
    const gpuResources=[
      {name:"GPU 사용률",unit:"%",limit:100,series:[series("gpuPct","사용률","#929aef")]},
      {name:"VRAM 사용량",unit:" MiB",series:[series("gpuMemoryUsedMiB","VRAM","#86badc")]},
      {name:"GPU 온도",unit:"°C",series:[series("gpuTempC","온도","#e8a36d")]},
      {name:"GPU 소비전력",unit:" W",series:[series("gpuWatts","전력","#cf94d9")]}
    ];
    const sampling=finite(data?.samplingSeconds)&&data.samplingSeconds>0?data.samplingSeconds:60;
    const sections=node("div","metric-gpu-panels");
    for(const item of gpuResources){
      const block=node("section","metric-gpu-panel");
      const heading=node("h3","",item.name);
      const key=item.series[0].key;
      block.append(heading,node("div","metric-current",value(m[key],item.unit)));
      if(key==="gpuMemoryUsedMiB"&&finite(m.gpuMemoryTotalMiB))
        block.append(node("p","metric-muted","전체 "+number(m.gpuMemoryTotalMiB)+" MiB · 사용 가능 "+(finite(m[key])?number(Math.max(0,m.gpuMemoryTotalMiB-m[key])):"측정 불가")+" MiB"));
      block.append(graph(item,rows,{sampling}));
      const vals=rows.map(r=>r.row[key]).filter(finite);
      const stats=node("p","metric-muted");
      stats.textContent=vals.length?"평균 "+value(vals.reduce((a,b)=>a+b,0)/vals.length,item.unit)+" · 최대 "+value(Math.max(...vals),item.unit)+" · 최소 "+value(Math.min(...vals),item.unit):"측정 기록 없음";
      block.append(stats);sections.append(block);
    }
    panel.append(sections,node("p","metric-muted metric-footnote","GPU 전력은 그래픽카드 측정값이며 서버 전체 소비전력이 아닙니다. 측정 누락은 그래프에서 연결하지 않습니다."));
    return panel;
  }
  function detail(resource,m,rows,data,state){
    const panel=node("section","metric-detail metric-"+resource.id);
    panel.id=state.id;panel.setAttribute("aria-labelledby",state.id+"-title");
    const header=node("header","metric-detail-head");
    const title=node("h2","",resource.name);title.id=state.id+"-title";
    const headline=node("div","metric-current",current(resource,m));
    header.append(title,headline);panel.append(header,node("p","metric-muted metric-detail-sub",capacity(resource,m)));
    const meta=node("div","metric-detail-meta");
    meta.append(node("span","",data?.stale?"갱신 지연 · 마지막 관측값":"관측된 사용량"),node("span","","최근 갱신: "+date(timestamp(data?.updatedAt))));
    panel.append(meta,graph(resource,rows,{sampling:finite(data?.samplingSeconds)&&data.samplingSeconds>0?data.samplingSeconds:60}));
    const stats=node("div","metric-statistics");
    for(const s of resource.series){
      const vals=rows.map(r=>r.row[s.key]).filter(finite);
      const group=node("section","metric-stat-group");
      group.append(node("h3","",s.name));
      const dl=node("dl","metric-stat-values");
      const values=[["현재",m[s.key]],["평균",vals.length?vals.reduce((a,b)=>a+b,0)/vals.length:null],["최대",vals.length?Math.max(...vals):null],["최소",vals.length?Math.min(...vals):null]];
      for(const [name,v] of values){const cell=node("div");cell.append(node("dt","",name),node("dd","",value(v,resource.unit)));dl.append(cell);}
      group.append(dl,node("p","metric-muted",vals.length?"유효 관측 "+vals.length+"개 · 표시된 기록 구간 기준":"유효한 기록 없음"));stats.append(group);
    }
    panel.append(stats);
    if(resource.capacity){
      const [used,total]=resource.capacity.map(k=>m[k]);
      if(finite(total)&&finite(used)&&total>=used)panel.append(node("p","metric-muted","사용 가능: "+number(total-used)+" "+(resource.capacityUnit||"GiB")+" · 전체: "+number(total)+" "+(resource.capacityUnit||"GiB")));
    }
    if(rows.length<2)panel.append(node("p","metric-muted","시간별 추이 데이터 축적 중"));
    panel.append(node("p","metric-muted metric-footnote","누락된 값·관측 간격은 그래프로 연결하지 않습니다. 평균·최대·최소는 유효한 기록만 집계합니다."));
    if(resource.note)panel.append(node("p","metric-muted metric-footnote",resource.note));
    return panel;
  }
  function render(target,data){
    if(!target)return;
    let state=states.get(target);
    if(!state){state={selected:"cpu",id:"metric-detail-"+(++sequence)};states.set(target,state);}
    const focused=document.activeElement;
    const focusId=target.contains(focused)?focused?.dataset?.resource:null;
    state.data=data;
    const rows=history(data?.ok?data:null),m=data?.ok&&data.metrics&&typeof data.metrics==="object"?data.metrics:{};
    const available=resources.filter(r=>!r.optional||r.series.some(s=>finite(m[s.key])||rows.some(row=>finite(row.row[s.key]))));
    if(!available.some(r=>r.id===state.selected))state.selected="cpu";
    target.replaceChildren();
    const intro=node("div","metric-intro");
    intro.append(node("strong","",!data?.ok?"측정 데이터 미수신":data.stale?"데이터 갱신 지연":"성능"),node("span","metric-muted","최종 수집: "+date(timestamp(data?.updatedAt))));
    target.append(intro);
    const workbench=node("div","metric-workbench"),list=node("nav","metric-resource-list");
    list.setAttribute("aria-label","서버 성능 항목");
    for(const resource of available){
      const selected=resource.id===state.selected;
      const card=node("button","metric-resource metric-"+resource.id+(selected?" is-selected":""));
      card.type="button";card.dataset.resource=resource.id;
      card.setAttribute("aria-pressed",String(selected));card.setAttribute("aria-controls",state.id);
      card.append(graph(resource,rows.slice(-60),{mini:true,sampling:finite(data?.samplingSeconds)&&data.samplingSeconds>0?data.samplingSeconds:60}));
      const text=node("span","metric-resource-text");
      text.append(node("strong","metric-resource-title",resource.name),node("span","metric-resource-value",current(resource,m)),node("small","metric-muted",capacity(resource,m)));
      card.append(text);
      card.addEventListener("click",()=>{state.selected=resource.id;render(target,state.data);});
      card.addEventListener("keydown",e=>{
        const index=available.findIndex(r=>r.id===resource.id);
        let next;
        if(["ArrowDown","ArrowRight"].includes(e.key))next=(index+1)%available.length;
        else if(["ArrowUp","ArrowLeft"].includes(e.key))next=(index+available.length-1)%available.length;
        else if(e.key==="Home")next=0;else if(e.key==="End")next=available.length-1;
        if(next!==undefined){e.preventDefault();state.selected=available[next].id;render(target,state.data);listButton(target,state.selected)?.focus({preventScroll:true});}
      });
      list.append(card);
    }
    workbench.append(list,state.selected==="gpu"?gpuDetail(available.find(r=>r.id===state.selected),m,rows,data,state):detail(available.find(r=>r.id===state.selected),m,rows,data,state));target.append(workbench);
    if(focusId)listButton(target,focusId)?.focus({preventScroll:true});
  }
  function listButton(target,id){return target.querySelector('[data-resource="'+id+'"]');}
  window.ServerMetrics={render};
})();
