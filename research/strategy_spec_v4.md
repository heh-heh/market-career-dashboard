# V4 전략 명세: S1 / S2 / S3 design freeze

기준: `strategy_research_v4.md`, commit `2dcda3f`. 이 문서는 선택된 세 전략의 구현 계약이다. 전략군을 추가하지 않았으며, 코드 구현·데이터 다운로드·백테스트·최적화·실거래 변경을 하지 않았다. 아래 READY는 **명세가 구현 가능함**을 뜻하며 수익성 승인이 아니다.

## 1. 공통 시간·데이터 계약

### 1.1 봉과 관측 시각

- 원시 1분봉은 `[start, end)` 구간이며 OHLCV는 `end`에 사용 가능하다. 저장 timestamp 의미는 loader에서 확인하여 이 모델로 변환한다. timestamp 의미를 확인하지 못하면 loader가 실패해야 한다.
- NY 정규장 개장부터 정렬한 5분봉은 정확히 다섯 연속 원시 봉으로 만든다. 15분봉은 정확히 열다섯 봉이며 baseline에는 OR15 이외 별도 higher-timeframe gate를 사용하지 않는다.
- `T`에 사용할 수 있는 봉은 `end <= T`뿐이다. 5분 trigger는 해당 5분봉 종료 후 entry한다. trigger를 만든 마지막 1분봉의 시가로 소급 진입하지 않는다.
- `B_j=(O_j,H_j,L_j,C_j,V_j)`는 현재 세션의 완료 5분봉이다. `c_t,h_t,l_t,v_t`는 완료 1분봉이다. 인덱스 범위는 양 끝을 포함한다.
- 현재 세션의 rolling 구조·volume·RS window에 전일 봉을 붙이지 않는다. ATR만 전일 정규장 봉으로 warmup할 수 있다. 세션 첫 TR은 직전 정규 세션 close와의 gap을 포함한다. split 등 가격 단위가 달라지면 조정 정합성이 확인될 때까지 해당 세션을 제외한다.
- 종목과 필요한 reference는 동일 종료 시각의 완료 봉을 요구한다. 1분 판단에서는 최신 완료 5분 reference snapshot만 사용한다(정렬상 age < 5분). 해당 snapshot 또는 그 계산 window에 결측이 있으면 CANCELLED다. 1분 reference 미완성 값을 forward-fill하여 쓰지 않는다.
- 필요한 원시 데이터의 중복 충돌, 결측, `volume < 0`, 비유한 값, 가격 ≤ 0, `low > min(open,close)`, `high < max(open,close)`, `high < low`는 오류다. warmup 부족은 IDLE에서 대기하고, 진행 중 event에 생긴 오류는 CANCELLED다.
- 휴장·조기 종료는 거래소 calendar로 결정한다. 오늘 세션의 마지막 완료 상태를 사후 전체 데이터로 추측하지 않는다.

### 1.2 공통 feature 공식

`x`는 종목 또는 reference를 뜻한다. 분모가 0이거나 필요한 window가 없으면 unavailable로 반환한다. ER만 변화 합계 0이면 0으로 정의한다.

| Feature | 고정 공식 / 범위 |
|---|---|
| Session VWAP | 완료 1분봉 `TP_i=(h_i+l_i+c_i)/3`; `W_x(T)=sum(TP_i*v_i)/sum(v_i)`, 오늘 개장부터 T까지. 모든 상태에서 같은 1분 기반 VWAP를 사용 |
| TR / ATR | `TR_j=max(H_j-L_j, abs(H_j-C_prev), abs(L_j-C_prev))`; `A_x(j)=mean(TR[j-13:j])`. Wilder 방식으로 바꾸지 않음 |
| Frozen event ATR | event가 IDLE→SETUP 되는 `B_j`에서 `A=A_x(j-1)`을 저장. 이후 경계·buffer·RS hold 검사 scale은 이 A로 고정 |
| VWAP distance | reference는 `D_x(j)=(C_x,j-W_x,j)/A_x(j)`; symbol event에서는 `(price-W_symbol(T))/A` |
| VWAP slope | `S_x(j)=(W_x,j-W_x,j-3)/A_x(j)`. session 내 네 VWAP 관측 필요; raw dollar slope 사용 금지 |
| ER12 | `abs(C_j-C_j-12)/sum(abs(C_i-C_i-1), i=j-11..j)`. session 내 13 closes 필요. 진단 변수이며 아래 baseline의 gate가 아님 |
| Opening gap | `G=(session_open - previous_session_close)/previous_daily_ATR14`. daily ATR은 전일 포함 마지막 14개 완료 정규 일봉 TR 평균 |
| ADV20 | 전일 포함 20개 완료 일봉의 `median(close*volume)` |
| Cumulative RVOL | `CV_today(T)/median(CV_d(same_elapsed_time), d=previous_20_sessions)`. 해당 경과 시간까지 연속 데이터가 있는 과거 세션이 최소 10개일 때만 사용 |
| RVOL fallback | 위 history 부족 시 `LOCAL5_j=V_j/median(V[j-10:j-1])`, 오늘 session 내 prior 10개 완료 5분봉 필요. mode=`LOCAL5`; cumulative RVOL과 같은 양으로 표시하지 않음 |
| Trigger volume (S1) | `TV1_t=v_t/median(v[t-20:t-1])`, 오늘 session 내 직전 20개 1분봉, trigger 제외 |
| Expansion volume (S3) | `EV5_j=V_j/median(V[j-10:j-1])`, 오늘 session 내 직전 10개 5분봉, trigger 제외. historical volume 유무와 관계없이 이 공식 고정 |
| Return15 | `r_x(j)=C_x,j/C_x,j-3 - 1`, 같은 세션·동일 종료 timestamp |
| Relative strength | `RS15(j)=r_symbol(j)-L*r_benchmark(j)`; `NRS15(j;A)=RS15(j)/(A/C_symbol,j-3)` |

