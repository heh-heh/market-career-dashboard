"use strict";
const scopes = ["server", "git"];
async function run(action) {
  const scope = action.split(".")[0];
  if (!scopes.includes(scope)) return;
  const output = document.getElementById(scope + "-output");
  const meta = document.getElementById(scope + "-meta");
  const buttons = [...document.querySelectorAll("#" + scope + "-actions button")];
  buttons.forEach(b => { b.disabled = true; });
  output.textContent = "조회 중: " + action;
  meta.textContent = "조회 중";
  meta.classList.remove("bad");
  try {
    const response = await fetch("/api/run?action=" + encodeURIComponent(action), {
      method: "GET", credentials: "same-origin", cache: "no-store"
    });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const result = await response.json();
    output.textContent = result.text || "(출력 없음)";
    meta.textContent = (result.ok ? "성공" : "실패") + " · " + new Date().toLocaleTimeString("ko-KR");
    meta.classList.toggle("bad", !result.ok);
  } catch (e) {
    output.textContent = "조회 오류: " + (e instanceof Error ? e.message : String(e));
    meta.textContent = "연결 오류";
    meta.classList.add("bad");
  } finally {
    buttons.forEach(b => { b.disabled = false; });
  }
}
for (const button of document.querySelectorAll("button[data-action]")) {
  button.addEventListener("click", () => run(button.dataset.action));
}
run("server.containers");
run("git.status");
