# Market & Career Dashboard

**주식 시장 · 게임/IT 취업 정보 · 기술 동향**을 한곳에서 확인하는 개인용 웹 대시보드입니다. 공개 리포트와 인증된 관리자 기능을 분리하고, 주식 전략 백테스트 및 AI 연구를 별도로 운영합니다.

[**대시보드 열기**](https://heh-heh.github.io/market-career-dashboard/) · [**서버 상태 그래프 (공개 조회)**](https://heh-heh.github.io/market-career-dashboard/server-status.html) · [관리자 로그인 (OTP)](https://heh-heh.github.io/market-career-dashboard/admin.html) · [데이터 수집 현황](https://heh-heh.github.io/market-career-dashboard/collection.html) · [**V0.9 연구 문서**](docs/v09/README.md)

> **프로젝트 성격:** 개인 연구·모니터링 서비스입니다. 주식 분석, 시뮬레이션 및 AI 학습 결과는 투자 수익을 보장하지 않습니다. 공개 화면의 수치와 자료는 데이터 소스·갱신 시각·수집 상태에 따라 달라질 수 있습니다.

## 주요 기능

| 영역 | 내용 |
| --- | --- |
| **종합** | 주식·취업 시장·개발 동향을 요약한 리포트 |
| **주식** | 종목별 시세 정보, OHLC 차트, 반도체·IT 관련 지표 및 뉴스 |
| **취업 시장** | 개발자 취업시장 및 관련 고용 지표 |
| **채용공고** | 최근 채용공고, 직무·경력·기술 스택 필터 |
| **동향** | 게임·IT·반도체 등 개발 및 산업 동향 |
| **관리자** | 거래·모의매매·백테스트, AI 연구, 서버/Git 콘솔, API 관리, 데이터 수집·다운로드 |

공개 페이지는 **GitHub Pages 기반 정적 프런트엔드**이며, 인증이 필요한 관리 기능과 일부 동적 데이터는 **별도 EC2 API 서버**를 사용합니다. 관리자 인증이 필요한 기능은 정적 페이지에 접속하는 것만으로 실행할 수 없습니다.

## V0.9 Virtual Market Arena — 연구 기획 문서

**공유 가상 주문장에서 AI 봇들이 경쟁·진화하는 V0.9 프로젝트**의 기획 및 기술 설계를 `main`에서도 읽을 수 있도록 정리했습니다. **현재 V0.9 실행 엔진은 구현·배포되지 않았으며 문서 검토 단계**입니다.

- [**V0.9 한눈에 보기 (시작 페이지)**](docs/v09/README.md) — 핵심 개념, 아키텍처 다이어그램, 16시간/8시간 가상 사이클, 5세션 번식, 개발 순서
- [종합 기획서](docs/v09/V09_PROJECT_PROPOSAL.md) — 요구사항, 범위, 일정, 리스크
- [기술·검증 설계서](docs/v09/V09_TECHNICAL_DESIGN.md) — 주문장, 원장, 지연, 정책·번식, 테스트
- [에이전트 교차 검토](docs/v09/V09_AGENT_REVIEWS.md) — 독립 의견, 반론, 미해결 위험

> 문서는 `strategy-engine-v3`의 V0.9 설계 원문을 복사해 보존한 것입니다. 연구용 코드·실행 환경·실거래 시스템을 `main`으로 병합하지 않습니다.

## 서버 모니터링과 관리자 접근 구분

- **공개 GitHub Pages:** [서버 상태 그래프](server-status.html)에서 CPU, RAM, 물리 네트워크 수신·송신, 루트·데이터 저장공간과 지원되는 경우 CPU 패키지 전력의 최대 12시간 추이를 읽기 전용으로 표시합니다. 서버의 자세한 식별정보, 파일 경로, 원격 명령 및 API 키는 출력하지 않습니다. 이 조회 데이터 자체는 인터넷에 공개됩니다.
- **홈서버 전용 관리자:** [GitHub Pages 로그인 화면](admin.html)에서 ID·비밀번호·Google OTP를 입력하되 실제 검증 및 작업 화면은 **Tailscale Serve / SSH 전용**입니다. API 키·원격 제어·Git 작업·서비스 조작·세부 로그는 절대 공개 API에 포함하지 않습니다.
- **전력 표기:** Intel RAPL이 제공되면 **CPU 패키지 전력**만 표시하며 **서버 전체 전력량은 측정기를 연결하기 전까지 알 수 없습니다.** 임의 소비전력을 꾸며서 표기하지 않습니다.
- **서버 설치:** [시스템 측정 서비스·공개 읽기 전용 API·관리자 화면 배포 방법](deploy/telemetry/README.md). GitHub 코드 배포만으로 홈서버의 실시간 지표가 자동으로 생기지는 않습니다.

## V0.7 AI 연구

현재 연구 개발은 [`strategy-engine-v3` 브랜치](https://github.com/heh-heh/market-career-dashboard/tree/strategy-engine-v3)에서 진행합니다. 관리자 **AI → 연구** 화면은 기존 여러 연구 패널 대신 **V0.7 가용 데이터 기반 5거래일 학습**에 집중합니다.

- **데이터 감사:** 2022~2025년 연구용 1분봉에 대해 종목별 결측·제외 사유 검사
- **가용 데이터 선별:** 누락된 종목을 영구 배제하지 않고 거래일·관측 시점별 유효성 평가
- **진화 학습:** 유효한 연속 **5거래일**마다 세대 평가·선발
- **학습 기간:** 2022~2023년
- **동결 평가:** 2024년 내부 홀드아웃 → 2025년 개발 검증
- **모니터링:** 진행 상태, 완료 세대, 제외 거래일, 성과 그래프·분석 및 결과 다운로드

**2026 Final OOS는 CLOSED**로 유지합니다. 누락 데이터는 보간하거나 임의 가격으로 채우지 않으며, 불완전한 검증은 `PARTIAL_UNVERIFIED` 등으로 별도 표기합니다. 이 연구는 **오프라인 시뮬레이션**으로, 실거래 엔진이나 주문 실행과 연결되지 않습니다. 실제 데이터에 대한 학습·검증 결과는 서버 산출물을 확인해야 하며, 코드 및 합성 테스트 통과만으로 수익성을 주장하지 않습니다.

자세한 기술 설계와 운영 절차는 다음 문서를 참고하세요.

- [V0.7 가용 데이터 평가 방식](https://github.com/heh-heh/market-career-dashboard/blob/strategy-engine-v3/research/evolution/AVAILABLE_DATA.md)
- [V0.7 장기 학습·검증 운영 안내](https://github.com/heh-heh/market-career-dashboard/blob/strategy-engine-v3/research/evolution/V07_MANAGED_RESEARCH.md)
- [Evolution 연구 엔진](https://github.com/heh-heh/market-career-dashboard/blob/strategy-engine-v3/research/evolution/README.md)

## 프로젝트 구조

| 경로 | 역할 |
| --- | --- |
| `index.html` | 공개 대시보드 |
| `admin.html` | 인증 기반 관리자 UI |
| `collection.html` | 데이터 수집 상태 화면 |
| `server.py` | 관리자·데이터·연구용 백엔드 API |
| `research/` | 백테스트·전략 검증·Evolution 연구 코드 |
| `v07_web_jobs.py` | V0.7 연구 작업 실행·상태 관리 (**연구 브랜치**) |
| `scripts/` | 데이터 수집, 검증 및 운영 지원 도구 |
| `.github/workflows/` | GitHub Pages 배포, 데이터 갱신 및 CI |

> 기본 브랜치 `main`과 연구 브랜치 `strategy-engine-v3`는 파일 구성이 다를 수 있습니다. 위 연구 관련 파일과 상세 문서는 **연구 브랜치 기준**입니다.

## 개발·배포 구분

- **프런트엔드:** HTML, CSS, JavaScript / GitHub Pages
- **백엔드 및 연구:** Python, GitHub Actions, AWS EC2, 별도 연구용 저장 공간
- **브랜치:** `main`은 기본 브랜치, `strategy-engine-v3`는 전략·연구 기능 개발 브랜치
- **Pages 배포:** UI 관련 파일 변경 시 `.github/workflows/pages.yml`에 따라 실행. README만 수정해도 웹사이트가 새로 배포되는 것은 아닙니다.
- **EC2 반영:** GitHub에 코드를 push하는 것과 **운영 서버 배포는 별개**입니다. 운영 저장소의 로컬 변경 및 실행 중인 연구 작업을 확인한 뒤 별도 절차로 적용합니다.

## 데이터·보안 정책

- API 키, 토큰, 비밀번호, 계좌 정보와 서버 전용 `.env`는 저장소에 커밋하지 않습니다.
- 외부 데이터와 채용공고는 각 제공자의 이용약관 및 갱신 정책에 따릅니다.
- AI 연구 결과는 데이터 누락, 선별 편향, 체결 가정 및 비용 모델의 한계를 가집니다.
- 기존 학습 산출물과 원본 데이터는 연구 UI 변경을 이유로 삭제·수정하지 않습니다.
- 관리자 기능 및 결과 다운로드에는 서버 측 인증과 접근 통제가 필요합니다.

## 라이선스

[MIT License](LICENSE)
