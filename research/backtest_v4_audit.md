# Frozen V4 backtester audit

대상: `strategy_spec_v4.md` (`c9e12ce`), `backtest_v4_strategies.py` (`187b8a2`에서 시작). 아래 위치는 감사 수정 후 코드의 함수 시작 지점이다. 변경은 입력 검증·오류 처리·성과 표시·기존 warmup 보존에 한정한다. S1/S2/S3의 전략 규칙과 기본 파라미터는 변경하지 않았다.

**최종 판정: NOT SAFE TO RUN — 실제 EC2 입력 파일의 timestamp/조정/point-in-time eligibility 검증 manifest가 아직 제공되지 않았다.** 코드 검토와 32개 작은 합성 테스트에서는 미래 봉 사용이나 early-entry를 발견하지 않았다. 검증 manifest를 준비하고 선언된 calendar 의존성을 설치해야 production 데이터 실행을 승인할 수 있다. 수익성·통계적 유의성·실제 시장 데이터 품질을 검증한 감사가 아니다.

## 1. 발견 사항과 최소 수정

CRITICAL: 확인된 항목 없음. HIGH 항목은 아래와 같다. 실제 데이터에 대한 H1의 외부 증거는 이번 작업 범위에서 확보하지 않았다.

| 등급/ID | 코드 위치 | 문제와 의미 | 최소 수정 / 상태 | 과거 성과 편향 방향 |
|---|---|---|---|---|
| HIGH H1 | `backtest_v4_strategies.py:74`, `validate_data_manifest`; 기존 `main`의 confirm flag | `--confirm-price-adjustment` 하나로 입력 기준을 검증했다고 표시했다. 종목/benchmark의 adjusted/unadjusted 혼용, leveraged ETF split, 가격·volume 단위 불일치를 검출할 근거가 없었다. 일관된 adjusted 데이터라도 미래 split을 반영한 역사 가격은 실제 당시 $5 eligibility와 달라질 수 있다. | 선택 종목+필수 reference별 SHA256, timestamp 의미, 공통 OHLCV adjustment basis, split 검토, as-traded $5/ADV20 eligibility 검토를 담은 manifest를 필수로 요구. 알려지지 않은 기준/혼합 기준/검증 누락/해시 변경은 실행 거부. **코드 보강 완료; EC2 실자료 근거 미확보.** | UNKNOWN. 가짜 gap·ATR·RS나 표본 제외로 상승/하락 모두 가능 |
| HIGH H2 | `backtest_v4_strategies.py:119`, `load_1m_data` | 동일 timestamp의 opens가 `[100,101,100]`이면 두 번째 행에서 제거한 시가가 세 번째 행에서 되살아났다. 알려진 가격 충돌에도 임의 open으로 진입하거나 예정 청산할 수 있었다. | timestamp별 `untrusted_open`을 유지해 충돌 시가는 이후 중복으로 복구하지 않음. 원래 open이 일치하고 나중 H/L/C만 잘못된 경우의 causal open 보존은 유지. 회귀 테스트 통과. | UNKNOWN. 임의 entry/exit 가격에 따라 상승/하락 가능 |
| HIGH H3 | `backtest_v4_strategies.py:857`, `metrics`; terminal summary | 미해결 거래는 명세대로 PnL에서 제외하지만 headline 성과가 조건부 결과임을 구분하는 validity flag가 없었다. 실패한 데이터 구간의 손실을 제외한 결과가 정상 결과로 읽힐 수 있었다. | `performanceValid=false`, `metricScope=RESOLVED_TRADES_ONLY`, unresolved count와 terminal 경고 추가. 가짜 청산 가격을 만들거나 미해결 거래를 삭제하지 않음. | UNKNOWN; 누락 거래가 손실이면 위쪽, 이익이면 아래쪽 |
| MEDIUM M1 | `backtest_v4_strategies.py:119`, timestamp parsing | END의 지원 의미가 충분히 명확하지 않았다. `10:00:59` 같은 inclusive-second label은 행 오류만 누적해 0 trade로 끝날 수 있었다. | END는 **exclusive minute boundary**로 manifest에 고정. `:59`/소수 초/비정규 minute label은 loader에서 즉시 실패. START/END 동등성 테스트 통과. | UNKNOWN; 잘못된 convention은 시프트 또는 표본 소실. 수정 후 잘못된 입력은 결과 생성 안 함 |
| MEDIUM M2 | `requirements.txt:4`; `exchange_sessions` | `exchange_calendars`가 필요하지만 requirements에 없었다. 설치된 환경에서만 우연히 동작했다. | `exchange_calendars>=4.5,<5` 최소 선언 추가. 기존 websockets 요구는 유지. 이 workspace에는 package가 없어 실제 package/calendar integration은 실행하지 않음. | 성과 편향 없음; runtime 실패/재현성 문제 |
| MEDIUM M3 | `backtest_v4_strategies.py:225`, `include_symbol_history`; symbol preparation in `main` | calendar 시작을 reference 데이터 시작으로 제한해, 이미 파일에 존재하는 그 이전 종목 일봉/ATR warmup까지 잘랐다. 초기 종목의 유효한 ≥60일 history가 있어도 없다고 판단할 수 있었다. | 종목 history calendar만 기존 symbol 데이터 시작까지 확장. reference-derived 평가 날짜와 OOS fold는 유지. coverage를 결과에 기록. 회귀 테스트 통과. | UNKNOWN; 유효 거래 일부 탈락, 빈도는 아래쪽 |
| LOW L1 | `manage_range`, `metrics`, `overlap_report` | OHLC로 intrabar exit의 정확한 초/동시 거래 간 순서는 알 수 없다. `exitTimestamp`는 해당 minute 시작, `exitIntervalEnd`는 끝이다. 같은 minute 내 정렬은 deterministic tie-break이며 실제 tick 순서가 아니다. | 기존 interval 표기 유지. 합산 cumulative-R/DD는 독립 단위-R 실험이며 portfolio DD로 해석하지 않도록 기존 assumption 확인. 전략 변경 없음. | 같은 minute의 합산 DD/동시 노출은 UNKNOWN; 개별 trade R은 변하지 않음 |

