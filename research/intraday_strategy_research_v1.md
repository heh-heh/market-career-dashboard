# 미국 주식 장중 전략 재연구 v1

작성일: 2026-10-10. 상태: **사전 가설·설계, 수익성 검증 없음**.
기준 저장소: strategy-engine-v3 원격 `95cf60b`와 같은 시점의 작업 트리.
이 문서는 기존 V3, Simple Momentum V1, frozen V4 S1/S2/S3를 교체하거나
수정하지 않는다. 새 가설의 ID는 IR1/IR2/IR3다. 기존 forward 로그는 읽어
재최적화하지 않았으며 변경하지 않았다. 선택한 세 가설을 검증하기 전에
production 엔진을 다시 작성하지 않는다.

## 1. 기존 V3 실패에서 알 수 있는 것

사용자가 보고한 forward 요약은 완료 25건, 승률 약 28%, 거래 수익률의 합
약 -9.41%, ORB_RETEST 손실 집중, VWAP 평균회귀 부진, finalScore와 실제
수익의 양의 관계 부재다. **이 세션에서 원시 거래 로그를 재분석한 수치는
아니다.** 수익률 합은 복리 계좌 수익률·drawdown과 다르다. 승률만으로 PF,
기대값, 비용 기여도를 역산할 수 없다.

25건은 구현/표본/실행 문제를 발견할 단서다. 전략군 전체의 무효를 증명하거나
threshold를 학습하기에는 부족하다. 점수를 가격 예측 확률로 간주한 것,
부적합 ETF 포함, 실패한 돌파의 continuation 오인, 손절 이후 불리한 체결,
일부 섹터/시간대의 집중이 가능한 원인이다. 현재 자료로 어느 원인이 실제
지배적이었는지는 판정하지 않는다. 앞으로 다음을 서로 분리한다.

1. SETUP이 가진 조건부 가설과 진입 이후 관측된 가격 경로.
2. 시장/섹터 regime 및 종목 종류.
3. trigger → 실제 처리 → 실제 관측 가격의 지연과 비용.
4. entry와 exit가 각각 손익에 기여한 정도.
5. score에 의한 API 조회 우선순위와 실제 기대수익의 추정.

ORB score에 수치를 더하거나 25건에 맞추는 변경은 하지 않는다.

## 2. 근거의 수준과 조사 한계

이 환경에는 public web search 도구가 없다. Crossref, DOI, OUP, NBER,
arXiv에 대한 읽기 요청이 proxy 403으로 차단됐다. 따라서 아래는 알려진
동료심사 연구/학술 문헌에 근거한 **문헌 종합**이며, 이번 세션에서 원문을
새로 열람·재현한 literature review라고 주장하지 않는다. 서지와 세부 실험
설계는 원문 접근이 가능한 환경에서 재확인해야 한다. 논문의 과거 효과가
2026년, Toss REST, 우리 universe와 체결 지연에서도 유지된다는 근거는 없다.
출처별 효과 크기나 수익률을 기억으로 만들어 인용하지 않는다.

