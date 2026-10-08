# V4: 미국 장중 전략 가설과 진입 상태 설계

상태: **사전 연구 설계. 수익성 검증 없음.** 데이터 수집·백테스트·최적화·실거래 변경을 수행하지 않았다. V3 TQQQ 평균회귀 regime 연구와 별개이며, 평균회귀 파라미터를 추가 탐색하는 문서가 아니다.

## 1. 시장 가정과 현재 코드의 재사용 범위

- 가설의 출발점은 가격 발견, 일시적 주문 불균형, 실패한 가격 수용, 변동성 전환이다. OHLCV만으로 기관 주문·스톱 사냥·실제 유동성 충격을 관측했다고 주장하지 않는다.
- 미국 정규장 09:30–16:00 America/New_York를 기준으로 한다. DST와 거래소 휴장·조기 종료 일정을 적용해야 한다. 전일 값은 **직전 완료 정규 세션**에서만 가져온다.
- 입력은 기존 `data/toss_1m/`의 종목별 1분 OHLCV다. 프리마켓, 뉴스, 호가, 체결 방향, 공매도 가능 여부는 확보되었다고 가정하지 않는다. 데이터 다운로드나 공급자 교체가 필요하지 않다.
- 원시 timestamp가 봉 시작/종료 중 무엇을 뜻하는지 먼저 확정한다. 모든 판단은 봉 종료 시점에만 가능하다. 5분·15분봉은 해당 시간 구간의 연속된 원시 봉이 모두 종료된 경우에만 확정한다. 결측 구간을 건너뛰어 하나의 봉으로 합치지 않는다.
- 체결은 trigger 종료 후 **다음 연속 1분봉 시가**다. 당일 최종 고가·저가·종가, 미래 swing, 미래 거래량, 미완성 reference 봉을 사용하지 않는다. 종목과 reference의 종료 시각을 맞추며 필요한 reference가 없으면 거래하지 않는다.
- 초기 실행 가설은 LONG만 사용한다. SHORT 대칭 규칙은 진단용이며 주문 가능성을 가정하지 않는다. SQQQ/SOXS의 LONG은 자체 가격 구조와 역방향 underlying 확인을 별도로 평가한다.
- TQQQ/SOXL은 독립적인 QQQ/SMH 신호 표본이 아니다. 일일 리셋·레버리지·복리 차이 때문에 단순 가격 비교를 피하고 같은 시간 구간의 레버리지 보정 수익률을 사용한다. 이것도 정밀 추적오차 모델은 아니다.

좁게 확인한 코드:

| 기존 파일 | 재사용할 부분 | 향후 주의할 부분 |
|---|---|---|
| `../strategy_engine.py` | 정규화, EMA, ATR, session VWAP, ER, 현재 전략 출력 구조 | aggregate의 완성/결측 검증, 실제 장 시작 데이터 유지, OR breakout/retest의 순서와 만료 상태가 필요하다. 현재 `adx_di` 값은 완전한 Wilder ADX와 다르므로 ADX로 단정하지 않는다. |
| `backtest_v2_tqqq_mr.py` | 기존 데이터 형식, next-minute-open, slippage, stop-before-target, 일일 setup 제한, chronological 평가 방식 | sequential resample, 결측 봉의 보유시간, gap-through-stop 처리를 미래 구현에서 명시적으로 검증한다. 기존 결과와 달라지는 실행 가정은 버전으로 구분한다. |
| `backtest_v3_regime.py` | 시장 regime 연구와 역할 구분 | 본 문서의 신규 event 전략과 섞어 파라미터 탐색하지 않는다. |

현재 점수 엔진의 지표를 다시 만들기보다, 향후에는 **시간 순서가 있는 event state**를 그 위에 추가한다. 기존 평균회귀의 약한 결과는 새로운 가설의 수익성 근거가 될 수 없다.

## 2. 공통 시계·상태·최소 feature 모델

### 표기와 사전 고정 규칙

