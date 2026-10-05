# 3분봉 단타 전략 V2 — 거래량/변동성 적응형

상태: RESEARCH ONLY  
실거래 봇에는 아직 적용하지 않음.

## 1. 연구 대상과 데이터 한계

대표 유동성 종목:
- NVDA
- AMD
- INTC
- AVGO
- MU
- TSM
- QCOM
- 시장 필터: NASDAQ Composite (^IXIC)

5년 평균 거래량은 NVDA 약 280.2M주, AMD 48.7M주, INTC 88.4M주, AVGO 27.4M주, MU 29.5M주, QCOM 11.7M주 수준으로 종목별 규모 차이가 크다. 따라서 '분당 거래량 N주' 같은 절대값보다 상대 거래량(RVOL)을 우선한다.

중요한 한계:
- 무료로 확인 가능한 공개 자료에서 대표 종목의 5년 전체 3분봉 OHLCV를 동일한 데이터 공급원으로 확보하지 못했다.
- 일부 공개 데이터셋은 2022년 이후 IEX 단일 거래소 기반이라 거래량이 전체 미국시장을 대표하지 않는다.
- 따라서 아래 수식은 '5년 전체 3분봉으로 통계적으로 확정된 최적값'이 아니라, 현재 확보된 3분봉 검증 결과와 장기 종목 특성에 맞춰 설계한 적응형 V2 후보식이다.
- 실제 적용 전 최소 6~12개월 이상의 동일 공급원 1분봉/3분봉 데이터로 워크포워드 검증해야 한다.

## 2. 공통 변수

3분봉 기준:

C, O, H, L = 현재 봉 종가/시가/고가/저가
C1, O1, H1, L1, V1 = 직전 봉
MA5, MA10, MA20 = 3분봉 5/10/20봉 단순이동평균
VMA5 = 최근 5봉 평균 거래량
MEDV20 = 최근 20봉 거래량 중앙값
ATR14 = 3분봉 ATR(14)
BODY = abs(C-O)
RANGE = H-L
BODY_RATIO = BODY / max(RANGE, epsilon)
RVOL = V / max(MEDV20, epsilon)

DayHighPct = (당일 최고가 / 전일 종가 - 1) * 100
DayLowRecoveryPct = (현재가 / 당일 저가 - 1) * 100
DollarValue3m = C * V

MA20Slope3 = MA20 / MA20_3 - 1
MA5GapATR = abs(C-MA5) / max(ATR14, epsilon)

시장 필터:
IXIC_MA20 = NASDAQ Composite 3분봉 MA20
IXIC_15m = 15분 전 대비 NASDAQ Composite 수익률

## 3. 공통 종목 발굴 조건

A. 거래량 상위 100위 이내

B. 가격:
5 <= C <= 300
단, 시간외 정수주문에서는 주문가능 금액으로 1주를 살 수 없는 종목은 자동매수 제외.

C. 거래량:
RVOL >= 1.20

D. 3분봉 거래대금:
DollarValue3m >= 1,500,000 USD

E. 당일 모멘텀:
DayHighPct >= 3.0
OR
DayLowRecoveryPct >= 4.0

F. 추격매수 방지:
MA5GapATR <= 1.20

G. 시장 급락 차단:
NOT(
  IXIC_C < IXIC_MA20
  AND
  IXIC_15m <= -1.0%
)

공통조건을 통과했더라도 패턴 점수가 부족하면 매수하지 않는다.

## 4. 패턴 1 — 상한선 V2

조정봉:
C1 < O1
AND C1 >= MA5_1 * 0.998
AND V1 <= VMA5_1 * 0.90

매수:
C > O
AND C > H1
AND RVOL >= 1.40
AND V >= VMA5 * 1.35
AND BODY_RATIO >= 0.45
AND MA5 > MA10
AND C <= MA5 + ATR14 * 1.0

추가:
최근 4봉 안에 종가 기준 국소 고점 대비 0.3% 이상 상승한 구간이 한 번 이상 존재해야 한다.

점수: +5

의도:
급등 → 짧은 저거래량 눌림 → MA5 지지 → 고점 재돌파 → 거래량 재유입.

## 5. 패턴 2 — 5MA 턴어라운드 V2

조정:
C1 < MA5_1
AND C1 > MA10_1
AND L1 >= MA10_1 * 0.997

매수:
C > O
AND C > MA5
AND C > H1 * 0.999
AND V >= max(V1 * 1.8, VMA5 * 1.20)
AND BODY_RATIO >= 0.45
AND (C-L) / max(H-L, epsilon) >= 0.55

점수: +4

보완:
V1 자체가 지나치게 낮은 봉에서 발생하는 허위 2배 신호를 막기 위해 V >= VMA5*1.20을 동시에 요구한다.

## 6. 패턴 3 — 20MA 눌림 V2

추세:
MA20 > MA20_3

눌림:
abs(L1 / MA20_1 - 1) <= 0.004
AND min(O1,C1) >= MA20_1 * 0.997

재확인:
(C1 / max(H[i-5:i-1]) - 1) <= -0.008
즉 직전 수개의 봉에서 실제 조정이 존재해야 한다.

매수:
C > O
AND C > MA5
AND C > H1
AND V >= VMA5 * 1.25
AND RVOL >= 1.20

점수: +3