RVOL history가 부족해도 자동으로 모든 event를 거절하지 않는다. cumulative/fallback 둘 다 없으면 unavailable 진단 값이다. **세 baseline은 cumulative RVOL을 필수 gate로 사용하지 않는다.** S1의 pullback contraction/TV1, S3의 EV5는 위의 별도 고정 공식으로 판단한다. 하나의 실행 중에 volume mode를 바꾸어 threshold를 재해석하지 않는다.

### 1.3 Reference mapping 및 market confirmation

| 거래 대상 | RS benchmark | L | Baseline 필수 방향 reference | 선택 진단 |
|---|---|---:|---|---|
| NVDA, AMD, MU, INTC, AVGO, MRVL, AMAT, LRCX, KLAC, TSM, QCOM | SOXX | 1 | QQQ, SPY, SOXX | SMH |
| AAPL, MSFT, META, AMZN, GOOGL, TSLA, PLTR, NFLX, COIN, MSTR | QQQ | 1 | QQQ, SPY | SOXX/SMH |
| TQQQ | QQQ | 3 | QQQ, SPY | SOXX/SMH |
| SOXL | SOXX | 3 | QQQ, SPY, SOXX | SMH |
| SQQQ（향후） | QQQ | -3 | QQQ, SPY의 bearish 상태 | SOXX/SMH |
| SOXS（향후） | SOXX | -3 | QQQ, SPY, SOXX의 bearish 상태 | SMH |

첫 baseline 주문 후보는 위의 일반 주식/TQQQ/SOXL이며 SPY/QQQ/IWM/SOXX/SMH는 context 전용이다. SMH가 없다고 SOXX를 실행 중 교체하지 않는다. SOXX가 필요한 event에 SOXX가 없으면 CANCELLED다. 모든 종목에 모든 reference를 요구하지 않는다.

완료 5분 snapshot에서 다음 boolean을 계산한다. `BEAR_x`는 이름이 아니라 이 공식으로만 판단한다.

```text
UP_x       := D_x > 0 AND S_x >= 0
BEAR_x     := D_x <= -0.50 AND S_x <= -0.15
MARKET_UP  := UP_QQQ AND NOT BEAR_SPY
SECTOR_UP  := UP_SOXX                       # 반도체/SOXL에만 필수
MILD       := NOT BEAR_QQQ AND NOT BEAR_SPY
RECOVERY   := MILD AND (D_QQQ > 0 OR D_SPY > 0)
```

S1은 MARKET_UP 및 해당할 때 SECTOR_UP을 요구한다. S2는 breakdown/reclaim에 MILD, trigger에 RECOVERY를 요구하며 sector는 진단만 한다. S3의 formation/hold는 MILD 및 해당할 때 NOT BEAR_SOXX, trigger는 MARKET_UP 및 QQQ의 `C_j/C_j-1-1 >= 0`, 해당할 때 SECTOR_UP을 요구한다. ER은 보고만 하므로 early-session ER warmup 부족 때문에 S2가 추가 지연되지 않는다. slope의 네 관측 요건 때문에 reference 준비는 정상 데이터에서도 09:50 이후다.

## 2. 공통 상태·실행·파라미터 규약

### 2.1 상태 처리 순서와 재시도

상태는 `IDLE → SETUP → ARMED → TRIGGERED → ENTRY → MANAGING → EXIT` 또는 체결 전 `CANCELLED`다. EXIT/CANCELLED는 하루 동안 terminal이며 다음 세션에만 IDLE로 리셋한다. IDLE은 gate 미충족 시 그대로 대기한다. **첫 SETUP부터 종목×전략당 한 번의 event attempt/day**를 사용한다. 취소 후 더 유리한 event를 다시 찾지 않는다. 기존 연구 문서의 first-ARMED 정책에서 여기서는 first-SETUP으로 명확히 고정한다.

IDLE의 terminal 전이도 고정한다: 일봉 eligibility 미충족/자료 부족, abs(G)>2.0, 확정된 데이터 오류이면 `IDLE→CANCELLED`다. 가격 구조/context의 일시적 미충족과 정상적인 intraday warmup 부족은 IDLE을 유지한다. 각 전략 창이 끝날 때까지 SETUP이 없으면 `IDLE→CANCELLED(reason=NO_SETUP)`다. trigger 가능한 마지막 관측을 처리한 뒤 창 종료를 적용한다. 원시 봉이 결측이면 이를 정상 warmup 부족으로 취급하지 않는다.

동일 관측에서 처리 우선순위:

1. 데이터 오류 / calendar cutoff / window 종료 검사.
2. 현재 상태의 price·context·expiry 취소 조건 검사.
3. 그 시각에 예정된 전략 trigger 검사.
4. 아직 transition이 없으면 다음 state 후보 검사.