- `A`: SETUP을 인식하기 **직전**까지 완료된 5분봉 14개의 평균 TR. 초기에는 기존 simple ATR 방식을 유지하고 setup 수명 동안 고정한다. 필요하면 전일 정규봉으로 ATR warmup을 하되 session VWAP/OR은 매일 리셋한다.
- `b = 0.10A`: 가격 경계 buffer. 모든 수치는 검증 전의 coarse 출발값이며 최적값이라는 뜻이 아니다.
- `OR5/15/30`: 09:30부터 해당 분까지의 고가·저가. 기본은 OR15. 완성 후 고정한다. 세 범위를 처음부터 모두 조합하지 않는다.
- rolling 가격 구조, impulse, pullback, box는 현재 정규 세션 안에서만 만든다. 필요한 세션 내 관측이 부족하면 해당 event를 평가하지 않는다. 전일 indicator warmup을 개장 이전 event로 오인하지 않는다.
- `slope = (VWAP_t - VWAP_{t-3}) / ATR_t`: 완료된 5분봉 기준. ER은 12개 가격 변화 기준이며 필요한 과거 봉이 없으면 unavailable로 처리한다.
- `RVOL(t)`: 직전 최대 20거래일의 **동일 세션 시간 구간** 거래량 median 대비 현재 완료 구간 거래량. 최소 10일 필요. 당일 다른 시간대와 단순 비교하면 개장 거래량 곡선을 왜곡하므로 대체하지 않는다.
- `M+`: QQQ가 VWAP 위이고 slope ≥ 0, SPY가 강한 하락 상태가 아님. `M-`는 반대. 강한 하락은 VWAP 아래, slope < -0.15, ER > 0.60의 동시 상태다. 그 외의 충돌 상태는 `MIXED`로 기록한다.
- 반도체 LONG의 `S+`: SOXX와 SMH 모두 VWAP 위, slope ≥ 0. 역방향 ETF의 underlying 확인은 반대로 적용한다. 개별 기술주는 QQQ, 반도체주는 SOXX를 RS reference로 쓴다.
- 숫자 경계를 ARMED 중에 움직이지 않는다. 상태는 `IDLE → SETUP → ARMED → TRIGGERED → ENTERED → EXITED/EXPIRED`다. 순서가 필요한 단계는 서로 다른 완료 봉에서 발생해야 한다.
- 모든 setup은 expiry를 가진다. 일일 제한은 종목×전략별 첫 번째 ARMED event만 평가하는 보수적 출발점이다. 취소된 event 뒤에 계속 재탐색하여 유리한 event를 고르지 않는다. 이 정책과 V2의 first accepted setup 정책 차이를 결과에 표시한다.
- 신호는 stop/target을 제안하고 기존 Risk/Execution 계층이 주문을 승인한다. 실거래 safety, 주문 중복 방지, sector 노출 제한은 그대로 유지할 대상이다.

### 공유 feature

| 범위 | 필요한 값 | 목적·인과성 |
|---|---|---|
| 원시/시계 | OHLCV, 종료 시각, session 날짜, 결측/완성 여부, 개장 후 경과 분 | reference 동기화, time stop, 데이터 유효성 |
| 시장 | QQQ/SPY VWAP 위치, slope, ER12, 5/15분 수익률 | 추세·중립·방향 충돌 확인 |
| 변동성/gap | 과거 완료 일봉 ATR14, 오늘 open/전일 close gap, 진입까지 20분 realized volatility | gap 규모 및 변동성 regime. 구간이 부족하면 unavailable. 과거 분위수는 전일까지만 산출 |
| 섹터 | SOXX/SMH VWAP 위치·slope·5/15분 수익률 | sector-wide 움직임 확인, 개별 종목과 구분 |
| 종목 | session VWAP, VWAP 거리/A, ATR14, EMA9/21, ER12, 5/15분 수익률 | 가격 기준점과 추세/횡보 진단. EMA는 pullback 지지 후보로만 사용 |
| 가격 구조 | OR5/15/30, 전일 high/low/close, 현재까지 session high/low, 과거 30분 high/low | event 경계. trigger 봉을 rolling 경계에 포함하지 않음 |
| 거래량 | 동일 시간 RVOL, impulse/pullback 평균 분당 volume, 최근 완료 20분 median volume | 수요 지속, 수축/확장 proxy |
| 상대강도 | `RS15 = r_symbol(15m) - L*r_reference(15m)` | 동일 timestamp 기준. 일반 종목 L=1, TQQQ/SOXL L=3, SQQQ/SOXS L=-3. 증권 metadata로 고정 |
| event 메모리 | frozen box/impulse/OR, extreme, trigger level, event age | 과거 상태를 현재 지표만으로 재구성하는 오류 방지 |

RS는 `RS15 / (A / price_at_window_start)`로 정규화한다. ETF에서 양의 RS가 곧 underlying 상승을 의미하지 않으므로 방향 확인을 별도로 한다. RSI·ADX·Bollinger를 기본 필수 feature로 추가하지 않는다. 범위·ER·ATR·VWAP가 이미 해당 역할을 설명한다.

### 공통 진입 및 청산 규약

Trigger 이후 reference 상태와 시장 구조가 유지되어야 한다. 다음 시가가 trigger close보다 LONG 방향으로 0.30A 넘게 뛰거나 stop 이하라면 entry를 취소한다. 목표가가 entry보다 높지 않거나 예상 순수익 공간이 초기 위험 1R보다 작으면 거래하지 않는다. 가격/volume 비정상, 미완성 reference, 결측, 거래정지, 과도한 spread(있을 때), 계좌 safety 위반도 NO TRADE다.

