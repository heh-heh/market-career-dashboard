# V4 baseline 재검증 및 분리 후보 — 2026-10-10

## 실행 범위와 근거

작업 브랜치 `strategy-engine-v3`, 시작 HEAD `56364130b274658753d71e1eba24c68c4832721f`.
실제 분석 대상은 사용자가 제공한 `v4-all-backtest-2026-10-10T08-09-47-731Z.zip`이다.
이는 **이전 EC2 실행**의 산출물이며 이번 작업에서 새로 실행한 역사 백테스트가 아니다.
정확한 원본 SHA256/runId/구현 버전은 `results/v4_comparison.json.baselineSource`에 기록한다.

현재 환경에는 `data/toss_1m/`, progress 파일, reviewed manifest가 없다.
EC2 연결 도구/인증도 확인되지 않았다. 새 실행 도구의 실제 호출은
`DATA_ACCESS_UNAVAILABLE ... no backtest launched`로 종료 코드 2를 반환했다.
분봉을 다운로드·재생성하거나 가짜 manifest를 만들지 않았다. 독립 IR1/IR2/IR3/ALL 전체 재실행 및 후보 실증은 **미실행**이다.

원격 `744a2b2931899f28874c0a4bd6541f9e3aa30eef` 위로 로컬 연구 커밋을 rebase했다.
원래 두 커밋 `61af4f6` / `5fb06a5`의 재배치된 커밋은 `2a2fabc` / `06bcdf0`이다.
원래 상태는 backup tag `v4-before-rebase-20261010`에 보존했다. 다른 dirty `work` checkout은 건드리지 않았다.
원격의 데이터 수집 통합 탭, `/api/ticks/status`, DAY/PRE/REGULAR/AFTER, 운영 service/auth를 보존했다.
Standalone tick 페이지는 없다. Collector의 concrete runtime bug 한 건만 수정했다:
`stream_once`의 `close_at` 인자가 종료 coroutine을 가려 연결 직후 TypeError가 발생한다.
인자 이름을 `session_close`로 바꿨으며 구독/저장/세션 규칙은 바꾸지 않았다. mock handshake/stream 테스트로 재현·검증했다.
기존 SQLite collector 테스트는 현재 upstream CSV collector API의 offline 회귀 테스트 21개로 이식했다.
실제 시장 데이터나 EC2 서비스를 연결하여 collector를 검증한 것은 아니다.

## 코드 경로 감사

`backtest_intraday_v4.py`:
원본 CSV/CSV.GZ → explicit start/end normalization → New York 거래일 k-way streaming →
XNYS session → `Session.observe(T-1m)` → benchmark context → 후보 queue → `evaluate` →
TRIGGERED → 다음 completed-close observation에서 `Book.enter` → 이후 close observation의
`Book.manage` → chronological exit-PnL metrics.

하루 전체 rows가 메모리에 있어도 features에는 `observe`된 prefix만 들어간다.
5분봉은 5개의 연속 완료봉이 있을 때만 공개한다. 미래 benchmark 채우기/backfill은 없다.
SETUP/ARMED 상태와 book은 strategy × symbol × date 및 전략별 계좌로 분리한다.
한 전략 book의 capacity는 1이며 ALL은 세 독립 $10,000 계좌, 거래당 $100이다.
IR1/IR2는 21개 stock, IR3는 SPY/QQQ만 사용한다. 전체 V3 universe 30종목 중
레버리지/역 ETF, IWM 및 sector reference를 거래 대상으로 강제 추가하지 않는다.
SOXX는 IR1 반도체 benchmark이며 SMH 자동 대체는 없다.

청산은 **완료 close 관측 proxy**다. high/low가 stop/target을 touch했다는 이유로 그 가격에
체결하지 않는다. observable close에서 hard stop → 기존 trailing stop → target → session/time/stall
우선순위를 유지한다. gap은 실제 관측 close와 불리한 비용으로 체결한다.
Missing data에는 fabricated fill 없이 DATA_STALE을 기록하고 회복 시 관측값으로 관리한다.
마지막까지 관측이 없으면 unresolved position이 결과에 남을 수 있어 최종 성과 주장은 차단해야 한다.
이 모델은 tick/quote 체결 검증을 대체하지 않으며 NEXT OPEN backtester와 섞어 비교하면 안 된다.