한 관측에서 SETUP→ARMED→TRIGGERED를 연쇄 수행하지 않는다. 전략 event 전이는 아래 지정된 서로 다른 종료 시각에서 일어난다. ENTRY→MANAGING만 실제 fill 직후 같은 시각의 bookkeeping 전이다. 마지막 허용 봉에서는 trigger를 먼저 평가하고, trigger가 없을 때 expiry 취소한다. price/context 취소가 trigger보다 우선하며 intrabar 시간 순서를 high/low로 재구성하지 않는다.

### 2.2 Entry / stop / target / exit

- Trigger 종료 T의 바로 다음 원시 1분 구간 시가 `O_next`로만 체결한다. next-open을 구하지 못하면 `MISSING_NEXT_OPEN`으로 취소하고 임의로 늦게 진입하지 않는다. 실제 timestamp상 다음 구간 시작과 T가 같아도 trigger 원시 봉과는 다른 봉이다.
- LONG modeled fill `E=O_next*(1+s)`, `s=0.0002`(2bps). 판매 fill은 model price `*(1-s)`. Commission/기타 수수료의 baseline 모델 값은 0이며 **완전한 실제 거래비용 모델이라는 뜻은 아니다**. 비용 계약을 바꾸면 별도 버전이다.
- 전략 breakout level `B`, chase limit `q`에 대해 `E <= B+q*A` 및 `E <= trigger_close+0.30A`를 둘 다 요구한다. modeled fill 기준이므로 next-open gap과 buy slippage가 이미 포함된다. 초과하면 취소하고 limit 주문으로 바꾸지 않는다.
- `R=E-stop > 0`, 고정 target `P > E`, `(P*(1-s)-E)/R >= 1.0`이어야 한다. entry cutoff나 최신 완료 reference 기준을 위반하면 취소한다. next-open 이후 high/low를 entry 승인에 사용하지 않는다.
- Entry의 reference 재검사는 해당 전략 TRIGGER 행의 boolean을 최신 완료 snapshot으로 다시 계산한다(S1 MARKET_UP/해당 SECTOR_UP, S2 RECOVERY, S3 MARKET_UP/QQQ return/해당 SECTOR_UP). S3의 3/3 RS도 최신 완료 5분 기준으로 유지되어야 한다. TV1/EV5는 trigger 봉의 확정 값이며 다음 봉 거래량으로 다시 검사하지 않는다. 필수 계산 unavailable 또는 0 denominator는 `FEATURE_UNAVAILABLE`로 취소한다.
- S1은 `O_next > max(origin, trigger_VWAP)`, S2는 `O_next > ORL`, S3는 `O_next > box_high`를 추가 요구한다. next-open까지 관측된 데이터만 검사한다.
- Stop/target은 ENTRY에서 확정해 MANAGING 동안 움직이지 않는다. 부분익절·trailing·EMA exit는 baseline에 없다.
- 진입 봉부터 stop/target을 검사한다. 시가가 stop 이하이면 더 불리한 시가에서 sell slippage 적용; 그렇지 않고 low가 stop 이하이면 stop 가격에서 적용. stop과 target이 같은 봉에서 닿으면 stop 우선. target 위 gap은 보수적으로 target 가격에 sell slippage 적용한다.
- Stop/target과 예약된 시가 청산이 경쟁하면 시가 stop gap → 예약 청산 → 해당 봉 stop/target 순이다. 두 시가 청산 사유가 겹치면 FORCE_EXIT → MAX_HOLD → STRUCTURE_EXIT → STALL_EXIT 순으로 reason을 기록한다.
- 구조/time 판단은 완료 봉 이후 다음 1분 시가 청산이다. 최대 보유시간은 fill 시각부터 실제 경과 분이며 해당 분 시가에 예약한다. MFE는 entry부터 현재 완료 봉까지의 `max(high-E,0)/R`이며 미래 봉을 포함하지 않는다.
- Force exit는 정규장 15:50 시가, 조기 종료일은 공식 close 10분 전 시가. 해당 시가가 없으면 `UNRESOLVED_DATA`로 기록하고 정상 청산 거래로 집계하지 않는다. position이 생긴 뒤 데이터 오류를 CANCELLED로 바꿔 거래를 삭제하지 않는다.
- MANAGING 중 실행에 필요한 1분봉에 결측/오류가 발견되면 EXIT로 전이하고 `reason=UNRESOLVED_DATA, exit_fill=null, realized_pnl=null`로 남긴다. 뒤의 정상 봉에서 가상 정상 체결을 만들지 않으며 미해결 거래 수를 성과와 함께 보고한다.

### 2.3 고정 상수와 조정값의 범위

각 전략 primary adjustable은 아래 정확히 6개다. 미래 coarse 대안은 사전 비교 후보일 뿐 Cartesian grid가 아니다. 그 외 값은 **FIXED**, 대안 없음(`—`)이다. 기준 문서와 충돌하면 이 명세가 우선한다.

