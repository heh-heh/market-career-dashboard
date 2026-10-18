# V0.8.3 — 학습할 때만 EC2 Spot 켜기 (Implementation / Gated)

> 2026-10-18. **코드·UI와 오프라인 테스트만 추가. AWS에 Spot/버킷/IAM/AMI/보안그룹/종료 타이머를 생성하거나 활성화하지 않음.** 기존 `t8i.small`, EBS, 실거래, V0.8.2와 45봇 이전 결과를 변경하지 않음. 2026 Final OOS **CLOSED**.

## 사용 방식

```text
기존 AWS t8i.small (24시간 운영)
  ├─ 관리자 → AI → 연구: 기존 2~50봇 설정 유지
  ├─ Spot 설정 확인 (조회, EC2 생성 없음)
  └─ "Spot으로 학습 시작" (명시적 별도 요금 승인)
         ↓ 사전 구성·IAM·신뢰된 번들·시간초과 감시 검사
    EC2 RunInstances Spot one-time, 단 1대
         ↓ 부팅 / 번들 SHA-256 & 버전 검증
    선별된 2022–2023 연구용 데이터 검증
         ↓
    Spot 컴퓨터에서 시험용 자원 산정(3회)
         ↓ 안전 통과 시
    2~50봇 2022–2023 전체 TRAIN, 5일마다 유전자 교체
         ↓ 완료·오류·최대 8시간·Spot 회수 중 하나
    진단/결과 S3 기록 → instance shutdown(terminate)
         ↓
    Spot 꺼짐, 기존 AWS 관리자 서비스는 계속 켜짐
```

Spot은 시스템 시작, 웹 페이지 접근, 리포트 조회, 일반 데이터 수집, 과거 결과 열람으로 **절대로 자동 생성되지 않습니다**. 관리자 인증된 별도 HTTP `prepare → approve → start`와 운영 환경의 **`V083_SPOT_LAUNCH_ENABLED=YES`**, 독립 종료 타이머 활성 조건이 모두 충족되어야 합니다.

## 구현된 코드

| 경로 | 기능 |
|---|---|
| `research/spot/v083_spot_lifecycle.py` | `plan/status/launch/stop/reap`. 일회성 Spot 1대, 비용/시간/인스턴스 종류 제한, IMDSv2, 암호화 gp3, 종료 시 삭제, 사용 중 중복 실행 차단 |
| `research/spot/prepare_v083_spot_bundle.py` | **로컬 준비 전용**: 기존 audit/selection 검증 후 소스·데이터를 새 경로로 재기록, 해시 계산, 번들 생성. 2026 행과 24GiB 초과 파일 입력 거부 |
| `research/spot/v083_spot_worker.py` | 워커에서 **반복 자원 검증 → 통과 시 TRAIN**, 결과/로그 생성. 모의 단계와 독립 저장 |
| `research/spot/systemd/v083-spot-reaper.service` + `.timer` | 기존 AWS가 **5분 간격으로 만료된 Spot만 강제 종료**. 아직 설치/활성화하지 않은 템플릿 |
| `v083_web_jobs.py` | 기존 관리자 인증을 통한 plan/status/stop 및 새 `v083_spot_control.py`의 비용·생성 분리 승인, 현재 봇 수/기존 작업/source pin 검증 |
| `admin.html`, `v083-web.js` | Spot 수동 상태 확인·유료 시작 승인·중지 버튼. 미구성 시 시작 불가 |
| `research/test_v083_spot_lifecycle.py` | AWS 호출이 없는 모의 시작/중단/중복 실행/최대 시간/비용/아카이브 무결성/타이머 테스트 |

### 실제 비용에 대한 필수 조건

- 1대만 허용. **8시간 이하**, 인스턴스 8~16 vCPU allowlist(`c6i.2xlarge` 우선), max Spot bid와 예산 목표 1회 최대 $2 예시를 서로 검증.
- Spot 서버는 On-Demand 대체, ASG 자동 교체, 무제한 재시도, 예약 반복 실행을 지원하지 않음.
- OS 부팅 후 스크립트는 `timeout`으로 학습을 종료하고 `shutdown -h now`으로 **Terminate** 행동 실행. EBS `DeleteOnTermination=True` + `Encrypted=True`.
- 부팅 실패/스크립트 정지에도 대비해 **별도 systemd 5분 종료 타이머**를 설치하고 실제 활성 상태를 확인해야만 Spot launch 허용. 단, 타이머와 네트워크/권한이 동시에 고장 나면 과금이 계속될 위험이 있으므로 AWS Budgets/Cost Explorer에서 별도 확인.
- $2 한도는 **최대 입찰가 × 8시간의 컴퓨팅 상한 목표**이지 실제 AWS 청구서 전체의 절대 상한이 아님. gp3, S3, S3 요청, 네트워크, 퍼블릭 IPv4, 세금 및 타이머 오차는 별도.
- 도쿄 `c6i.2xlarge` Spot 실제 조회 표본 $0.1964/시간(2026-10-18, 1a). 8시간 = 약 $1.57 **컴퓨팅만**. 실제 요청 직전 다시 가격 및 잔여 예산을 확인할 것.

