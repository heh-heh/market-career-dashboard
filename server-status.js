"use strict";
(() => {
  const status=document.getElementById("metricStatus");
  const target=document.getElementById("serverMetrics");
  async function refresh(){
    // The home Dashboard-only API already has its own PUBLIC read-only HTTPS
    // endpoint. Never call a private admin, Tailscale Serve :8444 or expose auth.
    // Prefer the verified configured public API; otherwise use the existing
    // read-only dashboard Funnel hostname (never the private admin :8444).
    const base=window.DASHBOARD_CONFIG?.apiBase||"https://heh-heh.tail39e546.ts.net";
    if(typeof base!=="string"||!/^https:\/\//.test(base)){
      status.textContent="공개 서버 지표 API 설정을 확인할 수 없습니다.";
      window.ServerMetrics.render(target,null);
      return;
    }
    status.textContent="측정 데이터 확인 중…";
    try{
      const controller=new AbortController();
      const timer=setTimeout(()=>controller.abort(),10000);
      let response;
      try{response=await fetch(base.replace(/\/$/,"")+"/api/server-telemetry",{
        cache:"no-store",signal:controller.signal,credentials:"omit"
      });}finally{clearTimeout(timer);}
      if(!response.ok)throw new Error("API 응답 "+response.status);
      const data=await response.json();
      if(data.mode!=="PUBLIC_READ_ONLY_AGGREGATED")throw new Error("공개용 서버 데이터가 아닙니다.");
      window.ServerMetrics.render(target,data);
      status.textContent=data.stale?"데이터 갱신 지연: 서버 수집기를 확인하세요.":"서버 지표가 갱신되었습니다.";
    }catch(err){
      status.textContent="현재 서버 지표를 조회할 수 없습니다. 홈서버의 공개 데이터 API 또는 수집기 연결을 확인하세요.";
      window.ServerMetrics.render(target,null);
    }
  }
  document.getElementById("refreshMetrics").addEventListener("click",refresh);
  refresh();
  setInterval(()=>{if(!document.hidden)refresh();},30000);
})();