## 발견한 문제와 분류

| 문제 | 분류 | 조치 / 근거 |
|---|---|---|
| ALL chronological fold는 $30,000 계좌를 $10,000으로 계산 | IMPLEMENTATION BUG | fold helper에 initial cash를 전달. fold 계좌 수익률/비율 drawdown의 약 3배 왜곡 수정. R·거래·PnL 불변 |
| ALL symbol group도 $10,000 고정 분모를 사용 | METRIC SEMANTICS DEFECT | strategy group만 전략당 원금, 다른 group/fold는 전체 선택 전략 원금으로 통일하고 명시 |
| Exclusive OOS 종료일 자체가 없으면 완전한 fold도 생략 | IMPLEMENTATION BUG | 종료일 이전 마지막 XNYS session까지 있으면 fold 포함. 다음 달·주말 가격을 요구하지 않음. 제공 ZIP의 기존 14-fold 범위는 불변 |
| UI 새 V4 실행은 이전 result/state/audit/decisions를 unlink | IMPLEMENTATION BUG / 결과 보존 | V4에 한해 같은 filesystem의 `backtest_archive/<prefix>-<uuid>/`로 rename한 후 새 실행. launch 실패해도 이전 자료 보존. V3/Simple 실행 변경 없음 |
| START/END provenance 미확정 | DATA PROVENANCE BLOCKER | ZIP 실행은 start 선언. 현재 Toss API 문서는 exclusive END로 설명하지만 실제 파일 provenance 확인 전 자동 이동 금지. campaign은 explicit kind 필수 |
| 모든 입력 EOF가 2026-10-01 10:59 ET | DATA QUALITY | 그날 15:30 clock 대상 없음이 파일 경계로 증명됨. 두 index decision은 target missing. 원본 삭제/날짜 제거 없음 |
| daily eligibility에 61 연속 유효 과거 session 필요 | INSUFFICIENT HISTORY / EXPECTED BASELINE | 한 missing session이 이후 history를 차단할 수 있음. 11,430/27,899 symbol sessions 미통과. 완화하지 않음 |
| legacy CONTEXT_FAILED / CLOCK catch-all | DIAGNOSTIC DEFECT | 직전 커밋의 atomic reason/clock coverage 유지. legacy 취소 시점 feature가 없어서 세부 과거 count는 복구 불가; 실제 데이터 rerun 필요 |
| seed universe는 현재 생존 종목 | SURVIVORSHIP / PROVENANCE LIMITATION | point-in-time universe 검증 필요. 성과를 과대해석하지 않음 |

Timestamp-kind end도 START로 정상화한 뒤 T+1m에 공개된다. 15:30 decision은
START 15:29 candle의 완료 close를 사용한다. START 15:30 high/close는 아직 사용할 수 없다.
Naive timestamp는 명시 timezone 없으면 실패한다. XNYS DST/early close 및 이러한 causal 경계는 기존 synthetic tests로 검증한다.
전체 기간의 실제 minute coverage/벤치마크 coverage는 ZIP의 기계 감사에서 deferred였고,
원본 분봉이 없으므로 이번에도 숫자를 만들어 제시하지 않는다.

## 보존된 실제 baseline 결과

`PROVISIONAL_UNREVIEWED_DATA`, 2021-12-01~2026-10-01, 1,213 sessions, 약 11.24M processed minute rows.

| 전략 | 거래 | 승/패 | 승률 | 기대 R | PF R | MDD R | 합산 거래 수익률 |
|---|---:|---:|---:|---:|---:|---:|---:|
| IR1 | 3 | 1/2 | 33.33% | -0.6830 | 0.0434 | 2.0491 | -1.5575% |
| IR2 | 2 | 0/2 | 0% | -1.1165 | 0 | 2.2329 | -0.8137% |
| IR3 | 1 | 1/0 | 100% | +0.4426 | null / infinite | 0 | +0.2729% |
| ALL | 6 | 2/4 | 33.33% | -0.6399 | 0.1224 | 3.8393 | -2.0984% |

