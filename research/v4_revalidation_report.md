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

원격 `origin/strategy-engine-v3`는 `b1271f3`까지 별도로 발전했다. 원격 V4 평가 규칙과
현재 baseline의 수치/진입·청산 규칙은 같으며 추가 context 진단이 있다.
정상 운영 중인 원격 tick collector/auth/monitor 구현은 로컬 구현과 다르므로 이번 작업에서
교체·통합·배포하지 않았다. 원격과 분기된 로컬 연구 커밋은 통합 검토가 필요하며 force push하지 않는다.

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

수수료 0은 검증되지 않은 가정이다. 이 표는 새로운 signal/guard/stop/position sizing으로 돌린
백테스트나 이상적 tick 체결 결과가 아니다. 비용을 없애도 이 고정 표본이 음수라는 사실만 확인한다.
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
기존 6거래에 fit하지 않았으며 연구 가설은 실제 rerun 전에 고정한다.
더 많은 후보/threshold grid를 생성하지 않는다.

## 재현 도구 / 보존

`run_v4_research_campaign.py`: baseline IR1 → IR2 → IR3 → ALL → 후보 IR1 → 후보 ALL,
각 job은 새 UUID campaign 하위 경로에 모든 state/audit/result/trades/decisions/log를 남긴다.
기존 역사/forward 데이터 및 dashboard 최신 result를 overwrite하지 않는다.
admin manager와 같은 global lock/active-job guard를 사용한다. job 실패·입력 hash 변경·data review mode
변경 시 campaign 중단. 다른 collector/paper/live 서비스는 재시작하지 않는다.
API start는 lock이 이미 점유되었으면 즉시 오류를 반환하므로 장시간 campaign 종료까지 HTTP를 막지 않는다.
`--cost-stress`는 고정된 5bps ALL 두 번만 추가하며 parameter search가 아니다.

`compare_v4_research.py`: actual result JSON 또는 admin ZIP을 읽고 결과를 재계산한다.
Candidate supplied 시 source hashes, selected universe, 기간, baseline parameters, spec digest,
execution assumptions, data mode가 동일해야 비교 가능하다. 다르면 comparable=false와 이유를 남긴다.
조건부 회계 counterfactual와 실제 전략 rerun을 명확히 분리한다. 원본 result를 수정하지 않는다.

## EC2 실행 순서

운영 중 collector/services를 건드리지 말고, 연구 변경을 검토·통합한 checkout에서 실행한다.
이 로컬 branch는 원격과 diverged 상태이므로 현재 상태로 force push하지 않는다.
수집 중 파일이 변하면 child의 전/후 hash 검증이 실패한다. 안정된 finalized source에서 수행한다.
타임스탬프/조정주가/identity/point-in-time 증거를 먼저 검토하고 manifest review flag를 임의로 true로 만들지 않는다.

```bash
cd /home/ubuntu/market-career-dashboard
git branch --show-current
git log -1 --oneline
: "${V4_TIMESTAMP_KIND:?원본 timestamp semantics를 확인하고 start 또는 end로 명시하세요}"
python3 research/audit_v4_clock_context.py --help
python3 research/run_v4_research_campaign.py --timestamp-kind "$V4_TIMESTAMP_KIND" --plan
python3 research/audit_v4_clock_context.py \
  --data-dir data/toss_1m --symbols SPY,QQQ,SOXX,AAPL,NVDA \
  --timestamp-kind "$V4_TIMESTAMP_KIND" \
  --from-date 2024-06-03 --to-date 2024-06-14 \
  --out /var/lib/market-career-dashboard/v4_research_audits/clock_202406.json
```

원본 clock audit helper의 CLI에 맞춰 2024-06-03~2024-06-14의 coverage/context를 먼저 확인한다.
Reviewed manifest가 실제 존재한다면:

```bash
python3 research/run_v4_research_campaign.py \
  --timestamp-kind "$V4_TIMESTAMP_KIND" \
  --data-dir data/toss_1m --manifest research/v4_data_manifest.json \
  --from-date 2024-06-03 --to-date 2024-06-14 \
  --results-dir /var/lib/market-career-dashboard/v4_research
```

Reviewed manifest가 아직 없다면 위 명령에 **명시적으로** `--provisional`을 추가한다.
파일이 존재하지만 invalid reviewed manifest인 경우 provisional로 우회하지 않는다.
정상 clock/context가 입증된 뒤 같은 명령에서 날짜 제한을 제거하여 전체 campaign을 실행한다.

```bash
python3 research/compare_v4_research.py \
  --baseline /var/lib/market-career-dashboard/v4_research/CAMPAIGN/baseline-all-2bps/result.json \
  --candidate /var/lib/market-career-dashboard/v4_research/CAMPAIGN/ir1-r1-all-2bps/result.json \
  --out /var/lib/market-career-dashboard/v4_research/CAMPAIGN/comparison.json \
  --report /var/lib/market-career-dashboard/v4_research/CAMPAIGN/comparison.md
```

기간별·종목별·regime별 결과는 원본 ALL 뿐 아니라 독립 baseline도 비교한다.
Standalone/ALL의 IR1 거래가 같아야 한다(기존 독립 전략 계좌). 차이가 나면 후보 성과보다 원인을 조사한다.
최종 판단 순서는 causality → 표본 → expectancy/PF → drawdown → OOS/연도/종목 안정성 → robustness이다.
50 미만 거래는 분석 도구에서 INSUFFICIENT_SAMPLE로 표시하며 50 이상도 검증 통과를 의미하지 않는다.

## 다음 연구 우선순위

1. 실제 raw timestamp semantics와 안정된 files, full-session 및 61-session history coverage 확인.
2. 동일 source/parameters/close execution으로 짧은 범위 독립 baseline/ALL 비교; atomic reject와 clock funnel 확인.
3. 전체 baseline 재실행; 구체적 context 병목과 entries의 실제 RS/pullback/regime를 기록.
4. 단일 IR1-R1 가설의 전체/OOS 평가; 시장 분류 복귀 전에 진입하는 일이 없는지 확인.
5. 충분한 표본이 생기면 fixed cost stress, 실행 지연, worst observable gaps, best month/symbol 제거로 검증.
6. 부족하거나 불안정하면 후보를 승격하지 않는다. forward paper/live 변경은 별도 검토 후에만 가능.

기존 V3/Simple, V4 frozen S1/S2/S3 backtester, collector, Toss auth, admin UI/Pages/live는 변경하지 않았다.
Baseline threshold/stop/target/slippage/entry/hold/universe는 그대로다.

## 검증과 변경 파일

사용자도 이 세션에는 EC2 연결이 없음을 확인했고, EC2 실행 도구·명령 준비로 진행하도록 답했다.
실제 재실행 결과를 생성했다고 주장하지 않는다.

- 변경 전 `python -m unittest discover -s research -p 'test_*.py'`: 217 tests PASS.
- 변경 후 같은 전체 suite: 239 tests PASS (신규 22개).
- 변경 Python 파일 `py_compile`: PASS.
- runner/campaign/comparison CLI `--help`: PASS.
- `node research/test_admin_backtest_smoke.cjs`: PASS.
- `node research/test_admin_paper_smoke.cjs`: PASS.
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
