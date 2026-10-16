# V0.7 가용 데이터 장기 연구 관리

새 기능은 기존 `v07_web_jobs.py`의 인증된 고정 명령/분리 프로세스를 확장한다.
7일 V07, strict 5일 V07, V3/V4, Simple, 수집기, 실주문 코드는 변경하지 않는다.
실제 EC2 장기 학습이나 검증은 개발/CI에서 시작하지 않는다.

## 먼저 기존 시험 결과 분석

알려진 EC2 시험 결과는 완료 세대 1, 무효 시도 2, 선택 거래일 5이다.
이 숫자만으로 어떤 종목/날짜가 원인인지 확정할 수 없다. 73개는 사전 감사의
잠재적 창 수이며, 보유 종목의 장중 결측으로 무효가 되는 실제 창은 별도다.
운영 서버 파일은 개발 환경에서 읽지 않았다.

설치 후 **관리 → 콘솔**에서 `v07 analyze`를 실행한다. 현재 job의 smoke,
완료된 train/holdout/validation을 읽어 `analysis.json`을 만든다.
기존 원본 run, 거래, 학습 결과를 수정하지 않는다. 다음 명령은 지정된 과거
시험을 별도로 분석한다(새 보고서 경로는 기존 run 밖이어야 하고 없어야 함).

```bash
cd /home/ubuntu/market-career-dashboard
.venv/bin/python v07_research_report.py \
  --run /mnt/research/v07-web-jobs/job-20261016T112358Z-6238c2b8505c/smoke/20261016T113459390939Z-available-c0e386c6 \
  --audit /mnt/research/v07-web-jobs/job-20261016T112358Z-6238c2b8505c/audit \
  --out /mnt/research/v07-trial-analysis-$(date -u +%Y%m%dT%H%M%SZ).json
```

보고서:

- 제외 날짜/원인/causedBy, 평가한 날짜와 실제 선발에 사용한 날짜
- 무효 창과 부분 창, 중단된 연속 창, 선택된 세대의 수익/fitness/MDD 분포
- 생존/사망/중립 개체, 부모 선발 횟수/이유, mutation/crossover/elite 기록
- 감사의 종목별 결측 분봉 수/결측 일수/비율, SPY/QQQ/SOXX 문제 일수
- 보유 중 관측 결측 카운트(단위: 개체 × 결측 관측; 서로 다른 장애 횟수가 아님)
- used 날짜와 선택 창의 일치, excluded와 used의 비중복, 5일 완성 여부,
  세대 수 및 window 파일 날짜의 일치, runner의 최종 SHA 검증 결과
- frozen 평가의 선택된 날짜별 개체 성과, 월별/진입 국면별 거래·손익,
  개체별 일말 누적 PnL drawdown 및 별도의 최대 일중 일별 MDD

과거 cancellation 카운터에는 종목/시각이 없을 수 있다. 이를 거래 종목이라고
임의 추정하지 않고 `UNKNOWN_IN_LEGACY_METRICS`로 표시한다. 종목별 감사 결측은
별도 사실로 보고한다. 원장 정합성 검사는 정책 독립 재시뮬레이션과 다르다.
원장 누락/불일치는 PASS가 되지 않으며 실제 전략 동등성은 기존 합성 회귀
테스트에서 검증한다. 보고서 `artifactHashes`는 읽은 핵심 원장의 SHA256이다.

## 학습/검증 규칙 보존

실제 학습 실행 파일은 검증된 별도 worktree
`/mnt/research/v07-availability-review`의 커밋
`60b0f43fc6cf2a3eafea823e7c2ef2dbcc811edc`에 고정한다.
HEAD가 다르거나 tracked 코드에 로컬 수정이 있으면 시작을 거부한다.
정책, fitness, genome, 비용, 데이터 선별 알고리즘을 이번 작업에서 바꾸지 않았다.

- train: 2022-01-01 ~ 2023-12-31 전체 XNYS 달력. 고정 20개체,
  설정상 상한 50. 기존 가용 모드처럼 자동 개체 수 증가는 사용하지 않는다.