ALL PF USD 0.1278, 평균 수익률 -0.3497%, median -0.4068%, average winner +0.1537%,
average loss magnitude 0.6014%, payoff 0.2556. 계좌 수익률 -0.006995%,
계좌 realized drawdown 0.006995%이며 거래 수익률의 합과 다르다.
평균 hold 1,230초, MFE 0.4735R, MAE -0.7972R. 4 hard stop, 1 time stop, 1 session exit.
미결 포지션 0, stale 영향을 받은 완료 거래 0.

14 rolling OOS folds 중 거래가 있는 fold는 4개, 양의 기대값은 1개.
10 empty folds를 성공으로 세지 않는다. Nonempty OOS median expectancy -1.0783R,
유한 PF R median 0. 단독 IR3 한 거래의 무손실 PF는 edge 근거가 아니다.
유일한 양의 연도 net PnL은 2025년 SPY 1거래다. 종목/연도/시장 구간별 edge 판단은 표본 부족이다.
년·월·종목·regime·session·exit·시간대·fold 상세와 realized equity curve는 비교 JSON에 있다.

### 병목

- IR1: 3,249 setups 중 CONTEXT_FAILED 2,862 (88.09%).
- IR2: 5,278 setups 중 CONTEXT_FAILED 3,650 (69.15%).
- IR3: 2,408 decisions 중 legacy CLOCK_ELIGIBILITY_CONTEXT_UNAVAILABLE 2,324 (96.51%).
  이 label에는 missing data뿐 아니라 own-direction, benchmark-direction, volatility, warmup 조건이 섞여 있다.
  전부 clock bug 또는 실제 15:30 누락이라고 단정할 수 없다.
- 45 signals → 6 entries. 나머지 39건은 RISK_COST_GUARD 14, ENTRY_REWARD_RECOVERY_FAILED 17,
  ENTRY_CONTEXT_FAILED 6, CHASE 2. 그대로 보존한다.
- IR1 legacy 취소값은 20분 market efficiency 조건을 매 minute 재검사하므로 controlled pullback 중
  시장 분류가 바뀌면 setup이 사라진다. 이는 코드상 확인된 baseline 조건이며 구현 버그로 고치지 않는다.

### 비용 비교의 한계

고정된 여섯 거래의 관측 entry/exit marks·quantity·원래 R denominator로 재계산한 **회계 counterfactual**:

| 비용/side | 합산 거래 수익률 | 기대 R | PF R |
|---|---:|---:|---:|
| 0 | -1.7395% | -0.5132 | 0.2051 |
| 2bps slippage + 1bps half-spread | -2.0984% | -0.6399 | 0.1224 |
| 5bps slippage + 1bps half-spread | -2.4570% | -0.7666 | 0.0698 |
| 10bps slippage + 1bps half-spread | -3.0542% | -0.9778 | 0.0302 |

수수료 0은 검증되지 않은 가정이다. 이 표는 새로운 signal/guard/stop/position sizing으로 돌린
백테스트나 이상적 tick 체결 결과가 아니다. 비용을 없애도 이 고정 표본이 음수라는 사실만 확인한다.
10bps 표는 기록된 returnPct/netR, 검증된 고정 $100 notional, 원래 3bps 비용에서의 대수 변환이다.
새 가격/시각을 만들어내지 않으며 원본 세 비용 시나리오는 그대로 보존했다. 실제 observed-mark 계산과의 등가는 synthetic 테스트로 검증했다.
Delay·quote spread·stop path stress는 실제 분봉/quote 없이는 재현할 수 없으므로 하지 않았다.

## 분리된 단일 후보: V4-IR1-R1

