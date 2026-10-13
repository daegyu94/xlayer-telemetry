# ADR-0006: 배포된 monitoring의 cluster lifecycle

- 상태: 승인
- 적용 범위: `xltel cluster up/status/down`

## 결정

기존 inventory에 선택적인 node별 `management` 정보를 추가하고, 명시한 SSH 대상·Python·config로 각 host의 기존 lifecycle을 실행합니다. 기존 로컬 `up/down/status`의 범위와 config 우선순위는 유지합니다. 초기 설치·설정 배포·업그레이드는 이번 기능에 포함하지 않습니다.

원격 worker의 시작 전후 identity 검사와 launcher는 같은 `lifecycle.lock`을 공유합니다. Launcher는 검증된 동일 lock descriptor를 받아 기존 `flock`과 PID·시작 시각·boot ID 검사를 유지하고, 관리 child에는 lock을 상속하지 않습니다. Cluster 시작 실패의 rollback은 이번 호출이 새로 시작한 role의 exact process identity가 유지될 때만 실행합니다.

## 이유

각 host에 수동으로 같은 명령을 실행하면 대상 config나 일부 종료를 누락하기 쉽습니다. 별도 SSH 조정 경로를 추가하고 실제 process 관리는 기존 launcher에 위임하면 종료·ownership 구현의 중복을 피할 수 있습니다.

## 실패 경계

전체 cluster에 대한 atomic 실행은 보장하지 않습니다. SSH 인증·timeout·출력 상한·비정상 응답은 node별 실패나 unknown으로 표시하고, 알려진 신규 role만 rollback합니다. 연결 단절 후 원격 실행 완료나 cleanup을 확인하지 못하면 `unconfirmed`로 보고하며 기존 자원을 임의로 종료하지 않습니다. 원격 응답과 launcher 원문 log는 그대로 출력하지 않습니다.

## 검증과 전환

`tests/test_cluster_lifecycle.py`에서 CLI 순서·사전 검증·부분 실패·기존 자원 보존·identity 변경·공유 lock·timeout·출력 상한·SSH session reaping과 격리된 실제 launcher 경로를 검사합니다. `management` 없는 기존 inventory는 validate/render에 계속 사용할 수 있으며, lifecycle에서는 필수 대상 정보를 SSH 실행 전에 요구합니다. 사용 절차와 남은 검증 경계는 [Multi-node](../multi-node.md#cluster-lifecycle)를 따릅니다.
