# V0.8 웹 관리자 운영 가이드 (기존 V0.7 결과 보존)

## 웹 화면과 역할

GitHub Pages 관리자 AI → 연구에서 V0.8이 기본적으로 보입니다. V0.7은 화면에서 숨기지만 기존 코드·연구 산출물·관리자 콘솔 명령은 보존합니다.

버튼:
- ① 10거래일/100개체 시험 학습 `v08 smoke`
- ② 2022–2023 장기 학습 `v08 train`
- ③ 2024 동결 홀드아웃 `v08 holdout`
- ④ 2025 개발 검증 `v08 validation`
- 결과 ZIP 준비 `v08 export`; `/api/research/v08/download`은 관리자 인증 필수
- `v08 status`, `v08 log`, `v08 report`, `v08 retry <stage>`

버튼은 API 응답 성공/차단 사유를 표시하며, 각 단계가 서버의 상태 파일로 확인되기 전에는 다음 단계를 시작할 수 없습니다. 브라우저 이탈 이후에도 Python worker는 유지되도록 설계했습니다. V0.8 데이터를 만들거나 연구 프로세스를 자동 시작하지는 않습니다.

## 운영 EC2에 설치하기 (수동 실행 필요)

GitHub Pages는 정적 프런트엔드만 배포하므로 EC2 API를 안전하게 한 차례 설치해야 실제 학습이 가능합니다. 운영 Git 체크아웃에 대한 pull/reset/stash/clean, 기존 EBS 삭제, 연구 프로세스 강제 종료를 금지합니다.

먼저 **기존 연구 worker가 실행 중이 아닌지** `v07 status` 와 /proc 조회로 확인하고, `KillMode=process`가 검토 및 적용되었는지 확인합니다. V0.8을 실행할 때 기존 2022~2025 가용 데이터 감사/선별 결과 `selection_plan.json`을 읽기 전용으로 사용합니다. V0.7 현재 job에 `select=completed` 상태가 필요합니다.

AWS Session Manager **Bash**에서:

```bash
TOOLS=/mnt/research/collection-deploy-tools-20261012
git -C "$TOOLS" fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3
git -C "$TOOLS" show origin/strategy-engine-v3:scripts/install_v08_admin_api_overlay.py > /mnt/research/install-v08-web.py
sudo python3 /mnt/research/install-v08-web.py --check
```

`READY` 상태, serverBefore/serverAfter SHA256, controllerSha 및 충돌 여부를 확인합니다. 충돌·설정 오류를 숨기고 강제 진행하지 않습니다. `--check`는 기존 코드/서비스를 수정하지 않습니다. 검토 후 **EC2 API만 한 번 재시작하는 변경**을 승인하는 경우:

```bash
sudo python3 /mnt/research/install-v08-web.py --apply
```

`COMMITTED`와 `apiHealth=PASS`를 확인합니다. 소스 체크아웃은 기존 도구 저장소가 포함하고 있는 **고정 커밋 `eed5a21d3bf7418daf7fc18f042eda43b586c0dc`**을 `/mnt/research/v08-source`에 별도로 둡니다. 작업은 해당 고정 커밋에서만 실행합니다. 이미 해당 경로가 있고 커밋/추적 파일이 다르면 설치를 차단합니다.

설치 도구는 API의 기존 인증된 관리자 콘솔에 V0.8 고정 명령 라우팅, 관리자 인증 ZIP 다운로드, 새 관리 모듈만 추가합니다. 운영 서버의 나머지 API·수집기·모의/실거래·V0.7 연구 파일은 변경하지 않으며, 운영 파일 백업은 `/mnt/research/v08-web-install-backups/`에 남습니다. health 검사가 실패하면 운영 파일을 백업으로 복원하고 API 복구 재시작을 시도하므로 실제 정상 상태는 별도 확인이 필요합니다.

## 웹에서 실행 순서

1. [관리자 페이지](https://heh-heh.github.io/market-career-dashboard/admin.html#ai)에 로그인합니다.
2. **① 100개체 시험 학습**을 누르고, 관리자 AI 화면에서 시작 승인/거부 메시지를 확인합니다.
3. 시험 상태가 완료되고 파일/로그를 확인한 뒤 **② 전체 학습**을 승인합니다.
4. 학습 완료·결과 검증 후 **③ 2024 홀드아웃 → ④ 2025 검증**을 순서대로 실행합니다.
5. 필요하면 결과 ZIP을 준비한 뒤 다운로드합니다.

기존 평가 창(연속 유효 5거래일), 부동소수점 `epsilon=1e-6` USD, 이익 부모만 번식, 다중 부모, 단일 부모 변이 및 부모 부재 시 무작위 회복 규칙은 V0.8 고정 코드가 결정합니다. 2026 Final OOS는 CLOSED 상태를 유지합니다.

## 한계와 안전

- EC2에 실제 접속·배포하지 않았다면 V0.8 웹 학습 작동 성공을 주장하지 않습니다.
- V0.8과 V0.7은 EBS의 별도 작업/결과 디렉터리를 사용합니다. `/mnt/research/v08-web-jobs`, `/mnt/research/evolution-v08-profit-only`; 기존 V0.7 연구는 유지합니다.
- `/mnt/research-extra` 2TB 추가 EBS는 현재 기본 출력 경로가 아닙니다. `--require-ebs` 검사 수정 없이 자동으로 이전하지 않습니다.
- 현재 학습이 끝난 상태/독립 검증 결과가 아니면 성과가 확정됐다고 표시하지 않습니다. 유효하지 않은 검증은 `PARTIAL_UNVERIFIED`로 남습니다.
- 재실행은 실패/중단된 단계에서만 가능합니다. 기존 연구 출력 디렉터리는 삭제하지 않습니다.