| 공통 FIXED 항목 | 기본값 | 대안 |
|---|---|---|
| Raw/state/context 간격 | 1분 / 5분 / OR 외 15분 gate 없음 | — |
| ATR / daily ATR / ER | 14 / 14 / 12 | — |
| VWAP slope lag / RS horizon | 3개 5분봉 / 15분 | — |
| cumulative RVOL history / 최소 history | 20 / 10 완료 세션 | — |
| LOCAL5 / TV1 / EV5 denominator | 이전 10개 5분 / 20개 1분 / 10개 5분 | — |
| 구조 buffer b | 0.10A | — |
| BEAR distance / slope | ≤ -0.50 / ≤ -0.15 | — |
| UP distance / slope, reference return floor | > 0 / ≥ 0, S3 return ≥ 0 | — |
| ADV / 가격 / daily history | ADV20 ≥ $50M / 전일 close ≥ $5 / ≥60 완료 일봉 | — |
| Gap exclusion | abs(G) > 2.0이면 취소 | — |
| Bid/ask가 제공될 때 spread ceiling | (ask-bid)/mid ≤ 0.005; 0 < bid ≤ ask | — |
| One event attempt/day | 종목×전략별 1 | — |
| Buy/sell slippage / 기타 비용 baseline | 각각 2bps / 0 | — |
| Trigger-close entry extension / 최소 예상 reward | ≤0.30A / ≥1.0R(net sell slippage 포함) | — |
| Force exit / 전체 신규 entry cutoff | close 10분 전 / 15:15 미만 | — |
| 경계 비교 / 단위 | 표의 >, ≥, <, ≤ 그대로; float 비교 epsilon 추가 없음 | — |

ADV/gap은 완료 일봉 또는 기존 정규 minute 집계로 산출 가능하지만 부족할 때 가짜 값을 만들지 않는다. 사전 eligibility 데이터 부족은 해당 세션 NO_TRADE다. 호가가 없으면 spread gate는 DISABLED로 기록하고 OHLCV를 호가처럼 사용하지 않는다. 계좌/API/주문 safety는 기존 Risk/Execution의 계약을 그대로 유지할 대상이며 이 문서에서 수치를 변경하지 않는다.

## 3. S1 — Impulse → Controlled Pullback → Continuation

### 3.1 고정 정의

진입 가능 시가 창은 `[10:00,14:00)`다. 이 안에서 SETUP을 찾되 마지막 close 이후 next-open도 창 안이어야 한다. liquidity/gap 공통 eligibility, MARKET_UP, 필요한 SECTOR_UP을 적용한다.

IDLE에서 `B_j` 종료마다 최근 **정확히 3개** 5분봉을 impulse 후보로 본다.

```text
origin = O[j-2]
end_close = C[j]
advance = end_close-origin
pre_high = max(H[j-8:j-3])                # impulse 직전 6봉 = 30분
impulse_high = max(H[j-2:j])
A = ATR[j-1]
impulse := advance >= impulse_atr*A
           AND C[j] > pre_high+0.10A
           AND C[j] > VWAP(T)
```

첫 impulse에서 origin/end_close/advance/impulse_high/A를 고정한다. impulse volume expansion은 보고 변수이지 추가 진입 gate가 아니다. 사전 30분 window가 필요하므로 실제 최초 SETUP은 충분한 세션 데이터 이후다.

후속 pullback은 `j+1..j+n`, `1<=n<=3`의 연속 완료 5분봉이다.

```text
PLOW(n) = min(L[j+1:j+n])
retracement(n) = (end_close-PLOW(n))/advance
contract(n) = (sum(V[j+1:j+n])/(5*n)) / (sum(V[j-2:j])/15)
qualifying_pullback := 0.20 <= retracement(n) <= pullback_max
                       AND contract(n) <= pullback_volume_max
                       AND every pullback C[k] > max(origin, VWAP(end_k))
```

EMA support touch, bearish candle body, ADX/RSI gate는 추가하지 않는다. 구조 보존과 retracement/volume을 baseline의 정의로 사용한다.

### 3.2 정확한 상태 전이

| 현재 → 다음 | 관측 조건 / 저장 동작 |
|---|---|
| IDLE → SETUP | 첫 완료 `B_j`에서 eligibility/context와 impulse 공식 충족. impulse snapshot 저장 |
| SETUP → ARMED | 이후 첫 qualifying_pullback 종료. `pivot=H[j+n]`, `arm_time`, 당시 PLOW를 고정. pivot을 이후 봉으로 갱신하지 않음 |
| SETUP → CANCELLED | 어느 후속 1분 low로도 retracement > pullback_max, 완료 1분 close ≤ origin 또는 해당 VWAP, context 실패. 세 번째 후속 5분봉까지 qualifying이 없으면 취소 |
| ARMED → TRIGGERED | arm_time 이후 1분 close가 **pivot+0.10A보다 큼**, `TV1 >= trigger_volume_min`, context 유지. arm_time+5분까지 허용. trigger 봉 포함 관측 pullback 최저가를 stop anchor로 고정 |
| ARMED → CANCELLED | 위 price/context 취소, entry 창 종료, 5분 trigger 대기 만료. 새 5분봉 종료 시 event 시작 이후 모든 완료 pullback 봉의 contract > pullback_volume_max도 취소. 취소와 trigger가 같은 시각이면 취소 우선 |
| TRIGGERED → ENTRY | 다음 1분 open에서 공통 guard 충족; `B=pivot+0.10A`, `q=entry_chase_atr`. STOP/TARGET/R 확정 |
| TRIGGERED → CANCELLED | 공통 guard 실패 또는 next-open 없음 |
| ENTRY → MANAGING | fill 직후 position 기록. 같은 진입 봉부터 stop/target 검사 |
| MANAGING → EXIT | stop, target, max hold, stall 또는 force exit의 공통 실행 규약 |

Stop은 impulse가 끝난 뒤부터 trigger까지 **모든 완료 1분 low**의 최소값-0.10A다. ARMED 뒤 저가가 조금 낮아지더라도 max retracement 위반 전에는 허용하므로 stop anchor가 trigger까지 갱신됨을 명시한다.