- 각 일별 계좌 $100 초기화, 유효한 **연속 5거래일**만 세대교체.
- 기준 종목 불완전/보유 종목 결측은 기존 규칙대로 창 전체를 버리고 다음
  달력 날짜부터 시작. 조용히 날짜를 건너뛰어 연속 창으로 묶지 않는다.
- 실제 selected 날짜만 fitness와 lifetime 계좌에 반영. 무효 시도 거래는
  진단용으로 남되 selectionTrades와 observedTradesIncludingDiscarded를 구분한다.
- END 분봉 완료 후 신호, 다음 관측 완료 분봉 CLOSE proxy 체결.
  각 방향 2bps slippage + 1bps half-spread. 실제 호가 체결 검증 주장이 아니다.
- BULL/BEAR/SIDEWAYS 특징과 기존 위험/익절/청산 규칙 그대로.
- source plan/evidence/source SHA256을 시작과 종료에 검증. 파일 변경 시 실패하며
  자동 감사 갱신·보간·가짜 가격·manifest 검증 완화는 없다.
- holdout: 2024년 전체 달력; validation: 2025년 전체 달력.
  **둘 다 동일한 train/final_population.json**을 사용한다. 2024 결과로 재선발하지 않는다.
- train 완료, 최소 1개 실제 세대, resultsValid/inputHashesVerified/CLOSED를 확인하고
  train/run.json과 final_population.json SHA를 `frozen_training.json`으로 봉인한다.
  평가 전에 다시 대조하고 기존 runner도 평가 전후 유전자 불변을 확인한다.
- 불완전한 연간 coverage/표본 부족은 `PARTIAL_UNVERIFIED`; 부분 관측 수익률과
  연간 검증 수익률(null)을 혼동하지 않는다. 기존 연간 최소 거래 표본 30개체별
  기준을 유지한다. 2025는 Development Validation이며 튜닝에 사용하지 않는다.
- PROVISIONAL_UNREVIEWED_DATA, 표본 선택 편향/생존자 편향 경고 유지.
  **2026 Final OOS CLOSED**. API/콘솔에 날짜나 임의 셸 인자를 받지 않는다.

## 관리자 웹 사용

**AI → 연구 → V0.7 장기 학습 관리**:

1. 먼저 관리 → 콘솔의 기존 코드 준비 → 30종목 감사 → 학습 구간 선별 →
   10거래일 시험을 완료한다. 현재 job이 이미 완료했다면 다시 prepare하지 않는다.
2. `시험 결과 상세 분석`에서 실제 제외 원인을 읽는다.
3. `2022~2023 전체 학습 시작`은 명시적 확인 후 독립 worker를 실행한다.
4. `동결 2024 홀드아웃`, 완료 후 `동결 2025 개발 검증`을 각각 실행한다.
5. 결과 ZIP 준비 → 완료 후 ZIP 다운로드. raw CSV/토큰/서버 로그는 포함하지 않는다.

버튼과 콘솔은 동일한 인증/명령 허용 목록을 쓴다. 로그인 없이 명령이나
다운로드가 불가능하다. 상태/로그는 15초마다 활성 AI/콘솔 화면에서 갱신한다.
숫자는 status/window/audit/analysis에서만 읽으며 가짜 진행률/수익은 없다.
그래프는 실제 완료된 창의 평균 수익률이다. 일별 완료 로그와 선발 세대는 다르다.

새 고정 콘솔 명령:

```
v07 analyze
v07 train
v07 holdout
v07 validation
v07 export
v07 report
v07 status
v07 log
v07 retry train
v07 retry holdout
v07 retry validation
```

기존 `prepare/audit/select/smoke/help` 유지. POST `/api/admin/console`의 기존 관리자
인증 재사용. `GET /api/research/v07/download`도 관리자 인증, 고정 현 job ZIP만
내려준다. 사용자 지정 경로/임의 명령/날짜/실주문/ARM 제어는 제공하지 않는다.

