"use strict";
/* Shared, dependency-free, read-only telemetry charts for public Pages and admin.
   Operates only on schema-approved server values; never sends control requests. */
(() => {
  const NS="http://www.w3.org/2000/svg";
  const el=(tag,cls,txt)=>{
    const node=document.createElement(tag);
    if(cls)node.className=cls;
    if(txt!==undefined)node.textContent=String(txt);
    return node;
  };
  const svg=(tag,attrs={})=>{
    const n=document.createElementNS(NS,tag);
    for(const [key,val] of Object.entries(attrs))n.setAttribute(key,String(val));
    return n;
  };
  const finite=value=>value!==null&&value!==undefined&&typeof value==="number"&&Number.isFinite(value);
  const format=(v,digits=1)=>finite(v)?v.toLocaleString("ko-KR",{maximumFractionDigits:digits}):"측정 불가";
  const pct=v=>finite(v)?format(v)+"%":"측정 불가";
  function chart(label,rows,series,{maxValue=null,unit="",note=""}={}){
    const panel=el("section","metric-panel");
    panel.append(el("h3","",label));
    const graphic=svg("svg",{viewBox:"0 0 640 174",role:"img","aria-label":label+" 최근 측정 그래프",preserveAspectRatio:"none"});
    for(const y of [32,65,98,131]){
      graphic.appendChild(svg("line",{x1:32,y1:y,x2:625,y2:y,stroke:"#34425c","stroke-width":1}));
    }
    const available=series.flatMap(s=>rows.map(r=>r?.[s.key]).filter(finite));
    const peak=maxValue||Math.max(1,...available)*1.15;
    const usable=rows.length>1&&available.length>1;
    if(usable){
      for(const s of series){
        let segment=[];
        const flush=()=>{
          if(segment.length>1){
            const line=svg("polyline",{points:segment.join(" "),fill:"none",stroke:s.color,
                                    "stroke-width":2.6,"stroke-linecap":"round","stroke-linejoin":"round"});
            graphic.appendChild(line);
          }
          segment=[];
        };
        for(let i=0;i<rows.length;i++){
          const v=rows[i]?.[s.key];
          if(!finite(v)){flush();continue;}
          const x=32+593*(i/(rows.length-1));
          const y=131-Math.min(1,Math.max(0,v/peak))*111;
          segment.push(x.toFixed(2)+","+y.toFixed(2));
        }
        flush();
      }
    }
    const top=svg("text",{x:3,y:23,fill:"#93a9cb","font-size":11});
    top.textContent=format(peak,2)+unit;
    graphic.appendChild(top);
    for(const [x,text] of [[32,"이전"],[584,"최근"]]){
      const t=svg("text",{x,y:159,fill:"#93a9cb","font-size":12});
      t.textContent=text;graphic.appendChild(t);
    }
    panel.appendChild(graphic);
    if(!usable)panel.appendChild(el("p","metric-muted","시간별 추이 데이터 축적 중"));
    const legend=el("div","metric-legend");
    for(const s of series){
      const v=rows.length?rows[rows.length-1]?.[s.key]:null;
      const wrap=el("span");
      const dot=el("span","metric-dot");dot.style.background=s.color;
      wrap.append(dot,el("span","",s.name+": "+(finite(v)?format(v,2)+unit:"측정 불가")));
      legend.appendChild(wrap);
    }
    panel.appendChild(legend);
    if(note){
      const details=el("details","metric-explainer");
      details.append(el("summary","metric-muted","측정 기준"),el("p","metric-muted",note));
      panel.appendChild(details);
    }
    return panel;
  }
  function render(target,data){
    if(!target)return;
    target.replaceChildren();
    if(!data?.ok){
      target.appendChild(el("div","metric-panel metric-muted","실시간 시스템 측정 데이터를 아직 받지 못했습니다. 홈서버 수집 서비스를 확인하세요."));
      return;
    }
    const rows=Array.isArray(data.history)?data.history.slice(-720):[];
    const m=data.metrics||{};
    const stamp=data.updatedAt?new Date(data.updatedAt):null;
    const text=stamp&&!Number.isNaN(stamp.getTime())?stamp.toLocaleString("ko-KR",{hour12:false}):"—";
    const intro=el("div","metric-intro");
    const actions=el("div","metric-intro-actions");
    const toggle=el("button","metric-button",target.classList.contains("metric-expanded")?"작게 보기":"크게 보기");
    toggle.type="button";
    toggle.setAttribute("aria-pressed",String(target.classList.contains("metric-expanded")));
    toggle.addEventListener("click",()=>{
      const enabled=target.classList.toggle("metric-expanded");
      toggle.textContent=enabled?"작게 보기":"크게 보기";
      toggle.setAttribute("aria-pressed",String(enabled));
    });
    actions.append(el("span","metric-muted","최종 수집: "+text+" · 60초 간격"),toggle);
    intro.append(el("strong","",data.stale?"● 데이터 갱신 지연":"● 서버 리소스 실시간 요약"),actions);
    target.appendChild(intro);
    const cards=el("div","metric-cards");
    const items=[
      ["CPU",pct(m.cpuPct),"사용률"],
      ["메모리",pct(m.memoryPct),
       finite(m.memoryUsedGiB)&&finite(m.memoryTotalGiB)?format(m.memoryUsedGiB)+" / "+format(m.memoryTotalGiB)+" GiB":"실측 대기"],
      ["네트워크 ↓",finite(m.networkRxMbps)?format(m.networkRxMbps,3)+" Mbps":"측정 불가","물리 NIC 수신"],
      ["네트워크 ↑",finite(m.networkTxMbps)?format(m.networkTxMbps,3)+" Mbps":"측정 불가","물리 NIC 송신"],
      ["루트 디스크",pct(m.diskPct),
       finite(m.diskUsedGiB)&&finite(m.diskTotalGiB)?format(m.diskUsedGiB)+" / "+format(m.diskTotalGiB)+" GiB":"실측 대기"],
      ["데이터 디스크",pct(m.dataDiskPct),
       finite(m.dataDiskUsedGiB)&&finite(m.dataDiskTotalGiB)?format(m.dataDiskUsedGiB)+" / "+format(m.dataDiskTotalGiB)+" GiB":"실측 대기"],
      ["CPU 패키지 전력",finite(m.cpuPackageWatts)?format(m.cpuPackageWatts,2)+" W":"측정 불가","RAPL · 서버 전체 전력 아님"],
      ["서버 전체 소비전력","측정기 미연결","스마트플러그 등 별도 계측 필요"],
    ];
    for(const [name,value,sub] of items){
      const item=el("div","metric-card");
      item.append(el("span","metric-muted",name),el("strong","",value),el("small","metric-muted",sub));
      cards.appendChild(item);
    }
    target.appendChild(cards);
    const charts=el("div","metric-charts");
    charts.append(
      chart("CPU 사용률",rows,[{key:"cpuPct",name:"CPU",color:"#8c9aff"}],{maxValue:100,unit:"%"}),
      chart("메모리 사용률",rows,[{key:"memoryPct",name:"RAM",color:"#5ed1a4"}],{maxValue:100,unit:"%"}),
      chart("실시간 네트워크",rows,[
        {key:"networkRxMbps",name:"수신",color:"#7cb7ff"},
        {key:"networkTxMbps",name:"송신",color:"#ffca7a"}],{unit:" Mbps",note:"활성 물리 네트워크 인터페이스 합계이며 VPN·컨테이너 트래픽은 중복 계산하지 않습니다."}),
      chart("저장공간 사용률",rows,[
        {key:"diskPct",name:"루트",color:"#d6a6ff"},
        {key:"dataDiskPct",name:"데이터",color:"#ff9ea9"}],{maxValue:100,unit:"%",note:"두 경로가 같은 물리 디스크일 경우 수치가 동일할 수 있습니다."}),
      chart("CPU 패키지 소비전력",rows,[
        {key:"cpuPackageWatts",name:"CPU package",color:"#ffb678"}],{unit:" W",note:"지원되는 Intel RAPL 에너지 센서가 있을 때만 표시합니다. 팬·GPU·스토리지·PSU 등을 포함한 시스템 전체 전력은 측정하지 않습니다."})
    );
    target.appendChild(charts);
  }
  window.ServerMetrics={render};
})();