**TARGET baseline: impulse_high 고정.** fixed 2R은 이전 고가까지 공간을 무시할 수 있고, trailing은 exit 파라미터를 늘리며, partial+trail은 event 검증을 복잡하게 한다. 첫 검증은 관측된 이전 impulse high로 끝낸다. 공통 1R 예상 reward 조건 미달이면 거래하지 않는다.

**TIME EXIT:** max hold 45분. fill 이후 첫 1분 종료가 경과 20분 이상이고 MFE < 0.30R이면 다음 시가 청산을 예약한다. 이후 stall을 재평가하지 않는다.

### 3.3 파라미터 및 no-trade

| Primary parameter | Default | 미래 coarse 값(기본 포함) |
|---|---:|---|
| impulse_atr | 1.0 | [0.75, 1.0, 1.25] |
| pullback_max | 0.50 | [0.40, 0.50, 0.60] |
| pullback_volume_max | 0.70 | [0.60, 0.70, 0.80] |
| trigger_volume_min (TV1) | 1.0 | [0.75, 1.0, 1.25] |
| entry_chase_atr | 0.30 | [0.20, 0.30, 0.40] |
| max_hold_minutes | 45 | [30, 45, 60] |

| S1 FIXED constants | Default | 대안 |
|---|---|---|
| Window / impulse / pre-high | [10:00,14:00) / 3봉 / 직전 6봉 | — |
| Pullback min retracement / max bars | 0.20 / 3봉 | — |
| ARMED trigger wait | 5분 | — |
| Stall test time / MFE floor | 20분 / 0.30R | — |

**NO TRADE:** 공통 제외, reference UP 미충족, origin/VWAP 훼손, max retracement/contract 위반, 과도한 chase, impulse high까지 공간 <1R, 시간 만료. **실패 모드:** 낮은 volume이 공급 부족이 아니라 수요 소멸인 경우, 마지막 impulse, 목표 고가 이전 재매도. **빈도: MEDIUM.** first-attempt 정책, historical liquidity 요건, market/sector 동시 확인, 1R 공간 부족 때문에 LOW가 될 수 있다.

## 4. S2 — Failed OR Downside Breakout → Reversal

### 4.1 OR와 failure 정의

기본 OR은 `[09:30,09:45)`의 **15분** high/low다. OR5보다 원시 봉 하나의 noise 영향을 줄이고 OR30보다 가격 발견 실패를 일찍 관측하기 위한 coarse 선택이며 성과 우위 주장이 아니다.

`ORL`, `ORH`, `ORM=(ORH+ORL)/2`는 완성 후 고정한다. entry 창은 `[09:45,11:00)`이며 reference slope 준비 전에는 대기한다. liquidity/gap 공통 eligibility를 적용한다. S1과 달리 MARKET_UP/SECTOR_UP을 요구하지 않는다.

### 4.2 정확한 상태 전이

| 현재 → 다음 | 관측 조건 / 저장 동작 |
|---|---|
| IDLE → SETUP | 완료 5분 snapshot에서 OR 완성, MILD, 창 유효, `0.50 <= (ORH-ORL)/A <= 2.50`. 이 첫 snapshot의 직전 ATR을 A로 고정 |
| SETUP → ARMED | 이후 완료 5분봉 `d`에서 `C_d < ORL`, `penetration=(ORL-L_d)/A`가 [break_min,break_max] 안. `failure_low=L_d`, breakdown_end를 고정 |
| SETUP → CANCELLED | MILD 소멸, 이후 어떤 완료 1분 low든 `ORL-break_max*A` 미만, entry 창 종료. SETUP에서 lower lows 자체는 아직 cancellation이 아님 |
| ARMED → TRIGGERED | `d+1..d+reclaim_max_bars` 중 첫 **완료 5분 close > ORL+0.10A**, RECOVERY 충족. 이 reclaim 마감 자체가 trigger이며 reclaim high의 추가 돌파를 기다리지 않음 |
| ARMED → CANCELLED | breakdown 뒤 어느 완료 1분 low든 failure_low보다 낮음(두 번째 lower low), MILD 소멸, 창 종료. reclaim 후보 close가 기준을 넘었으나 RECOVERY가 false인 경우에도 즉시 취소. 마지막 허용 봉까지 reclaim trigger가 없으면 취소 |
| TRIGGERED → ENTRY | 다음 1분 open guard 충족; `B=ORL+0.10A`, `q=entry_chase_atr`. failure_low/target 고정 |
| TRIGGERED → CANCELLED | 다음 open < failure_low, ORL 이하, guard 실패 또는 next-open 없음 |
| ENTRY → MANAGING | fill 기록 후 minute stop/target 처리 |
| MANAGING → EXIT | stop, 고정 target, max hold 또는 force exit |

Reclaim 봉이 failure_low 아래도 거래했다면 완료 1분 low 취소가 우선하므로 trigger가 되지 않는다. 이는 “cannot extend”를 미래 추론 대신 **첫 breakdown low를 이후에 갱신하지 못함**으로 정의한 것이다. `L == failure_low`는 갱신이 아니다.

**STOP:** failure_low-0.10A. **TARGET:** ORM 고정, 다른 target으로 자동 변경하지 않는다. ORM이 entry 위에 없거나 순수익 공간 <1R이면 취소한다. **TIME EXIT:** 30분; 추가 stall/trailing 없음.

