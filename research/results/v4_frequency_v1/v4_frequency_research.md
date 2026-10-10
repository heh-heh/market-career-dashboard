# V4 빈도 진단 준비 및 코드 감사

## 증거 범위

기준 브랜치 `strategy-engine-v3`, 감사 시작 HEAD `d97f348`.
아래 Full 수치는 사용자가 제공한 **end 라벨 결과 요약**이다. 이 세션에는 EC2
연결과 원본 result/audit/decisions가 없으므로 실제 세부 funnel이나 거래 날짜를
확인했다고 주장하지 않는다. 과거 저장소의 start 라벨 결과로 대체하지 않는다.
실제 역사 백테스트를 이 환경에서 실행하지 않았다.

| 버전 | 거래 | 승/패 | Expectancy R | PF R | MDD R | 거래 수익률 합 % |
|---|---:|---|---:|---:|---:|---:|
| Baseline IR1 | 2 | 2/0 | 1.2214 | 미제공, 손실 없음 | 0 | 1.7466 |
| Baseline IR2 | 4 | 3/1 | 0.8846 | 7.0066 | 0.5891 | 1.3710 |
| Baseline IR3 | 0 | 0/0 | 해당 없음 | 해당 없음 | 미제공 | 0 |
| Baseline ALL | 6 | 5/1 | 0.9969 | 11.1535 | 0.5891 | 3.1176 |
| 기존 IR1-R1 | 4 | 4/0 | 0.7033 | 미제공, 손실 없음 | 0 | 1.9716 |

Reported audit: `PASS_MECHANICAL_AUDIT_ONLY`, coverage 99.3788%, bad timezone 0.
Reported campaign: 5/5, comparable true. 최근 완전 OOS
2026-06-01~2026-09-01: IR1/R1 각각 0거래.

현재 판정: **INCONCLUSIVE**. Baseline은 통제로 보존한다. 약 5년 동안
6거래는 실전 사용·통계적 edge 판단에 부족하다. 높은 승률/PF로 이를 보완할 수 없다.

## 코드로 확인한 빈도 병목 후보

| 경로 | 확인한 동작 | 분류 | 실제 영향 |
|---|---|---|---|
| `History.eligibility` | 직전 61개 daily 값 중 None 하나라도 있으면 종목 제외 | INSUFFICIENT HISTORY / baseline data policy | 원본 계측 필요 |
| `Session.observe/finish` | regular prefix에서 분봉 gap 발생 시 당일 VWAP/RVOL unavailable; 불완전 daily는 None | DATA QUALITY / baseline data policy | coverage 총합만으로 추론 불가 |
| `context/allowed_context` | 방향·과거 RV percentile·전략별 market/sector 조건 | EXPECTED STRATEGY FILTER | 실제 component 입력·통과 건수 필요 |
| IR1/IR2 universe | 반도체 11 + tech 10 = 21개 entry 종목 | EXPECTED UNIVERSE DEFINITION | 참조 30종목과 entry universe 구분 |
| IR3 universe | SPY/QQQ만 entry; TQQQ/SQQQ 등은 자동 추가되지 않음 | EXPECTED UNIVERSE DEFINITION | 2개 entry 종목 |
| `Event/evaluate` | symbol×strategy×date 최초 setup 취소는 terminal; 재무장하지 않음 | EXPECTED STRATEGY FILTER | 임의 변경 금지 |
| `Book.enter` | cost/risk, chase, context, capacity, IR2 reward/recovery 검사 | EXPECTED EXECUTION FILTER | terminal 우선순위와 independent component를 구분 |

합성 회귀 테스트로 불완전 daily 하나가 이후 61개 세션 eligibility에 영향을 주는
기존 정책을 확인했다. **99.3788% 분봉 coverage는 61일 연속 완전 session을
보장하지 않는다.** 실제 탈락 비율이 확인되기 전 이 정책을 완화하지 않는다.

## IR3 시계와 조건

`read_stream`은 선언된 end 라벨을 start=end−1분으로 정규화한다.
`run_sessions`에서 T 시각에는 start=T−1분 봉만 관찰한 후 평가한다.
`Session.minutes/snapshots`는 observable end를 key로 사용한다.
따라서 15:30 ET decision의 정상 입력은 **end 15:30 = start 15:29**이고,
start 15:30 봉은 15:31까지 보이지 않는다. entry는 그 이후 완료된 분봉 close
proxy이며, 이 연구에서 next-open 모델로 교체하지 않았다.