핵심:
단순히 MA20에 닿았다는 이유로 매수하지 않는다.
반드시 '상승 추세 + 실제 눌림 + 20MA 지지 + 5MA/직전 고점 재돌파'가 모두 있어야 한다.

## 7. 패턴 4 — Opening/Pivot 지지 V2

TargetPrice:
우선 OpeningRangeHigh = 장 시작 후 첫 6개 3분봉의 최고가.
Pivot 데이터를 사용할 수 있는 경우 Pivot R2를 별도 후보로 계산하되, 기본값은 OpeningRangeHigh.

선행:
과거 어느 봉에서든 C > TargetPrice * 1.002 발생.

재시험:
TargetPrice * 0.997 <= min(L1,L2)
AND
min(C1,C2) >= TargetPrice * 0.999

매수:
C > O
AND C > H1
AND V >= V1 * 1.4
AND RVOL >= 1.20

점수: +3

목적:
단순히 첫 3분봉 고가 근처에서 횡보하는 종목이 아니라,
'실제 돌파 → 눌림 → 돌파 가격 지지 → 재상승'만 인정한다.

## 8. 패턴 5 — 질질이 V2

최근 이전 10봉에서:

MA5, MA10, MA20가 각각 존재할 것.

각 봉 k에 대해:
Spread_k =
(max(MA5_k,MA10_k,MA20_k)-min(MA5_k,MA10_k,MA20_k))
/ min(MA5_k,MA10_k,MA20_k)

max(Spread_k) <= 0.006

BoxHigh = max(H[-10:-1])
BoxRange = max(H[-10:-1]) - min(L[-10:-1])

수렴구간이 ATR 대비 지나치게 큰 경우 제외:
BoxRange <= ATR14 * 2.0

매수:
C > BoxHigh * 1.001
AND
V >= max(previous10Volume) * 1.60
AND
V >= VMA5 * 1.35
AND
BODY_RATIO >= 0.55

점수: +4

현재 봉은 BoxHigh 및 previous10Volume 계산에서 제외한다.

## 9. 거래량 재유입 보너스

VolumeReentry =
RVOL >= 1.50
AND V > V1
AND V > VMA5

만족 시 +2.

## 10. 추세 보너스

MA5 > MA10
AND MA10 > MA20
AND MA20Slope3 > 0

이면 +1.

## 11. 최종 진입 점수

PatternScore =
패턴 점수들의 합
+ VolumeReentry 보너스
+ Trend 보너스

매수 조건:
PatternScore >= 7

단일 약한 패턴 하나만 발생한 경우 매수하지 않는다.

최종 BUY:
CommonPrecondition == True
AND MarketRiskOff == False
AND PatternScore >= 7
AND PendingOrder == False
AND ManagedPosition == False

## 12. 기준봉/손절

기준봉은 '진입 원인을 만든 대량 거래량 장대양봉'으로 정의한다.

ReferenceMid =
(O_reference + C_reference) / 2

RiskR =
EntryPrice - ReferenceMid

다음 경우에는 매수하지 않는다:
RiskR <= 0
OR
RiskR > ATR14 * 1.20

하드 손절:
C < ReferenceMid
→ 전량 시장가 매도

즉, 원본 영상의 기준봉 몸통 중앙 손절은 유지하되,
손절폭이 ATR 대비 지나치게 큰 종목을 애초에 매수하지 않는다.

## 13. 익절/보유시간 보완

초기 R = EntryPrice - ReferenceMid

TargetPct =
clamp(1.5 * R / EntryPrice * 100, 0.8, 2.5)

1차 익절:
C >= EntryPrice * (1 + TargetPct/100)

시간 손절:
진입 후 10개의 3분봉(30분) 동안
C < EntryPrice * 1.003
AND
C < MA5
이면 매도.

추세 이탈:
진입 후 +1R 이상 도달한 적이 있고,
이후 종가가 MA5 아래로 내려오면 잔량 매도.

## 14. 포지션 사이징

1회 주문금액은 고정 $100만 사용하는 대신 계좌 규모를 함께 반영한다.

OrderUSD =
min(
  100,
  USD_CashBuyingPower * 0.20
)

단, 최소 주문 가능 금액 조건을 만족하지 않으면 매수하지 않는다.

일일 손실한도:
min(
  50,
  EquityUSD * 0.02
)

즉, 계좌가 작을수록 고정 $50 손실한도가 전체 계좌의 지나치게 큰 비율이 되는 문제를 방지한다.

## 15. 실거래 적용 순서

1. RESEARCH
2. PAPER
3. 실제 계좌 조회만 수행
4. 실주문은 소액 테스트
5. 일정 기간 워크포워드 검증
6. 검증 결과가 기준을 충족할 때만 AUTO ON

실거래 전에 확인해야 할 핵심 지표:
- 거래당 기대수익
- 승률
- 평균 승/평균 패
- Profit Factor
- Max Drawdown
- 30분 시간손절 비율
- 패턴별 승률
- 시장 위험 구간 승률
- 정규장/프리/애프터 세션별 체결률

'최소한의 수익률'을 보장하는 수식은 존재하지 않는다. 이 전략의 목적은 수익을 보장하는 것이 아니라 오신호와 과도한 손실을 줄여 양의 기대값이 관찰되는 조건만 실제 주문으로 연결하는 것이다.