진입 전 취소는 최신 완료 정보 또는 실제 next-open 가격만 사용한다. next-open 시점에 그 다음 봉의 high/low/close를 보지 않는다. 공통 강제 종료는 15:50 ET 또는 조기 종료 10분 전이며, 신규 진입은 15:15 이후 금지한다. 각 전략의 더 좁은 창이 우선한다. 초기 연구는 부분익절 없이 단일 target/stop으로 event 자체를 검증한다.

## 3. 조사한 전략군과 정확한 상태 설계

### A1. OR breakout → retest → continuation

**이유/regime:** 개장 가격 발견에서 새 범위 밖 가격이 받아들여지고 되돌림에서도 공급이 흡수되면 같은 방향 주문이 이어질 수 있다. 유동성이 충분하고 시장/sector 방향이 일치하는 장초반에 적합하다.

- **SETUP:** OR15 완료, OR 폭 0.5–2.5A, 09:45–11:00, M+ 및 해당 sector 확인.
- **ARMED:** 완료 5분 close > ORH+b, RVOL ≥ 1.2. ORH와 breakout 봉을 저장한다.
- **TRIGGER:** 이후 최대 3개 5분봉 중 low가 ORH±0.20A에 닿고 close > ORH인 retest가 완료된 뒤, 새 1분 close가 그 retest high+b를 넘는다.
- **ENTRY:** 다음 1분 시가. breakout 봉에서 즉시 진입하지 않는다.
- **INVALIDATION:** retest 이전/이후 5분 close < ORH-0.20A, M+ 소멸, retest/trigger 15분 만료.
- **STOP:** 관측된 retest low-b. **TARGET:** entry+2R. **TIME EXIT:** 45분 또는 공통 종료.
- **NO TRADE:** OR 지나치게 넓음, 과도한 breakout extension, 시장/sector 반대, 불완전 OR.
- **실패 모드:** 반복적인 범위 왕복, 개장 뉴스성 gap continuation의 마지막 매수, retest 체결 비용. OR5는 noise, OR30은 늦은 진입 위험이 있어 OR15부터 검증한다.

### A2. 실패한 OR 하방 돌파 → 범위 재진입 LONG — 추천 2

**이유/regime:** 새 저가가 유지되지 않으면 하방 continuation 진입자들의 청산과 범위 내 가격 수용이 반전을 만들 수 있다. 실제 short 포지션은 관측할 수 없으므로 이는 행동 가설이다. 중립 또는 회복 중인 시장에서만 평가한다.

- **SETUP:** OR15 완료, 폭 0.5–2.5A, 09:45–11:00, QQQ/SPY 둘 다 강한 하락이 아님.
- **ARMED:** 완료 5분 close < ORL-b이고 low가 ORL보다 0.10–0.75A 아래다. 첫 breakdown과 이후 extreme을 저장한다.
- **TRIGGER:** 다음 2개 5분봉 안에 close > ORL+b로 reclaim. 이 reclaim 봉이 끝난 뒤 최대 5분 안에 1분 close > reclaim high+b. 같은 시점 QQQ/SPY 모두 강한 하락이 아니고 둘 중 하나는 VWAP 위다.
- **ENTRY:** 다음 1분 시가. **INVALIDATION:** reclaim 만료, reclaim 후 새 1분 close < ORL-b, sweep extreme 갱신, 시장 강한 하락 재개.
- **STOP:** breakdown부터 trigger까지 관측된 최저가-b.
- **TARGET:** OR midpoint와 trigger 시점 VWAP 중 entry 위에 있는 더 가까운 값으로 고정. target 공간이 1R 미만이면 취소한다.
- **TIME EXIT:** 30분 또는 공통 종료. **NO TRADE:** directional selloff, 하방 gap이 계속 확장, sweep > 0.75A, reference 충돌/결측.
- **실패 모드:** 진짜 하락 추세의 일시 반등, 중간 reclaim 이후 재차 매도, 첫 target까지 공간 부족. 상방 실패 SHORT는 별도 진단이며 inverse ETF로 기계적으로 치환하지 않는다.

### B. impulse → controlled pullback → continuation — 추천 1

**이유/regime:** 강한 수요 이후 낮은 거래량의 되돌림에서 가격이 유지되면 새로운 매도 공급이 적을 수 있다. 다시 impulse 방향 가격을 수용할 때 지속 가설을 검증한다. 상승 추세·시장/sector 일치가 핵심이다.