H1의 manifest는 외부 검토를 파일 bytes와 연결하는 증거 기록이다. OHLCV만으로 split 이력이나 원본 가격 조정 정책의 진실을 독립적으로 증명할 수 없다. 확인하지 않은 내용을 `true`로 채워 넣으면 검증이 되지 않는다. manifest는 자동 생성하지 않았고 실제 CSV를 읽거나 내려받지 않았다.

## 2. 우선순위별 static / synthetic 결과

| 항목 | 판정 | 확인한 경로와 근거 |
|---|---|---|
| 1분 인과성 | PASS | `run_session`: current open → intrabar execution → completed close. 새 entry 판단에는 open과 직전 완료 정보만 사용. 미래 current H/L/C가 entry guard에 들어가지 않음 |
| 5분 인과성 | PASS | `build_5m_bars`, `align_completed_timeframe`: clock bucket의 end를 availability로 사용, `bisect_right(end <= T)`. 미래 end나 missing snapshot을 채우지 않음 |
| 15분 인과성 | PASS | 별도 15분 context gate 없음. OR15만 09:30–09:44 원시 봉의 완료 후 사용. 명세대로 최초 context-ready S2 SETUP은 09:50 이후 |
| rolling/percentile/daily | PASS | pandas `resample/merge/reindex/ffill/rolling/expanding/shift/groupby` 사용 없음. dict 기반 window는 현재/과거 인덱스에 제한. percentile filter 없음. eligibility는 `day < today`의 완료 일봉 및 오늘 open만 사용 |
| timestamp START/END | PASS, 입력 근거는 H1 | START 10:00은 [10:00,10:01). END 10:01은 같은 봉. END 10:00은 [09:59,10:00)이다. 끝 초를 10:00:59로 표시하는 convention은 지원하지 않고 실패함 |
| resampling/session | PASS | 10:00–10:04 start labels → end 10:05. 10:00/01/02/03/04에 미노출. missing 1분을 건너뛰어 압축 합치지 않음. 조기 종료 뒤 bucket을 만들지 않음 |
| next-open execution | PASS | trigger `end=T` 이후 다른 원시 봉의 `start=T`에서 진입. wall-clock 숫자는 같아도 같은 원시 봉이 아님. `start != trigger_time`이면 취소. 누락 다음 봉을 늦은 봉으로 대체 안 함 |
| stop/target/cost | PASS | low ≤ stop 우선, 그 뒤 high ≥ target. stop gap은 불리한 open, target gap은 target 가격. buy/sell 각각 2bps. commission/other fee 0은 기존 baseline이며 실제 총 비용 검증이 아님 |
| S1 | PASS | 최초 qualifying pullback에서 pivot 저장 후 갱신 없음. current 1분 volume / previous 20개 completed 1분 median; current 제외. context 유지/expiry/cancel/day lock 확인 |
| S2 | PASS | OR은 SETUP에 저장하고 이후 고정. breakdown은 SETUP 뒤 별도 완료 봉. `age=j-breakdown_index`가 **1 또는 2**일 때만 trigger. age 0은 불가, age 2에서 미충족이면 terminal CANCELLED, age 3 재진입 불가. lower-low cancellation이 reclaim보다 우선 |
| S3 | PASS | SETUP에서 box 고정, ARMED에도 그대로 사용(명세와 일치). 5분 close trigger, previous 10개 5분 volume median에 current 제외. latest 3 NRS observation은 frozen A로 계산. box boundary 이동 없음 |
| day isolation | PASS | 별도 `StrategyState`가 symbol×strategy×day별로 생성. EXIT/CANCELLED는 terminal; 다음 날 새 객체. diagnostic IDLE probe는 거래 상태를 변경하지 않고 skipped SETUP만 기록 |
| reference alignment | PASS | required QQQ/SPY snapshot 및 해당 SOXX를 동일 종료 clock에서 asof 정렬. 현재 snapshot 결측 시 미래 snapshot 대체 없음. SOXX→SMH fallback 없음. SMH는 명세상 optional이며 baseline에 미사용. S2의 sector는 진단만 하므로 필수 아님 |
| chronological OOS | PASS | session index 45/60/75/100%의 3개 순차 OOS. 같은 fold의 train/OOS disjoint, OOS끼리 disjoint. 후속 expanding train이 이전 OOS를 포함하는 것은 의도된 chronological 평가이며 parameter fitting 없음 |
| metrics | PASS, H3/L1 적용 | netR 기준. win rate=positive count/n; expectancy=mean(R); PF=sum(positive)/abs(sum(negative)); average loss는 양의 loss magnitude. DD는 exit timestamp순 cumulative R와 prior peak 차이, 초기 peak=0. zero-loss PF는 null+status, Infinity 출력 안 함 |
| overlap | PASS | `dailyS1S3=BOTH`는 같은 날 두 실제 trigger가 존재한다는 분류이며 시간 중복 판정 아님. 실제 trigger 차이를 별도 표시. temporal duplicate에는 ≤10분과 anchor overlap ≥1분이 추가로 필요 |

