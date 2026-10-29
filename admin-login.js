"use strict";
(() => {
  const HOST="https://heh-heh.tail39e546.ts.net:8444";
  const form=document.getElementById("login");
  const button=document.getElementById("submit");
  const message=document.getElementById("status");
  form.addEventListener("submit",async event=>{
    event.preventDefault();
    button.disabled=true;
    message.textContent="홈서버에서 인증을 확인하는 중...";
    const body={
      username:document.getElementById("user").value.trim(),
      password:document.getElementById("password").value,
      otp:document.getElementById("otp").value.trim(),
    };
    try {
      const abort=new AbortController();
      const timeout=setTimeout(()=>abort.abort(),12000);
      let response;
      try {
        response=await fetch(HOST+"/api/remote/login",{
          method:"POST",mode:"cors",credentials:"omit",
          cache:"no-store",redirect:"error",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify(body),signal:abort.signal
        });
      } finally {clearTimeout(timeout);}
      document.getElementById("password").value="";
      document.getElementById("otp").value="";
      if(!response.ok){
        if(response.status===401)throw new Error("ID, 비밀번호 또는 OTP가 일치하지 않습니다.");
        if(response.status===429)throw new Error("인증 시도가 너무 많습니다. 잠시 후 다시 시도하세요.");
        throw new Error("홈서버 인증 거절 (HTTP "+response.status+")");
      }
      const data=await response.json();
      if(!data.ok||typeof data.ticket!=="string")throw new Error("인증 응답 형식 오류");
      message.textContent="인증 성공 · 관리자 페이지로 이동합니다.";
      // One-time code is POSTed, never put into URL / localStorage / cookies on GitHub Pages.
      const relay=document.createElement("form");
      relay.method="POST";
      relay.action=HOST+"/auth/claim";
      relay.style.display="none";
      const field=document.createElement("input");
      field.type="hidden";field.name="ticket";field.value=data.ticket;
      relay.appendChild(field);
      document.body.appendChild(relay);
      relay.submit();
    }catch(err){
      document.getElementById("password").value="";
      document.getElementById("otp").value="";
      message.textContent=err instanceof TypeError || err.name==="AbortError"?
        "홈서버 HTTPS에 연결할 수 없습니다. Tailscale 연결 및 Serve :8444 상태를 확인하세요.":
        err.message;
      button.disabled=false;
    }
  });
})();