| ID / 학술 출처 | 연결되는 근거 | 우리 전략으로 외삽할 수 없는 부분 |
|---|---|---|
| E1 Gao, Han, Li, Zhou (2018), *Market intraday momentum*, Journal of Financial Economics 129(2), 394–414. [DOI](https://doi.org/10.1016/j.jfineco.2018.05.009) | 미국 시장의 첫 30분 수익과 마지막 30분 수익 사이의 예측 관계를 연구. IR3의 가장 직접적인 출발점 | 개별 종목 pullback, ORB, 60초 REST 체결, 15:50 청산의 수익성을 입증하지 않음. 논문의 전체 마지막 30분과 우리 마지막 약 20분은 다름 |
| E2 Heston, Korajczyk, Sadka (2010), *Intraday Patterns in the Cross-section of Stock Returns*, Journal of Finance 65(4), 1369–1407 | 장중 수익의 시간대별/동일 시간 구간 간 구조가 존재함 | 그 결과를 임의의 최근 20분 cross-sectional momentum의 증거로 대체할 수 없음 |
| E3 Cont, Kukanov, Stoikov (2014), *The Price Impact of Order Book Events*, Journal of Financial Econometrics 12(1), 47–88. [DOI](https://doi.org/10.1093/jjfinec/nbt003) | order-flow imbalance와 가격 변화의 관계. volume 단독보다 수급 상태와 depth가 중요함 | OHLCV의 큰 양봉/거래량을 실제 매수 주문 불균형이나 미래 alpha라고 부를 수 없음 |
| E4 Bouchaud, Farmer, Lillo (2009), *How Markets Slowly Digest Changes in Supply and Demand*, Handbook of Financial Markets: Dynamics and Evolution | 주문 분할/지속성과 유동성 공급이 가격 충격을 조절하는 미시구조 논의 | OHLCV만으로 metaorder 존재를 식별하거나 기관 매집을 확인하지 못함 |
| E5 Nagel (2012), *Evaporating Liquidity*, Review of Financial Studies 25(7), 2005–2036 | 단기 reversal과 유동성 공급의 상태 의존성 | 고변동성 때 reversal premium이 높을 수 있어도, 우리 느린 market-order fade가 그 premium을 획득한다는 뜻은 아님 |
| E6 Da, Liu, Schaumburg (2014), *A Closer Look at the Short-Term Return Reversal*, Management Science 60(3), 658–674 | reversal의 여러 원인을 분리하고 시장·산업/정보 효과를 구분해야 함 | OR15 failed breakdown의 직접 증거가 아니며, 기간/보유 방식도 다름 |
| E7 Lou, Polk, Skouras (2019), *A Tug of War: Overnight Versus Intraday Expected Returns*, Journal of Financial Economics 134(1), 192–213 | overnight와 intraday 수익원을 구분할 필요 | overnight gap을 장중 continuation 수익률과 섞어서 alpha를 측정하면 안 됨 |
| E8 Lee, Swaminathan (2000), *Price Momentum and Trading Volume*, Journal of Finance 55(5), 2017–2069 | 거래량과 momentum의 상호작용은 단순한 보편 법칙이 아님 | 월 단위 결과를 1분 volume spike의 거래 신호로 외삽하지 않음 |

E1은 IR3에 가까운 경험적 근거다. IR1은 E3/E4의 구조적 설명에 연결되는
**추론적 가설**, IR2는 E5/E6와 정합적인 **가격 수용 실패 가설**이다.
세 가지 모두 아래의 정확한 규칙 자체에 대한 검증 자료는 아직 없다.

## 3. 조사한 전략군

아래의 latency 평가는 예측 기간 대비 API 스캔 약 60초를 기준으로 한다.
모든 행은 raw 1분 OHLCV로 정의할 수 있지만, bid/ask·뉴스·실제 주문 흐름은
있는 것으로 가정하지 않는다. 데이터 형식의 가능성과 alpha의 존재는 별개다.

| 아이디어 | 행동/지속 가능한 이유 | 작동 가정 / 실패·regime | 1분/Toss·지연·세션·대상 | 판단 |
|---|---|---|---|---|
| 단순 momentum continuation | 분할된 수요가 일정 기간 지속 | 정보 수요 지속 / 늦은 진입·수급 종료·추세 반전 | OHLCV 가능, 20–45분이면 60초 지연 검증 가능. RTH 주식/주식 ETF. 갭을 분리 | IR1의 사건 순서로 한정 |
| Impulse→pullback→continuation | 상승 충격 이후 낮은 매도 활동과 구조 회복 | 시장 동행·상대강도 / 낮은 volume이 수요 소멸일 수도 있음 | 완료 5분 state + 완료 1분 trigger, 개장 auction 회피. 주식 우선 | **IR1** |
| Opening range breakout | 개장 가격 발견 이후 새로운 가격 수용 | 정보 변화·참여 / auction noise·false acceptance | OR5는 지연/노이즈 민감, OR15/30도 edge 미확인. RTH 주식/ETF | 독립 baseline 제외 |
| Failed OR breakout/breakdown | 범위 밖 거래를 시장이 유지하지 못함 | 중립/복구 시장 / 실제 악재의 하방 재평가 | OR15 고정, 실패 순서 5분, 1분 지연 허용 한계를 시험. RTH 주식 | **IR2, downside 실패의 LONG만** |
| VWAP reclaim/rejection | 평균 거래 가격 주변 포지션 기준점 | 유효한 선행 impulse / VWAP 반복 교차·정보 변화 | VWAP 자체는 예측 근거 없음. 1분/REST 가능. 주식/ETF | 선행 사건 없는 독립 전략 제외 |
| Relative strength | 공통 시장 움직임을 넘는 방향 지속 | 종목 수요 지속 / 단순 high-beta·누락된 sector/crypto 요인 | 동시간 benchmark 필수. 20분 이상, RTH 주식 | IR1에 결합; beta 진단 필요 |
| Gap-and-go | overnight 정보 재평가를 RTH가 계속 수용 | 진짜 catalyst / 이미 반영·auction 재조정 | 전일 raw 기준/개장 자료 필요. 프리마켓/뉴스 없어서 설명 불완전. 주식/ETF | 후속 연구; 첫 baseline 제외 |
| Gap-fade | 개장 불균형의 일시성 | 비정보성 gap / 진짜 정보 shock | 뉴스 없는 큰 gap fade는 위험, opening fill 지연 민감. 주식/ETF | 제외, IR2와 섞지 않음 |
| Volume/RVOL expansion | 참여가 많아 유동성과 가격 발견이 증가 | **방향은 별도 사건이 제공** / 양방향 거래·반대편 공급 | 과거 동일 시간 필요. 단순 거래량 spike는 초단기 지연 민감. 주식/ETF | universe/진단/작은 confirmation |
| Trend persistence | 포지션 조정이 일정 기간 지속 | 방향 흐름 / trend 종료·점심 chop | 중기 장중 신호는 가능; overnight momentum으로 대체하지 않음 | IR1·IR3의 가설 요소 |
| Compression→expansion | 대기 주문/낮은 변동 뒤 새로운 가격 수용 | 방향 수요가 따를 때 / 양방향 whipsaw·큰 spread | box 수치는 가능하지만 압축만으로 방향을 알 수 없음. 장중 주식/ETF | 첫 3개와 중복; 별도 전략 제외 |
| Extreme-move mean reversion | 일시적 유동성 수요의 충격 회복 | 정보 없는 불균형 / trend/news·liquidity 소멸 | Z는 중간 평균의 정규성/정상성을 보장하지 않음. 느린 fade에 특히 불리 | blind VWAP Z fade 제외 |
| Regime-conditioned momentum | 동일 사건도 공통 시장 방향에 따라 달라짐 | 낮은 방향 충돌 / regime 분류가 늦음 | past-only index 자료로 가능. 별도 alpha 모델이 아닌 활성화 가설 | 세 전략의 공통 layer |
| Cross-sectional momentum | 상대 승자가 자금 배분의 대상 | 산업 공통 충격 제거 / beta·시간대·survivorship | 30개 편향된 universe로 미국 전체 효과를 주장하지 못함 | 독립 long-short 제외; IR1 RS proxy |
| Price/volume acceleration | 진행 중 수요의 속도 증가 | 확장 지속 / exhaustion·climax | 수초 효과는 60초 REST가 제거할 수 있음. 개장/뉴스에서 특히 위험 | 독립 scalp 제외 |
| Liquidity/volatility filters | 비용과 관측 위험에 비해 이동 공간 확보 | 충분한 dollar liquidity / spread 확대·stale data | 실호가 optional, 없으면 명시적 비용 proxy. 주식/주식 ETF | alpha가 아닌 실행 가능성 |
| Closing index momentum | 개장 방향/참여가 장후반 포지션 수요와 관련 | opening/late 방향 정렬 / closing auction·새 뉴스·반전 | broad ETF, 15:30 이후; 60초 지연과 15:50 종료를 별도 시험 | **IR3**, E1의 제한된 변형 |

## 4. 선택한 세 가설과 서로 다른 수익원

### IR1_RS_PULLBACK — 상대강도 impulse 후 통제된 pullback 지속

**가설을 먼저 정한다:** 시장을 웃도는 상승 사건 뒤 일부 가격 되돌림이
더 낮은 분당 거래량으로 이루어지고, 과거 pullback 구조를 다시 넘을 때,
지속되는 수요의 continuation이 무조건 진입보다 유리할 수 있다.

US common stock의 RTH 중간 구간, 시장 TREND_UP/NORMAL_VOL만 첫 baseline으로
검증한다. 이는 E3/E4의 수급 설명과 맞지만 order flow를 직접 본 것은 아니다.
benchmark return을 뺀 값도 beta=1 proxy라 true residual alpha가 아니다.
단순 high-beta 설명이 OOS의 모든 수익을 설명하면 가설은 기각한다.

핵심 공식(모든 경계·상태는 [수식 명세](intraday_strategy_formulas_v1.md)):

```text
A = setup 직전 ATR5(14), setup 동안 고정
advance = C5[j] - O5[j-2]
RS20A = (log(Ci[t]/Ci[t-20])-log(Cb[t]/Cb[t-20])) * Ci[t-20]/A
impulse: advance >= 1.0A; C5[j] > pre_high30 + 0.10A; RS20A >= 0.25
PB = (C5[j] - min(L5[pullback])) / advance ∈ [0.20,0.50]
VC = mean(V5[pullback])/mean(V5[impulse]) <= 0.70
B = 첫 qualifying pullback의 마지막 5분 high + 0.10A(ARMED에서 고정)
trigger: 완료 1분 close > B; volume/median(직전 20개 완료 1분 volume) >= 1.0
stop = frozen pullback low - 0.10A
R = actual entry fill - stop
trail: 관측 MFE >= 1R 이후 max(initial_stop, observed_high - 1.0A)
```

새로운 lower low, VWAP/origin 이탈, context 실패는 trigger 전에 취소한다.
entry가 trigger close보다 0.30A 넘게 진행했다면 취소한다. 고정 target은 없다.
최대 45분 보유하며, 20분 시점의 관측 MFE<0.30R이면 다음 관측에서 청산한다.
late impulse, 정보 수요 종료, high-beta 오분류, 거래량 수축의 오해가 실패 요인이다.
빈도 예상은 MEDIUM→LOW다. 전체 funnel을 기록하고 적은 표본을 이유로 조건을 풀지 않는다.

### IR2_FAILED_OR — 개장 범위 하락 돌파 실패의 반전

**먼저 가설을 정한다:** 고정 OR15 아래에서 실제로 거래하고 마감했지만,
후속 구간에서 그 저가를 갱신하지 못하고 범위 안으로 복귀하는 사건은
범위 밖 가격의 수용 실패를 나타낼 수 있다. 시장이 계속 하락하지 않는다면
OR midpoint까지의 회복을 검증할 수 있다.

VWAP Z-score로 setup을 만들지 않는다. 범위 안 마감→범위 밖 마감→저가 갱신
실패→범위 재진입 순서를 요구한다. volume climax는 진단값이다. US common
stock만, 신규 setup은 RANGE/MIXED와 NORMAL_VOL, 절대 gap≤1.0 daily ATR다.
10:00–11:00만 진입하므로 가장 이른 개장 반전은 이번 baseline의 대상이 아니다.
고변동성 제외는 느린 실행의 위험 관리 가설이며, E5가 저변동성 fade의 우위를
증명해서 채택하는 것이 아니다. 뉴스 없는 OHLCV로 악재를 완전히 구분할 수 없다.

```text
ORL=min(low,09:30–09:45); ORH=max(high,same interval); 완성 후 고정
breakdown: prior C5>=ORL; C5<ORL-0.10A;
           0.10 <= (ORL-L5)/A <=0.75
failure_low=breakdown low, 고정
reclaim: breakdown을 제외한 다음 2개 완료 5분봉 중 C5>ORL+0.10A
         AND 모든 후속 1분 low>=failure_low AND market RECOVERY
stop=failure_low-0.10A; target=(ORL+ORH)/2
예상 net reward/(actual entry-stop)>=1.0
```

다음 관측 가격으로 진입한다. 새로운 lower low, 세 번째 봉의 reclaim, 시장의
지속 하락이면 거래하지 않는다. target 감지 후에도 실제 관측 가격으로 청산하며
과거 target 가격으로 돌아가 체결하지 않는다. trailing은 없고 최대 30분이다.
빈도 LOW. 진짜 가격 재평가, 평균의 변화, rebound가 이미 target을 넘은 gap,
실행 지연으로 reward 공간이 사라지는 것이 주된 실패 요인이다.

### IR3_CLOSE_FLOW — 초기 방향과 일치하는 장후반 지수 모멘텀

**먼저 가설을 정한다:** 첫 30분의 양의 수익과 참여가 크고, 15:30에도 지수의
방향이 유지된다면 시장 전체의 지속적인 포지션 수요가 cutoff까지의 양의
continuation과 관련될 수 있다. E1에 가장 가까운 검증 후보다.

SPY/QQQ만, full 390분 정규장만 다룬다. opening return은 09:30 open→10:00
close이며 전일 close→10:00의 gap 포함 수익이 아니다. NORMAL/HIGH_VOL,
해당 ETF의 UP과 다른 ETF의 non-DOWN을 요구한다. 현재 trend, RVOL, stop은
우리의 추가 가설이므로 E1이 정확히 이 규칙을 검증했다고 해석하지 않는다.

```text
u_open=log(C10:00/O09:30)
Z_open=u_open/std(previous60 sessions' u_open), min40
open_RVOL=sum(V09:30–10:00)/median(previous20 same intervals), min10
signal at15:30: Z_open>=0.50; open_RVOL>=1.25;
                 own ETF UP; other ETF not DOWN
stop=actual entry fill-1.0A
profit exit=15:50 이후 첫 유효한 현재 관측 가격
```

고정 R target과 trailing은 baseline에 없다. 방향의 지속을 먼저 검증하고
최적 target으로 중간 이익을 만들지 않는다. 15:50 forced exit가 time stop이다.
늦은 entry, cutoff의 stale data, 반대 closing flow, 마지막 10분을 거래하지 않는
차이가 실패 요인이다. 빈도 LOW, 각 ETF 최대 1 signal/day, strategy book의
최대 1 position 제한으로 실제 진입은 하루 최대 1건이다. E1을 완전 재현하지
않으며, 짧은 보유·실제 비용·지연에서 edge가 사라지면 기각한다.

세 가설은 개별 종목 수요 지속, 가격 수용 실패, 장후반 시장 흐름을 각각
검증한다. IR1과 IR3에는 시장 momentum/beta가 겹치므로 독립적인 alpha라고
부르지 않는다. 앞선 연구의 event 표현을 재사용하는 것은 구현상의 편의이며
성과의 근거가 아니다. 같은 이름의 기존 전략 결과를 새 가설의 증거로 쓰지 않는다.

## 5. universe와 후보 순위

Eligibility는 기술 score가 아닌 instrument/data/execution 품질 조건이다.

- 검토된 US common stock과 명시적 equity ETF만 허용한다.
- ETF allowlist는 SPY/QQQ/IWM/SOXX/SMH다. baseline 진입은 IR1/IR2 common
  stock, IR3 SPY/QQQ로 제한한다. 다른 ETF는 참조·진단용이다.
- bond/opaque/non-equity ETF, preferred, ETN, warrant, right, inactive, 비USD,
  leveraged/inverse를 제외한다. 기존 V3/Simple의 대상을 변경하는 것이 아니다.
- 전일 point-in-time 가격≥$5, ADV20=median(previous20 close×volume)≥$50M,
  선행 daily 데이터≥61세션. 가격 상한은 두지 않는다.
- 실제 bid/ask가 있으면 0<bid≤ask, spread/mid≤0.15%를 신규 진입에 적용한다.
  없으면 unknown을 기록하고 비용 proxy를 사용한다. OHLC를 호가라고 부르지 않는다.
- pilot seed는 universe_v3.json의 적격 종목이다. 성과에 따라 삭제하지 않는다.
  불명 ETF는 fail closed. 현재 seed로 과거 미국 전체의 survivorship-free
  cross-section을 검증했다고 주장하지 않는다.

처음에는 batch 현재 가격과 전일 cache로 15개 stock을 추린다. cheap priority는
`abs(log(current_price/prev_close))/(daily_ATR/prev_close)` 내림차순, ADV20
내림차순, symbol 순이다. volume ranking은 후보 발견의 보조 도구다. 고정 seed가
top100 ranking에서 사라졌다고 데이터 없이 삭제하지 않는다. 전체 미국 시장의
1분봉을 가져오지 않으며, open/pending 종목과 필요한 reference가 우선이다.

완료된 정규장 candle의 실제 prefix volume을 확보한 상세 후보 사이에서:

```text
attention_i=RVOLcum_i(t)
headroom_i=(daily_ATR_i/prev_close_i)/estimated_roundtrip_cost_fraction_i
CandidateScore_i=50*P_cross(attention_i)+50*P_cross(headroom_i)
```

같은 watermark의 percentile만 비교한다. attention은 참여의 증가, headroom은
friction 대비 이동 공간이라는 실행 가능성 proxy다. 두 순위의 같은 표는
학습된 이익 weight가 아닌 사전 Borda 순서다. score는 조회·capacity 순위이며
entry 확률·기대수익·risk 크기가 아니다. daily ATR은 20분 expected return도
아니다. RTH volume인지 불명인 ranking volume을 RVOL로 사용하지 않는다.
RVOL history 부족은 score=null/QUEUE_UNAVAILABLE, 0점으로 위장하지 않는다.
동점은 ADV20, symbol로 해결한다. IR3는 Z_open, spread, symbol 순을 사용한다.

정규화된 signed return/RS를 모든 전략의 공통 queue에 넣지 않는다. 반전 후보와
continuation 후보가 반대 방향을 필요로 하기 때문이다. RS는 IR1의 event에서만
직접 검증한다. stage별 누락·선택·취소·capacity 이유를 저장한다. score와 수익의
상관이 없다는 이유로 새 weight를 탐색하지 않는다.

## 6. 과거 정보만 사용하는 regime

현재 tech 중심 seed에서 QQQ를 primary, SPY를 방향 충돌 검사로 사용한다.
`UP_b = r20_b>0 AND C_b>VWAP_b AND ER20_b>=0.40`,
`DOWN_b = r20_b<0 AND C_b<VWAP_b AND ER20_b>=0.40`이다.

```text
TREND_UP:   UP_QQQ and not DOWN_SPY
TREND_DOWN: DOWN_QQQ and not UP_SPY
RANGE:      both ER20<=0.30 and both abs(normalized VWAP slope10)<=0.15
MIXED:      otherwise
```

위 순서대로 판정한다. volatility는 별도 축이다. 20분 realized volatility를
전일 이전 60세션의 같은 시간대 분포와 비교한다(min40). max(pQQQ,pSPY)≥0.90은
HIGH_VOL, 둘 모두≤0.20은 LOW_VOL, 나머지는 NORMAL_VOL. 참조/이력 부족은
UNKNOWN이며 신규 진입 금지다. sector는 SOXX의 return/VWAP, SMH는 선택적
진단만 사용한다. 모든 reference를 모든 event에 강제로 요구하지 않는다.

| 상태 | IR1 | IR2 | IR3 | 이유 |
|---|---|---|---|---|
| TREND_UP / NORMAL | 신규 가능 | 신규 setup 불가, 기존 recovery는 가능 | 해당 ETF UP이면 가능 | continuation과 회복 완료를 구분 |
| RANGE/MIXED / NORMAL | 불가 | setup 가능, RECOVERY에서 trigger | 해당 ETF UP·다른 ETF non-DOWN이면 가능 | strategy별 시장 사건의 차이 |
| TREND_DOWN | 불가 | 취소 | own UP/other non-DOWN 충족 못 하면 불가 | long-only baseline |
| HIGH_VOL | 신규/미체결 취소 | 신규/미체결 취소 | 조건부 가능 | 느린 stock fade의 실행 위험과 E1 유사 가설을 구분 |
| LOW_VOL | 불가 | 불가 | 불가 | 첫 baseline의 낮은 이동/비용 환경 제외 |
| UNKNOWN/data invalid | 신규 불가 | 신규 불가 | 신규 불가 | missing을 중립으로 취급하지 않음 |

기존 position을 regime label 하나로 자동 청산하지 않는다. own quote가 유효하면
reference 결측 중에도 exit 관리를 계속한다. threshold는 25건에서 학습하지 않는다.

## 7. 진입과 별도로 비교할 청산

| 모델 | 논리와 위험 | baseline 채택 |
|---|---|---|
| A 고정 ATR stop+고정 R target | payoff 비교는 쉽지만 구조와 수익 분포를 무시할 수 있음 | 미래 공통 control 하나, R 최적화 없음 |
| B ATR stop+trail | trend의 오른쪽 tail 유지, 샘플링으로 peak/stop 누락 가능 | IR3의 최초 baseline에는 제외, 고정 horizon부터 검증 |
| C structure stop+trail | setup 무효화 지점과 지속 가설의 결합 | IR1 PB low stop, +1R 이후 1A trail |
| D time stop | 기대한 가격 발견이 없을 때 종료, 늦은 trend를 자를 위험 | IR1 max45/stall20, IR2 max30, IR3 cutoff |
| E momentum decay | 두 연속 5분 close≤VWAP 및 5분 return≤0 같은 사전 정의 | 미래 단일 비교, baseline에 추가하지 않음 |

IR2는 structure stop+OR midpoint target이다. IR1은 trail, IR3는 시간 중심이다.
부분 익절은 제외한다. 이후 exit 비교는 같은 entry를 대상으로 하고 큰 grid를
만들지 않는다. MFE/MAE, 극값 최초 도달 시각, entry-to-MFE/MAE, holding time,
exit reason을 저장한다. 실제 관측 excursion과 역사 OHLC 범위 진단을 분리한다.
관측되지 않은 peak/stop을 실행 가능한 가격으로 복원하지 않는다.

## 8. 인과적 체결과 OHLCV의 한계

Signal에는 완료봉, 전일 값, 과거 rolling만 사용한다. 5분봉은 연속 5개 1분봉이
모두 끝나야 보인다. reference는 같은 watermark 이하의 완료 snapshot만 사용한다.
미래 nearest/bfill, 현재 session의 최종 volume/high/low를 사용하지 않는다.
실제 source timezone과 START/exclusive END 의미는 증거 없이 정하지 않는다.

Forward는 trigger→ENTRY_PENDING→후속 scan의 첫 fresh 현재 가격→비용 적용이다.
source/receive/decision을 구분한다. 이미 완료된 다음 분의 open으로 소급 체결하지
않는다. 최대 지연 90초, buy ask/sell bid가 실제로 있으면 사용하고 그렇지 않으면
current last와 비용 proxy임을 표시한다. 새 연구는 forming candle 체결 fallback도
쓰지 않는다. gap stop은 현재의 더 나쁜 관측 가격이며 theoretical stop이 아니다.

OHLCV만으로 당시 REST scan의 호가나 executable price를 복원할 수 없다.
역사 baseline은 trigger의 **엄격히 다음** minute close를 관측 proxy로 사용한다
(보통 T+60초). 이것은 실제 quote-fill 실증이 아니다. signal의 close에서 fill하거나
과거 next-open을 가져오는 것도 금지다. stop/target은 후속 관측 가격으로 판단하며
high/low로 이상적인 fill을 만들지 않는다. 1분 미만 latency 실험을 1분 자료로
만들지 않는다. close는 last-trade age가 불명이라는 한계를 결과에 함께 기록한다.

Slippage는 2bps/side, bid/ask 없는 연구의 half-spread proxy는 별도로 1bp/side다.
실호가를 사용하면서 spread를 다시 더하지 않는다. commission/regulatory fee의
0 baseline은 확인된 fee schedule이 아니다. fee 확인 또는 합리적인 보수적 bound
검증 전 경제적 PASS를 내지 않는다. 기존 엔진 비용은 변경하지 않고 별도
execution/cost model ID로 비교한다.

open position의 outage는 DATA_STALE, price 없음, cash 불변이다. 복구된 첫 관측
가격으로 현재 risk를 평가하고 missed historical stop에 돌아가지 않는다.
cutoff를 지났으면 현재 가격으로 늦은 청산을 기록한다. 미복구 position은
UNRESOLVED, realized PnL=null이며 손익 표본에서 조용히 삭제하지 않는다.

## 9. 한 baseline과 chronological 검증

각 전략은 처음에 사전 baseline 하나만 둔다. code/parameter/manifest/fee/
execution version을 고정한다. 불완전한 수집 데이터를 실행해 최종 성과를 주장하지 않는다.

- 12개월 train→3개월 validation→3개월 OOS, 3개월 step, 최소 3 OOS fold.
  train은 기술·data quality 확인이며 파라미터 학습이 아니다. validation에서
  구현/설계 오류를 고치면 별도 version과 이후 미관측 OOS가 필요하다.
- split은 session 단위, 모든 symbol에 같은 calendar. OOS 직전 1세션 embargo와
  position 미이월. warmup은 당시까지의 과거만 사용한다.
- 3 fold에는 대략 24개월과 warmup/embargo가 필요하다. 부족하면 NOT_EVALUABLE,
  작은 holdout으로 대체하지 않는다. 이후 rolling train에 앞선 OOS가 과거로
  들어갈 수 있지만, 그것을 보고 rule을 바꾸면 같은 OOS를 새 검증으로 주장하지 않는다.
- signal attribution과 capacity-limited paper books를 별도 집계한다. 동시 선택은
  고정 candidate 순위/trigger 시각/symbol로 결정하고 누락 이유를 보존한다.
- net expectancy R, PF, chronological drawdown이 primary다. 동시각 청산은
  batch로 합산해 drawdown이 동점 순서에 따라 바뀌지 않게 한다.

모든 strategy/symbol/regime/session/year/month/fold에서 trades, win rate,
mean/median return, avg win/loss, payoff, PF(R 및 USD), expectancy, drawdown, 일별
Sharpe-like consistency, duration, MFE/MAE/time-to-extreme를 보고한다.
pooled R 곡선은 실제 cash equity가 아니다. RTH 밖 baseline 진입은 0건이며
outage의 늦은 청산만 별도 affected 집계한다.

최소 비교를 사전 등록한다. IR1에서는 RS 없는 동일 event의 단일 ablation,
IR3에서는 동일 late-context지만 opening Z/RVOL을 쓰지 않는 단일 control,
IR2에서는 고정 10:15 진입/30분 보유의 적격 stock control을 진단으로 둔다.
control과의 우열로 이후 OOS에서 rule을 선택하지 않는다. exposure 차이는
benchmark 동일 보유 구간 수익과 함께 보고한다. 이런 비교도 인과 실험은 아니다.

연구상 INTERESTING(이익 보장 아님): 3개 이상 OOS fold 모두 positive net expectancy R와
positive net USD PnL, median PF_R≥1.15, 각 fold clean 완료≥30, pooled OOS≥120과 unresolved/affected 수
공개. 이는 최소 서술 표본이지 약한 edge를 검출할 충분한 statistical power의
보장이 아니다. primary 손익에는 affected도 포함한다. 미평가 unresolved가 남으면
그 손실을 보수적으로 한정하기 전 PASS를 내지 않는다. day-block bootstrap CI가 0을 포함하면 WEAK다. 표본 부족은
INCONCLUSIVE, negative/불안정이면 FAIL. 3전략을 함께 살펴본 선택 편향을 명시하고
가장 좋은 결과를 고른 뒤에는 추가 untouched forward 기간을 요구한다.
특히 IR3은 quarterly fold에 30건이 없을 가능성이 높다. 최소 시간 범위와 최소
표본은 다른 조건이다. trade 수 부족을 발견하면 시간을 추가 확보하거나 성과를
보지 않고 더 긴 OOS block을 사전 등록한다. 거래 수를 맞추려고 entry 조건을 풀지 않는다.

positive period USD PnL의 합 중 50% 초과가 한 fold/month에 집중되면 경고한다.
연도 기준은 최소 3년의 OOS가 있을 때 적용하며, 그 전은 annual robustness 미검증이다. 그 구간
제외 stress로 edge가 사라지면 WEAK/FAIL이다. 짧은 기간으로 year 분산을 검증했다고
하지 않는다. 어떤 판정도 현재 25건을 재학습하는 데 사용하지 않는다.

## 10. robustness 사전 계약

한 번에 하나의 요소만, 같은 dates/universe에서 baseline과 비교한다.

1. 각 primary의 이웃 coarse 값 두 개. Cartesian grid와 최적 한 점 선택 금지.
2. slippage 2→5→10bps/side, half-spread 1→3→5bps/side, 실제 fee 확인/stress.
3. 관측 지연 60→120→180초. 120/180초는 baseline 90초 제한을 벗어난
   stress-only model이다. 엄격한 제한을 유지한 경우의 cancellation rate도 별도 보고한다.
4. stop gap, 최악 관측 지연, quote outage. historical stop fill 금지.
5. deterministic half-universe, sector별/전체 seed 비교.
6. 서로 다른 year/vol regime, 최고 수익 symbol 및 best month 제외. 이는
   ex-post stress라는 표시를 하고 다음 universe를 유리하게 고르는 용도로 쓰지 않는다.
7. trading-day block bootstrap으로 시장·동시 거래 의존성을 보존한다.
   trade-order shuffle은 drawdown 경로 stress이며 alpha/독립성 증명이 아니다.
8. IR1 beta=1 RS를 과거 60 daily OLS beta 진단과 대조한다. 결과를 보고 최적
   beta를 고르지 않는다. 동시간 volume·고정 seed의 survivor bias도 공개한다.

한 특수 기간만 좋거나 작은 비용·지연 증가에 무너지면 중단한다.
수익성을 만들기 위해 PF가 조금 개선되는 조건을 추가하지 않는다.

## 11. 필요 데이터와 구현 순서

필요 자료는 finalized timestamp/OHLCV, symbol identity, session calendar,
point-in-time price/volume/metadata, consistent adjustment, 선행 daily 61세션,
동시간 volume 20세션(min10), volatility 60세션(min40)이다. IR1의 benchmark는
tech QQQ, semiconductor SOXX다. 시장은 QQQ/SPY, IR3은 두 ETF만 있으면 되므로
전체 30종목을 실행 전 필수로 요구하지 않는다. SMH는 optional 진단이다.
뉴스·premarket·호가가 없다는 사실을 표시하고 mock으로 대체하지 않는다.

기존 build_v4_data_manifest.py의 hash/timezone/duplicate/adjustment 검증을
재사용할 수 있다. 그 PASS는 quote 체결 유효성의 증명은 아니다.

1. 원문·서지 재확인과 실제 데이터 manifest/completeness 확인.
2. causal alignment/regime/universe/observable execution의 shared contract.
3. 문헌에 가장 가까우며 단순한 IR3, 사건 순서가 중요한 IR2, 상태가 많은 IR1 순.
4. tiny synthetic으로 aggregate availability, future reference 금지, 다음 관측
   체결, latency 취소, gap/stale, 일일 제한, cash isolation을 먼저 검증.
5. finalized data에서 각 baseline의 chronological 평가. 최소 3 fold와 충분한
   표본 없이 robustness 결론을 내지 않는다.
6. entry/exit attribution과 제한된 stress 후 추가 독립 forward paper 기간.
   기존 V3/Simple/logs/ARM/broker 실행은 이번 작업에서 변경하지 않는다.

**이번 작업은 연구 문서와 수식 명세만 작성하며 backtester는 보류한다.**
수집 완료, timestamp/adjustment/metadata/fee 증거, 충분한 시간 범위,
quote proxy와 실제 체결의 차이에 대한 검증이 남아 있다. 기존 frozen V4
코드를 덮어쓰지 않는다. 세 가설 모두 수익성이 검증되지 않았다.