가설: controlled pullback 동안 market ER이 낮아져 MIXED가 되더라도 기준 종목과 시장이
하락 추세로 전환하지 않았다면, setup을 버리는 대신 원래 timeout 안에서 상승 추세 재확인을 기다릴 수 있다.

- IDLE setup admission: 기존 TREND_UP/NORMAL_VOL 그대로.
- SETUP/ARMED persistence만: 기존 gate 또는 MIXED/NORMAL_VOL AND QQQ, SPY가 각각 UP/OTHER.
- 반도체 SOXX의 기존 u20≥0 / close>VWAP 확인 필수, missing은 거절.
- signal: 기존 full TREND_UP gate 복귀 필수. MIXED의 breakout은 아직 signal이 아니다.
- 실제 진입: 기존 `Book.enter` 완전 동일. 다음 observable close/90초/cost/chase/RS guard 불변.
- 모든 숫자, pullback 구조, frozen trigger/stop, 취소·대기 timeout, trailing/target/hold/금액/universe 불변.
- IR2/IR3 변경 없음. baseline 기본값 unchanged, 후보는 `--research-variant ir1-r1`로 명시 선택.
- `candidatePersistenceAdmitted` 관측 count로 후보가 setup을 유지한 횟수 추적.

**후보 결과: NOT_RUN. 개선·악화 및 edge에 대한 결론 없음.**
**Verdict: inconclusive** — 6거래로 유효성을 판단할 수 없다.
기존 6거래에 fit하지 않았으며 연구 가설은 실제 rerun 전에 고정한다.
더 많은 후보/threshold grid를 생성하지 않는다.

## 재현 도구 / 보존

`run_v4_research_campaign.py`: baseline IR1 → IR2 → IR3 → ALL → 후보 IR1 (정확히 5 job),
각 job은 새 UUID campaign 하위 경로에 모든 state/audit/result/trades/decisions/log를 남긴다.
기존 역사/forward 데이터 및 dashboard 최신 result를 overwrite하지 않는다.
admin manager와 같은 global lock/active-job guard를 사용한다. job 실패·입력 hash 변경·data review mode
변경 시 campaign 중단. 다른 collector/paper/live 서비스는 재시작하지 않는다.
API start는 lock이 이미 점유되었으면 즉시 오류를 반환하므로 장시간 campaign 종료까지 HTTP를 막지 않는다.
비용 민감도는 비교 도구의 고정 trades 회계 분석이다. `--cost-stress` 추가 backtest는 차단했다.

`compare_v4_research.py`: actual result JSON 또는 admin ZIP을 읽고 결과를 재계산한다.
Candidate supplied 시 source hashes, selected universe, 기간, baseline parameters, spec digest,
execution assumptions, data mode가 동일해야 비교 가능하다. 다르면 comparable=false와 이유를 남긴다.
조건부 회계 counterfactual와 실제 전략 rerun을 명확히 분리한다. 원본 result를 수정하지 않는다.

## EC2 실행 순서 — 아래 명령을 각각 한 줄씩 실행

EC2에 접근할 수 없어 다음 명령은 **미실행**이다. `.venv/bin/python`을 사용한다.
긴 실행은 nohup으로 분리하여 SSH/SSM 응답 timeout을 피한다. 각 코드 블록은 한 줄이다.
같은 셸에서 순서대로 실행한다. 시스템 서비스는 시작·중지·재시작하지 않는다.

```bash
cd /home/ubuntu/market-career-dashboard
```
```bash
git status --short
```
```bash
git branch --show-current
```
```bash
git fetch origin
```
```bash
git pull --ff-only origin strategy-engine-v3
```
`strategy-engine-v3`인지 확인한 후에만 pull한다. Dirty/conflict/다른 branch이면 중단하고 기존 파일을 보존한다. reset/clean/stash를 쓰지 않는다.
```bash
git rev-parse HEAD
```
```bash
.venv/bin/python -m unittest discover -s research -p 'test_*.py'
```