전체 자료를 메모리에 precompute한 사실만으로 leakage가 되는 것은 아니다. 사용 시점의 인덱스·availability를 검토했고, 미래 봉 변경으로 이전 snapshot이 바뀌지 않는 합성 테스트를 수행했다. 실제 과거 market data로 invariant 검사를 한 것은 아니다.

## 3. 작은 합성 회귀 테스트

파일: `test_backtest_v4_strategies.py`. 원시 가격은 명시적인 몇 개 봉/최대 25개 1분봉 fixture이며 시장 데이터로 제시하지 않는다. 상태 tests는 필요한 메모리 snapshot을 구성한다. 60일 eligibility test는 일봉 cache 값만 만든 unit test이며 역사 OHLCV dataset이나 full backtest가 아니다.

| 요청 사례 | 테스트 / 결과 |
|---|---|
| A: completed 5m 이후 entry | `test_A_completed_five_minute_signal_enters_only_next_open` / PASS |
| B: same candle stop+target | `test_B_stop_beats_target_with_slippage` / PASS |
| C: S1 pivot 고정 | `test_C_s1_pivot_does_not_follow_subsequent_high` / PASS |
| D: S2 첫 follow-up reclaim | `test_D_s2_first_following_bar_valid` / PASS |
| E: S2 두 번째 reclaim | `test_E_s2_second_following_bar_valid` / PASS |
| F: S2 세 번째 reclaim 불가 | `test_F_s2_third_following_bar_invalid` / PASS |
| G: S3 box/EV5 고정 | `test_G_s3_box_frozen_and_prior_ten_volume_excludes_trigger` / PASS |
| H: reference 결측/future-fill 금지 | `test_H_missing_current_reference_cancels_instead_of_future_fill` / PASS |
| I: symbol/strategy/day isolation | `test_I_locks_isolated_by_symbol_strategy_and_day` / PASS |