Volume climax는 **필수 아님**. breakdown/reclaim V, EV5, cumulative RVOL/mode는 진단으로만 기록한다. QQQ/SPY 자체 OR reclaim을 요구하지 않으며, breakdown 동안 MILD, trigger 때 RECOVERY만 요구한다. sector reference는 실패와 동행 여부를 보고만 한다.

VWAP mean reversion과의 구분: VWAP Z-score·deviation이 setup을 만들지 않는다. 고정 개장 범위 아래의 실제 거래와 마감 → lower-low 실패 → 범위 재진입 마감이라는 순서를 요구한다. VWAP는 reference recovery 정의에만 사용하고 symbol 목표는 ORM이다.

### 4.3 파라미터 및 no-trade

| Primary parameter | Default | 미래 coarse 값(기본 포함) |
|---|---:|---|
| opening_range_minutes | 15 | [5, 15, 30] |
| break_min (ATR penetration) | 0.10 | [0.05, 0.10, 0.20] |
| break_max (ATR penetration) | 0.75 | [0.50, 0.75, 1.00] |
| reclaim_max_bars | 2 | [1, 2, 3] |
| entry_chase_atr | 0.30 | [0.20, 0.30, 0.40] |
| max_hold_minutes | 30 | [20, 30, 45] |

| S2 FIXED constants | Default | 대안 |
|---|---|---|
| Entry window | [09:45,11:00) | — |
| OR width bounds | [0.50,2.50]A | — |
| second lower-low tolerance | 0（strict `<`） | — |
| Target | OR midpoint（0.50×range） | — |

향후 OR30 비교 시 entry 시작은 OR 완성 시각 10:00으로 늦춘다. OR5도 context warmup을 건너뛰지 않는다. 기본 수치들의 대안을 동시에 조합하지 않는다.

**NO TRADE:** 공통 제외, OR 불완전/폭 범위 밖, 과도한 penetration, second lower low, MILD/RECOVERY 미충족, 늦은 reclaim, ORM까지 공간 부족. **실패 모드:** 실제 selloff의 중간 반등, macro shock, 스톱을 건드린 뒤 반전한 날을 OHLCV 순서로 낙관 처리하는 오류. **빈도: LOW.** first-SETUP 소비, no-second-low 조건, 짧은 개장 창과 market recovery/1R 조건이 표본을 줄일 수 있다.

## 5. S3 — Relative-Strength Hold + Compression → Expansion

### 5.1 상대강도·compression·구조

entry 창은 `[10:30,14:30)`다. RS horizon은 **15분**이며 benchmark/L은 공통 mapping대로 고정한다. 일반 주식의 L=1은 beta 추정이 아니라 간단한 사전 가설이다. 레버리지 보정 RS 역시 정밀 tracking-error 모델이 아니다.

완료 `B_j`에서 box는 최근 6봉 `[j-5:j]`, 비교 window는 그 box **이전** 12봉 `[j-17:j-6]`이다. 전일 봉으로 이 window를 채우지 않는다. SETUP 후보에서 A=ATR[j-1]로 다음을 계산한다.

```text
BH = max(H[j-5:j]); BL = min(L[j-5:j])
box_width = (BH-BL)/A
compression_ratio = mean(TR[j-2:j]) / mean(TR[j-17:j-6])
formation := box_width <= box_width_max
             AND compression_ratio <= compression_max
             AND NRS15(j;A) >= rs_min
             AND C_j >= VWAP(end_j)
```

TR contraction과 bounded box라는 두 측정만 사용한다. Bollinger·EMA gate·session-high 거리·선행 impulse 요건을 추가하지 않는다. 필요한 18개의 현재 세션 5분봉 때문에 **정상 세션에서도 첫 SETUP은 11:00 이후**다. 10:30은 허용 창의 하한일 뿐 데이터를 억지로 보충할 근거가 아니다.

### 5.2 정확한 상태 전이

| 현재 → 다음 | 관측 조건 / 저장 동작 |
|---|---|
| IDLE → SETUP | 첫 formation, MILD, 반도체라면 NOT BEAR_SOXX, eligibility 및 창 충족. BH/BL/A/setup_index 저장 |
| SETUP → ARMED | 이후 첫 완료 5분봉 k가 `BL <= C_k <= BH`, `L_k >= BL`, `C_k >= VWAP(end_k)`이고 최근 **3개** 종료 시각 각각의 `NRS15(i;frozen A) >= rs_min`. 이전 두 RS를 현재 A로 정규화하는 계산은 현재까지의 가격만 사용하며 과거 주문 신호를 소급 변경하지 않음 |
| SETUP → CANCELLED | 어느 완료 1분 low < BL, 최신 완료 5분 NRS15 < rs_min, 5분 close < VWAP, MILD/필수 sector not-bear 실패. ARMED 전 5분 close > BH이면 `EARLY_ESCAPE`로 취소(빠른 돌파를 소급 채택하지 않음) |
| ARMED → TRIGGERED | 이후 완료 5분 close > BH+0.10A, `EV5 >= expansion_volume_min`, 최근 3개의 NRS15 ≥ rs_min, MARKET_UP, QQQ 5분 return ≥ 0, 반도체라면 SECTOR_UP. trigger 전의 box는 계속 고정 |
| ARMED → CANCELLED | 완료 1분 low < BL, 최신 완료 5분 NRS15 < rs_min 또는 close < VWAP, MILD/필수 sector NOT BEAR_SOXX 실패, entry 창 종료. `C > BH`이나 trigger 조건 중 하나라도 실패하면 `UNCONFIRMED_ESCAPE`로 취소하여 이미 breakout한 뒤 늦게 신호를 만들지 않음 |
| SETUP/ARMED → CANCELLED | setup 다음부터 총 wait_max_bars개의 완료 5분봉 안에 trigger가 없음. 마지막 봉의 trigger는 허용. 동일 봉에서 SETUP→ARMED→TRIGGERED 연쇄 불가 |
| TRIGGERED → ENTRY | 다음 1분 open guard 충족; `B=BH+0.10A`, `q=entry_chase_atr`. `STOP=BL-0.10A`; `TARGET=E+2R` |
| TRIGGERED → CANCELLED | BH 이하 next-open, 공통 guard 실패 또는 next-open 없음 |
| ENTRY → MANAGING | fill 기록; box/stop/target 고정 |
| MANAGING → EXIT | stop/target, 완료 5분 `C < BH-0.10A` 후 다음 시가 STRUCTURE_EXIT, max hold 또는 force exit |

