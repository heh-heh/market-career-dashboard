# V0.9 Virtual Market Arena — 독립·교차 검토 보고서

> 기획·설계 문서 합본 · 2026-10-17 · 코드 조사 기준 `24313f4874445fc4749ed8d5fcb9ffd5e04f0ea6`
> **연구 전용 / 구현·학습 미실행 / 2026 Final OOS CLOSED / Notion 미게시**

이 파일은 기존 모듈형 문서를 3개 공개용 합본으로 묶은 자료입니다. 원문의 모듈 경로는 출처 표기이며, 문서 간 링크는 이 합본 3개 안에서 연결됩니다. 다른 에이전트는 사실·설계 제안·미검증 상태를 구분하고 운영 코드나 데이터를 변경하지 않아야 합니다.

## 함께 읽을 문서

- [V0.9 Virtual Market Arena — 종합 기획서](V09_PROJECT_PROPOSAL.md)
- [V0.9 Virtual Market Arena — 기술·검증 설계서](V09_TECHNICAL_DESIGN.md)
- [V0.9 Virtual Market Arena — 독립·교차 검토 보고서](V09_AGENT_REVIEWS.md)

## 이 파일의 목차

- [전문 에이전트 검토·교차 반박·수정 기록](#source-07-reviews-md)
- [System Architect — 독립 Round 1](#source-reviews-architect-round1-md)
- [System Architect — Round 2 상호 비판과 설계 정정](#source-reviews-architect-round2-md)
- [System Architect — Round 4 최종 문서 검토](#source-reviews-architect-round4-md)
- [V0.9 독립 검토 1 — Quant & Market Research](#source-reviews-quant-round1-md)
- [Quant & Market Research — Round 2 반박·합의](#source-reviews-quant-round2-md)
- [Quant 최종 재검토 — Round 4](#source-reviews-quant-round4-md)
- [Red Team Round 1 — V0.9 설계 차단 조건](#source-reviews-redteam-round1-md)
- [Red Team Round 2 — 교차 반증 및 해결안 평가](#source-reviews-redteam-round2-md)
- [Red Team Round 4 — 통합 문서 최종 조건 검수](#source-reviews-redteam-round4-md)
- [System Architect — 반복 검토 지침](#source-agents-system-architect-md)
- [Quant & Market Research 검토 지침](#source-agents-quant-market-md)
- [Red Team 역할 지침](#source-agents-red-team-md)


---


<a id="source-07-reviews-md"></a>

*원문 모듈: `07_reviews.md`*

## 전문 에이전트 검토·교차 반박·수정 기록

이번 세션에서는 실제 Codex sub-agent 세 개를 별도로 생성했다. 서로 다른 프로세스의
독립 실험이나 외부 전문가 인증을 의미하지는 않는다. 동일 계열 AI의 검토로 인한
공통 오류 가능성은 남으며, 인간 금융/시스템 검토와 구현 시험을 대체하지 않는다.

### 검토자와 원문

| 역할 | 실제 task | Round1 독립 초안 | Round2 교차 검토 | Round4 통합본 재검토 |
|---|---|---|---|---|
| System Architect | /root/v09_architect | [R1](V09_AGENT_REVIEWS.md#source-reviews-architect-round1-md) | [R2](V09_AGENT_REVIEWS.md#source-reviews-architect-round2-md) | [R4](V09_AGENT_REVIEWS.md#source-reviews-architect-round4-md) |
| Quant & Market Research | /root/v09_quant | [R1](V09_AGENT_REVIEWS.md#source-reviews-quant-round1-md) | [R2](V09_AGENT_REVIEWS.md#source-reviews-quant-round2-md) | [R4](V09_AGENT_REVIEWS.md#source-reviews-quant-round4-md) |
| Red Team & Quality | /root/v09_redteam | [R1](V09_AGENT_REVIEWS.md#source-reviews-redteam-round1-md) | [R2](V09_AGENT_REVIEWS.md#source-reviews-redteam-round2-md) | [R4](V09_AGENT_REVIEWS.md#source-reviews-redteam-round4-md) |

R1은 같은 사용자 목표와 코드 기준만 받아 다른 역할 결론을 읽지 않고 작성했다.
R2는 세 R1 파일을 모두 받은 뒤 상호 반박했다. R3는 총괄이 아래 evidence ledger와
통합 문서로 반영했다. R4는 통합 주요 문서를 다시 읽고 모순·미확인 게이트를 지적했다.
검토 중 발견한 모순을 숨기기 위해 R1을 사후 덮어쓰지 않았다.

### Round3 수정·처리 ledger

| ID | 제기자·주장 | 근거·심각도 | 수정·예상 영향 | 검증·최종 상태 |
|---|---|---|---|---|
| X01 | Quant/RedTeam:16h wall·synthetic 혼용 | R1간 시각 의미 충돌, High | 가상16h+분석8h budget, wall time별도; 실제R는캘린더 | 03/05; 경계fixture미실행 |
| X02 | Architect/RedTeam: hybrid최종목표단정 | 과거anchor와내생fill정합성미증명, High | A/R우선, H는별도ADR보류 | 03/05/ADR01; 미검증 옵션 유지 |
| X03 | 모두:독립Account를sharedledger로복사불가 | 원장상대계정없음/중복체결, Critical | 새정수청산원장·유한MM·Issuer/Treasury | 05, G0; 구현차단 |
| X04 | RedTeam:종료lastprice pump·가짜청산 | 유동성없는평가수익, Critical | 참가봇실청산·wholeworldinvalid·실패보존 | 04/05; G2/G4필수 |
| X05 | Architect:allparticipantsflat은재고보존충돌 | MM초기주식에최종보유자필요, Critical | 진화봇flat,MM/backgroundinventorycarry·별도보고 | 05; 원장검증미실행 |
| X06 | Quant:daily100 score≠5일복리 | reset자본분모다름, High | ΣrealizedPnL/100을score로명명, Treasury이체분리 | 04; 실행시보고서검산 |
| X07 | RedTeam:integer여도fee dust 가능 | 부분체결rounding/예약초과, High | cashatom1e-8USD·order누적ceil fee delta | 05; G0 dust공격미실행 |
| X08 | Quant/RedTeam:ownerSTP가전원금지가능 | 동일인소유population과실법규혼동, Critical | 가상경제계좌STP·cross-bot진단, 규제준수주장금지 | 05/ADR08; ring공격필수 |
| X09 | Architect:무한seller 유동성 없으면거래불가 | 봇현금100/재고0, High | Issuer→finiteMM genesis재고·유한cash | 05; M0 숫자미정·차단 |
| X10 | Quant:2seed로통계증명불가 | 공동세계표본비독립, High | smoke2만,20세계는시작표본계획;CI/검정력별도 | 06; 효과실험미수행 |
| X11 | RedTeam:계보담합은현금보존으로미방지 | 손실을한봇에전가가능, Critical | 상대방분해·frozenopponents·독립세계·공격정책 | 06/G4; 미검증 |
| X12 | Architect:cancel우대phase불공정 | 늦게도착취소가앞선주문무효화, High | 같은arrivalqueue sequence대로처리 | 03/05; queuefixture미실행 |
| X13 | 모두:PC처리량미확인 | 사양미확정·현재엔진LOB아님, High | 규모별warmup/반복예산·UNKNOWN상태 | 03/08; PC확인필요 |
| X14 | RedTeam:invalid봇만제외하면선택편향 | 공동시장영향은남은봇에도존재, High | 전체세계무효·분모/원인보존·임의seed재시도금지 | 05/06; 데이터편향보고필수 |
| X15 | Architect/RedTeam R4:ACKdrain경계누락 | close후확정fill과report지연혼동, Critical | closedbook·사전확정report만유한상한drain·완료후validcommit | 03/05보완; 구현시험필수 |
| X16 | 총괄:NotionparentURL≠작성권한 | 도구·credential binding없음, Low | Markdown위키완성·미게시명시 | publication manifest; 게시차단 |

| X17 | Architect R4:유효1개fallback오해 | 공동세계99invalid제외선발위험, Critical | 정상세계전원valid먼저;단일사전등록세계/fixture만예외 | 04명시; 정책fixture필수 |
| X18 | Quant R4:원시CI·같은날seed반복 | 다중탐색·시장표본독립성오인, High | 보정검정/CI일치·R의timeblock·invalid분모·꼬리표본한계 | 06보완;실험미수행 |

### 합의와 남은 이견

A/R 분리, synthetic 시간, finite 경제원장, 인과적 정보, 실제 청산, 2026 차단에는
조건부 설계 합의가 있다. H의 장기 가치와 추가 현실성은 합의된 사실이 아니다.
이후 H를 구현하려면 별도 비용·반증 ADR이 필요하다.

시장조성자는 초기 stock inventory를 갖기에 모든 actor의 현금 손익만으로 전체
시장가치를 해석하지 않는다. 진화봇과 외부 actor의 평가·reset 계약은 다르다.
현재 규칙이 실제시장 모델로 충분한가, 5세션 적응이 유용한가, 이익이 외부 subsidy에
의존하지 않는가는 **열린 연구 질문**이다.

Round4가 설계 문구 수정을 수용해도 `IMPLEMENTATION_READY`로 승격하지 않는다.
Critical 불변식의 코드는 아직 없으며 PC·미세구조 데이터·통계적 효과도 미측정이다.


---


<a id="source-reviews-architect-round1-md"></a>

*원문 모듈: `reviews/architect_round1.md`*

## System Architect — 독립 Round 1

### 0. 검토 범위와 판단의 한계

V0.9는 **설계 단계**이며 본 문서는 실행 엔진 구현·성능 시험·배포를 하지 않는다. 개인 PC의 CPU·RAM·GPU·저장장치 사양은 알려지지 않았다. 16시간 가상 시장 / 8시간 분석은 연구 운영 창이며 실제 달력의 미국 거래시간이나 16시간 연속 정규장을 뜻하지 않는다. 5개의 유효 거래 세션이 완성될 때 세대교체한다. AWS에서는 기존 수집기와 API를 그대로 운영하며 새로운 시장 시뮬레이션 부하를 배치하지 않는다.

코드에서 관찰한 사실과 설계 제안을 구분한다. 현재 V0.8.2는 주문장 시장이 아니라 **외생적 1분봉 가격을 공유하는 독립 포트폴리오 replay**다. 이를 실제 상호작용 시장이나 검증된 투자 지능으로 부르는 것은 부정확하다.

검토 기준: 최신 원격을 반영한 `24313f4` 소스. 기존 역할별 결론은 읽지 않고 독립 분석했다.

### 1. 실제 파일 근거

| 파일 / 심볼 | 현재 기능 | V0.9 재사용 판단 |
|---|---|---|
| `research/evolution/features.py:MinuteFeatureProvider.prepare` | 세션의 completed END 시각 인덱스, 연속 prefix, 정렬된 종목, 공유 read-only feature matrix | 입력 관측/특성 계약 재사용. 대형 이벤트 저장·공유 메모리는 별도 adapter |
| `research/evolution/features.py:benchmark_for_symbol` | SOXX/QQQ 등 명시적 benchmark, 미등록 종목은 오류 | 매핑 버전 고정 재사용. synthetic 자산에서 실제 benchmark인 척하지 않을 것 |
| `research/evolution/indicators.py:compute_indicators` | END timestamp, 연속 세션, gap 이후 중단, SMA200 warmup, 미래 접근 차단 | replay feature baseline으로 그대로 유지. warmup과 session-reset 정책 명시 |
| `research/evolution/indicators.py:IndicatorCache` | 종목·세션별 1회 계산을 모든 genome이 공유 | 계산 공유 원칙 재사용. 새로운 replay event cache와 분리 |
| `research/evolution/indicator_engine.py:evaluate_day` | accounts별 독립 현금/lot, 다음 completed-minute close proxy, 기존 portfolio risk cap | 비교 baseline으로 보존. matching engine으로 직접 변환하지 않음 |
| `research/evolution/indicator_engine.py:Portfolio` | reserve와 Account lot, pending, trade, equity aggregation | 실험 상태/성과 참고. 시장 전체 금융 원장으로 재사용하지 않음 |
| `research/evolution/account.py:ExecutionCostModel` | BUY +3bp / SELL -3bp 기본 외생 비용 | OHLCV replay용. 실제 synthetic spread와 중복 비용 부과 금지 |
| `research/evolution/account.py:Account` | fractional long-only, 한 position, 비용/MFE/MAE/entry latency | 독립 정책 평가 baseline. 주문 예약·체결 분개를 지원하는 새 원장 필요 |
| `research/evolution/genome.py:Genome` | immutable dataclass, strict schema/hash, uniform crossover, bounded mutation | 명시적 V09 envelope/adapter를 통해 seed 가져오기. 기존 JSON schema 변경 금지 |
| `research/evolution/run_v07_available.py:run` | 유효 5일 완료 후 lifetime/선발 commit, 결측 시 window 무효, checkpoint | 실험 기록/완료 원자성 원칙 재사용. 공유시장 세션의 동일성은 새 계약 필요 |
| `research/evolution/adaptive_capacity.py:ResourceMonitor` | host CPU/RAM/swap·RSS 측정, 제한 초과 fail, 선택적 proc inspection bounded | PC 전용 resource profile로 재사용 검토. EC2 certificate를 PC로 복사해 신뢰하지 않음 |
| `v082_web_jobs.py` | pinned source, lock, stage order, protocol check, 인증 하의 연구 실행 | 기존 endpoint 불변. V09는 독립 결과 schema/선택적 read-only 요약만 후속 검토 |
| `v082-web.js` | 서버 protocol 일치 확인, 15초 갱신, 단계별 상태 | UI 관례만 재사용. AWS에서 PC 작업을 자동 실행하는 기능으로 확장하지 않음 |

#### 최신 bot roster 재사용

`v082_bot_roster.py`는 `V082_BOTS_1`, 파일 4MiB/lineage tail 2MiB 상한, symlink 거부, PID와 run path를 통한 제한된 실행 연결을 제공한다. `bot-roster.js`는 **현재 미평가 자식**과 **직전 평가 개체**를 별도로 보여주며 상속 lifetime을 자식 자신의 검증 수익으로 표시하지 않는다. 이 의미적 구분과 read-only bounded reader는 V09에도 재사용할 가치가 크다. 다만 현재 `MAX_BOTS=50`/V082 protocol/ID 형식을 V09용으로 조용히 변경하지 말고 별도 schema와 adapter를 만든다. V09 신규 UI도 가상 시장 mark equity, 실현 손익, 외부 자본 주입, 평가 완료 여부를 별도 열로 표시해야 한다.

#### 명확한 아키텍처 차이

현재 엔진에서 봇 A가 매수해도 봇 B의 관측 가격이나 체결 가능 수량은 변하지 않는다. 동일 가격 경로를 여러 봇이 보더라도 서로 거래하지 않으므로 shared **data**이지 shared **endogenous market**은 아니다. 미래의 V09 결과에는 시장 모드를 반드시 표시해야 한다.

### 2. 세 가지 시장 모드 결정안

| 모드 | 가격·유동성 원천 | 장점 | 위험/금지할 주장 | 권고 |
|---|---|---|---|---|
| `HISTORICAL_REPLAY` | 읽기 전용 Toss 1m, 향후 received ticks | 기존 V082와 비교, causal 회귀 검증, 최소 구현 | OHLCV만으로 queue position·실제 bid/ask·체결량 식별 불가. agent impact 학습 주장 불가 | **첫 단계** |
| `ENDOGENOUS_SANDBOX` | 주문장 + 인공 market maker/유동성 공급자 + 외생 fundamental shock | 주문·재고·충격·상호작용 연구 가능 | 봇끼리의 부가 실제 초과수익처럼 보임, 인공시장 exploit, 공급자 subsidy | 독립 두 번째 단계, 투자 성과와 분리 |
| `ANCHORED_HYBRID` | 과거 기준 경로 + 명시적 background liquidity + 봇 주문 영향 | 현실 reference와 상호작용을 절충 | anchor 재고/현금 무한 보조, 충격 리셋·장부손익 불일치 | 독립 검증 후 연구 옵션 |

**결정 제안:** V09 초기 milestone을 replay mode의 결정론적 scheduler/ledger/checkpoint로 제한하고, 같은 event/observation interface를 사용하는 endogenous sandbox를 두 번째 milestone으로 만든다. Hybrid는 가장 어렵고 검증 부담이 크므로 default로 삼지 않는다. 다만 사용자가 처음부터 endogenous 시장을 필수로 요구하면 replay를 V09 완성품으로 이름 붙이지 않고 준비 단계로 표시한다.

세 모드는 동일 성과 리더보드에 섞지 않는다. `marketMode`, `priceFormationVersion`, `liquidityModelVersion`, `executionModel`, `dataSnapshotHash`가 없으면 결과를 비교 불가능으로 표시한다.

### 3. 프로세스와 경계

개인 PC의 단일 `arena supervisor`가 아래 모듈을 조정한다.

1. `DatasetSnapshotReader`: 허용 원본을 read-only 열고 파일 hash/범위/END timestamp/타임존/가격 조정 선언 확인. 2026 원본 row 접근 금지.
2. `SessionPlanner`: exchange calendar에서 valid 5개 연속 세션 window를 사전 감사. 불완전 관측을 보간하지 않는다.
3. `EventClock`: simulation clock와 wall clock 분리. 멈춤/재시작해도 simulation 시각 불변.
4. `ObservationBus`: 현재까지 공개된 정보만 immutable snapshot으로 전달.
5. `PolicyBatch`: 동일 snapshot에서 모든 봇의 의도를 batch 생성. 순회 중 체결을 끼워넣지 않는다.
6. `RiskAndReservation`: cash/inventory/주문수/size 검증 및 예약.
7. `ExecutionVenue`: replay fill adapter 또는 별도 matching engine 중 정확히 하나.
8. `Ledger`: double-entry 분개·예약·체결·수수료·인벤토리·마크 기록.
9. `GenerationController`: 5 valid sessions 완료 후에만 평가/선발/변이/체크포인트 commit.
10. `AnalysisWorker`: immutable 완료 artifact를 읽어 분석. 시장 이벤트 상태를 직접 수정하지 않는다.

하나의 bot마다 Python process 또는 전체 CSV 사본을 만들지 않는다. 우선 단일 프로세스 vectorized policy + 하나의 순차 venue/ledger를 권고한다. 병렬화는 순수 feature/policy 계산에만 적용하며 결과 commit 순서는 단일화한다. GPU는 필요 조건이 아니다.

### 4. 결정론적 이벤트 순서

정렬 키 제안: `(simulation_timestamp_ns, phase, event_sequence)`.

- phase 0: 현재 시각까지 실제 observable한 외생 market event 공개.
- phase 1: 이전 의도에서 발생한 latency 만료/cancel/체결 처리. 미래 row 접근 금지.
- phase 2: 포지션 mark 및 risk snapshot, completed bar event 확정.
- phase 3: 모든 봇이 동일 snapshot version에서 policy 평가.
- phase 4: 새 주문에 deterministic latency와 심사 결과를 부여하고 미래 queue에 넣음.
- phase 5: 공개 depth/mark 업데이트 및 audit/checkpoint.

기본 주문 latency는 양수여야 하며 동일 관측을 보고 그 관측 가격에 즉시 체결하지 않는다. 같은 시각의 cancellation과 execution 우선순위는 고정 계약으로 테스트한다. 물리 receive 순서를 갖는 tick replay에서는 기록된 local receive sequence를 보존하고 존재하지 않는 exchange sequence를 만들지 않는다.

동시 주문의 agent ID 사전순 우위는 편향이다. 동률 처리에 seed 기반 공정 순열을 사용하되 session+event별로 결정론적이어야 하고 seed 변경 민감도를 기록한다. 정책 난수·시장 난수·번식 난수는 독립 스트림이다. agent 수 변경이 시장 난수 전체를 교란시키지 않게 한다.

### 5. 주문장과 매칭 계약 (sandbox에만)

최초 venue는 long-only / spot / 지정가·시장가·취소로 제한한다. 공매도·마진·레버리지·복잡 주문을 추가하지 않는다. inverse ETF는 실제로 long 매수하는 별도 자산으로만 표현한다.

- 가격은 정수 tick, 수량은 정수 lot 단위. fractional share 지원 시 명시된 소수 lot scale을 사용한다.
- price-time priority, 부분 체결, 잔량/예약 현금 해제, 취소 ACK, self-trade prevention을 명세한다.
- 시장가는 존재하는 반대편 depth까지만 체결. 잔량 IOC 취소 또는 명시적 reject, 무한 유동성 금지.
- 주문 접수 시 매수 최대 notional+fee 예약, 매도 보유 수량 예약. 이중 spend 방지.
- limit BUY는 limit 초과 가격에 체결하지 않음. gap 시 worse observable price를 쓰되 limit 제한 유지.
- market maker/background 공급자는 독립 계정이다. 현금·재고 한도와 손익·외부 보조금을 기록한다.
- self-trade/wash trade는 fitness 이득을 만들지 않도록 미체결 또는 별도 규칙. 수수료 회수로 현금 생성 금지.

Replay에는 진짜 depth가 없으므로 위 matching 품질을 주장하지 않는다. replay 참여율 제한은 모델 가정이며 시장 전체 체결량을 봇들이 중복 무한 소비하지 않도록 공유 capacity가 필요한지 별도 명시한다. 이 가정이 기존 V082와 다르면 동일 baseline이라고 주장하지 않는다.

### 6. 원장과 불변식

실현 손익, cash balance, mark-to-market equity는 별도 값이다. 두 agent 간 체결은 cash·asset 이전이며 양쪽 모두 수수료를 낼 경우 venue fee account로 이동한다. 자산 가격 상승으로 mark equity 총합이 변하는 것과 현금 보존은 구분한다.

필수 assertion:

- cash/수량/예약 금액 유한·허용범위, 가용 cash = total cash - reserved cash.
- 모든 fill의 매수 수량 = 매도 수량, 동일 trade ID 양쪽 분개.
- closed system cash 총합은 외부 입출금 없이 불변(venue fee account 포함).
- 외부 공급자의 출입금은 event로 추적하여 순수 agent 수익과 분리.
- fill 합계는 order quantity 초과 금지, 취소/재시작 후 중복 fill 금지.
- generation 교체/일별 $100 reset은 실제 시장 현금 주입 event로 분리; 봇의 수익으로 산입 금지.

기존 `Account`가 외생 체결가의 buy/sell 비용을 계산하는 방식은 double-entry venue가 아니므로 `ArenaLedger` 신규 모듈이 필요하다. 기존 class를 확장하여 레거시 동작을 바꾸지 않는다.

### 7. 16시간/8시간 운영과 5세션 진화

운영 창은 PC의 wall-clock schedule이다. 16시간 내 여러 세션을 빠르게 replay할 수 있고, 느린 PC라면 한 세션도 끝나지 않을 수 있다. 시간이 다 됐다고 미완료 세션/평가 window를 완료 처리하지 않는다.

`RUN_MARKET → DRAIN_TO_CHECKPOINT → ANALYZE_COMPLETED → WAIT_NEXT_WINDOW` 상태를 권고한다. 안전 checkpoint 지점은 event boundary이고 세대 fitness commit 지점은 valid 5-session boundary다. 16시간 경계에서 전체 window가 미완료면 pending 그대로 보존한다. 분석은 직전 committed generation만 읽는다.

8시간 분석 창은 반드시 8시간 계산할 의무가 아니다. 분석이 끝나면 idle한다. 자동 분석은 단순한 성과·diversity·성별/국면별 샘플·부모 집중도·거래 비용·시장 exploit 감시다. 이 단계에서 2025 수치를 보고 임계값을 자동 재조정하지 않는다. arbitrary agent code self-modification이나 neural RL은 초기 범위 밖이다.

### 8. 저장 및 복구

신규 PC output root만 쓰고 원본/기존 artifact는 read-only다. 권고 산출물:

- `run.json`: source/code/config hash, mode, PC fingerprint, seed, data partition, OOS CLOSED.
- `events/<partition>.jsonl.zst` 또는 chunked SQLite WAL: append-only event/order/fill ledger.
- `checkpoints/<sequence>/`: market book, account/reservations, pending queue, RNG streams, source cursor, generation pending evaluation.
- `generations/<id>/`: population/evaluation/selection/lineage/metrics.
- `analysis/<commit-id>/`: committed input hashes와 report.
- `status.json`: atomic replace, 현재 wall/simulation clock, 상태, 최근 heartbeat, checkpoint seq.

정확한 포맷은 실제 처리량 측정 후 선택한다. SQLite WAL은 단일 writer, transaction boundary, periodic checkpoint가 명확하다는 장점이 있다. JSONL은 replay/debug 용이하지만 대형 raw event dict는 비용이 크다. full state를 매 이벤트마다 JSON 저장하지 않는다. checkpoint는 temp+fsync+atomic rename, 참조 event sequence commit과 일치한다. crash 후 tail partial record는 원본 삭제가 아니라 output recovery journal에 격리 기록한다.

resume는 code/config/data hashes가 동일할 때만 허용하며 바뀌면 새 run으로 분기한다. PID만 아닌 OS lock을 사용한다. 개인 PC sleep/hibernation/network loss와 source disk 분리도 테스트한다.

### 9. 개인 PC 자원 예산: 수치가 아니라 검증 가설

실제 PC 사양을 모르므로 적정 population·event/s·16시간 처리 거래일 수를 산정하지 않는다. 50개체라는 V082 설정이나 Codex synthetic runtime을 PC capacity로 가져오지 않는다.

먼저 사양 인벤토리(CPU logical/physical, usable RAM, swap, free disk, OS, Python/NumPy/BLAS, 전원/sleep)를 기록한다. 다음에 작은 fixed dataset, fixed seed로 warmup+반복 측정한다. 이 문서에서는 실행하지 않는다.

예산식:

- 특성 배열 RAM ≈ `bars_in_chunk × symbols × features × dtype_bytes` + validity masks.
- 정책 batch RAM ≈ `symbols × population × score_bytes` + genome matrix.
- event queue RAM ≈ `max_pending_orders × bounded_event_bytes`; 예약 queue 상한 필수.
- ledger 공간 ≈ `events × measured_serialized_bytes`; 실제 표본으로 측정 후 여유 10GB/10% 중 큰 값 등 보수 guard 확정.
- runtime은 feature 공유 비용 + policy `O(times × symbols × bots × feature_count)` + venue `O(events log price_levels)` + serialization.

endogenous 가격 때문에 agent별로 feature를 재계산하면 population배 비용이 발생한다. 공개 시장 feature는 한 번, bot 상태 feature만 개별 계산한다. CPU worker 수는 core 수 이하를 자동 가정하지 말고 BLAS oversubscription을 막아 작은 반복 측정으로 정한다.

### 10. 재사용 / 변경 / 신규 모듈

**재사용:** 원본 reader의 timestamp/calendar/manifest 개념, immutable genome serialization, causal indicator 공식, mutation/crossover 개념, resource telemetry, existing artifact schema와 비교 adapter.

**별도 adapter로 변경:** feature input을 chronological public-event interface로 감싸기, PC source snapshot과 2026 gate, replay execution provenance를 명확화, 기존 lineage를 V09 run lineage에 연결.

**신규:** event clock, bounded order/event queues, matching venue, double-entry ledger, scheduler, source adapters, price formation/background agent, checkpoint transaction, exploit diagnostics, mode-separated comparison.

V082 feature 공식과 genome 구조를 처음부터 재작성할 이유가 없다. 반면 현재 독립 `Account`를 공유 시장 금융 원장으로 가정하는 것은 위험하다.

### 11. 최소 구현 순서와 승인 경계

1. 문서에서 mode/자금 주입/latency/16h8h/invalid-session 규칙 freeze.
2. PC inventory와 isolated source snapshot 계약, final OOS gate test.
3. event clock + observation boundary + determinism tests.
4. replay adapter + V082 동일 입력/설정 회귀 비교; 동일함을 주장하려면 trade-level 확인.
5. ledger invariants + order reservation + crash/restart 테스트.
6. synthetic venue와 명시적 background agent를 별도 sandbox로 추가.
7. 5 valid session reproduction/checkpoint 및 fairness/exploit tests.
8. 짧은 PC smoke 후에만 16h schedule; AWS runtime 미변경.

### 12. Round 2에서 반박/확인이 필요한 질문

- 사용자 목표의 "가상 시장"은 endogenous 가격 형성이 필수인가, shared replay도 준비 단계로 허용되는가?
- 16h/8h는 실제 PC 운영 시간을 뜻하는가, simulation calendar 시간인가? 현재 권고는 wall-clock 운영 창이다.
- 학습용 배경 유동성의 자본 주입을 어떻게 계량할 것인가?
- daily $100 reset을 유지하면서 shared market 보존을 어떻게 보고할 것인가? 주입 장부 분리를 권고한다.
- V082 SMA200 세션 warmup을 그대로 유지할지, 다른 버전의 feature warmup을 만들지? 임의 완화는 하지 않는다.
- 경쟁 성과가 실제 외생 시장의 OOS edge로 오해되지 않게 mode별 별도 보고가 충분한가?

### Round 1 결론

핵심은 많은 봇을 실행하는 것이 아니라 **관측 시각·이벤트 순서·자금 보존·가격 형성·체크포인트**의 계약이다. 기존 V082는 좋은 독립 replay baseline이지만 실제 공유 주문장을 제공하지 않는다. V09를 기존 연구 코드에 덧붙이는 대신 독립 arena namespace에 만들고, 정책/지표만 adapter로 가져오며, PC 측정 전 capacity나 투자 성능을 약속하지 않는 설계를 권고한다.


---


<a id="source-reviews-architect-round2-md"></a>

*원문 모듈: `reviews/architect_round2.md`*

## System Architect — Round 2 상호 비판과 설계 정정

검토 기준: `24313f4`. 읽은 독립 의견: `quant_round1.md`, `redteam_round1.md`. 이 라운드도 계획만 작성하며 실행·학습·벤치마크·배포하지 않았다.

### 1. 내 Round 1에서 정정할 항목

#### 1.1 16시간/8시간 의미

내 Round 1의 "16h/8h는 wall-clock 운영 창" 권고는 확정 규칙으로 삼을 근거가 부족했다. Quant가 요구한 가상 세계의 16시간/8시간 구분이 더 일관적이다. 다음처럼 정정한다.

- **Arena A:** simulation clock에서 거래 16시간, 분석 8시간. 분석 동안 시장 world는 frozen, 신규 market/order/fill event가 발생하지 않는다. 이는 실제 미국 16시간 정규장이라는 뜻이 아니다.
- **Replay R:** 실제 미국 calendar의 REGULAR 6.5시간, 조기 종료는 해당 날짜 캘린더대로. 가상16시간으로 늘리거나 존재하지 않는 분봉을 생성하지 않는다.
- **PC 운영:** 별도 `wallclockRuntimeLimit`/pause schedule이다. simulation clock 비율과 별도 기록. PC가16시간 동안 몇세션 처리하는지는 측정 전 UNKNOWN이다.

8시간 freeze는 scheduler의 논리적 phase이며 실제 CPU를8시간 점유해야 한다는 요구가 아니다. 다음 phase를 즉시 진행할지 운영자가 wallclock대기를 지정할지는 별도 runtime setting이다. 분석은 frozen timestamp 이후 데이터를 읽지 않는다.

#### 1.2 replay 우선이라는 구현 순서의 한계

내 R1의 replay→sandbox 권고는 회귀 검증 비용을 낮추는 순서였지만, shared virtual market 자체가 목표라면 replay만으로 V09를 완성했다고 부르면 안 된다. 두 독립 track R/A를 문서상 처음부터 정의하고, R은 실제시장 전이 검증 및 baseline 보존, A는 shared market 연구 목적이라고 명시한다. replay milestone 완료가 A의 ledger/matching 승인을 뜻하지 않는다.

#### 1.3 취소와 fill 우선순위

R1 phase표에서 "latency 만료/cancel/체결"을 묶은 표현은 취소에 전역 우선권이 있는 것으로 구현될 위험이 있다. **금지:** 같은timestamp이면 모든cancel을 먼저 처리하는 blanket rule. 접수된 exchange arrival queue가 순서를 결정한다. cancel은 요청이며 도착·처리·ACK 전 원주문은 체결될 수 있다.

### 2. 두 isolated track 제안에 대한 결정

#### Track R — Actual 1m Replay

- 실제 END-labeled 완료1분봉, 실제calendar, readonlysource, 정확한benchmark시각, 기존 next completed close proxy를 명시한다.
- 이벤트 구조는 A와 adapter contract를 공유할 수 있으나 **LOB상호작용/실제호가/queueposition가 없다**.
- 기존 V082 결과와 parity를 주장하려면 설정/seed/원본snapshot동일 + trade-level diff가 필요하다. 포트폴리오/volume participation을 추가하면 새replayversion이다.
- 미래 tickreplay는 별도 ReceivedEventReplayadapter다. 1mreplay를 tickreplay라고 부르지 않는다.

#### Track A — Shared Synthetic LOB

- 가격은 주문장 체결로 형성한다. 실제 과거close에 덮어쓰거나 `fill_at_anchor`하지 않는다.
- 과거reference를 사용할 경우 공개된 **past-only reference**가 backgroundactor의 다음행동에만 영향을 줄 수 있다. 해당 actor가 미래reference를 알면 privileged futureactor가 되어 다른실험이므로 MVP에서 금지한다.
- 원칙상 synthetic generator latentfundamental은 agent에 비공개로 존재할 수 있으나 지금 권고된 과거reference version에서는 공개된과거만 입력한다. 서로다른모드를 silently혼합하지 않는다.
- mode label은 `SYNTHETIC_ARENA_ONLY`이며 historicalreference사용 시 하위manifest에 `referenceSource=PAST_COMPLETED_1M`와hash를 기록한다. 실제시장체결 또는미국marketreplica주장금지.

**찬성 조건:** 두 track의 실행결과/selection artifacts/runID namespace가 독립이고, 하나의rank/fitnessleaderboard에 합쳐지지 않아야 한다. 전이가능성은 frozenpolicy를 R에서 별도로 평가한 경우에만 논의한다.

### 3. Quant 제안의 구현 가능성과 반론

#### 3.1 Hybrid를 최종틀로 먼저 고정하는 것에는 반대

Quant는 hybrid를 최종틀로 권고하지만 현재1m데이터는 실제depth·orderflow를 식별하지 못한다. 따라서 hybrid는 정확도높은최종단계가 아니라 **검증되지않은model하나**다. synthetic scenario보다 더 현실적이라고 자동가정할수없다. A에서 pastreference를 쓰는옵션은가능하되 최종진화방향으로필수화하지않는다. 낮은복잡도의A가 보존식/공정성/실패복구를먼저통과해야한다.

차단테스트: reference경로를변경했을때ledger가새현금/재고를생성하거나tradeprice를직접overwrite하면실패. reference가제공되지않은상황도정의된scenario로실행가능해야하며무한fillfallback금지.

#### 3.2 관측/체결 보고 지연은 새상태모델 비용이 큼

Quant의 "fill report 수신후 agent state 갱신"은 필요하지만 금융원장까지 지연시키면이중spend된다. authoritativevenueledger는 fill시점즉시갱신, agent-visible portfolio는 report수신까지stale일수있다. risk/reservation검사는 authoritative잔고기준이다. 두상태를분리하지않으면 이미체결된매수금액을다시쓸수있다.

차단테스트: fillACK지연중두번째주문을제출해도예약된자금/수량을초과할수없어야한다. policy는미수신filldetails를미리볼수없어야한다. restart후inflightfillreport도중복반영금지.

#### 3.3 liquidation value를부모순위에쓰는대안은초기엔불허

Quant의 실행가능청산가치 mark는유용한진단이지만 **실제청산완료**와동등하지않다. 깊이를동시에다른계좌가소비하면각계좌의정적liquidationestimate가집합적으로실현불가능하다. 첫A는세션종료까지실제관측가능체결로flat이된경우만평가유효. 미청산은INVALID_SETTLEMENT이고0손익도손실임의확정도아니다. 전량평가의globalcondition을정한다.

차단테스트: 같은biddepth를모든계좌가청산가능하다고중복계산하여모두SURVIVE하는경우실패. partialclose/depthexhaustion후하나라도평가계좌미청산이면해당공유world의세션은invalid,5sessioncounter증가금지.

#### 3.4 상대집합확장은순차구현

Quant의다양한opponentfamily/crossplay는타당하지만MVP에서모두구현하면generator오류를추적하기어렵다. 초기finiteMM+boundedrandom+cash-only+공격fixture로시작,다음단계에서inventory-aware/noisyvalue/trendopponent추가를버전분리한다. 미래성을숨기는seed모델의세부parameter를policyfeature에넣지않는다.

### 4. Red Team 반론의 수용 및 보완

#### 4.1 "보수적청산평가"허용폭을좁힘

RedTeam은실제청산또는보수적평가를제시했다. 초기설계에서는보수적이라는단어가임의종가fill의허가가될수있으므로 **실제매칭청산만유효**로좁힌다. unpriceable을0으로세팅하지않는다. 유동성이없어서invalid가많은것은threshold완화사유가아니라scenario경제설계결함/가정민감도증거다.

#### 4.2 owner/lineage금지는동일하지않음

모든봇이한사용자의실험이라고모든match를막으면arena목적이사라진다. 반대로부모계보가다르다는이유로담합을무시할수없다. `economicOwnerId`는시나리오에서배정하는가상경제주체이고`botId/lineageId/experimentTeamId`와구분한다. 같은economicOwnerdirectmatch금지,다른owner사이계보집중/순환transfer진단필수. lineage 전체거래금지는MVP기본이아니다.

차단테스트: owner ID를policy가임의변경할수없음. 같은owner직접match거절,다른owner순환매매총현금은fee만큼감소,특정bot이기부받은PnL은opponentloss와함께노출.

#### 4.3 개체수증설은시장모형변경

Resourcecalibration은처리능력측정이지시장성과비교가아니다. A에서50→100개체는총자본·orderflow·경쟁이달라진다. 고정총자본을나누는실험과각bot100달러를유지하는실험은다르며configmode/version을명시해야한다. recommendation은"이PC에서안전한N"이지"더우수한N"이아니다.

### 5. 정수 tick/microlot의정확성계약

가격intticks·수량intmicrolots에찬성한다. 이는fractionalUSlitorder를재현하는주장이아닌합성venuequantum이다. 중요한추가요건: 가격×수량을다시floatUSD로변환해원장을업데이트하지않는다.

권고: `cash_atoms`의scale을`price_tick × quantity_quantum`과정확히호환되게정하고notional은정수곱으로계산한다. fee율rational계산과roundingrule(예:각fillfee올림)을고정한다. 거래별fee최소atom때문에smallorder가불리할수있음은기록한다. 사용자표시만decimal로변환한다.

차단테스트: 최소가격/최소수량·maxsize·partialfills누적·fee올림의잔여금이fee계정에정확히귀속. float tolerance로원장불일치를넘기지않는다. limitBUY예약금에는최악허용fee가포함되어야한다.

### 6. exchange arrival queue의정확한우선순위

1. 각agent는receive된snapshot만보고decisionAt을기록한다.
2. order/cancel에는`sendAt + outboundLatency`로arrivalAt을부여한다.
3. queue는`(arrivalAt, deterministicTieSequence)`로정렬한다. type별전역cancelpriority없음.
4. 기존restingorder는가격-시간우선. 신규arrival이cross하면그때book을소비한다.
5. 동일arrivaltie는seed/phase고정공정순서로정한다. policy결과를실행하는목록순서에의존하지않는다.
6. fill은atomicledgercommit후ackqueue에들어간다. observationpublication과neworderarrival경계도명시한다.

차단테스트: cancel보다상대orderarrival이빠르면체결,반대면cancel성공. exacttie는고정seed에서반복동일하고agentIDpermutation편향을보고. delay0이면동일snapshot즉시유리체결이가능해지므로MVP기본은양수latency와명시한최소timequantum을요구한다.

### 7. 종료와평가원자성: 반드시결정할계약

세션close직전에새BUY차단및청산요청시간을설정하되구체값은다음spec에서고정한다. 주문장에는최종종료event이있고close후매칭금지,analysisphase에는worldfreeze다. **청산 grace period를8시간분석으로몰래확장하지않는다.**

미청산/미보고/결측조건은구분한다:

- authoritativeposition존재: `INVALID_UNLIQUIDATED_POSITION`.
- flat하지만fillreport가아직agent에미도착: reportdrain계약내에서신규match없이ACK처리가능. authoritative평가와policy정보경계를유지.
- reference/requiredobservation누락: `INVALID_REQUIRED_OBSERVATION`.
- 원장불일치: `FATAL_LEDGER_INVARIANT` 즉시정지.

공유A는참가자가서로가격을바꾸므로한bot만invalid로빼고나머지를동일world정상평가라고부르는것은selectionbias다. MVP는하나라도evaluationparticipant가미청산이면wholeworldsessioninvalid. 전체세션의이벤트/원장/실패원인은보존하고,5연속유효sessionwindow를reset한다. 무효시점에봇자산을없애고새100을주입하지않는다. **다음에피소드개시조건**은현재world를실패로봉인하고분리된genesis로새world시작하는것이며지난세션을유효로소급하지않는다.

### 8. 구현 전차단테스트목록

| ID | 반드시실패/통과해야할시나리오 | 승인조건 |
|---|---|---|
| A-TIME-01 | 16hsynthetic / 6.5hactual / earlyclose | namespace및duration혼합없음 |
| A-CAUSAL-01 | END봉T를T이전policy호출에서조회 | 접근거절,미래benchmark도동일 |
| A-QUEUE-01 | buy/cancel동시및전후arrival | 도착순서계약대로,blanketcancel우선없음 |
| A-LEDGER-01 | partialfill+cancel+fee+restart | intcash/asset총합정확보존 |
| A-INFO-01 | fillACK지연중재주문 | authoritative예약상태안전,agent미래정보없음 |
| A-MM-01 | MMcash/inventory0및재충전시도 | 주문거절/명시적fundingevent,자동무한충전없음 |
| A-CLOSE-01 | 종료시depth부족/partialliquidation | wholeworldinvalid,reset/evolve안함 |
| A-ANCHOR-01 | reference업데이트 | 과거공개입력만,trade/markoverwrite금지 |
| A-EXPLOIT-01 | lastpricepump/wash | 조작mark로부모통과불가,fee+상대손실공개 |
| A-RESUME-01 | queue/ledger/RNG각경계crash | uninterruptedrun과statehash동일 |
| R-PARITY-01 | V082동일snapshot/seed/config | trade-level차이0또는새모델로명시 |
| GLOBAL-OOS | 2026숫자row/feature접근 | CLOSED예외,학습/분석둘다차단 |

### Round 2 결론

두독립track,정수원장,finiteMM,실제청산완료만유효,arrivalqueue우선순위는조건부동의한다. 다만16h/8h의wall/simulation혼동은내R1에서정정해야하고,hybrid를더현실적인최종목표로확정하는것과단순liquidationmark로생존평가하는것은반대한다. **현재판정은 READY_FOR_DETAILED_SPEC / IMPLEMENTATION_NOT_YET_APPROVED**다. 특히부모평가전globalflat·funding/reset·agent보고지연과authoritativeledger구분은문서고정과차단테스트가필요하다.


---


<a id="source-reviews-architect-round4-md"></a>

*원문 모듈: `reviews/architect_round4.md`*

## System Architect — Round 4 최종 문서 검토

기준: `24313f4` 및 현재 V09 계획 문서. `03_architecture`, `04_bots_evolution`, `05_market_simulation`, `06_validation`, `09_risks_adrs`를 대조했다. 본 검토는 실제 코드 테스트·시뮬레이션·자원 측정을 수행하지 않았다.

### 판정

**DESIGN CONDITIONAL / IMPLEMENTATION NOT READY.** 주요 경제·시간 계약은 Round2보다 명확해졌다. 그러나 아래 문서 충돌과 종료 보고 처리 경계를 수정해야 설계 동결이 가능하다. 수정 후에도 G0~G3의 구현·실측 증거가 없으므로 실행 안전성 또는 학습 준비 완료 판정으로 바뀌지 않는다.

### 1. 핵심 정정 수용

#### 진화 봇만 종료 flat

`05_market_simulation.md`의 종료4~5항을 수용한다. 내 Round2의 "whole participant flat" 표현은 MM/background까지 포함하는 것으로 읽히면 주식 보존과 충돌한다. genesis에서 발행된 주식은 누군가 보유해야 한다. 정확한 유효성 조건은 **EVOLVING actor 전원 flat**, MM/background는 finite inventory를 지속 보유하며 진화 부모 평가에서 제외한다. 전체 원장의 주식 총합은 여전히 보존한다.

이 수정은 무조건 유동성을 보장하지 않는다. MM가 보유상한/현금부족 때문에 진화 봇의 잔량을 못 받아주면 world invalid가 맞다. MM의 position을0으로강제하거나재고를issuer로몰래반환하여성공처리하면실패다.

#### 유한Treasury의일별100복원

종료정산이정상완료된진화봇의cash에대해profit sweep/topup을명시적transfer로처리하고PnL에서제외하는방안에동의한다. MM/background무료reset금지와Treasury고갈시새세션차단도맞다. 세대children100지급은퇴역봇잔고회수+새봇계좌funding을같은financialtransaction으로기록해야한다. **수익/적자보전transfer는기록되지만투자수익이아니다.**

#### cashatom과누적수수료

centtick×microshare=10^-8USDcashatom은일관된다. order누적notional의ceilfee차액을fill별청구하는방식은독립partialfillrounding보다예약상한을맞추기쉽다. 최소orderfee가이미청구된주문을cancel/new하면새order누적값이reset되어추가rounding손해가생길수있으며이를가짜alpha로보여서는안된다. 정수1bps는syntheticdesignvalue이고Toss실요율이아니다.

### 2. 정확한 수정 요청

#### D1 — H모드의지위충돌 (문서 HIGH)

`03_architecture.md` 첫절: "최종 선택은 … 과거 맥락 adapter를 추가하는 단계적 hybrid".

`05_market_simulation.md`: "H는 최종 목표로 확정하지 않으며 A/R의 연구 가치가 없으면 도입하지 않는다".

`09_risks_adrs.md` ADR01/공개이견2: A/R우선,H보류.

**수정:** 03을 "R과작은A독립우선,H는별도ADR에서효용/정보/모델위험검토후선택적도입"으로통일한다. hybrid가더현실적인필연적진화라는주장은하지않는다.

**승인 기준:** 모든요약/아키텍처/로드맵이H보류를같은의미로표현. R의실제가격과A의체결가격동기화/overwrite금지는유지.

#### D2 — 16h이후ACK/feeddrain계약누락 (구현 차단 HIGH)

현재05종료3항은마감이후신규주문/소급fill금지와잔량취소를명시하지만,마감직전확정fill의지연보고를8h분석phase에서어떻게다루는지없다. 03checkpoint에pendingack를포함하는것만으로는충분하지않다.

**수정:**

1. exchange는16h에서닫힌다. 이후매칭/신규주문수락/새원본futuredata공개는없다.
2. 마감시확정된잔량취소는원장예약해제와동일transaction으로처리한다. 이는agentcancel이우선권을얻은것이아닌venueclose행위다.
3. 마감전이미확정된fill/cancel/terminalreport만기존latency대로drain할수있다. 공개feeddrain도이미확정된past사건만허용한다.
4. authoritativeledger는fill시점에즉시갱신되고agent-visible계좌는ACK수신후갱신된다. 미수신fill을policy에미리보이지않는다.
5. reportdrain의pending건수/종료조건과허용latency상한을명세한다. 보고가분석budget을넘으면PAUSED_REPORT_DRAIN_OR_INCOMPLETE이고다음세션/번식차단;report를삭제하고성공처리하지않는다.

**차단 테스트:** 종료1tick전fill→ACK종료후도착,close와partialfill/cancel경쟁,ACK중복/restart,분석phase에새match요청,분석phasefuturebar공개. ACKdrain이새거래나숨은청산grace가되면실패.

#### D3 — 평가유효성범위 (문서 MEDIUM)

04의유효봇1개분기는legacyselection정책호환설명으로는맞지만A에서는하나의진화봇이라도미청산이면wholeworldinvalid다. 따라서정상A의100봇world에서"유효한봇1개만남겨선택"은허용되지않는다.

**수정 제안:** 단일유효후보는단일평가봇으로사전등록된world또는정책unitfixture에서만가능하고,100봇world의나머지99개invalid를제외한복구경로가아님을04에명시한다. 단일수익부모는100봇모두평가유효한가운데1개수익인경우이므로별개다.

#### D4 — 실패world와자금이력 (문서 MEDIUM)

05의실패world봉인+사전등록다음genesis는정확하다. 다만새genesis자금을직전lossrefund처럼합산하면선택편향이된다.

**추가 조건:** worldsealedAt,invalidReason,pendingpositions/reservations,최종ledgerhash를보존하고새world는newworldID/newgenesisfunding이다. 같은generation/fitnesswindow를이어붙이지않는다. 실패world손실을0으로치환하거나분모에서누락하지않는다. 시스템crash만동일hash체크포인트에서resume한다.

### 3. 최종 blocking test 계약

| 계약 | 최소반증fixture | 통과해야할결과 |
|---|---|---|
| 진화봇flat | 봇flat/MM보유,봇미청산/MM보유 | 전자는유효가능,후자는wholeworldinvalid |
| 예약/수수료 | 1order를Npartialfill,limit/collar경계,feeatoms | 총fee=ceil누적notionalfee,가용cash음수0 |
| 종료보고 | 마감전fill/마감후ACK | 신규match없이report만drain,양원장일관 |
| 마감경쟁 | close직전/동시/직후주문및cancel | 사전정의arrival/venueclose순서와동일 |
| Treasury | 이익sweep/손실topup/자금부족/세대교체 | 명시적transfer,성과제외,부족시차단 |
| 정보 | 미래bar/미수신fill/latent조회 | policy접근실패,과거결정hash불변 |
| 복구 | partialfill후crash/ACK전crash | event+ledger+pendingreporthash동일 |
| 실패정산 | depth부족,미청산종료 | 창무효,기존positions보존,진화/100reset금지 |
| PC자원 | 사양미확인 | UNKNOWN,1000개체가능주장없음 |

이표는**실행한테스트결과가아니다**. 향후구현승인을위한기준이다.

### 4. 현재상태와조건부승인범위

- **설계결정수용:** R/A분리,가상16h+8h,finiteMM/treasury,정수단위/누적fee,진화봇실제청산,무효world봉인,PC실측전capacityUNKNOWN.
- **설계동결전수정필수:** D1모드충돌,D2보고drain,D3개별유효성단일후보오해차단.
- **구현착수전열린사양:** 초기유한budget/자산수,queue/rate/depth상한,reportlatencybound,PCinventory,source/provenance접근계획. 미정값을실측값으로채우지않는다.
- **실험승인전차단:** G0~G2실제통과및G3실제PC측정. 문서완성/Notion게시/기존V082테스트통과로대체할수없다.

최종평가: **설계문서완성은조건부가능,구현·실행·학습은NOTREADY**. 기존AWS/수집기/실거래/2026격리는유지되며이번검토에서아무런운영변경도하지않았다.

### 최종 재검토 Addendum — 통합 문서 보완 후

수정된03/04/05/06을다시직접확인했다. D1은03의H별도ADR보류표현으로해소됐다. D2는03의「종료후보고drain의경계」와05종료3항에서해소됐다: 마감전에확정된보고만배달,추가매칭/주문/source관측/다음시나리오공개금지,유한`max_report_delivery_latency_ns`필수,최종보고불일치/상한초과는`INVALID_COMMUNICATION`이다. authoritative원장과agent보고도분리됐다.

D3은04의「단일유효후보분기의한계」가명시적으로99무효봇제외를금지하고사전등록단일봇world/정책fixture로한정하여해소됐다. D4도같은추가절의새worldID/독립funding/새window및05의실패세계봉인으로설계상해소됐다. MM/backgroundinventorycarry,진화봇만flat,유한Treasury,profittransfer성과제외규칙을재확인했다. 06은반복seed가동일역사기간의새시장표본이아님을명시하고다중검정에맞춘CI/검정해석을보완했다.

**최종상태: 해당문서모순D1~D4는SPEC_RESOLVED. 계획문서산출물로는수용한다. IMPLEMENTATION_NOT_READY/EXECUTION_BLOCKED는유지한다.** 남은것은사전등록finitebudget/rate/latency구체값,PCinventory,원장·인과성·복구G0~G2실제구현증거와G3실측이다. 문서의테스트목록을실제통과결과로취급하지않는다. 이번재검토도실행·벤치마크·학습·배포없이문서만확인했다.


---


<a id="source-reviews-quant-round1-md"></a>

*원문 모듈: `reviews/quant_round1.md`*

## V0.9 독립 검토 1 — Quant & Market Research

작성일: 2026-10-17 UTC. 감사 기준: `strategy-engine-v3` / `24313f4`.
범위: 설계 검토만. 전략 구현·학습·백테스트·운영 배포를 수행하지 않았다. 다른 검토자의 결론을 읽기 전에 작성한 독립 1차 의견이다.

### 1. 핵심 판단

**권고: 한 시장 안의 경쟁과 실제 미국 주식에서의 수익 가능성을 서로 다른 실험으로 분리한다.** V0.9의 16시간 거래+8시간 분석은 가상 세계의 시간 규칙이며 미국 정규장 규칙이 아니다. 5세션마다 번식하는 것은 실험 편성 규칙이지 통계적 우월성의 증거가 아니다. 모든 상대 봇이 같은 정책으로 바뀌면 환경도 바뀌므로 세대 간 단순 수익 비교는 통제실험이 아니다.

초기 가상시장은 명시적인 단일 거래소·가격시간우선·부분체결·예약 원장으로 제한한다. “미국 시장 재현”이라고 부르지 않는다. 실제 미국 시장은 여러 거래소·장외 유동성·라우팅·경매·특수 주문·보호호가·거래중단 등으로 구성되며 단일 LOB와 같지 않다. 1분봉으로 실제 큐 위치나 체결 가능한 호가 깊이를 복원할 수 없다. LOSSY Toss tick/orderbook도 완전한 거래소 테이프가 아니다.

**연구 상태 표시를 세 개로 구분한다.**

1. `SYNTHETIC_ARENA_ONLY`: 자체 생성 가격·내생 봇 경쟁. 실제 시장 수익 주장 금지.
2. `HISTORICAL_CONTEXT_HYBRID`: 과거 1분봉을 문맥·기초가치 경로로 사용하지만 체결은 별도 가상 LOB에서 발생. 역사적 체결 재현이라고 부르지 않음.
3. `OBSERVED_MARKET_REPLAY`: 수신된 quote/tick 및 관측 지연을 사용한 별도 재생 실험. 완전한 큐 복원 또는 실제 주문 체결 보장은 아님.

2026 Final OOS는 이 세 모드 모두에서 CLOSED다. 동결 이전 자동 연구에 2026 가격·호가·성과를 넣지 않는다. 캘린더의 날짜 존재 자체와 최종 OOS 가격 데이터 접근은 구분하되, 데이터 reader의 연도 가드는 유지한다.

### 2. 기존 코드에서 확인한 사실과 재사용 한계

직접 확인: `research/evolution/account.py`, `genome.py`, `features.py`, `engine.py`, `indicator_engine.py`, `regime_timing.py`, `survival_breeding.py`.

- `Genome`은 frozen dataclass다. 기존 선형 feature 가중치, BUY/SELL threshold, 손절·익절·보유시간·비중, 선택적 context/indicator 가중치를 JSON으로 보존한다. 기존 스키마를 덮어쓰지 말고 arena adapter와 새 버전 스키마를 둔다.
- 기존 `ExecutionCostModel`은 BUY에 `(slippage_bps+half_spread_bps)`를 더하고 SELL에서 뺀다. 기본은 편도 3bps다. 이는 LOB 체결 모형이 아니라 close proxy 비용 모형이다.
- 기존 `Account.buy()`의 `executionModel`은 `NEXT_COMPLETED_MINUTE_CLOSE_PROXY`다. `engine.py`는 pending 신호 이후 minute 관측의 close로 체결한다. 이것을 arena의 호가 매칭과 같은 것으로 취급하면 안 된다.
- 기존 계좌는 분수 수량·long-only이고 평균단가/현금/실현손익/비용을 분리한다. V0.8.2 `Portfolio`는 독립 lot 계좌와 reserve를 가진다. 공유 LOB에서는 계좌 하나의 fill이 다른 계좌의 상대 체결과 정확히 연결되어야 하므로 현재 독립 fill 모델을 그대로 복사하면 거래량과 현금이 이중 생성될 수 있다.
- `market_context()`는 exact-time SPY·QQQ가 모두 있어야 유효하다. 5/15/30분 수익·VWAP·slope 및 과거 변동성 문턱으로 BULL/BEAR/SIDEWAYS를 구분한다. arena의 시장 문맥과 참가자의 주문 의도는 별개 객체로 둔다.
- `window_objective()`는 일별 순수익, 이어 붙인 MDD, 과매매·일별 편차·진입 및 청산 품질·국면 집중 벌점을 사용한다. 비용은 이미 순손익에 있어 별도 costPenalty=0이다. arena에서 spread와 fee를 다시 중복 차감하면 안 된다.
- `valid_window()`는 유효한 완료 5세션/5일·finite windowPnl/fitness/MDD를 요구한다. Profit pool이 없을 때 유효 평가 상위 2개를 원금 보존 부모로 선택한다. **이 선택 정책은 회계·체결 검증을 대신하지 않는다.** 무체결·stale mark·조작 가격으로 평가된 수익은 부모 자격에서 먼저 제외해야 한다.
- 원래 5세션 objective의 고정 $100 리셋 의미와 지속형 arena 자본의 의미는 다르다. V0.9에서는 `evaluationCapital`, `liveArenaEquity`, `lineageLifetimeEquity`, `externalTransfer`를 구분해야 한다. 기존 산출물의 windowReturn 의미를 조용히 바꾸지 않는다.

### 3. 미국 시장 사실 검증과 가상 규칙의 경계

#### 검증 방식

아래 URL을 2026-10-17에 HTTP로 직접 조회했다. 페이지 title과 해당 문구/초록을 확인한 것만 `본문 확인`으로 표시했다. HTTP 200이어도 “Page Not Available”이면 출처로 인정하지 않았다. 조회 가능한 문서가 최신 법률 전체를 보장하지 않는다. 적용 전 venue·계좌·상품별 문서를 다시 고정해야 한다. 아래는 투자·법률 자문이 아닌 시뮬레이터 범위 결정이다.

| 출처/발행기관·연도 | 정확한 URL | 확인 범위 및 한계 |
|---|---|---|
| NYSE, Holidays & Trading Hours, 상시 갱신 | https://www.nyse.com/markets/hours-calendars | HTTP200 본문 확인. Core 09:30–16:00 ET, venue별 early/late session 및 경매가 다름. NYSE Arca는 early 04:00부터, late20:00까지라는 표도 존재. 모든 종목/브로커가 같은 시간에 거래 가능하다는 뜻 아님. 휴장·조기종료 연도별 일정 별도 적용. |
| SEC/OIEA Investor.gov, Types of Orders, 연도 미확정 | https://www.investor.gov/introduction-investing/investing-basics/how-stock-markets-work/types-orders | HTTP200 제목/본문 확인. Market/limit/stop 기본 구분. 실제 execution price 보장·venue 구현을 추정하지 않음. |
| SEC/OIEA, New T+1 Settlement Cycle, 2024 | https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin | HTTP200 본문 확인. 2024-05-28 compliance date, 대부분 broker-dealer 증권거래의 표준결제 T+1, 예외 존재. 모든 과거 2022~2023 거래를 T+1로 소급 재현하면 안 됨. |
| FINRA, Understanding Settlement Cycles, 갱신 연도 미확정 | https://www.finra.org/investors/insights/understanding-settlement-cycles | HTTP200 본문 확인. 2024년5월 이후 대부분 증권거래 T+1 설명. 현금계좌의 즉시 무제한 자금 재사용 허용 근거로 사용 금지. |
| FINRA, Frequent Intraday Trading: Understanding the Basics, 상시 문서 | https://www.finra.org/investors/investing/investment-products/stocks/day-trading | HTTP200 제목/본문 조회. 계좌별 일중거래·증거금 규칙의 현재 적용 세부는 추가 법규 확인 필요. 특정 PDT 금액/횟수 규칙을 2026 현재 유효하다고 여기서 확정하지 않음. |
| FINRA Regulatory Notice14-28, 2014-06-30 | https://www.finra.org/rules-guidance/notices/14-28 | HTTP200 본문 확인. self-trades 관련 Rule5210 공지, effective2014-08-25. 모든 우연한 self-match가 동일한 불법행위라는 주장 아님. Arena는 더 보수적으로 같은 beneficial-owner 매칭 방지. |
| Nasdaq Trader Price List–Trading, 상시 갱신 | https://www.nasdaqtrader.com/Trader.aspx?id=PriceListTrading2 | HTTP200 제목/표 조회. 거래·liquidity removal/provision 등 복잡한 요율. 수치별 적용 tier/qualification 미검증이므로 실요율 하드코딩 근거로 쓰지 않음. |
| Nasdaq FIX specification, 버전/발행일 별도 확인 필요 | https://www.nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/inet_fix_sb.pdf | HTTP200 PDF668147bytes 존재 확인만. 이 검토에서 PDF의 priority·수수료·모든 order flag 본문을 검증하지 않았음. 가격시간우선 규칙을 “Nasdaq 전체 규칙과 동일”이라고 주장하지 않음. |
| Byrd, Hybinette, Balch, ABIDES, 2019 | https://arxiv.org/abs/1904.12066 | HTTP200 제목/초록 확인. Agent-based discrete-event, exchange와 개별 agent 간 configurable pairwise latency, 다수 agent 지원을 설명. 우리 구현의 고충실도 인증이나 실제 수익성 근거 아님. |
| Dave Cliff, BSE, 2018 | https://arxiv.org/abs/1809.06027 | HTTP200 제목/초록 확인. 최소 LOB 시장 시뮬레이터의 목적·실험 가능성. 현실 시장 복제 증명 아님. |

실패·제외한 경로: `https://www.nasdaqtrader.com/Trader.aspx?id=TradingHours`는 HTTP200이지만 제목 `Page Not Available`; `https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/investor-bulletin-new-t2-settlement-cycle`는503; 추측한 cash-account 설명 URL은404였다. 이들을 검증 근거로 사용하지 않았다.

#### 설계에 반영할 사실

- 미국 **정규장**은 NYSE core 표의 6.5시간이다. 16시간 거래는 별도 가상 시간축이다. 8시간 분석 동안 매칭·추가정보 접근·배당 등 세계 진행 여부를 명시해야 한다. 권고: world frozen, 기존 시점 이후 실제 가격 접근 금지, 재현 가능한 평가·번식만 실행.
- 실제 extended session은 venue·브로커·상품별 가능시간·유동성이 다르다. Toss DAY/PRE/REGULAR/AFTER label과 가상16시간을 혼합하지 않는다.
- Market order는 체결 가격 보장이 아니다. Limit order는 지정가보다 불리한 가격에 체결시키지 않지만 미체결/부분체결될 수 있다. Stop은 시장 주문으로 전환되는 종류와 stop-limit을 구분한다. V0.9 MVP는 LIMIT, MARKET, CANCEL만 권고하며 stop은 agent가 관측 이후 발행하는 위험 관리 동작으로 모델링한다.
- Fractional share 지원은 기존 모의계좌 편의이고 실제 거래소의 모든 주문 단위와 같지 않다. Arena는 고정 소수점 수량 quantum을 명시한 합성 규칙으로 운영한다.
- 가격시간우선은 V0.9의 명시적 단일 venue 가정이다. 수정 주문이 priority를 보존하는지·잔량 감소 정책·same-time tie-break를 스펙으로 고정한다. venue 전체를 대표하지 않는다.
- 결제는 실행과 다르다. cash account를 현실적으로 주장하려면 settled/unsettled cash·거래일 캘린더·과거 결제 규칙을 따로 모델링해야 한다. V0.9 MVP는 즉시결제 가상현금 모드로 명시하고 실제 현금계좌 호환이라고 주장하지 않는다. margin/short는 별도 승인 전 제외한다.
- 비용은 fee sink 또는 exchange treasury에 이체되는 원장 항목이다. Maker rebate를 지원한다면 treasury에서 지급하며 잔고 제한·외부 보조금 출처를 기록한다. Spread는 LOB 매수·매도 체결에서 이미 발생하므로 legacy half_spread를 다시 붙이지 않는다.

### 4. 시장 모형 세 가지 비교

| 모형 | 식별할 수 있는 것 | 식별할 수 없는 것 | 주요 위험 |
|---|---|---|---|
| 고정 역사 경로 replay | 동일 외생 정보에서 정책의 결정 차이, 비용/지연 민감도 | 참가자가 원래 시장에 준 impact, 실제 queue fill | 1분봉 내 경로를 만들면서 역사라고 착각, counterfactual 체결 과신 |
| 순수 endogenous LOB | 상호작용, 재고관리, 경쟁, 혼잡, 체결·취소 행동 | 실제 미국시장 alpha | 봇이 만든 가격에 봇을 채점, exploit·담합·무유동성 |
| hybrid 외부 anchor+endogenous LOB | 고정 외생 환경+시장 충격의 분리, 외부 유동성과의 경쟁 | 완전한 실제 시장 체결 | anchor를 무한체결 가능한 가격으로 오해, 공짜 liquidity와 보조금 |

권고는 **hybrid를 최종 실험 틀로 하되 첫 구현은 독립 synthetic scenario**다. 외부 liquidity provider는 finite cash/inventory, seed고정, 주문·취소·latency를 가진 실제 actor로 동작해야 한다. `fill_at_anchor()` 같은 우회 체결 금지. 역사 1분봉은 완료 후 공개되는 context로만 사용하며, 그 high/low에 맞춘 가상 intra-minute 경로를 사실로 취급하지 않는다. 미래 anchor는 시나리오 엔진 내부에 있어도 agent observation API에 절대 노출하지 않는다.

같은 historical 경로를 두 모형에서 비교할 때도 거래량·spread·depth·execution opportunity가 다르면 동일 실험이 아니다. 시나리오 manifest에 정보 경로와 체결 세계를 별도 hash로 저장한다.

### 5. 공유 LOB의 회계와 조작 방지

#### 원장 불변식

봇 i의 현금 `C_i`, 잔고 `Q_i`, 예약 현금 `RC_i`, 예약 수량 `RQ_i`를 둔다. Long-only에서 `C_i>=0`, `Q_i>=0`, `0<=RC_i<=C_i`, `0<=RQ_i<=Q_i`여야 한다. 예약은 소유권 변경이 아니라 사용가능 잔고의 제약이다.

수량 q, 가격 p의 매수/매도 매칭이면:

- buyer cash `-=p*q+fee_buy`; buyer inventory `+=q`.
- seller cash `+=p*q-fee_sell`; seller inventory `-=q`.
- fee ledger `+=fee_buy+fee_sell`.
- 두 order의 remaining quantity 및 reservation을 **동일 transaction**에서 갱신한다.
- 내부 현금 총합+fee treasury는 외부 입출금 외에는 보존한다. 총 수량은 발행/소각 외에는 보존한다.
- 외부 market maker도 동일 계좌 원장을 사용한다. 초기 재고·현금의 출처는 genesis allocation으로 기록한다.
- Corporate action은 발행사 ledger의 명시적 이벤트다. 배당 현금의 상대계정, split전후수량/가격 quantum조정, 소각/발행 출처가 없으면 지원하지 않는다.

`mark_price` 변경으로 모든 봇 평가자산이 동시에 늘어나는 것은 현금 창조가 아니지만, 그 자산을 전부 현금화할 수 있다는 뜻도 아니다. Last trade가 아니라 정책과 독립적인 외부 reference와 bid-side liquidation value를 함께 기록한다. Depth가 없으면 `UNPRICEABLE`로 두며 임의로0 또는 종가청산을 만들지 않는다.

#### 담합/자전/조작

- bot_id가 달라도 같은 lineage/experiment owner이면 self-match 정책을 정해야 한다. MVP는 동일 beneficialOwner 내부 match 금지 또는 oldest incoming cancel을 명시한다. “서로 다른 유전자니까 정상 거래”로 인정하지 않는다.
- 전체 population이 한 경제 주체라면 모든 bot간 match를 금지하면 arena 자체가 사라진다. 따라서 `economic_owner`와 `experimental_team`을 구분하고 평가용봇 소유구조를 명시한다. bot간 허용 거래라도 closed-cohort의 현금합은보존되어야 하고, 서로 수익이 났다는 표시만으로 alpha를 주장하지 않는다.
- genome에 상대 계좌 ID/parent ID/평가 종료 직전 시세 조작용 힌트를 입력하지 않는다. order rate, cancel rate, crossed-order 및 반복 상대방 거래 diagnostics를 남긴다.
- 평가 직전 작은 거래로 last price를 올리는 marking-the-close exploit, 자식에게 자산을 싸게 넘기는 transfer, market maker의 무한재고를 이용한 arbitrage를 독립 공격 시나리오로 테스트한다.
- reproduction 시 clone에게 자본을 지급하는 행위는 신규 funding event다. 모든 child에100을 주면서 부모자산을 계속 남기면 currency가 매세대 생성된다. 매 cohort를 별도 유한예산 경기로 reset하거나 population treasury에서 자본을 배분해야 한다. 성과와 funding을 반드시 분리한다.

### 6. 16+8 시간과 인과성 계약

시간은 `simulation_event_time`, `exchange_arrival_time`, `agent_receive_time`, `decision_time`, `order_send_time`, `ack_time`, `fill_time`을 구분한다. 이벤트 큐는 `(event_time, deterministic_sequence)` 순서이며 같은 시각 참가자 순서 편향을 seed별 검증한다. Agent는 receive된 observation만 읽는다. 주문을 제출했다고 즉시 fill을 아는 것이 아니라 fill report가 agent에 도착한 후 상태를 갱신한다.

- OHLCV end-labeled 완료시각T 데이터는T보다 이른 observation에 없음.
- 주문이 이미 지나간 호가에 체결되는 retroactive fill 없음.
- partial fill 이후 cancel 도착 전 추가체결 가능. cancel-request는cancel-ack가 아님.
- Market order는 도착시 존재하는 book만 소모. 잔량 정책은 IOC 또는 명시적 reject; 무한 외부체결 금지.
- Stop을 넘은 gap은 실제 도착시 매도가능bid로 체결. 이론stop가격 보정 금지.
- 분석8시간에 정책이 볼 수 있는 자료는 방금끝난 session까지. 다음 session hidden seed/anchor·future dividends·future context 없음.
- 5세션 번식 전 모든 participant의 평가 종료 상태가 동기화되어야 한다. 미청산/미관측 상태를 수익0으로 만들지 않는다.

### 7. Fitness와 공정 비교

선택 자격과 ranking을 분리한다. 회계/인과성/완료세션/가격평가 가능성 검증을 통과한 뒤에만 수익 부모·원금보존 부모 규칙을 적용한다. 새arena에서 legacyfitness를 그대로 “보존”한다고 부르지 말고 동일 구성요소가 어떤 체결·평가 자산 기준인지 버전화를 한다.

권고 기록: netrealizedPnl, executable liquidation PnL, reference-mark equity, fees, impact/slippage decomposition, inventory risk, invalid/unpriceable count, exposure time, trade count, queue time, partialfill ratio, cancel ratio, concentration, opponent/seed ID. Profit과 return의 denominator는 initial capital+외부cashflow를 구분한다.

**반드시 분리할 평가:**

1. Training selection: 5session경기, 다음세대생성. 통계적확증아님.
2. Common-opponent evaluation: 동결 상대집합과 동일한scenario seeds를 공유하는 **별도 세계**에서 baseline/candidate 각각 평가. 같은 LOB에 두 후보를 넣으면 서로 가격을 바꾸므로 paired counterfactual비교가 아니다.
3. Cross-play: 서로 다른 세대·스타일 상대와 경쟁하여 exploitability 측정.
4. Historical transfer: 2022~23학습,2024내부holdout,2025개발검증. 2026접근없음. synthetic게임 성과와별도표시.

유리한 상대 한 종류에만 적응하면 시장 일반화라고 부르지 않는다. Zero-intelligence, liquidity-providing, noisy-value, inventory-limited, momentum, adversarial cancellation 등 독립 opponent family를 고정하고 학습과평가seed를분리한다. 연구자로서 threshold값을이번문서에서최적화하지않는다.

#### 사전 등록할 연구 가설

- H1: 같은 정보·자본·seed·고정 상대에서, arena-trained정책이 legacy-adapted정책 대비 비용차감 liquidation return의 paired 차이가 양수인가?
- H2: 개선이 특정 liquidity/latency/상대seed에만 집중되지 않는가? out-of-scenario spread/depth/latency 악화에서 성능부호/위험이 유지되는가?
- H3: 표면수익이 단순 inventory beta/mark조작/fee중복오류 때문이 아닌가? cash-only·buy-hold·random baseline대비 설명력과 조작검사를통과하는가?
- H4: synthetic성능 순위가2024/2025별도causalreplay 순위와 어느정도일치하는가? 작은표본상관은불확실성과함께보고한다.

보고: seed별paired분포, 중앙값·하위꼬리·최대낙폭·파산/미체결/가격평가불가비율, liquidity비용, 전체실험수, 실패실험보존. 봇100개×5일을500독립표본으로취급하지않는다. 같은시장·상대·seed의결과는상관되어있으므로 world/seed/dayblock단위bootstrap이나반복실험을쓴다. Bootstrap이독립성을만들어주지는않는다.

### 8. 중단 조건과 승인 기준

다음은 수익과 무관한 hardstop이다: 보존식위반,음수예약/잔고,미래정보,허위체결,시장시간라벨혼용,수익평가불가,원본데이터변경,2026접근,운영리소스위험,실주문모듈접근.

설계 동결 전에 결정할 항목:

- 16시간 동안 synthetic시간과 wallclock비율, 8시간worldfreeze 규칙.
- Genesis cash/inventory/issuerledger 및 세대 funding/reset회계.
- owner/selftrade정의와 cross-lineagecollusiondiagnostics.
- 외부liquidity actor의유한자본·리스크·정보가용성.
- 평가기준가격(독립anchor와청산가치),unpriceable처리.
- 가격/수량quantum,priority/partialfill/cancel의deterministic순서.
- frozenopponent/seed분리와negativecontrol.

**1차 결론: 설계 검토 계속 가능. 아직 구현 승인 근거로 충분하지 않다.** 위 결정이 해결되어야 arena내 성공과 외부시장 일반화가 구분되며, 현재 legacy회계·costproxy를 새 LOB에 섞는 오류를 피할 수 있다.


---


<a id="source-reviews-quant-round2-md"></a>

*원문 모듈: `reviews/quant_round2.md`*

## Quant & Market Research — Round 2 반박·합의

2026-10-17 UTC. `architect_round1.md`, `redteam_round1.md`를 독립 1차 작성 후 읽었다. 본 검토도 설계만이며 실행·실제 시장 검증은 없다. 기준 코드 `24313f4`.

### 1. 합의 가능한 최종 방향

**R/A 두 트랙을 엄격히 분리하는 수정안에 동의한다.**

- **R: 실제 1분봉 replay**. 과거 확정 관측을 쓰는 기존 V082의 비교 baseline. 상호 가격 충격·진짜 호가 깊이·큐 우선순위 연구라고 주장하지 않는다. 완료 minute-close proxy의 한계를 그대로 표시한다.
- **A: synthetic shared LOB**. 유한자본·유한재고의 background/MM와 봇의 주문이 가격을 만든다. 공개 외부 reference는 과거까지이며 LOB가격을 역사종가로 강제 덮어쓰지 않는다. Synthetic transfer는 별도 연구 가설이지 설계 보장 아니다.
- 초기에 hybrid라는 제3모드의 체결/수익을 섞지 않는다. A의reference가 실제1m에서온경우도 `syntheticExecution=true`로 표시하고 역사실제체결이 아니라고 명시한다.
- 16시간은 가상arena의 운영clock이며,390개 미국정규장 minute을960개의 실거래 minute으로 뻥튀기하지 않는다. 실제시간16h/8h 운영scheduler와 가상거래session의길이를 각각 config에 기록한다. 스케줄 경계에서 incompletewindow를 완료로승격하지않는다.
- 정수 가격tick/정수 수량microlot은 가상거래소의 수치모형이다. 실제미국lit거래소의fractionalorder지원이나현재SECtick규칙을 재현한다는주장없음.
- same-agent/account STP와 연관계보거래 diagnostic을 구분한다. 모든simulationbot을 소유한 연구자의 법적계좌로 해석해 일반적인시장조작법률판정을 내리지않는다. 실제사용할법률해석은 범위밖이다.

### 2. 두 검토에 대한 비판과 보완

#### Architect: 모드와 단일 writer는 적절하나 기존지표 재사용도 의미확인이 필요

`MinuteFeatureProvider`와 indicator공식을재사용하는것이 바람직하더라도 A에서 minute을합성하는 clock, 거래가없는minute, VWAPvolume0, 가격평가불가에 대한 semantics가 필요하다. `no trades`와 `missing feed`는 다르다. 거래가없었다는 사실이확정된 빈minute을 원본결측처럼 해석하거나 마지막가격을 신규체결로복제해서는안된다. A에서 calendar16시간확장은 새featureadapter에격리하고 R의원래ENDtimestamp/warmup을바꾸지않는다.

Replay단계에 새로운공유체결volume cap을넣으면 기존V082baseline과다르다. 그모형이유용해도 `R_EXECUTION_REVISION`으로 별도버전표시해야한다. 기존V082와trade-level동일성검사가목표인R0에는원래proxy를보존한다. 모형개선과정확한복제는같은작업이아니다.

#### Red Team: 경제보존·청산 우선순위는 옳으나 최소2seed는 효능판정 기준으로 부족

2seed는 deterministic차이와테스트연결을보는 최소smoke일뿐이다. 실험효능판정은 사전에정한독립scenario수·정밀도·최대예산을요구한다. 작은PC에서그수를충족하지못하면 `INSUFFICIENT_EVIDENCE`로끝내야지 seed2개이익을 robustness로승격하지않는다.

전체cohort의집단순자산은 mark변화때문에증가할수있다. 그자체를현금보존위반으로보면안된다. **현금+fee treasury보존**, **자산수량보존**, **청산평가차이**를분리한다. 상대손실과수수료를포함한실현cashflow검산이우선이며 독립reference로mark한평가자산합의증감은별도표시한다.

**무상inventoryreset/마지막가parent선발 차단에는 완전동의한다.** 종료시점관측가능depth를사용한유한시간청산실패는 `INVALID_LIQUIDATION`이다. 미청산position을0원에없애거나anchor로현금화하지않는다. 단, invalid결과를연구집계에서빼면선택편향이므로 전체attempt분모에포함한다.

### 3. 일별100과5세션평가의 정확한 수식

기존V082호환선택의경우일별시작평가자본 `K_d=100`이고 일별모든비용차감실현손익을 `Pi_d`라한다. 5일평가:

`windowPnl = Σ(d=1..5) Pi_d`

`windowReturnOnResetCapital = Σ Pi_d / 100 = Σ dailyReturn_d`

이는 `Π(1+dailyReturn_d)-1`이라는복리5일수익률이아니다. 5일동안총500달러가있었다는평균성과를표시하려면 `ΣPi_d/500`을별도평균일수익으로표시한다. 세지표를같은`return`필드에넣지않는다.

각세션말관측가능청산완료와잔여주문취소를먼저검증한다. 이후cash회수/새100funding을명시적ledgerevent로처리한다. 일별reset이기존Pnl를제거하지않도록 evaluation원장과funding원장을분리한다. Lifetime지표역시dailyprofit합·가상의compoundedcounterfactual중어떤것인지필드명으로구분한다. 과거자산누적을그대로유전한자식은자신이벌어들인수익으로표시하지않는다.

A전체가같은100리셋을사용하더라도 MM의잔여inventory를임의로reset하면다음날환경이바뀐다. 고정된scenario세션초기화계약으로reset할지 연속세계로갈지선택한다. 초기권고는 **명시적독립dailyepisode 초기화**와genesisledger이며,원래장부를덮어쓰지않고회수·정산·다음episode생성을기록한다. 연속5일세계에서일별봇reset만하는실험은별도버전이다.

### 4. 수정안 적용 후 남는 CRITICAL/HIGH

| 수준 | 남은 사항 | 진행 게이트 |
|---|---|---|
| CRITICAL | session말 finite청산시간·marketorder잔량·STP/cancel우선순위가 숫자/규칙으로freeze되지 않음 | 모든미청산은INVALID,수익0금지. 구현전에terminalevent순서/청산관측시각/주문잔량정책명세 |
| CRITICAL | dailygenesis funding/withdrawal와MM잔고reset의일관성 | 원장예시로BUY→partial→SELL→fee→정산→다음100주입의모든상대계정합검산. 불명확하면matching구현보류 |
| CRITICAL | observation/arrival/fill/notification 정보경계와generatorprivileged자료격리 | hiddenfuture접근테스트와접근불가능schema. 2026숫자데이터decode차단. 단한futureevent유출도전체runINVALID |
| HIGH | reference변경·MM재충전이profit을보조하는정도 | 고정MM/finitebudget/noimplicitrecharge가default. 보조금별도보고,MM충격/부재반증실험 |
| HIGH | A의trades없는minute과missingobservation의차이 | `NO_TRADE_OBSERVED`와`DATA_MISSING`구분. fakeOHLCV/volume생성금지 |
| HIGH | winner의통계적효능/계보·세계내상관 | frozenopponents/seeds/scenario와별도worldpaired평가,independence단위/표본계획사전등록 |
| HIGH | PCcapacity미확인 | hardwareinventory후짧은측정만. maxpopulation수치나16h처리량약속금지. AWS학습부하없음 |
| HIGH | synthetic성과의R/미국시장transfer미확인 | A와R성과분리,실제forwardpaper별도. 2026닫힌상태유지. 자동실거래승격금지 |

이표의항목은 문서상해결가능한사항과구현후테스트로만검증가능한사항을구분해야한다. 문서가구체화돼도 `ENGINE_VERIFIED`가되지는않는다.

### 5. 정량 반증 게이트 — threshold 최적화와 분리

#### G0 정확성(허용오류0)

고정seed의eventtape와임의checkpointresume에서 동일statehash·동일fillID/분개. 모든event에서현금/재고예약/fee보존정확일치(정수회계),overfill0,negativeavailable0,selfmatch0,futureobservation0,2026decode0. 한건이라도실패면수익분석전중지한다.

#### G1 exploitation 대조군

cash-only의trade0,fee0,자본이체제외Pnl0을확인한다. 왕복trader는동일가격·양의fee조건에서양의순손익을만들수없다. 동일owner양방향·3계좌wash·종료직전markpump·무한cancel공격을넣고 positivefitness가생기면원장을검산한다. 무차익주장을하라는뜻이아니며수익원인에실제상대손실/시장충격/보조금이명시돼야한다.

#### G2 비용·환경 의존성

수치사전고정반증조건 예: explicitfee2배,관측/주문latency2배,backgrounddepth절반,최상위성과scenario제거. 이는통과하게튜닝하는grid가아니라민감도를보고하기위한stress다. 모든stress양수수익을무조건요구하지않지만,한조건에서만유리하면`MODEL_DEPENDENT`이며robust전략으로승격하지않는다. spread는endogenous체결에이미포함되어legacyhalfspread를중복부과하지않는다.

#### G3 효능 주장(선발과별개)

실험전frozenopponent집합,scenario seeds,관측창,최대독립world수,주요지표를등록한다. 주요지표는candidate-baseline의독립world별비용차감청산수익차이로한다. pairedworld는동일외생seed/상대/초기자본을쓰되후보끼리같은LOB에넣지않는다. 95%불확실성구간과하위꼬리·invalid율을보고한다. 구간이0을포함하거나world수가부족하면`INCONCLUSIVE`,모든차이를bestbot로가리면안된다.

여러후보선택후같은evaluationseed를재사용해최고를골랐다면그세트는이제development다. 새frozen검증없이최종증거로재사용하지않는다. 평가결과를본뒤confidence기준이나failure처리를바꾸지않는다. 사용자요청의5session진화는G3표본부족을해결하지않는다.

### 6. 출처 검증 재확인

정확한URL/기관/연도/HTTP본문확인/실패URL은 `quant_round1.md`의출처표에있다. 본문을확인한NYSEcore09:30–16:00ET,SEC/OIEA market/limit/stop및2024-05-28 T+1설명은위수정안과일치한다. Nasdaq전체priority/currentfee와2026계좌규제세부는검증하지않았으므로A의가상규칙으로만표시한다. ABIDES/BSE초록은event-based/LOB연구의선례이지우리시장현실성이나수익보증이아니다.

### Round 2 결론

**두트랙분리·유한MM·관측가능청산·정수회계·100reset수익정의수정안에동의한다.** 현재상태는`DESIGN_REVIEW_READY`,`NOT_IMPLEMENTED`,`NOT_PERFORMANCE_VALIDATED`다. terminal/funding/time계약을문서로동결한뒤정확성테스트중심의작은구현단계를검토할수있다. 수익성/통계우월성/PC1000개체용량은아직검증되지않았다.


---


<a id="source-reviews-quant-round4-md"></a>

*원문 모듈: `reviews/quant_round4.md`*

## Quant 최종 재검토 — Round 4

검토일 2026-10-17 UTC. 검토대상은 최종 README, `03_architecture.md`, `04_bots_evolution.md`, `05_market_simulation.md`, `06_validation.md`, `09_risks_adrs.md`다. 구현·학습·성능시험·운영배포를 수행하지 않았다.

### 최종 판정

**DESIGN PACKAGE REVIEWED / IMPLEMENTATION GATES STILL CLOSED.**

현재 문서는 실제시장과 합성시장, 자본과 수익, 원장과 평가자산, 번식과 통계검증을 구분한다. 이는 설계적 모순을 줄였다는 판정이지 엔진정확성·수익성·개인PC수용량이 검증됐다는 판정이 아니다. 기존 연구 원본·서비스와2026 Final OOS는 변경하지 않는 계획이다.

### 정정사항 수용

1. **진화 참가봇만 종료 flat.** MM/background의재고는 계속 존재하며 해당actor별손익·현금·재고를공개한다. 이들은 부모선발대상이아니다. 모든actor flat을요구해초기주식을소멸시키는모순이해소됐다.
2. **유한 Treasury의 daily100 재조정.** profit sweep/topup을tradePnL에서제외하고,MM자본/재고를무상초기화하지않는다. Treasury고갈은실패/중단사유이지자동보충트리거가아니다.
3. **window score 의미.** `Σ5 realized_net_PnL / 100`은daily100reset연구score이며5일복리수익률이아니다. 자본입출금과lineage상속이수익으로계산되지않아야한다.
4. **미청산세계 전체무효.** 진화봇하나라도미청산이면해당세계/창을봉인하고실패분모에포함한다. 유리한봇만선발하거나미청산을0손익처리하지않는다. 다음seed는사전등록순서이며재시도최적화가아니다.
5. **정수금액.** price tick=$0.01,quantity quantum=10^-6share에서notional atom=10^-8USD는산술적으로일치한다. 정수1bps fee와주문별누적ceil의차분방식은부분체결합과주문fee상한의정합성을유지할수있는설계다. 실제코드의correctness는아직시험하지않았다.
6. **비용분리.** Arena는매칭spread/impact+explicitfee,Replay는기존2+1bps/sideproxy다. A에고정halfspread를다시부과하지않는다. 동일costmodel이므로공정하다는주장도하지않는다.
7. **A/R우선,H보류.** 과거종가로book을덮어쓰지않는다. 16+8은가상clock/분석budget이며미국390분가격을960분관측으로확장하지않는다.
8. **표본계획.** 독립20세계는초기계획일뿐검정력보증아니다. smoke2seed는인프라용이다. 다중가설과반복선택을고려한보정된추론이필요하다.
9. **Notion.** parent URL이나문서준비는쓰기권한·실제게시증거가아니다. 현재미게시표시는정확하며게시완료라고보고해서는안된다.

### 최종 문서 정합성 보완 요청

#### M1 — hybrid의지위 한문장 통일

`03_architecture.md`는“최종선택은…단계적hybrid”라고쓰지만 `05_market_simulation.md`는“H는최종목표로확정하지않으며…별도ADR”이다. **후자를최종결정으로통일**해야한다. 현재미래효용미입증인H를확정제품목표로읽히게하면안된다. 신규연구결과없이도수정가능한문서표현문제다.

#### H1 — nominal95%CI와다중검정보정 일관화

`06_validation.md` H1행의“95%CI하한>0”은다중가설confirmatory주장시원시95%CI를그대로적용한다는뜻이면불충분하다. **사전등록된familywise보정CI 또는 Holm보정검정과일치하는추론**으로표현한다. 모든표본/대조가명확하지않으면탐색결과로남긴다. 성과를본뒤correction을고르지않는다.

#### H2 — independent world와historical episode 구분

같은R날짜경로에서seed만바뀌면그것은새로운시장역사가아니다. seed는population/training변동성을만들수있지만동일날짜공통shock에대한독립성을만들지않는다. A에서는독립generatorworld, R에서는시간block/기간의존성과동일경로공유를보고하고반복seed에의한표본부풀림을금지한다. 세계단위paired평가와timeblockcluster계약이같이필요하다.

#### M2 — tail수치와invalid의통계 처리

20세계에서worstdecile은약2세계만반영하므로정밀한tail추정이라고부를수없다. 원시분포와불확실성을같이표시한다. 미청산invalid를분모에포함하되그수익을임의0으로대체해평균내지않는다. 유효조건부수익분포와전체invalid율을나란히보고하고,승격판정에failure ceiling을사전등록한다. 결과가좋지않다는이유로사후failure ceiling을완화하지않는다.

이항목들은설계문서의정확성을위한보완이며엔진구현이나새threshold최적화가아니다.

### 아직 남는 실제 검증 차단

- **CRITICAL/미검증:** event별현금·수량·fee·예약보존,이중체결,신규주문/cancel경쟁,actualpartialfillfee,resume동일성.
- **CRITICAL/미검증:** 관측가능시각·미래latent격리·2026numericaccess차단·동일봉소급체결부재.
- **CRITICAL/미검증:** 실제finitebook청산과whole-world invalid처리,봉인세계가번식되지않음,100reset상대계정.
- **HIGH/미검증:** wash/markpump/MMdrain공격과reference의존도,외부수익원분해.
- **HIGH/미측정:** PCmemory/CPU/disk/eventbacklog,최대1000개체를지원한다는증거없음.
- **HIGH/미입증:** A→R전이효과,2025반복선택편향,현재30종목survivorship,실제quote/tick가격조정·수신provenance.

### 받아들이지 않는 주장

- “가상시장우승=실제주식수익”, “5일생존=통계적으로우수”, “20seed면유의성확보”는거절한다.
- “단일FIFO가미국전체시장”, “fractionalmicrolot이실제미국거래소규칙”, “1bps가Toss실수수료”, “즉시가상정산이현금계좌T+1준수”는거절한다.
- “문서에원장불변식을썼으니원장검증완료”, “Notion URL이있으니게시완료”, “기존V082runtime으로1000개체LOBcapacity확인”은거절한다.
- “미청산을빼고유효수익만좋으므로승격”, “MM손실에서나온수익이면항상버그”도거절한다. 전자는선택편향이고후자는정상적인가격위험이전과회계오류를혼동한다.

### 출처 상태

공식NYSE/SEC·Investor.gov/FINRA및ABIDES/BSE의정확URL·발행정보·2026-10-17직접조회범위·조회실패는독립Round1에보존되어있다. Nasdaq규칙전체·2026계좌규제·실제brokerfee를검증완료했다고주장하지않는다. 최종설계는그미확인사항을현실동일성주장으로사용하지않으므로적절하다.

**결론:** 위문서표현정합성을반영하면설계패키지를사용자검토용으로정리할수있다. 수익성이나실행안전성을승인한것은아니며,구현은M0규칙freeze와PC사양·자금예산확정이후별도단계다.

### Round 4 최종 재확인 — 반영 후

같은날수정된03/04/05/06및02출처표를다시읽었다.

- 03과05의A/R우선·H별도ADR보류가정렬됐다. H의실효성을미리승인하지않는다.
- 03의마감후drain은마감전에확정된fill/cancel보고만유한지연상한까지전달한다. 추가매칭·주문·다음source관측을금지하고상한초과INVALID_COMMUNICATION을보존한다. 이것은지연된알림과새로운미래체결을구분하는올바른설계보완이다. 실제상한값은manifest에서유한값으로freeze해야한다.
- 04는수익부모1명과평가유효1명인상황을구분했다. 다중봇세계99명무효를버리고1명부모만선택하지않으며,정책fixture/사전단일봇세계에만single-validfallback을한정한다.
- 06은동일R날짜seed반복을시장독립표본으로취급하지않고timeblock/RNG불확실성을분리한다. 보정검정/동시CI의일관성,20세계tail의불안정성,conditional성과+전체invalid율,사전failureceiling도명시됐다.
- 02는본문/초록/서지메타데이터/도달확인의증거수준을구분했다. Crossref1997/2006항목은서지확인만으로한정하고본문효과나성능수치를만들지않았다. ABIDES/BSE의존재를이프로젝트의새로움이나수익성증거로사용하지않는다.

**이전M1/H1/H2/M2문서보완요청은설계수준에서반영됐다.** 남은차단은기존에명시한미구현G0~G2/G4의정확성·공격시험,PC실측G3,실데이터provenance/효능G5~G7및M0의유한예산·rate/latency설정freeze다. 문서검토가이를통과시킨것은아니다.

최종상태: **설계패키지정리·사용자검토에동의 / 엔진구현검증·훈련·수익성·실거래승인없음 / Notion미게시 / 2026CLOSED.** 새코드·학습·배포·서비스·원본데이터변경없음.

#### 최종 한정 delta — MM0 및 genotype 부록

05의MM0후보식과04의genotype표/의사코드부록을추가검토했다. 기존stop/takeprofit/maxhold/positionfractionbound는실제 `config.py` 및 `v082_indicator_config.json`과일치한다. 신규gene/bound는제안값·inactive출발·단일변수실험으로표시되어최적화결과를가장하지않는다. simplex교차후repair와실제mutation을구분하고부모불변성을요구한점도적절하다.

MM0의inventoryskew/spread식은가격단위reference와무차원재고비율을사용하는명시적후보다. 과거공개변동성의단위·시간스케일, `Qmax>0`,finite `a,b,k` 및target범위,초기현금/재고,quote주기·size·stop은M0에서정의해야한다. 현재는채택/검증미완료로명시되어있으므로추가Critical모순으로분류하지않으며 **M0_OPEN / 실행불가**다. 분모0·비유한모수·비양수가격·자원초과거절은실제schema검증과회귀시험이필요하다.

빈book/유동성소진을무료체결이나데이터결측으로바꾸지않고,항상예정된closecontrol이벤트로유한시간내종료하여미청산INVALID를기록하는계약은타당하다. Participation의과거volume0차단과함께양쪽거래가영원히시작되지않는시나리오도가능하므로background/genesis활동과NO_TRADE상태를manifest에명시해야한다. 해당실패를“학습수익0”으로승격하지않는다.

의사코드의`while time<close`는실행코드가아니다. 추후구현은close와동일시각이벤트의우선순위·남은예약취소·preclose보고drain을03/05의전체계약에맞춰시험해야한다. 이문서에루프가있다는이유로종료경계검증을완료했다고주장할수없다.

**delta결론: 설계초안에추가Critical모순발견없음. MM0파라미터/경제예산미확정,구현·인과성·보존·성능게이트는여전히닫혀있음.** 신규엔진/실행/배포는수행하지않았다.


---


<a id="source-reviews-redteam-round1-md"></a>

*원문 모듈: `reviews/redteam_round1.md`*

## Red Team Round 1 — V0.9 설계 차단 조건

기준 HEAD: `24313f4` (`strategy-engine-v3`).

상태: **IMPLEMENTATION BLOCKED / 연구 설계 검토**. 실행·훈련·벤치마크·배포하지 않았다. 이 문서는 독립 검토이며 다른 역할의 초안을 읽기 전에 작성했다.

### 실제 V0.8.2와의 차이

직접 확인한 소스는 `research/evolution/indicator_engine.py`, `features.py`, `survival_breeding.py`, `data_access.py`, `account.py`, `v082_indicator_config.json`이다. 현재 시스템은 외생적인 실제 완료 1분봉 가격을 여러 독립 계좌가 공유한다. 계좌별 매수는 다른 봇의 호가나 체결에 영향을 주지 않는다. `MinuteFeatureProvider`는 완료 시각과 정확한 벤치마크 관측을 사용하며 결측을 보간하지 않는다. `Account`는 다음 완료 분봉 종가 대용 체결과 비용을 사용한다. 따라서 기존 엔진은 가격 내생적인 shared LOB의 매칭 엔진·청산소·시장조성자 검증을 제공하지 않는다. 최신 `v082_bot_roster.py`는 연구 산출물을 읽어 개체 정보를 보여주는 read-only 모듈이며, shared LOB를 구현하는 코드가 아니다. 기존 V081 생존 번식 정책을 그대로 재사용해도 가상 시장에서의 담합·가격 조작 방지는 해결되지 않는다.

현재 data_access의 2026 차단은 날짜 범위 확인 후 OHLCV 수치 해독을 제한하는 구현이다. V0.9가 별도 가상 시장을 만든다는 이유로 이 데이터 격리를 제거할 수 없다. 혼합 연도 파일의 무결성 해시와 날짜 라벨 스캔은 가격 특징 입력과 구분해야 한다.

### 심각도별 위험 및 재현 가능한 차단 게이트

| 수준 | 위험 / 반증 실험 | 통과 기준 / 실패 시 조치 |
|---|---|---|
| CRITICAL | 현금·주식 생성, 이중 사용, 음수 예약 잔고. 주문/부분체결/취소/동시 교차 순서를 고정 seed로 반복하고 매 이벤트 원장 검산 | 수수료·명시적 외부 현금흐름을 제외한 총 현금과 총 주식 보존. 예약+가용 잔고 일치. 가격/수량 integer ticks/lots 사용. 단 한 건 불일치도 매칭 구현 차단 |
| CRITICAL | 자기체결·공통 조상 봇 간 자금 이전으로 선택을 조작. 동일 owner 양방향 주문, 두 계좌 순환 매매, 3계좌 wash cycle 공격 정책 | 직접 자기체결은 거절. lineage 간 거래 자체는 금지할 수 없으므로 순환 거래/집중 상대방 지표를 별도로 표시. 비용 포함 집단 순자산 증가가 외부 유입 없이 발생하면 실패. 개별 승자 PnL은 이전된 손실과 함께 보고 |
| CRITICAL | 마지막 체결가 펌핑으로 미실현 수익/부모 순위를 부풀림. 얇은 호가에서 소량 고가 체결 후 대량 재고 보유 | 부모 자격은 실행 가능한 청산 또는 명시적 보수적 청산 평가가 끝난 후 결정. 마지막가 단독 mark로 생존 금지. 미청산/유동성 부족은 INVALID_SETTLEMENT, 0 손익 처리 금지 |
| CRITICAL | 16h 종료 순간 주문/강제청산/8h 분석 경계에서 미래 정보·무상 재고 소멸 발생 | eventTime, observedAt, decisionAt, acceptedAt, executedAt 분리. 미관측 사건으로 주문 금지. 종료 주문 수락 규칙·잔고 이월/리셋·미체결 주문 취소를 버전 고정하고 경계 1 tick 전후 회귀 테스트 |
| HIGH | 시장조성자(MM)의 무한 현금/무한 재고 또는 재충전 보조금이 봇의 가짜 alpha가 됨. MM off/on, finite inventory, replenishment budget perturbation | 봇+MM+외부 계정 통합 원장 보존. 모든 보조금 별도 항목. 이익이 단일 보조금 설계에서만 존재하면 SIMULATOR_EXPLOIT, 이전 가능성 주장 금지 |
| HIGH | 잠재 가치/유동성 모델을 만든 공식과 동일한 특징을 봇에 제공해 정답 누출. generator seed·latent fundamental·future orderflow 노출 공격 | policy observation schema는 공개 과거 사건만 허용. privileged namespace 접근 금지 테스트. latent 모델 바꾸거나 숨기면 붕괴하는 성과는 모델 정답 암기로 분류 |
| HIGH | 관측 지연 없는 취소·즉시 체결·무한 주문으로 시뮬레이터 API 경쟁. latency/rate limit/cancel fee perturbation | 동일 event budget 및 공개 우선순위; 결정론적 지연 큐. 최소 2개 독립 환경 seed와 지연 조건에서 결과 보존 여부를 보고. 실거래 수익 증거로 해석하지 않음 |
| HIGH | 수익 부모만 남기는 정책이 위험 은폐/우연한 꼬리 이익을 선택. 5일에 거래 1건 승자와 꾸준한 봇 비교 | 5일은 번식 주기일 뿐 유의성 기준 아님. 거래 수/독립 에피소드/상대방/손익 집중도를 함께 보고. frozen out-of-environment 평가 전 CHAMPION ≠ APPROVED_STRATEGY |
| HIGH | 거래하지 않으면 비용/위험 0이므로 무거래 봇이 안전 기준을 독점 | cash-only baseline을 필수 표시. 무거래는 NEUTRAL, edge 증거가 아님. 거래 강제 보너스 금지. 유효한 수익 부모가 없을 때 복구 정책과 실패 상태를 명시 |
| HIGH | 동일 arena에 50→1000 봇을 늘리면 환경 자체가 변함. 개체 수 증대를 전략 개선으로 오인 | 총 초기 유동성·총 부·주문량 대비 자본을 함께 고정한 통제 실험과 자연 증가 실험을 분리. 서로 다른 population 성과 직접 우열 판정 금지 |
| HIGH | 16h를 1분 행동 960회로 볼지 subsecond 사건으로 볼지 미정이면 PC 최대 개체 수 추정 무의미 | 로컬 PC CPU/RAM/OS/여유 디스크 확인 전 RESOURCE_CAPACITY_UNKNOWN. events/sec, 주문/봇 상한, 보관량, 반복 횟수 명세 후 짧은 프로파일만 승인 가능 |
| MEDIUM | 매칭 순서가 bot ID/목록 순서에 유리함 | ID permutation + 동일 행동 paired runs; 동시 주문 도착 순서 결정 규칙 문서화. 동률 처리 효과 별도 보고 |
| MEDIUM | 매 세대 $100 리셋·dead 재고 정리·elite 복사가 자본을 몰래 주입 | 연구용 daily reset과 시장 통합 원장 분리. 주입/회수 주체와 금액 명시. arena 간 결과 이월 조건 고정 |
| MEDIUM | 계보 유사도가 높아 1000봇을 독립 표본으로 오인 | genome hash, 부모 기여 집중도, 유효 유전자 다양성 보고. 표본 단위는 봇 수가 아니라 독립 환경 에피소드도 포함 |
| MEDIUM | 소수 좋은 seed/시장 상태만 채택하는 선택 편향 | 실행 전 seed 목록·생성기 버전·실패 포함 전체 실험 registry 고정. best seed 제거, worst 환경 결과 표시 |
| MEDIUM | 로그가 무한 증가하거나 체크포인트가 불완전해 유지비 폭증 | 이벤트/분석 로그 분리, bounded RAM, 원장 replay hash, crash/restart equivalence, 디스크 예산 초과 시 새 실험 중단. 원본 자동 삭제 금지 |
| LOW | 가상 16h를 실제 미국 정규장으로 오해 | SYNTHETIC_MARKET_RESEARCH_ONLY 표시. 실제 거래일 캘린더/가격 시간과 가상 clock을 별도 namespace로 기록 |

### 필수 반증 배터리

1. 대조군: cash-only, bounded random trader, 수수료만 손실을 내는 왕복 trader, 알려진 wash/manipulation 공격자.
2. 원장: 1개의 deterministic event tape를 replay해서 상태 hash 일치. 임의 시점 crash→resume 결과도 동일해야 한다.
3. 경계: 16h 직전 주문, 마지막 부분체결, 취소 경쟁, 시장 유동성 소진, 8h 분석 시점의 외부 입력 금지.
4. 독립성: training 생성기와 다른 seed만이 아니라 다른 주문 흐름·volatility·유동성·MM 규칙을 동결 평가. 새 환경은 관측 후 재조정하지 않는다.
5. 비용·지연: 비용 2배, latency 증가, depth 절반, 큰 주문 impact에서 생존 여부. 이는 성능 최적화가 아니라 구조 의존성 측정이다.
6. 번식: 유효 평가 0개→중단, 무수익 유효→명시적 원금보존 정책, 1부모→self crossover 없음, 복구 자식 실제 유전자 변화, 5개 유효 연속 세션만 세대교체.

### 순서와 상태

- **READY FOR SPECIFICATION**: 가상 시장만의 연구 목적, 기존 AWS/실거래/2026 격리.
- **BLOCKED: ECONOMIC_ACCOUNTING_SPEC**: MM/외부 거래자/현금 주입/종가 청산/초기 재고 원장 필요.
- **BLOCKED: TIME_AND_MATCHING_SPEC**: 16h clock, 사건 단위, 관측·주문 지연, 가격-시간 우선순위, 시장종료 처리 필요.
- **BLOCKED: LOCAL_HARDWARE_UNKNOWN**: 최대 1000은 목표 상한이지 실행 가능 증거가 아니다.
- **BLOCKED: TRANSFER_CLAIM**: synthetic 성과는 실제 Toss 1m/tick/forward paper의 edge 증거가 아니다. 별도 동결 검증 전 실제 적용 금지.

위 차단을 해소하기 전에는 shared-LOB 학습 엔진 구현·대규모 훈련·AWS 연결을 승인하지 않는다. 설계 문서를 작성하는 것과 구현이 검증됐다고 선언하는 것을 구분한다.


---


<a id="source-reviews-redteam-round2-md"></a>

*원문 모듈: `reviews/redteam_round2.md`*

## Red Team Round 2 — 교차 반증 및 해결안 평가

기준 HEAD `24313f4`. architect_round1 및 quant_round1을 읽고 작성했다. 런타임 구현·학습·성능 측정은 하지 않았다. **아래 “해결”은 설계 합의의 해결이지 구현이 안전하다는 증거가 아니다.**

### 1. 의견 차이와 채택할 해결

#### A/R 분리는 동의, replay를 주 목표의 대체물로 삼는 데 반대

Architect는 replay를 첫 단계로, Quant는 hybrid를 장기 실험 틀로 제안한다. 사용자 요구는 동일 세계 안의 경쟁 시장이므로 A=`SYNTHETIC_SHARED_LOB`가 핵심 목표여야 한다. R=`TRUE_1M_REPLAY`는 독립 회귀·실시장 전이 대조군이지 A의 구현 완료 대용물이 아니다. Hybrid는 최초 범위에서 제외해야 한다. 동시에 세 모드를 구현하면 회계·시간·성과의 의미가 섞이고 검증 부담이 급증한다.

**설계상 해결:** A/R 결과 schema와 provenance namespace 분리, 비교 가능 범위 명시. **남은 증거:** R adapter가 V082와 같은 데이터/설정일 때 trade-level 회귀를 통과하는지, A가 R의 원가·체결 proxy를 몰래 사용하는지 정적·동적 검증.

#### 16h 표현의 충돌

Architect는 wall-clock 운영 창으로, Quant는 가상세계 시간으로 설명했다. 이 둘을 동시에 “16시간”이라고 쓰면 재시작/종료 규칙이 서로 달라진다. 주 해결안인 **가상 세션 16h + 8h 분석 단계**를 채택하고, 실제 PC 벽시계 처리시간은 별도 값으로 둬야 한다. 가속/정지 비율이 가상 시간의 의미를 바꾸지 않아야 한다. 8h는 분석 phase budget이며 8시간 실제 CPU 사용 의무가 아니다.

**반증:** 느린 PC에서 벽시계16h가 지나도 가상세션 미완료라면 번식하지 않음. sleep→resume으로 가상 사건 생략 없음. 분석 단계에 다음세션 시장사건 공개 없음.

#### 같은 owner 금지의 과도한 현실성 주장

Quant가 제안한 beneficial owner 정책은 현실 의미로 정확하지만, 모든 봇의 실제 인간 소유자가 동일하면 전체 arena 거래를 금지하게 된다. MVP는 **동일 bot-account 자기체결 방지(STP)**로 한정하고, 다른 봇·공통 조상 간 거래는 허용하되 집단 자금 이전/담합 진단을 한다. 이를 “실제 beneficial-ownership 규제 준수”라고 부르면 안 된다.

**남은 High:** STP만으로 wash ring을 막을 수 없다. 2/3개 봇 손실이 한 봇의 번식 성공을 사는 공격을 반드시 실행해야 한다. 개별 rank가 오르더라도 집단 손익·부모 lineage 집중·상대방 기여가 숨겨지면 설계 실패다.

### 2. 주 해결안별 조건부 판단

| 해결안 | 설계상 해결되는 점 | 여전히 필요한 구현 증거 |
|---|---|---|
| 유한 MM + capital injection ledger | 무한 유동성/무상 replenishment를 명시적으로 금지 | genesis/재충전/수수료 treasury/세대 reset까지 통합 보존식, MM 소진 시 미체결 처리. 장부에 기록했다는 것만으로 경제적 보조금 의존성이 사라지지 않음 |
| 독립 세계 paired evaluation | 같은 시장에서 후보가 서로 가격을 바꾸는 비교 오류 감소 | 시장 RNG와 정책 RNG 분리, 고정 상대/공통 외생 shock, 세계별 replay hash. 봇 수 변화가 market seed 전체를 교란하면 paired라 부를 수 없음 |
| 미관측 청산 invalid, no reset/evolve | 가짜 종료가격·재고 소멸 방지 | 청산 deadline, pending ACK/fill/cancel drain 순서, book empty 처리. 유동성 없는 봇의 invalid를 전체 cohort에서 어떻게 집계하는지 명시. invalid를 탈락시켜 survivor만 성과보고하면 다시 편향 |
| integer ticks + fictional microlots | float 예약/현금 오차 감소 | price×quantity와 수수료 반올림 통화 quantum, dust 계정, 부분체결 잔량 처리. integer 사용만으로 세금/fee 반올림 이익의 반복 수확이 없어지지 않음 |
| latent future 비노출 | 정답 공개 금지 | observation allowlist, debug/status/checkpoint 정책 전달 금지. RNG state/hiddenseed/order generator 내부 접근 테스트. 정책 입력 값의 shape/timing이 hidden shock을 누출하는 side channel도 확인 |
| synthetic16h | 실제 US 정규장과 혼동 감소 | 날짜/sessionTime/벽시계/실제historicalTime 별도 저장. 16h를 실제 미국 trading calendar에 억지 배치 금지 |

### 3. Round 1 이후 추가 High 위험

1. **현금만100으로 주고 봇 재고0이면 봇간 거래를 시작할 seller가 없다.** MM/issuer genesis inventory와 fair value·초기 capital 배분이 반드시 있어야 한다. MM 공급을 늘려 수익을 쉽게 만드는 것은 실력 개선이 아니다.
2. **강제 청산도 시장 충격이다.** 모든 봇의 종료 매도는 유한 depth를 소모한다. bot ID순 liquidation이 순위를 바꿀 수 있다. 미리 고정한 공정 청산 순서와 순서 민감도, 체결 불가율을 보고해야 한다. 독립 sidecar 청산은 같은 경기 실제청산과 다른 평가 모델임을 표시한다.
3. **선택 직전에 모든 재고를 팔게 하는 전략만 보상할 수 있다.** 이는 명시된 flat-at-session-end 규칙으로는 유효하지만 장기재고/실시장 일반화의 증거가 아니다. 청산비용을 objective에 중복 벌점 부과하지 않는다.
4. **8h 분석에 자동 코드 self-modification을 넣으면 재현성이 깨진다.** V09 초기 범위는 고정 스키마 mutation/crossover와 deterministic reports뿐. 연구자 hypothesis/코드 변경은 별도 승인·새 run·새버전이다.

### 4. 최소 반증 게이트

- **G0 격리:** A는 합성 입력만, R은 허용 2022~2025자료만. 2026 수치 입력 요청/경로 escape/실주문 import를 차단. AWS 운영 프로세스 무변경.
- **G1 보존:** 임의 주문/취소/부분체결/재시작 event tape 매단계 현금·재고·예약 불변식. treasury 포함 실제 transfer 합계0. 1건 위반도 차단.
- **G2 인과성:** 미래event/latent변수 sentinel을 바꿔도 공개prefix까지 observation/decision/체결이 동일. latency 이후만 ack/fill 정책에 전달.
- **G3 공격자:** self-match, wash ring, 마지막가펌프, fee-rounding, cancel storm을 negative control로 실행. 조작이익·보조금은 정상alpha와 분리, 은폐되면 실패.
- **G4 종료:** no bid/partial/ACK지연/재시작 중 종료에서 허위청산0건. invalid cohort는 no evolution 및 실패artifact 보존. 이전완료 세대만 분석.
- **G5 반복성:** 동일 config/data/code/seed/event tape의 모든 ledger/state hash 일치. bot 순열 및 청산 순서 민감도 별도 보고.
- **G6 연구:** 5세션은 번식만 허용. frozen independent-world/opponent/OOD 평가와 cash-only/random 대조군 결과 없이 승자를 투자후보로 승격하지 않음. rawbot count를 독립표본 수로 사용 금지.
- **G7 자원:** PC 사양 확인 전50/1000 어느 수도 안전 판정 불가. event budget/로그량/queue상한/디스크guard 확정 후 승인된 짧은 측정만 가능.

### 결론

**DESIGN CONDITIONALLY ACCEPTABLE / IMPLEMENTATION STILL BLOCKED.** A/R 분리·유한자본·가상시간·실행가능정산·관측경계는 올바른 방향이다. 그러나 재정 보존, 담합, 청산 순서, 미관측 invalid cohort, integer fee dust, 숨은정보 격리에는 문서 외 실제 증거가 없다. 수익률이 높아도 이 게이트를 우회할 수 없다. 마지막 검토에서는 통합 문서에 이 차단과 조건부 상태가 유지되는지 확인하겠다.


---


<a id="source-reviews-redteam-round4-md"></a>

*원문 모듈: `reviews/redteam_round4.md`*

## Red Team Round 4 — 통합 문서 최종 조건 검수

검수 대상: README, 03_architecture, 04_bots_evolution, 05_market_simulation, 06_validation, 09_risks_adrs. 문서만 읽었으며 실행·훈련·배포는 하지 않았다.

### 판정

**PLANNING REVIEW COMPLETE / M0 SPECIFICATION FREEZE STILL CONDITIONAL / NO RUNTIME SAFETY CLAIM.**

기존 Critical 항목의 해결 방향은 통합됐다. 그러나 조건부 차단이 존재하며 수익성·실행안전성·1000봇 처리량의 검증으로 읽어서는 안 된다. 현재 문서로 학습을 시작할 수 없다.

### 설계에서 해결된 항목

- 가상16h 세션과 분석8h budget, 실제 R 정규장 및 PC wall clock을 분리했다.
- 봇 중 하나라도 미청산이면 전체 세계/평가창 INVALID_SETTLEMENT이며 유리한 봇만 선발하지 않는다. 가짜청산·0손익·무상reset을 금지했다.
- 외부 MM/background는 flat 의무가 없고 재고를 carry하며 진화 부모에서 제외된다. 초기 주식 보존과 종료청산의 충돌을 해소했다.
- 유한 Treasury가 bot cash를100으로 조정하는 순입출금을 기록하고 trading PnL에서 제외한다. Treasury 고갈은 신규세션 차단이다.
- $0.01 tick × 10^-6 share =10^-8 USD cash atom과 order 누적 notional fee 차액 방식은 부분체결 rounding 불일치를 줄이는 명세다. 실제venue 규칙으로 주장하지 않는다.
- 계좌 STP와 cross-bot 담합 진단을 구분하며 실제 beneficial ownership 준수를 주장하지 않는다.
- 2seed는 infrastructure smoke,20seed는 출발 표본계획으로만 표시했다. 5session 번식과 통계적 확증을 분리했다.
- Critical 구현 게이트와 개인PC미측정,2026CLOSED,원본read-only를 유지했다.

### 문서 차단·추가 명시가 필요한 항목

#### H-R4-01: H 보류와 통합 아키텍처 문구 충돌

검수 시 03에는 “최종 선택 ... 단계적 hybrid”가 있고 05/ADR01은 H를 보류한다. README의 실제가격 context도 합성MVP에서 실제데이터adapter를 이미 쓴다고 읽힐 수 있다. **최소 수정:** 03/README는 A synthetic와 독립R만MVP, H는 별도ADR전미포함으로 일치시킨다. 이전 라운드의 이견은 수정하지 않고 이력에 남긴다.

#### H-R4-02: 마감 후 pending ACK drain

16h 이전 유효 도착/체결이 있었지만 fill report가16h이후 수신될 수 있다. cutoff 뒤 모든 사건을 지우면 체결이 실종되며, pending ack를 관측하지 않은 채부모를 선발하면 정보/계좌 상태가 불일치한다.

**필수 계약:**
1. matching cutoff는16h다. arrival가16h이상인 신규주문은 체결하지 않고 reject report를 낸다.
2. cutoff전에 실행된 fill은 원장상 유효하며, 그 보고는16h이후 정상 지연으로 전달할 수 있다. 이는 소급신규체결이 아니다.
3. 마감시 남은 주문의 취소·예약반환·fill/cancel/reject report를 settlement-only drain으로 마무리한다. 이동안 정책은 신규 위험명령을 낼 수 없다.
4. drain 상한은유한설정이다. ACK 영구미도착/누락이면 시스템실패·invalid 상태를 기록하고 valid/final fitness/evolution을 금지한다. 누락을 0체결로 해석하지 않는다.
5. 분석은 최종완료검증 뒤에만 committed episode를 읽는다. 체결 발생시각·보고수신시각·정산완료시각은 별도필드다.

**반증 fixture:** cutoff−1tick fill, cutoff+latency ACK; cutoff전 cancelrequest 이후 fill; cutoff이후 orderarrival; 마지막fillreport누락; drain중checkpointcrash. 재시작도 원장/ACK상태hash가 같아야 한다.

#### H-R4-03: MM quoting·재고 소진·시장 무활동

유한MM은 적절하지만 quote 공식·빈도·양측재고·spread bound·reference가 미정이면 가격형성 실험은 아직 재현가능하지 않다. MM가cash 또는inventory를소진하면 quote가 사라질 수 있다. 이때 lastprice forward-fill로새 거래를 만들거나 infinite bid를 복구하면 안 된다.

**필수 계약:** M0에 고정할 `MMModelVersion`, quote시점, 가격/수량산식, initialfinitecapital, inventoryskew, starvation/halt정책, 종료liquidity정책을 source/config에 봉인. numeric값은이번검토에서 만들지 않는다. no-MM/no-bid는 정상미체결 또는 INSUFFICIENT_LIQUIDITY/INVALID_SETTLEMENT로 기록하며 중단·실패세계분모에 포함한다. deadloop를 방지하는 유한event horizon/maxpending이 있어야 한다.

**반증 fixture:** seller재고0genesis, MMinventory0, MMcash0, 모든봇HOLD, 매수만모든봇, 마지막10분동시매도. 시장이 멈춰도실행기가무한loop하거나 가격/현금을창작하면 실패다.

### 남은 Critical 구현 증거

1. 원장 보존: genesis,일일sweep/topup,자식funding,fee/dust,부분체결취소를 포함한 exactinteger invariants.
2. 인과성: futureevent/latent/seed 변경에 대해 공개prefix까지 모든policy입력과decisionhash불변. debugging/checkpoint조회가 우회경로가아님.
3. 정산: 실제 진화봇flat+예약0+pendingreportsdrained 전valid불가. 정산실패world는봉인,성공개체만구제하지않음.
4. 조작: selfmatch,washring,lastpricepump,fee-rounding,cancelstorm,MMdraining negativecontrols. 집단보존과별개인개별부모자격조작을 독립세계평가로 드러낼것.
5. 복구: 동일seed/code/config/input과order tape에서 crash/resume 및 eventbatch변경후체결·원장digest동일.

### 최종 의견

문서 방향은 일관된 연구 제안으로 발전했다. 위 두 가지 시간/모드 문구와MM미정상태를 명확히하면 **설계 제안 게시 가능**이다. 그러나 M0의MM·시간·데이터·자원설정 freeze와 G0~G7의 실제 시험 전에는 구현안전성/훈련준비/실시장전이를 승인하지 않는다. 이 조건부 판단을 최종README와검수표에서 삭제하지말아야 한다.

### 수정 후 마지막 재검수

동일 문서의 실제 수정본을 다시 읽었다.

- **H-R4-01 해결(명세 수준):** 03은 H adapter를 별도 ADR로 보류하며 A/R 가치가 없으면 도입하지 않는다고 정정했다. README도 최초 A는 합성 시나리오만, 실제가격 맥락은 추후 H 승인 뒤라고 구분한다. 이제 hybrid를 이미 확정된 MVP로 해석할 근거는 없다. 다만 아키텍처 그림의 역사 모수추정 가지는 선택적 미래 가지임을 텍스트와 함께 읽어야 한다.
- **H-R4-02 해결(명세 수준):** 03의 종료보고 절과05의 마감 규칙은 거래를 더 진행하지 않는 유한 report drain, `max_report_delivery_latency_ns`, authoritative ledger와 보고 일치 전 valid commit 금지, 초과/불일치 `INVALID_COMMUNICATION`을 명시했다. 구현에서 cutoff 직전 fill/늦은 ACK/마감 cancel/늦게 도착한 신규주문의 reject report가 모두 누락 없이 전달되는지 여전히 검증해야 한다. 마감 자동취소의 효력 시각과 기존 예약 반환도 동일 transaction으로 fixture에 고정해야 한다.
- **생존자 선택 누수 수정 확인:** 04 단일 유효 후보 분기는 사전 등록 단일봇 세계/정책 fixture로 한정한다. 100봇 중99무효를 버려1부모로 복구하는 것은 전체세계무효 규칙 위반이라고 명시했다.
- **통계 분모 수정 확인:** 06은 유효세계 conditional PnL과 전체요청 invalid/미청산 비율을 동시에 보고하며 실패율 상한 초과 시 수익과 무관하게 실용 승격을 막는다. 무효를0수익으로 대체하지 않는다. 이는 결과 분석에서 서로 다른 분모를 혼합하지 않도록 후속 schema 검증이 필요하다.
- **H-R4-03 미해결/정직하게 OPEN:** 09의 초기symbol/scenario/유한 MM·Treasury 예산, 공개depth·지연·rate limit 질문이 열려 있다. quote 산식, 재고 소진/no-bid event horizon, 실제 PC 정보는 확정되지 않았다. 그러므로 이 문서는 실행 가능한 매칭/시장모형 frozen spec이 아니다.

**최종 상태: NOT READY FOR IMPLEMENTATION/TRAINING.** 문서 정합성 수정은 확인했지만 금융 원장·체결·인과성·복구·공격자 테스트는 실행되지 않았다. PC 수용량도 미측정이다. Notion 게시 성공/실패와 무관하게 이 판단은 유지한다. 설계 제안 공개와 M0의 추가 의사결정 작업은 가능하다.


---


<a id="source-agents-system-architect-md"></a>

*원문 모듈: `agents/system_architect.md`*

## System Architect — 반복 검토 지침

### 역할

V0.9 Virtual Market Arena의 데이터/이벤트/정책/시장/원장/진화/운영 경계를 설계하고 다른 제안의 모호한 계약을 찾아낸다. 목표는 수익률 극대화가 아니라 결정론적·재현 가능한 research-only 시스템이다.

### 매 라운드 수행

1. 자신의 판단 전에 실제 코드 심볼과 버전을 확인한다. 기존 V082가 shared input replay인지 endogenous venue인지 구분한다.
2. 사실, 설계 제안, 측정되지 않은 가설, 운영 데이터가 없어 미확인인 사항을 구분한다.
3. mode별 observation timestamp/latency/fill/mark/matching/data contracts를 명시한다.
4. event order와 동일 시각 tie-breaking, RNG stream 분리, bounded queue/backpressure를 검토한다.
5. cash/inventory/reservation/fee/외부 자본 주입의 보존 불변식을 확인한다.
6. 일별 평가 reset, lifetime equity, 세대 계보, 공유 venue 자금의 혼동을 차단한다.
7. 5 valid session 완료 원자성과 16h/8h wall-clock pause/resume를 별개로 다룬다.
8. PC capacity는 실제 사양과 반복 측정 전 확정하지 않는다. GPU·대규모 process pool을 기본 요구하지 않는다.
9. 유사 기능은 기존 모듈 재사용/adapter/신규 필요로 분류하고 기술 비용을 설명한다.
10. 다른 역할과 의견이 다르면 단순 합의 대신 반례·검증 테스트·남은 결정 항목을 기록한다.

### 보호 경계

- 계획 단계에서는 runtime 코드·데이터·인증·서비스·배포·학습 실행을 변경하지 않는다.
- AWS는 기존 수집기/API 용도 유지, arena는 개인 PC에서만 후속 실행을 설계한다.
- V07/V08/V081/V082 원본 코드/결과 재현성과 기존 OOS CLOSED 보존.
- 2026 데이터의 읽기/학습/정책 선택을 금지한다.
- real broker, live order, ARM, token, collector에 연결하지 않는다.
- raw ticks는 LOSSY received events이며 완전한 거래소 tape나 exchange sequence라고 부르지 않는다.

### 권장 산출물

라운드별 독립 보고서: 근거 파일/심볼, 대안 비교, 추천과 조건, 결정론 순서, 회계 불변식, 자원 가설, 복구 계약, 미해결 질문. 후속 보고서는 반박을 수용/거절한 이유와 변경된 결정만 명확히 추가한다.


---


<a id="source-agents-quant-market-md"></a>

*원문 모듈: `agents/quant_market.md`*

## Quant & Market Research 검토 지침

역할은 시장 사실·실험 가설·통계 해석·회계 경제성을 독립 검토하는 것이다. 전략 수익을 보장하지 않는다.

1. 사용자 요청과 실제 저장소의 feature/genome/account/execution/fitness/selection 코드를 먼저 읽는다. 문서상 버전과 실제 감사 commit을 기록한다. 다른 검토자의 결론은 독립 Round1 작성 후 읽는다.
2. 공식 거래소·SEC·FINRA 문서를 우선한다. 논문은 저자/연도/정확한 URL/확인일을 기록한다. HTTP200은 본문확인과 다르다. 실패URL, 현재적용불명확, 문서존재만확인한항목을 구분한다. 검색snippet이나 기억을 법규·실요율로 확정하지 않는다.
3. 미국정규장·장전후·Toss DAY와 가상세계의 시간규칙을 구분한다. venue·계좌·상품별 차이를 생략해서 현실동일성을 주장하지 않는다.
4. 역사replay/내생시장/hybrid의 질문과 한계를 명시한다. 1분봉·LOSSYtick에서 완전호가/큐/거래내역을 복원했다고 주장하지 않는다.
5. 모든 거래의 현금·재고·수수료 상대계정,예약,외부유동성·발행사·보조금·세대자본이체를 추적한다. mark상승과실현가능수익을 구분한다. selftrade·담합·종가조작·무한유동성 exploit을 검토한다.
6. 관측·결정·도착·체결·통보시각과 미체결/부분체결/취소경쟁을 구분한다. 과거이상가격으로 소급체결하지 않는다. 현재bar미래정보를 금지한다.
7. 5session번식은통계적확증이 아니다. 동결상대/독립seed/world단위평가,paired별도세계비교,cross-play,negativecontrol,실패실험보존을 요구한다. population개체수를 독립시장표본수로 부풀리지 않는다.
8. 실제데이터/서비스·토큰·기존결과를변경하지 않는다. 2026FinalOOS닫힘,실주문없음. 구현·학습·배포·git은 상위작업이 명시한 범위만 수행한다.
9. 검토산출물에는 검증사실,미확인사항,상충요구,결정필요사항,진행차단조건을 나눈다. 숫자threshold를성과에맞춰조정하지 않는다.


---


<a id="source-agents-red-team-md"></a>

*원문 모듈: `agents/red_team.md`*

## Red Team 역할 지침

임무: 다른 팀의 성능 목표와 독립적으로 가짜 수익, 누출, 경제적 무결성 실패, 과적합 및 운영 위험을 반증한다. 계획 단계에서는 런타임 코드·데이터·운영 서비스·Git 상태를 변경하지 않는다.

매 검토에서 실제 기준 버전/확인 파일과 미확인 가정을 구분한다. 이익 수치 대신 보존 원장, 시간 인과성, 정산 가능성, 외부 보조금, 공격 정책, 독립 환경 평가를 먼저 확인한다. 기존 AWS 수집·실거래·V0.7~V0.8.2 산출물은 보호하고 2026 Final OOS를 열지 않는다.

출력: Critical/High/Medium/Low별 위치 또는 설계 항목, 실패 메커니즘, 최소 반증 실험, 재현 가능한 통과·실패 조건, 해결 주체, BLOCKED/READY 상태. 알 수 없는 PC 성능이나 가상 수익률을 추정 사실로 쓰지 않는다.

체크리스트: 자기체결/다계좌 담합, MM 무한 유동성, hidden generator leakage, 16h/8h 경계, 현금·재고 보존, 마지막가 펌핑, 재시작/리플레이, 비용/지연/impact, 무거래 중립, 5일 소표본, 생존자 편향, population 증가 환경 변화, 데이터 격리, 실제 시장 이전 격차, 자원·로그 유지 비용.

최종 승인은 문서 합의가 아니라 재현 가능한 구현 검증 단계별 증거에 의존한다. 다른 역할의 답변을 읽기 전 Round 1 독립 검토를 먼저 남긴다. 교차 비판에서 인정한 수정과 남은 차단을 구분한다.


## 검증·출처 메타데이터

### source_manifest.json

```json
{
  "auditedAt": "2026-10-17",
  "branch": "strategy-engine-v3",
  "commit": "24313f4874445fc4749ed8d5fcb9ffd5e04f0ea6",
  "evidenceKind": "SOURCE_STATIC_REVIEW_NOT_EC2_OR_RUNTIME_VERIFICATION",
  "files": [
    {
      "path": "research/evolution/genome.py",
      "sha256": "3b00377d96cdaf8e5b4e8e00b2294496c5ad8fae133f7743e8f936238e9a7f2e",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/population.py",
      "sha256": "dff749d97ebff7d8700536403e54a30e93db2eca4fe7adb4cf374b1544295797",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/features.py",
      "sha256": "70bf3fe1291efa6936516232b13a778c9559ac2ead8bbc048ea6e7f5af194e53",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/indicators.py",
      "sha256": "3c679e6e50232c7cc63c8e98208b9fe4ca7519f371931958834ec9ac935820c8",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/indicator_engine.py",
      "sha256": "2424f62bcdc1ed6b05d33d9c6b0231cf82fe69289ae75976596b670ea1e31cbb",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/account.py",
      "sha256": "b03013486e3cc7c156068fa21810ec78e9774204bf223d8679597ac30124b91f",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/regime_timing.py",
      "sha256": "c00fa3d4eb87f874293878cfd5173e6983f00bfdc605db1c94b9cdc39441536f",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/survival_breeding.py",
      "sha256": "a4f6f1bc83e5cdcef85188a21afa88c5ab4095e3596df5dbf0a00f5ad226a8b1",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/profit_only.py",
      "sha256": "3a13412b9b9e0301081d1371c792eeb21a95241c5642bfdfc3c2e0e31c9b48df",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/config.py",
      "sha256": "90ee0b5d3b692731260f6ee04024b23d42e3467b9167f04b157008e44e162f57",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/data_access.py",
      "sha256": "07a949731bfc993efe959b10f320ece2e8c4a0c12f9d4c3c161d33dcf5893f3c",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/available_data.py",
      "sha256": "dd5d48372416cbbb8ba07b47727a9c3c89e83027624b1b3dbe86901b751dd861",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/run_v07_available.py",
      "sha256": "ed55d82b4910a5aba476942e86335eee0f4972f24a96d43e748c86436372e400",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/run_v07_campaign.py",
      "sha256": "2b86f075588e1f6ff96c14e40c18d5f4019984b5704c6dadd5560a6041a6e188",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/v07_holdout_config.json",
      "sha256": "92b475605112b597272365b44df7614f58d80ccd40864544f9edebb643ce8209",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/run_v081.py",
      "sha256": "985017929dc13152f28596282082cfa68d6f00fc022d81cd1508b69a9b0b1540",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/calibrate_v081.py",
      "sha256": "8483b504ee862bfca77ff96c823a86696cd0ef062d9d71c4e85b5abdc3a50781",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/adaptive_capacity.py",
      "sha256": "5955413ea48b0d735023835a35658ac1d6c1f236e7d630c067362450676a6089",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/run_v082.py",
      "sha256": "314796cb0562da760de5572628b02107e15a6459a9368a75692b7c5854fc7580",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/calibrate_v082.py",
      "sha256": "b139b18ec96045060e82fed9beb294f382590a9f83f195ad9bc5269188945d31",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/v082_indicator_config.json",
      "sha256": "01589c896dd6f4e28c826a5bc8fe8637af840cc27e192278a08070da562925ad",
      "matchesAuditedCommit": true
    },
    {
      "path": "research/evolution/V082_INDICATOR.md",
      "sha256": "89ef6ddd934a903d8f05da9da151c38098e5a0027b6607f1928defd3c1b07653",
      "matchesAuditedCommit": true
    },
    {
      "path": "v082_web_jobs.py",
      "sha256": "8794ea5ae7c987912eb759b15e08f4754e1897a31f849dc9340066c71823ca84",
      "matchesAuditedCommit": true
    },
    {
      "path": "v082_bot_roster.py",
      "sha256": "b801373b973f7607353686727b7aaf7a2027b9d64cd6be9f5beba492a64c01e1",
      "matchesAuditedCommit": true
    },
    {
      "path": "v082-web.js",
      "sha256": "bc2e53f99380600079605323ea089d620c53b9075bd2ec8af9a9f9096703cee0",
      "matchesAuditedCommit": true
    },
    {
      "path": "bot-roster.js",
      "sha256": "6547115d38500c2875301595ccd72eed112bda28e829d416a070107ce473bb40",
      "matchesAuditedCommit": true
    },
    {
      "path": "bot-roster.css",
      "sha256": "eac779b6ec2d928a79d3f8234aaa9eefc3ee40041c008609466d1fe30ad73db5",
      "matchesAuditedCommit": true
    },
    {
      "path": "admin.html",
      "sha256": "f01661c9fb88bd33ea55c539ba95382736732ff1e627a1b74d8696eb4166c912",
      "matchesAuditedCommit": true
    },
    {
      "path": "server.py",
      "sha256": "2f3e1816d5f4bb2025c91946db9008864bdbc6f8443534f105d661ef244942e2",
      "matchesAuditedCommit": true
    }
  ]
}
```

### publication_manifest.json

```json
{
  "status": "BLOCKED_NOTION_CONNECTION_REQUIRED",
  "requestedParentUrl": "https://app.notion.com/p/sungwon910/3fc32f2ae7a6805288e8f253fc9cb944",
  "parentVerifiedByApi": false,
  "pages": [],
  "published": false,
  "credentialsStored": false
}
```

### review_registry.json

```json
{
  "reviews": [
    {
      "role": "architect",
      "actualSubagentTask": "/root/v09_architect",
      "round": 1,
      "path": "reviews/architect_round1.md",
      "sha256": "80e4bf6711d458e3a45d895e7b9886272b02a5e7bfe46b69987b475697324505",
      "independentHumanCertification": false
    },
    {
      "role": "architect",
      "actualSubagentTask": "/root/v09_architect",
      "round": 2,
      "path": "reviews/architect_round2.md",
      "sha256": "898125e7547d8513febd36cadb6c981db7a80253a766a96427ae53caa82b078e",
      "independentHumanCertification": false
    },
    {
      "role": "architect",
      "actualSubagentTask": "/root/v09_architect",
      "round": 4,
      "path": "reviews/architect_round4.md",
      "sha256": "cba6f394fa00a2c491b076f7c41fb6223af659208c7781d1c9f2ce66dc0d5b3d",
      "independentHumanCertification": false
    },
    {
      "role": "quant",
      "actualSubagentTask": "/root/v09_quant",
      "round": 1,
      "path": "reviews/quant_round1.md",
      "sha256": "3917dc57111c923053128913e625273d46028a8b384de76005dc62622bd13e33",
      "independentHumanCertification": false
    },
    {
      "role": "quant",
      "actualSubagentTask": "/root/v09_quant",
      "round": 2,
      "path": "reviews/quant_round2.md",
      "sha256": "01283c74477eb4bf738b2da42564b19e5726826a2cc9e73c1bc7fb9e8d483116",
      "independentHumanCertification": false
    },
    {
      "role": "quant",
      "actualSubagentTask": "/root/v09_quant",
      "round": 4,
      "path": "reviews/quant_round4.md",
      "sha256": "1aed2556285e06b58b05c9a01ceb54bc0b9cbd041f16ba11438662a3fcafcbfa",
      "independentHumanCertification": false
    },
    {
      "role": "redteam",
      "actualSubagentTask": "/root/v09_redteam",
      "round": 1,
      "path": "reviews/redteam_round1.md",
      "sha256": "d6bb9b105927c18a5b72e6fbdd4514ed5c96e2277d3d13530b2ed83bd6c81278",
      "independentHumanCertification": false
    },
    {
      "role": "redteam",
      "actualSubagentTask": "/root/v09_redteam",
      "round": 2,
      "path": "reviews/redteam_round2.md",
      "sha256": "54fc58d1392c53c25a19e847a3d3b7501ffcbde6629b3985238fec3df70ee1c6",
      "independentHumanCertification": false
    },
    {
      "role": "redteam",
      "actualSubagentTask": "/root/v09_redteam",
      "round": 4,
      "path": "reviews/redteam_round4.md",
      "sha256": "d2b9d82436409fb666d8cf6c41f13d9318fc483382d412408844c170f3547863",
      "independentHumanCertification": false
    }
  ],
  "round3Integration": "07_reviews.md",
  "finalDecision": "IMPLEMENTATION_NOT_READY_EXECUTION_BLOCKED"
}
```

### validation_report.json

```json
{
  "validationKind": "DOCUMENT_ONLY_STATIC",
  "markdownFiles": 27,
  "reviewReports": 9,
  "localLinksChecked": 60,
  "mermaidBlocks": 8,
  "mermaidRenderingVerified": false,
  "brokenLinksOrFences": [],
  "sourceFilesChecked": 29,
  "sourceFilesUnchanged": true,
  "runtimeTestsRun": false,
  "trainingRun": false,
  "performanceBenchmarksRun": false,
  "EC2Changed": false,
  "NotionPublished": false,
  "finalOosGate": "CLOSED",
  "passed": true
}
```