- **SETUP:** 10:00–14:00. 최근 3개 완료 5분봉의 처음 open→마지막 close 상승 ≥ 1A, 마지막 close > 그 3봉 시작 이전 30분 high+b, VWAP 위, M+/해당 sector 확인. 이 3봉을 impulse로 고정한다.
- **ARMED:** 그 뒤 1–3개 완료 5분봉이 impulse 상승폭의 20–50%를 되돌린다. pullback 평균 분당 volume ≤ impulse 평균의 0.70. 최소 한 봉의 low가 그 봉 VWAP 또는 EMA21의 ±0.30A 구간에 닿고, 모든 pullback close가 impulse 시작 가격 및 VWAP 위다.
- **TRIGGER:** 마지막 완료 pullback 5분봉 high+b를 이후 1분 close가 상향 돌파하고, 그 1분 volume ≥ 이전 완료 20분 volume median. M+/sector 확인을 다시 검사한다. 새 5분봉이 완성되면 경계는 해당 완료 pullback 봉으로 갱신하지만 총 3봉 한도를 유지한다.
- **ENTRY:** 다음 1분 시가. **INVALIDATION:** retracement > 50%, close가 impulse 시작/VWAP 아래, 시장 확인 소멸, pullback 3봉 또는 마지막 pullback 후 trigger 대기 5분 만료.
- **STOP:** 모든 관측 pullback low의 최소값-b.
- **TARGET:** 고정 impulse high. entry 이후 target까지 1R 이상일 때만 진입한다. 초기에는 임의 trailing을 붙이지 않는다.
- **TIME EXIT:** 45분; 진입 20분 동안 MFE < 0.30R이면 다음 1분 시가 청산. **NO TRADE:** 이미 impulse high를 넘겨 목표 공간 없음, 고거래량 깊은 되돌림, 지지 구간에 닿지 않은 추격, 시장 혼조.
- **실패 모드:** impulse가 마지막 매수였음, volume 수축이 수요 소멸을 의미함, 높은 sector 상관으로 동일 event를 중복 계산함.

### C. 중립 시장의 일시적 범위 overshoot 평균회귀

**이유/regime:** 방향성이 약한 시장에서 범위를 잠깐 벗어난 매도 불균형이 해소되면 거래가 많이 이루어진 중심 가격으로 복귀할 수 있다. 모든 VWAP deviation을 fade하는 전략과 구분한다. V3와 겹치므로 신규 우선 구현 대상에서 제외한다.

- **SETUP:** 10:00–14:00, QQQ/SPY ER ≤ 0.40 및 |slope| ≤ 0.15, 종목 ER ≤ 0.40. 직전 30분 low를 고정한다.
- **ARMED:** 1분 low가 그 low-b 아래로 내려가고 close가 VWAP-1.5A 아래. 이 1분 volume이 직전 20분 median의 2배 이하이며 동일 시간 RVOL < 2다.
- **TRIGGER:** 이후 3분 안에 1분 close가 고정 30분 low+b 위, 직전 1분 high 위로 복귀한다. 중심 가격이 trigger까지 급격하게 이동하지 않아야 한다(|slope| ≤ 0.15).
- **ENTRY:** 다음 1분 시가. **INVALIDATION:** 새 저가, 시장 directional 상태, 3분 만료.
- **STOP:** overshoot부터 trigger까지 최저가-b. **TARGET:** trigger VWAP 고정, 최소 1R 공간. **TIME EXIT:** 20분.
- **NO TRADE:** 강한 gap continuation, 큰 volume climax, 동시 시장 selloff, 종목/시장 고ER, VWAP가 매도 방향으로 이동.
- **실패 모드:** 정보성 재평가를 일시적 충격으로 오판, 횡보 추정의 lag, 비용이 작은 복귀폭을 잠식. 큰 거래량 dislocation은 이 전략에 섞지 않고 H에서 조사한다.

### D1. gap-and-go LONG

**이유/regime:** 전일 범위 밖 새 가격이 개장 후에도 유지되면 overnight 재평가가 지속될 수 있다. 프리마켓 없이 첫 정규장 행동으로 가격 수용을 확인한다.

- **SETUP:** 양의 gap이 전일까지만 계산한 일봉 ATR의 0.3–1.5배, 오늘 open > 전일 high. 첫 15분 완료, M+/sector 확인.
- **ARMED:** 첫 15분의 세 5분봉 모두 close > 전일 high 및 각 봉 VWAP, 첫 15분 RVOL ≥ 1.2.
- **TRIGGER:** 이후 완료 5분 close > OR15 high+b. 그 뒤 최대 5분 안에 1분 low가 ORH±0.20A에 닿고 close > ORH+b.
- **ENTRY:** 다음 1분 시가. **INVALIDATION:** close < 전일 high, 시장 확인 소멸, retest 만료.
- **STOP:** retest low-b. **TARGET:** entry+2R. **TIME EXIT:** 45분. 신규 진입은 11:00까지.
- **NO TRADE:** gap 1.5일봉ATR 초과, range 안으로 재진입, opening volume 부족, 시장 반대.
- **실패 모드:** overnight 뉴스의 마지막 가격 반영, 넓은 opening range, ORB와 같은 거래를 독립 전략으로 중복 계산. A1과 비교 시 event 중복률을 반드시 보고한다.