SETUP/ARMED에서 RS/context 검사는 5분 종료마다, low 이탈은 1분 종료마다 수행한다. SETUP 첫 봉에서 hold가 이미 3/3이어도 최소 한 후속 inside-box 봉을 거쳐 ARMED가 되고 그보다 나중 봉에서 trigger해야 한다. 이는 “hold”를 별도 관측으로 확인하기 위한 baseline이며 최소 대기 때문에 빠른 breakout이 탈락함을 로그에 남긴다.

**TARGET:** fixed 2R. **TIME EXIT:** 60분, stall/partial/trailing 없음. bearish 시장에서 가격을 버티는 동안 후보가 될 수 있지만, reference가 회복되지 않으면 entry하지 않는다.

### 5.3 파라미터 및 no-trade

| Primary parameter | Default | 미래 coarse 값(기본 포함) |
|---|---:|---|
| rs_min (NRS15) | 0.25 | [0.00, 0.25, 0.50] |
| compression_max | 0.70 | [0.60, 0.70, 0.80] |
| box_width_max | 1.20 | [1.00, 1.20, 1.50] |
| expansion_volume_min (EV5) | 1.50 | [1.25, 1.50, 2.00] |
| wait_max_bars (SETUP 이후 합계) | 3 | [2, 3, 4] |
| entry_chase_atr | 0.30 | [0.20, 0.30, 0.40] |

| S3 FIXED constants | Default | 대안 |
|---|---|---|
| Window / RS horizon / hold | [10:30,14:30) / 15분 / 최근 3개 중 3개 | — |
| Box / recent TR / comparison TR | 6봉 / 3봉 / box 이전 12봉 | — |
| Target / max hold | 2R / 60분 | — |
| structure-failure level | BH-0.10A | — |

**NO TRADE:** 공통 제외, reference/RS history 부족, volume denominator 0, 낮은 TR이 결측 때문인 경우, box 이탈 후 늦은 confirmation, chase, sector 방향 실패. **실패 모드:** beta 변화에 의한 가짜 RS, 매수 압력 없이 거래가 줄어든 box, 돌파 후 방향 없이 확대되는 변동성, benchmark와 동일 event를 독립 edge로 중복 계산. **빈도: LOW.** 90분 current-session warmup, 3/3 RS hold, inside-box 확인, 첫 attempt 제한과 sector confirmation이 표본을 줄인다.

## 6. Long / bearish exposure 계약

이 명세의 모든 ENTRY는 LONG이다. naked short를 추가하지 않는다. 미래 bearish exposure는 QQQ 하락 context→SQQQ LONG, SOXX 하락 context→SOXS LONG으로 표현할 수 있다. 반드시 inverse ETF **자체의 상승 구조**와 underlying bearish 확인을 함께 설계해야 한다.

| 향후 경제적 방향 | 주문 instrument | 필요한 별도 검증 |
|---|---|---|
| 기술주 bullish | TQQQ 또는 개별 주식 LONG | 현재 LONG 계약 |
| 반도체 bullish | SOXL 또는 개별 반도체 LONG | 현재 LONG 계약 + SOXX |
| 기술주 bearish | SQQQ LONG | QQQ/SPY bearish, inverse ETF 자체 bullish event, L=-3 RS |
| 반도체 bearish | SOXS LONG | SOXX bearish, inverse ETF 자체 bullish event, L=-3 RS |

S1/S3는 향후 inverse ETF의 상승 event에 사용할 수 있으나 현재 MARKET_UP을 그대로 붙이면 모순이므로 별도 버전이 필요하다. S2의 OR downside failure LONG은 bullish 반전이며 이를 QQQ bearish 신호로 뒤집지 않는다. 어떤 전략에도 양방향 지원을 강제하지 않는다. SOXL/SOXS, TQQQ/SQQQ를 자동 교환하거나 동시 보유 승인하는 로직은 여기서 설계하지 않는다.

## 7. Signal overlap과 중복 식별

- **S1↔S3:** 추세일의 작은 pullback이 compression box와 겹치고 같은 reference 회복 봉에서 두 trigger가 나올 수 있다. 이름만 다를 뿐 신규 정보가 아닐 수 있다.
- **S2↔S1:** 개장 실패 반전 이후의 impulse/pullback이 같은 회복 leg를 다시 거래할 수 있다.
- **S2↔S3:** failed OR reclaim 뒤의 안정화 box가 breakout할 수 있다. 시간 창은 일부 겹치지만 S3 warmup 때문에 빈도는 제한될 가능성이 있다.