원본 export·수집기와 timestamp 증거를 확인하고 **아래 둘 중 정확한 한 줄만** 실행한다.
START/END를 guess하거나 manifest review flag를 true로 만들지 않는다.
```bash
export V4_TIMESTAMP_KIND=start
```
또는:
```bash
export V4_TIMESTAMP_KIND=end
```
```bash
V4_RUN_ROOT="/home/ubuntu/v4-research/$(date -u +%Y%m%dT%H%M%SZ)"
```
```bash
mkdir -p "$V4_RUN_ROOT"
```
```bash
.venv/bin/python -c 'import json; from pathlib import Path; p=Path("data/toss_1m_progress.json"); print(json.dumps(json.loads(p.read_text()),indent=2) if p.is_file() else "progress file absent; verify collector/export stability separately")'
```
기계 감사만으로 수집 종료·price adjustment/provenance를 인증하지 않는다. 파일이 변하면 audit/backtest가 fail closed하므로 안정된 source가 선행되어야 한다.

### 전체 기존 데이터 감사 — 전략 백테스트 아님
```bash
V4_AUDIT_SYMBOLS="$(.venv/bin/python -c 'import json; print(",".join(json.load(open("research/universe_v3.json"))["symbols"]))')"
```
```bash
nohup .venv/bin/python research/audit_v4_clock_context.py --data-dir data/toss_1m --symbols "$V4_AUDIT_SYMBOLS" --timestamp-kind "${V4_TIMESTAMP_KIND:?timestamp semantics declaration required}" --out "$V4_RUN_ROOT/v4_data_audit.json" --report "$V4_RUN_ROOT/v4_data_audit.md" --sessions-out "$V4_RUN_ROOT/v4_data_audit_sessions.jsonl" > "$V4_RUN_ROOT/audit-terminal.log" 2>&1 < /dev/null &
```
```bash
tail -n 30 "$V4_RUN_ROOT/audit-terminal.log"
```
```bash
.venv/bin/python -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r["status"],json.dumps(r.get("aggregate",{}),indent=2)); assert r["status"]=="PASS_MECHANICAL_AUDIT_ONLY"' "$V4_RUN_ROOT/v4_data_audit.json"
```
JSON은 파일 hash/schema/rows/first/last/duplicates/conflicts/order/volume/currency/UTC-KST-ET-DST samples와 symbol coverage를 포함한다.
JSONL은 regular completeness/15:29 observable context/15:30 START 존재/history61/benchmark/early close를 포함한다.
PRE/AFTER 수치는 ET clock proxy이며 Toss DAY session provenance를 인증하지 않는다.
`PASS_MECHANICAL_AUDIT_ONLY`라도 coverage·history가 부족하면 원인을 검토한다. 전 기간 실제 감사는 이 세션에서 미실행이다.

### 작은 구간 5개 job — runtime/schema/provenance smoke
Reviewed manifest가 있으면 검증하여 사용한다. 아래 provisional 옵션은 **manifest가 없을 때만** unreviewed mode를 명시한다. 기존 invalid manifest는 우회하지 않는다.
```bash
nohup .venv/bin/python research/run_v4_research_campaign.py --data-dir data/toss_1m --timestamp-kind "${V4_TIMESTAMP_KIND:?timestamp semantics declaration required}" --provisional --from-date 2024-06-03 --to-date 2024-06-14 --results-dir "$V4_RUN_ROOT/smoke" > "$V4_RUN_ROOT/smoke-terminal.log" 2>&1 < /dev/null &
```
```bash
tail -n 40 "$V4_RUN_ROOT/smoke-terminal.log"
```
```bash
.venv/bin/python -c 'import json,sys; from pathlib import Path; paths=list(Path(sys.argv[1]).glob("*/campaign.json")); assert len(paths)==1; r=json.load(open(paths[0])); print(r["phase"],r.get("error"),r["completed"]); assert r["phase"]=="completed" and len(r["completed"])==5' "$V4_RUN_ROOT/smoke"
```
짧은 범위에서 fold 부족으로 `NOT_EVALUABLE`이면 정상이다. context availability, 시간 정렬, state/result/log/decision/source provenance를 확인하고 다음으로 진행한다.