## 실행 수명·중복 방지·복구

- 브라우저/SSM과 독립된 start_new_session, DEVNULL stdin, 닫힌 HTTP descriptor.
- start 직렬화와 별도로 `research.lock` flock을 worker와 학습 자식에 상속한다.
  API 재시작 후에도 잠금이 유지되며 worker만 죽고 자식이 살아도 중복 실행 금지.
- worker argv 전체 토큰 + `/proc` start tick + boot ID를 함께 검증.
  재부팅/죽은 PID/재사용 PID는 INTERRUPTED이지 완료가 아니다.
- **systemd 주의:** start_new_session은 cgroup 분리가 아니다. API cgroup 안에서
  시작할 때 API KillMode가 process가 아니면 `API_CGROUP_WOULD_KILL_RESEARCH`로
  차단한다. 설치 도구가 자동으로 systemd 설정을 바꾸지 않는다.
  운영자가 기존 프로세스 관리 정책과 충돌 여부를 검토해 API 서비스의
  `KillMode=process`를 먼저 확보해야 API 재시작 생존을 보장할 수 있다.

```bash
systemctl show market-career-dashboard.service -p KillMode -p ControlGroup -p MainPID
```

미설정인 경우 검토 후, 기존 drop-in을 덮어쓰지 않는 예:

```bash
sudo install -d /etc/systemd/system/market-career-dashboard.service.d
sudo sh -c 'set -C; printf "[Service]\nKillMode=process\n" > /etc/systemd/system/market-career-dashboard.service.d/v07-research-lifetime.conf'
sudo systemctl daemon-reload
systemctl show market-career-dashboard.service -p KillMode
```

`process`인지 반드시 확인한다. 위 설정은 서버 운영 정책 변경이므로 자동
수행하지 않았고, 수집기 서비스에는 적용하지 않는다. 업데이트 시 살아 있는
worker/flock이 있으면 설치를 막는다. 기존 API/수집기 무분별한 재시작 금지.

- 실패/중단 후 `v07 retry train|holdout|validation`은 seed/기간/설정을 그대로
  처음부터 재실행한다. **체크포인트 이어하기는 지원하지 않는다.**
- 이전 stage 상태/로그는 retries 아래에 복사하고, 이전 run 디렉터리는 이동/삭제하지
  않는다. 재시도는 별도 retry-UUID 출력 root를 사용하여 이전 완료 파일과도
  혼동하지 않는다. 이미 봉인된 학습은 재실행하지 않고 새 job을 요구한다.
  새 runner가 UUID 결과 디렉터리를 추가한다. 성공한 stage는 재실행 금지.
- 완료 run이 둘 이상이면 임의 선택하지 않는다. 무효한 연구는 관리자가 원인을
  확인한 뒤 별도 prepare job에서 실행한다. 원본 자료는 그대로 보존한다.
- 각 stage 로그 8MiB × 현재/이전 두 파일. 원래 연구 산출물에는 로그 회전을 적용하지 않는다.
- 학습은 단일 nice +10 CPU 프로세스, BLAS/OMP/MKL 각 1 thread. 데이터 일별
  공유 특징 계산, 동시 job 한 개. RAM/swap 위험이면 기존 runner가 안전 실패한다.
- EBS 출력 및 매일 최소 max(10GiB,10%) 디스크 여유 유지. ZIP도 비압축 입력
  크기를 기준으로 보수적으로 검사. collector/API 프로세스를 종료해서 자원을 확보하지 않는다.

## 충돌 검사 / 백업 / 배포 / 롤백

운영 저장소가 ahead/behind이고 로컬 수정이 있어도 강제 pull/reset/clean/stash하지 않는다.
별도 tools checkout에서 fetch만 하고 설치 도구가 공개 predecessor를 기준으로
서버·admin을 3-way merge한다. local 변경과 겹치면 **쓰기 전에 중단**한다.
web module은 알려진 759238f 버전 또는 동일 새 코드만 허용한다.
토큰/인증/수집기/원본 데이터/연구 결과/운영 Git index는 배포하지 않는다.