### D2. gap-fade: 하방 gap 거부 LONG

**이유/regime:** 전일 범위 밖 가격이 개장에서 거부되면 overnight 불균형 일부가 해소될 수 있다. D1의 continuation과 별도 가설이며 sign만 바꾼 동일 전략이 아니다.

- **SETUP:** 음의 gap 절대값 0.3–1.5 전일 일봉 ATR, open < 전일 low, 09:45–10:30, 시장 강한 하락 아님.
- **ARMED:** 첫 15분 마지막 close > 전일 low 및 VWAP, 첫 15분 low를 저장한다.
- **TRIGGER:** 이후 최대 10분 안에 완료 1분 close > 첫 15분 high+b, QQQ/SPY 둘 중 하나 VWAP 위이고 모두 강한 하락 아님.
- **ENTRY:** 다음 1분 시가. **INVALIDATION:** 전일 low 아래 재마감, opening low 갱신, 10분 만료.
- **STOP:** 첫 15분 low-b. **TARGET:** 전일 close, 최소 1R 공간. **TIME EXIT:** 45분.
- **NO TRADE:** 시장/sector 강한 하락, 큰 정보성 gap 가능성을 시사하는 지속적인 가격·volume 확장, 전일 close가 entry 아래.
- **실패 모드:** fundamental repricing을 gap fill로 오판, 시장 반등과 무관한 개별 악재, gap 크기만으로 fade를 선택하는 오류.

### E/G. 상대강도 유지 + 압축 → 확장 — 추천 3

**이유/regime:** 시장 움직임에도 가격이 잘 버티는 종목에서 좁은 범위가 형성되면, 시장이 회복될 때 지연된 수요가 가격 경계를 넘을 수 있다. 변동성 축소 자체가 방향을 예측하지는 않으므로 상대강도와 reference 회복을 함께 요구한다.

- **SETUP:** 10:30–14:30. 최근 6개 완료 5분봉을 box로 고정. 폭 ≤ 1.2A, 마지막 3봉 median range ≤ 그 6봉 이전 12봉 median range의 0.70. 이때 정규화 RS15 ≥ 0.25, 종목 close ≥ VWAP.
- **ARMED:** 다음 최대 3개 5분봉 동안 box 안에서 close 유지, box low 미갱신, 정규화 RS15 ≥ 0.25 유지. sector/market의 강한 하락은 없어야 한다. box 경계와 A를 다시 최적화하지 않는다.
- **TRIGGER:** ARMED 이후 완료 5분 close > box high+b, 동일 시간 RVOL ≥ 1.5. QQQ 5분 수익률 ≥ 0, QQQ close > VWAP/slope ≥ 0, SPY 강한 하락 아님. 반도체는 S+도 요구한다.
- **ENTRY:** 다음 1분 시가. **INVALIDATION:** trigger 전 box low 이탈, RS 기준 소멸, reference 강한 하락, 3봉 만료.
- **STOP:** frozen box low-b. **TARGET:** entry+2R.
- **TIME EXIT:** 60분; 진입 후 완료 5분 close가 box high-b 아래면 다음 1분 시가 조기 청산.
- **NO TRADE:** 넓은 box, 낮은 데이터 품질로 생긴 가짜 압축, ETF leverage 미보정 RS, 방향 reference 미확인, 이미 box를 크게 벗어난 next-open.
- **실패 모드:** breakout liquidity trap, 우연한 RS 또는 beta 변화, squeeze 이후 양방향 변동성만 확장. RS가 강하다는 이유로 시장 하락을 계속 매수하지 않는다.

약한 시장에서 버티는 종목은 **후보 등록**까지 가능하나 entry는 reference 회복 후다. 시장 상승 중 약한 종목은 LONG 후보에서 제외한다. SHORT/역방향 ETF 연결은 이후 별도 설계가 필요하다.

### F. market-leader confirmation: 독립 주문 전략이 아닌 overlay

**이유/regime:** 같은 경제 노출을 가진 유동성 높은 reference에서도 가격 경계가 수용되면 개별 종목의 돌파가 독립 noise일 가능성을 줄일 수 있다. QQQ/SPY/SOXX/SMH의 움직임이 미래 종목 가격을 선행한다고 가정하지 않는다.

- **SETUP:** A1/B/E-G의 symbol event가 존재하고 필요한 reference가 동기화되어 있다.
- **ARMED:** QQQ가 이전 30분 high+b_reference 위에서 완료 5분 마감. 반도체에는 SOXX/SMH의 VWAP 위 상태를 추가. 이때 symbol의 과거 RS를 저장한다.
- **TRIGGER:** 다음 2개 5분봉 안에 해당 symbol 전략의 자체 trigger 발생, leader가 breakout 경계/VWAP 위를 유지한다.
- **ENTRY:** symbol 전략의 next-minute-open. **INVALIDATION:** leader 경계 이탈/expiry 또는 symbol invalidation.
- **STOP/TARGET/TIME EXIT:** symbol 전략 그대로. 새로운 중첩 target을 만들지 않는다.
- **NO TRADE:** 이미 끝난 symbol trigger에 나중의 leader 확인을 소급 적용, reference 결측, semiconductor reference 충돌.
- **실패 모드:** 상관된 신호를 독립 확인처럼 과대평가, follower 진입이 너무 늦어짐, leader false breakout. 필수 확인과 단순 진단의 차이는 사전 선언한 한 번의 ablation으로만 비교한다.

