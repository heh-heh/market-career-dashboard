# 신규 후보 사전등록 상태

**BLOCKED_PENDING_QUANTIFIED_BOTTLENECK_REVIEW**

신규 후보는 아직 선택하거나 구현하지 않았다. 기존 IR1-R1은 보존한다.
EC2 원본 artifact는 이 세션에서 읽을 수 없다. 사용자가 전달한 성과 요약은
gate별 입력·탈락 건수나 개별 거래의 원인을 제공하지 않으므로, 신규 후보의
경제적 가설을 그 요약에 사후 맞추지 않는다.

먼저 `analyze_v4_frequency.py`로 기존 end campaign의 terminal reasons,
IR3 clock coverage, history 부족, 연도·종목·regime별 분포, R1 신규·소실
trigger를 확인한다. 기록되지 않은 조건은 계측한 짧은 replay로 확인한다.

이후에만 최대 두 후보를 다음 형식으로 사전등록한다.

1. 수치와 분모가 확인된 지배적 병목 gate.
2. 가설 및 경제적/시장 구조적 이유.
3. 변경하는 gate 하나와 보존하는 baseline·비용·exit 규칙.
4. 예상 빈도 변화 및 신규 admission의 정의.
5. 실패 모드와 폐기 기준.
6. 고정된 chronological/OOS 평가 계획; 검색이나 결과 기반 재튜닝 금지.

30건 미만은 강한 결론을 내리지 않으며, 50건 미만은 production-ready로
분류하지 않는다. 50건 이상이어도 OOS·기간·종목·비용 안정성을 확인해야 한다.
최근 완전 OOS에서 계속 0건이면 전체 거래 수 증가만으로 개선으로 채택하지 않는다.
5년 동안 계속 10건 안팎이면 현재 opportunity 정의는 실전 적용에 부적합하며
재설계 대상으로 분류한다. 거래 빈도 제약과 edge 존재 여부는 별도로 판단한다.