## 아직 유료 실행 전에 준비해야 할 것

1. **비공개 연구 데이터 버전드 S3**. 번들 S3 메타데이터 `sha256`, `source-commit`과 정확한 Object `VersionId` 필요. 기존 2TiB EBS 복제하지 않음. 코드가 기존 파일 전체를 복사하지 않으며, 원본 데이터에 2026 행이 포함되어 있으면 안전상 번들 생성 차단. 필요한 경우 2025까지의 자료만으로 다시 audit/plan을 만들어야 함.
2. **검증된 x86_64 Spot AMI**. AWS CLI, Python 3.11+ (tar filter 지원 여부 확인), `/opt/v083/venv/bin/python`, 연구 의존성이 미리 설치되어야 함. 부팅 userdata 다운로드 가능하도록 네트워크와 IAM 준비. 이 AMI 자체는 아직 생성하지 않았음.
3. **전용 IAM 인스턴스 프로필, 보안 그룹, 서브넷**. 브로커 비밀번호/실거래 권한 없음. 외부에서 SSH/웹 인바운드 차단, S3 버전드 번들 읽기 및 해당 job prefix 결과 쓰기만 허용. 기존 서비스 IAM 권한과 분리. AWS API 자원 조회/생성/종료를 위해 기존 관리자 인스턴스 역할에도 **태그 조건을 가진 최소 권한**이 필요하며, 이는 아직 허용하지 않았음.
4. **독립 타이머 설치 및 테스트**. `systemd v083-spot-reaper.timer`가 `active`인지 확인; 운영 환경 변수 `V083_SPOT_REAPER_READY=YES`과 `V083_SPOT_LAUNCH_ENABLED=YES`는 모든 검토와 실제 유료 실행을 승인받은 다음에만 설정.
5. **체크포인트 복구**. `--resume-from`은 완료 세대의 RNG·유전자·확정 원장만 새 실행으로 복원한다. Spot은 불변 S3 commit reference로 복구하며 새 job ID, 이전 prefix 읽기 IAM 검토, 신규 비용·생성 승인이 필요하다. 자동 재생성은 하지 않는다. 실제 회수 검증은 별도 승인된 pilot에서 필요하다.
6. **대시보드 결과 연동**. 실제 `output/live_status.json`의 학습·체크포인트 상태를 전용 Spot 영역에 표시한다. S3 조회 비용은 `monitor_reads_approved=true` 별도 검토가 필요하다. 기존 로컬 세대 그래프·원본 결과에는 자동 병합하지 않는다.

## 현재 비활성 설정

`v083_spot_config.example.json`은 **enabled=false** 샘플이다. 개인정보/실제 IAM/정확한 내부 네트워크/S3 bucket 정보 없이 그대로 Git에 둔다. 실제 준비 시 전용 EBS의 `/mnt/research/spot-control/v083-spot-config.json`에만 저장하고, `enabled=true`, 예산, 버전, SHA, 현재 V0.8.3 `campaign_job`·`source_commit`을 모두 확인한다. 이 파일의 AWS 식별자들을 챗/공개 GitHub에 업로드하지 않는다.

예시 승인 전 로컬 명령 (실제 컴퓨팅/인프라 생성 없음):

```bash
python research/spot/v083_spot_lifecycle.py plan --config /mnt/research/spot-control/v083-spot-config.json
python -m unittest research.test_v083_spot_lifecycle -v
```

**실제 운영에 서버 생성/요금이 발생하는 명령은 별도 승인이 필요한 다음 단계의 운영 절차로 남긴다.** 이 문서를 생성하거나 GitHub Actions를 실행하는 것만으로 인스턴스는 시작되지 않는다.

## 검증 상태와 출시 조건

- P0 설계 + launch/worker/bundle/reaper **코드 작성**.
- 모의 AWS 단위 테스트 통과 전에는 EC2 백엔드 배포 금지.
- P1 중단 후 재개 기능 구현 + 무결성·OOS/장부 테스트 통과 전에는 **실제 장기 학습 승인 금지**. 오프라인 중단 복구 테스트는 추가됐으며 실제 인프라 검증은 남아 있다.
- 이후 AMI, S3, IAM, 네트워크, 종료 타이머를 실제로 생성하는 행위는 사용자가 명시적으로 허락해야 함.
- 첫 유료 시험은 최대 1대, 시간·비용 상한 재확인, 실제 시간/비용/안전성 기록, 종료 및 정리 여부 수동 재검증.

GitHub의 이 설계/코드가 곧바로 Spot 사용 가능하다는 의미는 아니며, AWS 관련 서비스 또는 유료 구성은 현재까지 **변경 없음**.

현재 구현·검증·비용 및 남은 운영 절차는 [V083_SPOT_READINESS.md](V083_SPOT_READINESS.md)를 우선 참고한다. 과거 가격 표본은 이번 개발 세션에서 재조회한 값이 아니다.