### H. exhaustion / failed momentum reversal

**이유/regime:** 이미 진행된 방향성 움직임에서 큰 volume에도 더 이상 가격이 확장되지 않고 구조가 깨지면, 마지막 추격 주문 이후 반전 가능성이 있다. C의 낮은 효율 range 복귀와 달리 **선행 추세 + climax + 확장 실패**가 필수다.

- **SETUP:** 10:00–14:30, 직전 15분 하락폭 ≥ 2A, 새 session low 발생. LONG 대칭만 우선 정의한다.
- **ARMED:** 새 저가 5분봉 동일 시간 RVOL ≥ 2.5. climax low와 midpoint를 고정한다.
- **TRIGGER:** 다음 2개 완료 5분봉이 climax low-0.10A보다 낮아지지 않고, 둘 중 뒤의 close가 climax midpoint 위. 이후 최대 5분 안에 새 1분 close가 이 두 봉 high의 최대값+b를 넘는다. QQQ/SPY 동시 강한 하락이 없어야 한다.
- **ENTRY:** 다음 1분 시가. **INVALIDATION:** climax low-0.10A 이탈, climax 이후 15분 만료, reference selloff 지속.
- **STOP:** climax와 확인 구간 최저가-b. **TARGET:** trigger VWAP 고정, 최소 1R 공간. **TIME EXIT:** 30분.
- **NO TRADE:** 큰 volume만 있고 확장 실패 없음, 시장 동시 추세, 저유동성, 높은 비용, VWAP가 목표 공간을 제공하지 않음.
- **실패 모드:** trend pause를 exhaustion으로 오판, 정보성 매도 재개, intrabar reversal을 OHLCV로 과대해석. 높은 volume은 반전의 충분조건이 아니다.

OR compression followed by expansion은 A의 고정 OR 경계를 E/G의 압축 상태로 사용하는 변형이다. 첫 연구에서는 별도 후보를 늘리지 않고 E/G의 동적 intraday box부터 검증한다. Intraday Close Momentum은 이전 결과가 약하고 이번 질문의 신규 event 가설이 아니므로 우선 후보에서 제외한다.

## 4. 시장 regime 및 공통 실패 진단

| 후보 | 기대 regime | 명시적으로 제외 | 주된 실패 |
|---|---|---|---|
| OR retest / gap-and-go | 가격 발견 후 방향 수용 | mixed market, 큰 extension | late breakout / 왕복 |
| impulse pullback | 확인된 상승 추세 | 깊고 고거래량 pullback | 마지막 impulse / 추세 종료 |
| failed OR | 중립·회복, 하방 가격 거부 | strong directional selloff | 일시 반등 후 재하락 |
| neutral MR / gap-fade | 안정된 중심 가격·범위 복귀 | 정보성 gap·동시 market trend | 진짜 재평가 fade |
| RS compression expansion | 종목의 견고함, reference 회복 | reference selloff·가짜 압축 | trapped breakout / beta 오판 |
| exhaustion | 선행 추세 후 확장 실패 | climax만 있고 구조 확인 없음 | trend pause를 반전으로 오판 |

시간대·gap·RV·ER·reference alignment를 거래별로 저장한다. regime label은 **entry 시점** 값만 사용한다. 이후 하루 전체를 본 뒤 trend/range day로 분류한 값은 설명용 별도 분석이며 진입 필터나 성과 선택에 사용할 수 없다.

## 5. 상위 5 후보 순위

각 점수는 1(낮음)–5(높음)의 **설계 판단**이다. 빈도는 예상치이며 실측치가 아니다. 합계는 검증된 edge나 투자 적합성을 뜻하지 않는다. 동점은 규칙 단순성을 우선했다. overlay F는 독립 후보로 순위를 매기지 않았다.

| 순위 | 후보 | 경제/행동 이유 | 신호 명확성 | 백테스트 용이성 | 예상 빈도 | 과적합 저항 | 데이터 호환 | 시장/sector 확인 | 합계 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | B: controlled impulse pullback | 5 | 5 | 4 | 3 | 4 | 5 | 5 | 31 |
| 2 | A2: failed OR reversal | 4 | 5 | 5 | 3 | 4 | 5 | 5 | 31 |
| 3 | E/G: RS compression expansion | 4 | 5 | 4 | 3 | 4 | 5 | 5 | 30 |
| 4 | D1: gap-and-go | 4 | 4 | 5 | 2 | 3 | 5 | 5 | 28 |
| 5 | A1: OR breakout retest | 4 | 4 | 5 | 3 | 3 | 5 | 4 | 28 |