미래 ledger에 `session,ticker,economic_group,direction,strategy,event_id,anchor_start,anchor_end,trigger_time,entry_time,B,stop`을 저장한다. event_id는 `session/ticker/strategy/first_setup_time`으로 고정한다. `economic_group`은 TECH_LEVERAGED(TQQQ/SQQQ), SEMI_LEVERAGED(SOXL/SOXS), 나머지는 ticker이며 sector는 별도 태그다.

사전 duplicate 정의:

1. 동일 session/ticker/LONG, trigger 시각 차이 ≤10분이며 두 anchor 시간 구간이 1분 이상 겹치면 `SAME_EVENT_DUPLICATE` pair.
2. 동일 ticker/LONG의 가상 MANAGING 구간이 실제로 겹치면 `CONCURRENT_EXPOSURE` pair. 이는 entry filter가 아니라 사후 overlap 진단이다.
3. 동일 session/economic_group, trigger 차이 ≤10분이면 `RELATED_INSTRUMENT` pair; 별도의 direction/underlying 태그로 반대 노출도 표시한다.

10분/1분은 FIXED, 대안 없음이다. pair 관계는 비추이적일 수 있으므로 임의 cluster 합산을 하지 않고 pair 수·고유 event 수·공통 reference 시각을 보고한다. 종목이 다른 반도체들의 같은 시장 event는 `sector_correlated`로 표시하되 duplicate로 삭제하지 않는다. 우선순위·allocation·position netting은 아직 결정하지 않는다.

## 8. 최소 구현 순서와 확인 항목 — 실행하지 않음

1. **시간/feature 계약부터:** 기존 reader/계산을 재사용해 completed-bar clock, ATR snapshot, reference mapping, RVOL mode, RS, completeness를 분리한다. calendar/source timestamp 확인은 loader의 통과 요건이지 새 수집 단계가 아니다.
2. **공통 실행 ledger:** next-minute-open, entry guards, stop gap, stop-before-target, 예약 시가 exit, first-attempt/day를 하나의 계약으로 만든다. 실거래 코드에 연결하지 않는다.
3. **S2 → S1 → S3 순서:** 고정 OR/reclaim으로 상태 clock을 먼저 확인하고, impulse/pullback 메모리, 마지막으로 multi-symbol RS/box를 추가한다. 이는 구현 순서이며 수익성 순위가 아니다.
4. **작은 수작업 경계 예제:** threshold equality, 두 번째 low와 reclaim 동시 발생, window 마지막 시각, setup/trigger 같은 봉, 1분 gap, same-candle stop/target, stale benchmark, maximum wait 마지막 봉, next-open chase를 확인한다. mock 예제를 실제 시장 성과로 보고하지 않는다.
5. **수집 완료 후에만 baseline 평가:** 세 단일 config, chronological 3-fold 이상, 전략별/train/OOS/연도/시간/regime/ticker 결과와 cancellation funnel을 저장한다. 아직 parameter 대안은 실행하지 않는다. 향후 필요할 때 하나의 primary 값만 인접 coarse 값으로 비교하며 Cartesian grid를 만들지 않는다.

Funnel/decision에는 이전·다음 state, 판단 timestamp, 고정 A/anchor, benchmark timestamp, 비교한 값과 threshold, 명시적 cancel reason을 저장한다. 신호 부족이면 어느 전이에서 표본이 사라졌는지 먼저 확인한다. 거래 수를 늘리기 위해 safety나 규칙을 사후 완화하지 않는다.

## 9. FINAL DESIGN FREEZE

이 파일은 이전 연구 문서의 후보 스케치를 다음과 같이 확정한다: S1은 EMA/VWAP support-touch 추가 조건 없이 origin/VWAP close 보존을 사용하며 최초 qualifying pullback high를 고정한다. S2는 reclaim high 재돌파 대기를 제거하고 완료 5분 ORL reclaim 자체를 trigger로 삼으며 목표를 OR midpoint 하나로 고정한다. S3는 TR 평균 contraction, 3/3 RS hold, 단일 SOXX sector reference를 사용한다. 모두 한 가지 baseline이며 다른 전략을 추가한 것이 아니다.

| 전략 | Freeze 상태 | 고정된 trigger / 미해결 전략 규칙 |
|---|---|---|
| S1 | **READY FOR BACKTEST IMPLEMENTATION** | ARMED 이후 1분 close > frozen pullback high+0.10A, TV1 ≥1.0, context 유지. 미해결 전략 규칙 없음 |
| S2 | **READY FOR BACKTEST IMPLEMENTATION** | 첫 breakdown 후 2개 5분봉 이내 close > ORL+0.10A, second lower low 없음, RECOVERY. 미해결 전략 규칙 없음 |
| S3 | **READY FOR BACKTEST IMPLEMENTATION** | ARMED 이후 5분 close > frozen box high+0.10A, EV5 ≥1.5, 3/3 RS hold와 지정 reference 확인. 미해결 전략 규칙 없음 |

자료 timestamp 의미·가격 조정·coverage 확인은 구현 시 입력 검증 요건이다. 이를 충족하지 않은 데이터에서는 실행을 거부한다. READY를 데이터 준비 완료·실거래 승인·수익성 검증으로 해석하지 않는다.