### 全範囲 — smoke 정상 확인 후에만
```bash
nohup .venv/bin/python research/run_v4_research_campaign.py --data-dir data/toss_1m --timestamp-kind "${V4_TIMESTAMP_KIND:?timestamp semantics declaration required}" --provisional --results-dir "$V4_RUN_ROOT/full" > "$V4_RUN_ROOT/full-terminal.log" 2>&1 < /dev/null &
```
```bash
tail -n 40 "$V4_RUN_ROOT/full-terminal.log"
```
```bash
V4_FULL_CAMPAIGN="$(.venv/bin/python -c 'import json,sys; from pathlib import Path; paths=list(Path(sys.argv[1]).glob("*/campaign.json")); assert len(paths)==1; r=json.load(open(paths[0])); assert r["phase"]=="completed" and len(r["completed"])==5; print(paths[0].parent)' "$V4_RUN_ROOT/full")"
```

### IR1 baseline vs IR1-R1 — 동일 source/cost/기간 비교
```bash
.venv/bin/python research/compare_v4_research.py --baseline "$V4_FULL_CAMPAIGN/baseline-ir1-2bps/result.json" --candidate "$V4_FULL_CAMPAIGN/ir1-r1-ir1-2bps/result.json" --out "$V4_FULL_CAMPAIGN/v4_comparison.json" --report "$V4_FULL_CAMPAIGN/v4_comparison.md"
```
```bash
.venv/bin/python research/compare_v4_research.py --baseline "$V4_FULL_CAMPAIGN/baseline-all-2bps/result.json" --out "$V4_FULL_CAMPAIGN/v4_all_analysis.json" --report "$V4_FULL_CAMPAIGN/v4_all_analysis.md"
```
```bash
.venv/bin/python -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r["verdict"]); print("final complete OOS fold:",json.dumps(r["baseline"]["finalCompleteOOSFold"],indent=2)); print("candidate:",r["candidate"].get("status",r["candidate"].get("verdict"))); print("comparability:",r["comparison"])' "$V4_FULL_CAMPAIGN/v4_comparison.json"
```
독립 baseline 4개와 단일 IR1 후보가 모두 UUID 하위 새 경로에 남는다. 기존 결과/forward logs를 overwrite하지 않는다.
최종 OOS 종료일이 exclusive인 것을 확인하고, source 마지막 완전 session과 audit JSONL을 대조한다.
동일 universe/parameters/execution/data mode/file hash를 검증하기 전 비교 가능이라고 주장하지 않는다.
50거래 미만이면 verdict는 inconclusive이며 그 이상도 pass/edge를 자동으로 주장하지 않는다.
새 실제 결과가 나온 뒤에만 저장소 비교 리포트를 업데이트한다.

## 다음 연구 우선순위

1. 실제 raw timestamp semantics와 안정된 files, full-session 및 61-session history coverage 확인.
2. 동일 source/parameters/close execution으로 짧은 범위 독립 baseline/ALL 비교; atomic reject와 clock funnel 확인.
3. 전체 baseline 재실행; 구체적 context 병목과 entries의 실제 RS/pullback/regime를 기록.
4. 단일 IR1-R1 가설의 전체/OOS 평가; 시장 분류 복귀 전에 진입하는 일이 없는지 확인.
5. 충분한 표본이 생기면 fixed cost stress, 실행 지연, worst observable gaps, best month/symbol 제거로 검증.
6. 부족하거나 불안정하면 후보를 승격하지 않는다. forward paper/live 변경은 별도 검토 후에만 가능.

기존 V3/Simple, V4 frozen S1/S2/S3 backtester, Toss auth, index 데이터 수집 UI/Pages/live는 그대로다.
Admin 변경은 V4 preflight 표시/unsupported start 방지뿐이며 collector 변경은 위 coroutine 이름 충돌 2줄뿐이다.
Baseline threshold/stop/target/slippage/entry/hold/universe는 그대로다.

## 검증과 변경 파일