C는 이미 V3에서 연구하는 약한 평균회귀와 중복되고, D2/H는 뉴스·실제 liquidity 관측이 없는 상태에서 지속 추세를 잘못 fade할 위험이 더 크다. 조사 대상으로 유지하되 최초 구현 3개에 넣지 않는다.

## 6. 추천 3개와 구현 전용 의사코드

서로 다른 가설을 선택한다: **지속 수요**, **실패한 가격 수용**, **상대강도와 변동성 전환**. 실제 수익률 상관은 미확인이다. 세 전략 모두 같은 시장 반등에 의존할 수 있으므로 이름이 다르다는 이유로 portfolio 분산을 주장하지 않는다.

아래는 위 상태 규칙을 참조하는 의사코드다. `past`는 해당 event 이전 완료 데이터, `now`는 현재 종료 시점 snapshot이다. 각 `transition`은 event 시각·경계·탈락 사유를 저장한다. `next_open`은 실행 시점에 처음 알 수 있는 값이다.

```python
# B: impulse/pullback continuation
def setup_B(now, past):
    return in_window(now, "10:00", "14:00") and impulse_3bars(past, now, min_atr=1.0) \
        and breaks_pre_impulse_30m_high(now) and market_sector_up(now)

def armed_B(event, now):
    return ordered_pullback(event, now, bars=(1, 3), retrace=(0.20, 0.50)) \
        and volume_ratio(event.pullback, event.impulse) <= 0.70 \
        and support_touched(event, tolerance_atr=0.30) and closes_hold_vwap_and_origin(event)

def trigger_B(event, now):
    return later_1m_close(now) > event.last_completed_pullback_high + event.b \
        and now.volume_1m >= now.previous_20m_volume_median and market_sector_up(now)

def entry_B(event, next_open):
    stop_price = stop_B(event)
    target_price = target_B(event)
    return guarded_next_open(event, next_open, stop_price, target_price, min_reward_r=1.0)

def stop_B(event): return event.pullback_low - event.b
def target_B(event): return event.impulse_high
def exit_B(position, now):
    return stop_or_target(position, now) or time_limit(position, 45) \
        or (elapsed_minutes(position) >= 20 and position.mfe_r < 0.30) or session_force_exit(now)

# A2: failed downside OR breakout
def setup_A2(now, past):
    return completed_or15(now) and in_window(now, "09:45", "11:00") \
        and or_width_atr(now) in closed_interval(0.5, 2.5) and not strong_market_down(now)

def armed_A2(event, now):
    return now.close_5m < event.or_low - event.b \
        and downside_excursion_atr(event, now) in closed_interval(0.10, 0.75)

def trigger_A2(event, now):
    # reclaim must first complete within 2 five-minute bars; then wait at most 5 minutes
    return event.completed_reclaim and later_1m_close(now) > event.reclaim_high + event.b \
        and not strong_market_down(now) and any_market_reference_above_vwap(now)

def entry_A2(event, next_open):
    return guarded_next_open(event, next_open, stop_A2(event), target_A2(event, next_open), 1.0)

def stop_A2(event): return event.observed_sweep_low - event.b
def target_A2(event, entry_price):
    return nearest_above(entry_price, [event.or_midpoint, event.trigger_vwap])
def exit_A2(position, now):
    return stop_or_target(position, now) or time_limit(position, 30) or session_force_exit(now)

# E/G: relative-strength compression expansion
def setup_EG(now, past):
    return in_window(now, "10:30", "14:30") and box_width_atr(last_6bars(now)) <= 1.2 \
        and range_ratio(last_3bars(now), prior_12bars_before_box(now)) <= 0.70 \
        and now.normalized_rs15 >= 0.25 and now.close >= now.vwap

def armed_EG(event, now):
    return later_completed_bar(now) and close_inside_frozen_box(event, now) \
        and low_holds(event, now) and now.normalized_rs15 >= 0.25 and not strong_market_down(now)

def trigger_EG(event, now):
    return later_completed_5m_close(now) > event.box_high + event.b \
        and now.same_time_rvol >= 1.5 and market_recovering(now) and required_sector_up(now)

def entry_EG(event, next_open):
    return guarded_next_open(event, next_open, stop_EG(event), target_EG(event, next_open), 1.0)

def stop_EG(event): return event.box_low - event.b
def target_EG(event, entry_price): return entry_price + 2 * (entry_price - stop_EG(event))
def exit_EG(position, now):
    return stop_or_target(position, now) or completed_5m_close(now) < position.box_high - position.b \
        or time_limit(position, 60) or session_force_exit(now)
```