XNYS 달력의 actual open/close를 사용한다. early close에는 15:30 decision 자체가
없으며 기존 clockCoverage/sessionExclusionReasons에 별도로 집계된다.
QQQ/SPY는 같은 observable timestamp로 exact lookup하고 future fill하지 않는다.

EST/EDT 및 전환 전후의 정상 full session 합성 fixture에서 IR3가 TRIGGERED가 된다.
이는 조건이 논리적으로 성립할 수 있음을 증명하며, 역사상 edge를 증명하지 않는다.
실제 0건의 원인을 구현 bug 또는 희박한 conjunction이라고 아직 단정할 수 없다.
새 계측은 target bar, daily eligibility, 방향/vol history, opening history,
own UP, other not DOWN, opening Z≥0.5, opening RVOL≥1.25와 conjunction을 분리한다.
missing opening 값의 threshold 실패를 추정하지 않고 측정 분모에서 제외한다.

## 계측 및 분석 산출물

`--funnel-diagnostics`는 opt-in이다. 기본 조건·threshold·stop·target·hold·slippage·
spread·비용 guard·시간·one-attempt 정책을 유지한다.

- `frequencyDiagnostics`: state별 conditional waterfall의 input/pass/reject,
  reject%, chain-input survival. IDLE/SETUP/ARMED는 **반복 decision 관측** 단위다.
- session eligibility는 **symbol×strategy×session** 단위다.
- context/clock/entry component는 **독립 검사**이며 서로 합산하면 안 된다.
- 기존 diagnostics의 terminal cancellation은 한 event당 한 원인이다.
  legacy alias를 추가 탈락으로 합산하지 않는다.
- year/symbol/regime/time/benchmark/market confirmation/sector confirmation은
  각각 marginal breakdown이다. Cartesian product나 무제한 candle 로그를 만들지 않는다.
- R1이 neutral maintenance를 허용한 시점은 event당 최대 5개 causal sample과
  total count를 보존한다. 기존 artifact에 없는 sample은 만들어내지 않는다.

`analyze_v4_frequency.py`는 기존 completed 5-job campaign만 읽는다.
새 UUID 디렉터리에 JSON, Markdown, job별 conditional funnel/lifecycle CSV,
후보 사전등록 상태를 생성한다. 원본 result/audit/decisions 변경을 hash로 감지하고
audit/runId/source-hash 불일치, 실패 audit, timestamp assertion 불일치를 거부한다.

기존 artifact에 gate 계측이 없다면 `NOT_MEASURED`, CSV는 header-only로 출력한다.
대신 기존 unique session stage funnel·terminal reasons·Top 20·facet 분포는 사용한다.
R1 비교는 symbol/date/actual trigger identity를 사용하며 새 거래와 사라진 거래를
모두 표시한다. 변경된 trigger를 같은 거래로 합치지 않는다.
정확한 동일 setup 여부, baseline cancellation, MFE/MAE/hold/exit/R와 source provenance를
JSON에 남긴다. median R, 빈 해 포함 거래 수, OOS activity/latest fold,
concentration, 비용 sensitivity는 기존 분석/성과 함수를 재사용한다.

## PnL 단위

실제 엔진 수식은 `quantity=N/E`, `pnlUsd=quantity*(X−E)`,
`returnPct=100*(X/E−1)`이다. 따라서 `pnlUsd=N*returnPct/100`.
N=$100이면 두 값은 **단위가 다른데 수치가 동일**하다. 이것만으로 bug가 아니다.
기본 자본 $10,000/strategy, ALL $30,000 기준 portfolio return은
`100*sum(pnlUsd)/initialCash`다. 기본 sizing이 동일할 때 ALL 3.1176%의 trade-return
합은 계좌 수익률 약 0.010392%다. 이 값은 기본 sizing을 전제로 한 대수 계산이며,
이 세션에서 원본 계좌 설정을 검증했다는 뜻이 아니다.

## EC2 실행 순서

저장소 루트에서, 기존 서비스/collector를 재시작하지 않고 실행한다.