다음은 EC2에서 운영자가 수행할 명령이며 개발 환경에서는 실행하지 않았다.

```bash
TOOLS=/mnt/research/collection-deploy-tools-20261012
git -C "$TOOLS" fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3
git -C "$TOOLS" show origin/strategy-engine-v3:scripts/install_v07_web_console_overlay.py > /mnt/research/install-v07-managed.py
sudo python3 /mnt/research/install-v07-managed.py --managed --check
# READY 및 변경 파일/SHA/충돌 결과 검토 후, 미국 정규장 밖에서만 적용:
sudo python3 /mnt/research/install-v07-managed.py --managed --apply
```

필요 도구: Git, node(--check), 기존 연구 venv. installer는 추가 패키지를 자동 설치하지 않는다.
변경 파일별 private EBS 백업, SHA/owner/mode 보존, 원자 교체, API 구문 및 JS 문법 검사,
API health 확인을 수행한다. API 서비스만 필요한 경우 한 번 재시작하며 수집기는 건드리지 않는다.
health 실패 시 자동 롤백. 성공 후에도 아래 명령으로 manifest의 신규 SHA와
일치하는 파일만 수동 롤백할 수 있다(후속 로컬 변경이 있으면 차단).

```bash
sudo python3 /mnt/research/install-v07-managed.py --rollback /mnt/research/v07-web-install-backups/<해당-백업-ID>
```

실제 보호된 UI의 로그인/버튼/다운로드, API feature 설정, Token Broker/수집기
동작은 운영자가 확인해야 한다. installer는 인증된 관리자 검증을 대신했다고 주장하지 않는다.
GitHub Pages(main)에는 자동 반영하지 않는다. EC2 관리자 페이지를 제공하는
경로/정적 Pages 배포는 분리해서 확인한다. 로컬 커스텀과 merge 충돌이면 운영
저장소를 덮어쓰지 말고 출력된 충돌 파일을 별도 검토한다.

### 최초 장기 학습과 검증 명령

관리자 콘솔에 아래를 **완료를 확인하며 하나씩** 입력한다(SSH 셸 명령 아님).

```
v07 analyze
v07 train
v07 status
v07 log
# train 완료 및 동결 봉인 후
v07 holdout
# holdout 완료 후; 부분 검증은 PARTIAL_UNVERIFIED 유지
v07 validation
v07 export
```

결과: `/mnt/research/v07-web-jobs/<job>/train|holdout|validation/<unique-run>/`.
각 run에 run/status, 날짜 원장, 창/세대/개체 평가, 거래, 최종 개체군, lineage가 있다.
`analysis.json`, `frozen_training.json`, `research-results.zip`은 job 아래 파생 산출물이다.
현 current job을 유지하므로 이미 시행한 감사·시험을 재사용할 수 있다.
자료 SHA가 달라졌다면 새 감사가 필요하며 자동으로 검증 플래그를 참으로 바꾸지 않는다.

## 개발 검증과 한계

- 전체 기존 회귀 + 신규 lock/재시작/봉인/연간 범위/분석/ZIP/SHA/인증/롤백 테스트.
- 기존 20개체×10일 가용 데이터 합성 테스트의 strict 동등성/2세대/무효 창 누적
  오염 방지/frozen 검증/2026 차단을 계속 실행한다. 실제 성과라고 주장하지 않는다.
- EC2 전체 장기 학습, 2024/2025 검증, 운영 감사 원본 분석, 서비스 설정 확인은
  연결이 없어 실행하지 않았다. 학습 시간/메모리/결과 크기를 실제라고 추정하지 않는다.
- 코드 실행 경로가 이전 검증 커밋에 고정되므로 기존 규칙의 결과 재현성을 유지한다.
  새로운 작업 관리/분석/UI 코드는 배포 커밋으로 별도 버전 관리한다.