구조에 따른 invalidation/expiry는 각 후보의 정의를 적용하며 모든 함수 호출 전에 검사한다. `stop_or_target`은 minute-level 실행 규칙을 뜻한다. stop/target intrabar 판단을 제외한 time/structure exit는 판단 후 next-minute-open이며 강제 종료는 미리 정한 시각의 시가다. 결측 때문에 해당 시가가 없으면 성공 체결로 간주하지 않고 데이터 오류로 기록한다.

## 7. 최소 향후 백테스트 계획 — 지금은 실행하지 않음

1. **데이터 확인:** 수집 완료 후 기존 파일의 timestamp 의미·split/가격 조정·중복·결측·정규 세션·reference 동기화만 확인한다. feature warmup 부족을 0으로 채우지 않는다. 과거 동일 시간 RVOL에 당일 이후 데이터를 넣지 않는다. 결측과 거래 불가 종목별 탈락 수를 보고한다.
2. **사전 등록:** 상위 3개 각각 위의 단일 config를 고정한다. OR15를 사용하며 5/15/30 조합이나 indicator grid를 돌리지 않는다. 유니버스는 데이터 커버리지가 확인된 고정 목록이고 reference는 주문 후보와 구분한다. 결과를 본 후 winner ticker만 남기지 않는다.
3. **실행 계약:** next-minute-open, 양방향 2bps slippage 출발, stop-before-target. stop을 시가가 넘어선 경우 LONG은 더 불리한 시가로 stop 체결한다. commission/규제 수수료는 기존 가정을 명시하고 실제 비용을 추가 확보하면 별도 표시한다. 2bps만으로 spread·모든 수수료가 포함되었다고 주장하지 않는다. bid/ask가 없으면 같은 ledger를 5bps로 한 번 비용 stress할 수 있으나 비용 수준을 최적화하지 않는다.
4. **작은 실험:** 완료된 features를 한 번 계산하고 3개 event ledger를 만든다. 각 전략의 market/sector confirmation을 제외한 사전 정의 ablation을 최대 한 번씩만 추가할 수 있다(전체 최대 6개 설정). 조건을 반복 변경하며 양수 결과를 찾지 않는다. 첫 연구는 각 전략 독립 결과부터 보고하며 동시 이벤트를 portfolio 수익으로 합산하지 않는다.
5. **시간 순서:** 데이터 길이가 허용하면 최소 3개 expanding chronological fold를 쓴다. 초기 45% train → 다음 15% OOS, 60% train → 다음 15% OOS, 75% train → 마지막 25% OOS를 출발점으로 한다. OOS 구간은 겹치지 않는다. 경계 session을 분리하고 다음 구간으로 열린 포지션을 넘기지 않는다. train은 기준 점검용이며 threshold를 fit하지 않는다. 과거로부터 계산한 RVOL은 OOS에서 새로 완료된 과거 세션만 순차적으로 갱신한다.
6. **보고:** 전체/train/각 OOS의 trades, win rate, net expectancy R, PF, 누적 R 최대 drawdown, 보유시간, MAE/MFE, 비용을 출력한다. 종목×전략, 연도, 시간대(09:30–10:00 / 10–11 / 11–12 / 12–14 / 14–15 / 15–16), entry 시점 gap/RV/ER/alignment regime, 탈락 funnel도 함께 저장한다. 각 그룹의 n을 반드시 표시하고 희소 그룹의 높은 PF를 근거로 필터를 추가하지 않는다.
7. **판정:** 모든 OOS expectancy > 0, median OOS PF ≥ 1.15, 각 fold 최소 12 trades/합계 36 trades를 연구상 최소 문턱으로 제안한다. 이는 통계적 확증이 아니다. 한 fold가 positive-fold gross profit의 70% 초과를 차지하면 집중 경고를 표시하고 PASS를 보류한다. 여러 연도를 포함할 때 같은 연도 집중도도 확인한다. 2/3 positive 또는 작은 PF 우위는 WEAK, 음의 median expectancy/불안정 fold는 FAIL이다. 부족한 표본은 INCONCLUSIVE로 분리한다.
8. **중단 원칙:** FAIL 이후 parameter mining을 하지 않는다. WEAK면 어느 event/실행 가정이 깨졌는지 기록하고 추가 독립 기간을 기다린다. OOS 결과를 보고 top 후보를 고르는 순간 selection bias가 생기므로 최종 확증에는 별도 미사용 기간 또는 미래 paper 관측이 필요하다. 아직 live 연결 단계가 아니다.

초기 목표는 거래 수를 늘리는 것이 아니라, **그럴듯한 event가 실제로 반복 가능한 순수익을 만드는지 반증 가능한 방식으로 확인하는 것**이다. 조건에 맞는 event가 없으면 NO TRADE가 정상이다.