사용자도 이 세션에는 EC2 연결이 없음을 확인했고, EC2 실행 도구·명령 준비로 진행하도록 답했다.
실제 재실행 결과를 생성했다고 주장하지 않는다.

- 변경 전 `python -m unittest discover -s research -p 'test_*.py'`: 217 tests PASS.
- 이전 로컬 변경 후: 239 tests PASS.
- 최신 원격 통합 후: 256 tests PASS (원격 진단 8개 보존, 추가 통합 9개; collector 21개는 현재 API로 이식).
- 변경 Python 파일 `py_compile`: PASS.
- runner/campaign/comparison CLI `--help`: PASS.
- `node research/test_admin_backtest_smoke.cjs`: PASS.
- `node research/test_admin_paper_smoke.cjs`: PASS.
- `node research/test_data_collection_smoke.cjs`: PASS (index syntax/통합 탭/상태 렌더링/no standalone page).
- 이전 five-day synthetic golden: 동일 1 entry / 1 exit, entry 109.0577075 / exit 109.46715,
  동일 기존 funnel. 새 source candle은 15:29~15:30, entry observation은 15:31로 분리됨.
- 후보 deterministic tests: MIXED에서는 ARMED 유지하나 signal/entry 불가,
  TREND_UP 복귀 시 동일 frozen structure trigger, timeout·sector·missing context 규칙 유지.
- V4 이전 result 보존, launch 실패 보존, API campaign lock 즉시 거절, no-data Popen 미호출: PASS.
- `BASELINE` 전체 숫자 dict와 제공된 EC2 artifact configuration.parameters: 동일.
- source/candidate hash mismatch 비교 불가, empty fold 명시, 비용 counterfactual/집중도 분석의 입력 불변: PASS.

핵심 코드:

- `backtest_manager.py`: V4 결과 보존 및 충돌한 실행 lock에 즉시 오류.
- `research/intraday_v4_metrics.py`: fold capital 및 exclusive calendar 종료 처리.
- `research/intraday_v4_engine.py`: opt-in candidate context hook, 동일 baseline trigger/entry gate, entry candle provenance.
- `research/backtest_intraday_v4.py`: 분리 variant 선택/출력, signal candle/context/features, group capital 의미, code hashes.
- `research/intraday_v4_candidates.py`: 단일 사전등록 IR1-R1 hypothesis.
- `research/run_v4_research_campaign.py`: 보존형 독립 baseline/candidate sequential 실행.
- `research/compare_v4_research.py`: 기존 실제 ZIP 분석 및 동일 데이터 비교 검증.
- `research/test_v4_revalidation.py`, `research/test_v4_clock_context.py`, `research/test_intraday_v4.py`: synthetic 회귀 검증. 축약된 mocked signal에 source candle이 없으면 UNAVAILABLE이며 만들어내지 않음.
- `research/results/v4_comparison.json`, `research/results/v4_comparison.md`: 기존 실제 artifact 재분석, candidate NOT_RUN.
- `research/results/v4_validation_status.json`: 실제 실행 부재/preflight block와 완료한 로컬 검사 기록.
- 이 문서: 감사 근거/한계/EC2 명령.

수익 개선·악화는 새 실제 data campaign을 실행하기 전까지 알 수 없다.
표본 부족한 원본 결과를 근거로 production 또는 forward paper 후보를 승격하지 않았다.

## 현재 판정과 추가 결과

**inconclusive**. 기존 실제 6거래의 최대 연승 1 / 연패 2. 월·연도 일관성과 상·하위 5거래 기여를 비교 JSON에 추가했다.
6거래에서 상위·하위 5거래는 겹치므로 합산 bucket으로 해석하지 않는다.
현재 baseline 전체 재실행·IR1-R1·EC2 데이터 감사는 미실행이다. 새 edge/개선/악화 결론은 없다.
클라우드 setup 스킬에 따라 /tmp venv와 offline 테스트용 설치·시작 지침 draft를 저장했다.
설정 저장은 EC2 배포나 새 환경 publication이 아니다. 사용자가 환경 설정에서 검토·저장·publish해야 이후 환경에 반영된다.