추가 검증: START/END 변환 동등성, inclusive :59 거부, gaps/조기 종료 경계, future-row mutation, today EOD cache와 eligibility 분리, warmup 보존, breakdown-bar age=0 제외, second lower low 우선, OR 고정, stop/target gap, late-open 취소, mapping, manifest 혼합/증거 누락/timestamp 불일치/변경 bytes 거부, third-duplicate open, known-open 보존, 수학 지표, zero-loss PF, unresolved validity, folds, overlap.

실행/결과:

```bash
python -m unittest discover -s research -p test_backtest_v4_strategies.py -v
python -m py_compile research/backtest_v4_strategies.py research/test_backtest_v4_strategies.py
python research/backtest_v4_strategies.py --help
```

32 tests PASS. compile/help PASS. historical simulation·최적화·download·live/frontend 변경 없음. 실제 `exchange_calendars` integration은 workspace에 package가 없으므로 미실행; 선언 누락은 수정했다. synthetic early-close 테스트를 실제 거래소 calendar 검증으로 표현하지 않는다.

## 4. 실행을 막는 외부 입력 검증

기존 `--confirm-price-adjustment`는 유지하지만 단독으로 통과하지 않는다. 추가 필수 옵션은 `--data-manifest PATH`다. 실자료 확인 후 reviewer가 다음 version-1 schema를 작성해야 한다. 예제의 placeholder hash는 실행 가능한 검증 증거가 아니다.

```json
{
  "version": 1,
  "verified_by": "실제 reviewer 이름/식별자",
  "verification_note": "source timestamp 정의, 가격/volume adjustment 정책, split/ETF corporate-action 근거, 당시 $5/ADV20 eligibility 확인 기록의 위치",
  "symbols": {
    "TQQQ": {
      "sha256": "<compressed TQQQ.csv.gz bytes의 실제 64자리 lowercase SHA256>",
      "price_basis": "split_adjusted_ohlcv",
      "timestamp_kind": "start",
      "timestamp_semantics": "minute_start",
      "naive_timezone": "America/New_York",
      "split_consistency_verified": true,
      "point_in_time_eligibility_verified": true
    }
  }
}
```

- 같은 schema로 선택 종목과 필수 QQQ/SPY, 필요한 SOXX entry를 추가한다. 선택하지 않은 30개 전체를 요구하지 않는다.
- `price_basis`는 `split_adjusted_ohlcv` 또는 `unadjusted_split_free_ohlcv`. 선택 입력 전체에서 동일해야 한다. 전자는 가격뿐 아니라 volume과 ADV 의미도 확인해야 한다. 후자는 **해당 파일 기간이 실제로 split-free임**을 확인해야 한다.
- split-adjusted 과거 가격은 미래 split 이후의 scale일 수 있다. $5 eligibility와 ADV20가 당시 as-traded 기준 판단과 같다는 근거를 별도로 확인해야 한다. 확인할 수 없다면 해당 필드를 true로 쓰지 말고 실행을 보류한다. 자동 adjustment correction/factor 추정은 구현하지 않았다.
- END일 때 `timestamp_kind=end`, `timestamp_semantics=exclusive_minute_end`. :59 inclusive label을 end flag로 추측 변환하지 않는다.
- timezone-aware 원시 파일만 허용하려면 `naive_timezone=null`; naive 파일은 기존 NY convention을 실제 source 근거로 확인한 경우만 `America/New_York`를 선언한다. naive UTC를 NY로 조용히 해석하도록 허용하지 않는다.
- SHA256는 compressed 파일 bytes다. load 중 파일 size/mtime/inode가 변하거나 hash가 다르면 결과 생성 전에 실패한다. 수집기를 중단하거나 데이터를 복사/다운로드하지 않는다. 계속 수집 중인 파일이 바뀌면 검증 버전과 달라져 실행이 거부되는 것이 정상이다.
- 결과의 `dataVerification`, `dataCoverage`, `performanceValid`, `unresolvedTrades`를 확인해야 한다. `performanceValid=false`의 conditional PF/expectancy로 전략을 PASS 처리하지 않는다.

현재 코드의 입력 차단과 synthetic audit은 완료했지만, EC2 source review는 수행하지 않았다. 실제 데이터 증거 없이 전체 실행을 승인하지 않는다.

**NOT SAFE TO RUN**
