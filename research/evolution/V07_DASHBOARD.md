# V0.7 연구 대시보드

GitHub Pages의 `admin.html#ai`만 갱신한다. EC2 배포·재시작, 학습 중단, 수집기/매매/정책 변경은 하지 않는다.
기존 V0.7 학습·동결 검증·분석·ZIP·재실행 버튼과 인증을 그대로 사용한다.

- 활성 화면에서 15초마다 기존 `v07 status`, `v07 log`, `v07 report`를 조회한다.
- AI 화면에서만 기존 읽기 전용 `/api/admin/server-health`를 추가 조회한다.
  CPU는 `cpu.usedPct`를 사용하며 load average를 사용률로 환산하지 않는다.
  선택적인 health 조회가 실패해도 학습 상태는 표시하고, 없는 사용률은 집계 중으로 남긴다.
- 처리일은 `completedSessions / requestedSessions`, 실제 평가일은 `evaluatedSessionCount`다.
  사용일은 최종 `selectedSessionCount` 또는 실행 중 `selectedSessions`다.
  제외일은 최종 `excludedSessionCount`를 우선한다. 실행 중에는 처리일-사용일-미확정 창의
  일수가 모두 존재하고 정합적일 때만 도출한다. 직접 결측일과 버려진 창의 전체 일수를 혼동하지 않는다.
- 완료 세대 수와 무효 시도는 보고된 값만 표시한다. 현재 5일 창 진행률은
  `windowSessionsCompleted / evaluationWindowSessions`가 있을 때만 표시한다.
  잠재 창 개수를 목표 세대 수로 표시하거나 ETA를 추정하지 않는다.
- 그래프는 train의 `completedTrainGenerations` 이하이며 5개의 평가 날짜가 있는 세대만 표시한다.
  수익률/MDD는 비율을 %, fitness는 원점수로 표시한다. 누락된 관측은 선을 연결하지 않는다.
- 단계 상태는 worker 상태를 우선한다. 실패/중단된 worker를 run.json의 오래된 running 값으로 덮지 않는다.
  재실행 버튼은 실패/중단 시에만 활성화하며, 실제 실행 허가는 기존 backend flock/안전 게이트가 결정한다.
- 감사의 제외 표본과 실제 실행의 완료 분석을 구분한다. 최신 처리 사유는 상태와 로그에서 확인한다.
- 상태 파일 갱신 시각이 없으면 미제공으로 표시한다. 별도의 조회 시각은 브라우저가 응답을 받은 시각이며 KST다.
  갱신 실패 시 마지막 수치가 남아 있음을 경고한다. unknown/null을 0이나 수익률로 만들지 않는다.
- 최근 로그 25줄, 실패 재실행/JSON/감사/시험 분석은 details로 분리한다. Final OOS는 CLOSED다.

## 검증

```bash
node research/test_v07_dashboard_smoke.cjs
python -m unittest discover -s research -p 'test_*.py'
for test in research/test_*_smoke.cjs; do node "$test" || exit; done
```

개발 환경에 Playwright/Chromium이 있을 때만 선택적으로:

```bash
BROWSER_BIN=/usr/bin/chromium node research/test_v07_dashboard_browser.cjs
```

브라우저 검사는 실제 admin HTML을 사용한다. 인증 시작만 합성으로 대체하고 모든 네트워크를
가로채며 EC2로 전달하지 않는다. 320/390/768/1280px에서 넘침, 로그25줄, 완료 세대 필터,
기존 학습 버튼의 모의 요청을 검사한다. `/tmp/v07-dashboard-<width>.png`는 합성 fixture 화면이다.
실제 운영 학습 성과를 보여주는 이미지가 아니다. 운영 서버에 Playwright/Node를 설치할 필요는 없다.

UI 번들의 현재 SHA256과 이전 공개 버전 hash는 함께 유지한다. 기존 설치 도구와의 호환성을
회귀 검증하되 이번 작업에서는 EC2 설치 명령을 실행하지 않는다. Pages 자동 배포만 사용한다.