```bash
# 1. 최신 코드 동기화: 작업 상태를 먼저 확인; merge가 불가능하면 중단한다.
git status --short
git branch --show-current
git fetch origin strategy-engine-v3
git merge --ff-only origin/strategy-engine-v3

# 2. 기존 완료 Full만 읽는다. 데이터 다운로드/백테스트/manifest 변경 없음.
python research/analyze_v4_frequency.py \
  --research-root /home/ubuntu/v4-research \
  --timestamp-kind end
```

출력: 선택된 기존 run-root의 `funnel/<UTC+UUID>/` 아래
`v4_funnel_diagnostics.json`, `.md`, job별 `_funnel.csv`, `_lifecycle.csv`,
`candidate_hypotheses.md`, `state.json`.
명시적 campaign 선택은 `--campaign /실제/완료/full/campaign-directory`를 사용한다.
자동 선택은 가장 최근 완료 full campaign이다. 결과 sourceCampaign과 hash를 확인한다.

기록되지 않은 gate는 먼저 **IR3 10거래일**만 새 폴더에서 측정한다.
기존 historical collector가 원본 파일을 변경하면 audit/hash guard가 실행을 거부한다.
`--provisional`은 외부 provenance 검토를 승인하지 않으며 그 표시를 계속 보존한다.

```bash
V4_FREQ_RUN=$(mktemp -d /home/ubuntu/v4-research-frequency-smoke.XXXXXXXX)
python research/backtest_intraday_v4.py \
  --strategy ir3 --symbols SPY,QQQ \
  --data-dir data/toss_1m --timestamp-kind end --provisional \
  --funnel-diagnostics --from-date 2026-08-03 --to-date 2026-08-14 \
  --out "$V4_FREQ_RUN/result.json" --state "$V4_FREQ_RUN/state.json" \
  --log "$V4_FREQ_RUN/run.log" --trades-csv "$V4_FREQ_RUN/trades.csv" \
  --data-audit "$V4_FREQ_RUN/audit.json" --decisions "$V4_FREQ_RUN/decisions.jsonl"
```

평가 날짜 이전 데이터는 causal warmup으로 읽지만 거래를 평가하지 않는다.
IR3 clock/history 분류를 먼저 확인한 뒤 같은 짧은 날짜에서 5개 독립 비교를 한다.
이 단계 역시 아직 이 세션에서 실행하지 않았다.

```bash
V4_FREQ_ROOT=$(mktemp -d /home/ubuntu/v4-research-frequency-campaign.XXXXXXXX)
python research/run_v4_research_campaign.py \
  --data-dir data/toss_1m --timestamp-kind end --provisional \
  --funnel-diagnostics --from-date 2026-08-03 --to-date 2026-08-14 \
  --results-dir "$V4_FREQ_ROOT/full"
```

완료한 신규 campaign-directory를 `analyze_v4_frequency.py --campaign`에 넘긴다.
동일 source hash/parameters/execution에서 opt-in off/on의 trades·signals·PnL을
비교한다. full 계측 재실행은 짧은 비교가 동일함을 확인한 후 한 번만 실행한다.
분석된 병목 전에는 신규 후보나 최적화 실행을 하지 않는다.

## 모니터와 남은 작업

기존 public V4 status에 read-only funnel 상태 및 신규 후보 차단 상태를 추가한다.
기존 audit/smoke/full/compare, Tick 탭, V3/Simple paper/admin 인증은 유지한다.
새 handler나 실행 endpoint는 추가하지 않는다. analysis process가 사라진 상태는
INTERRUPTED로 표시한다. main/Pages 동기화와 EC2 API 재시작은 실행하지 않았다.

남은 증거: 실제 gate 건수/분모, IR3 역사 conjunction, R1 신규 거래 날짜·종목·원인,
기간·종목 concentration, 마지막 OOS 및 비용 변화. 현재 새 후보는 0개이며,
`candidate_hypotheses.md`에 계측 이후 최대 두 개만 사전등록하는 조건을 남겼다.
실제로 5년간 10건 안팎이 유지되면 현재 구조는 실전 적용 대상으로 채택하지 않는다.

검증 기록은 `validation.json`을 참조한다. 역사 수익률 결과를 새로 생성한 보고서가 아니다.
