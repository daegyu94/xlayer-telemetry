# Quality Goals

XLayer Telemetry의 목표는 사용자가 Run → Slow Step → Baseline → Candidate → Supporting / Counter / Missing Evidence → Timeline / Subsystem을 일관되게 조사하는 것입니다. 기능 구현, CPU / Synthetic validation, 실제 분산 Agent RL validation은 각각의 상태로 관리합니다. 현재 개발 단계와 실환경 계획은 [Development Status](https://github.com/daegyu94/xlayer-telemetry#development-status)와 [Validation](../validation/README.md)을 따릅니다.

## Hard Gates

| Area | Acceptance criteria | Evidence |
| --- | --- | --- |
| Functional correctness | 선택한 Cluster / Run / observer / Step / resource / window / Timezone이 이동·Reload·Back / Forward에서 유지됨 | 실제 Grafana Browser journey와 URL / 결과 assertion |
| Telemetry correctness | Query·unit·statistic·entity·scope 보존; Missing / Stale / Unknown / Query Failure / measured zero 구분 | Canonical source 대조, negative fixture, 실제 datasource 응답 |
| Regression safety | 설명되지 않은 새 실패와 Critical / High regression이 없음 | 같은 환경의 baseline / final test 이름·결과와 같은 commit tree |

Critical 항목은 다른 영역의 개선으로 상쇄하지 않습니다. Shared resource의 시간적 correlation을 Run attribution이나 causality로 표현하지 않으며, 관측하지 않은 수치·health·ownership을 UI에 생성하지 않습니다.

## Product Gates

| Area | Acceptance criteria |
| --- | --- |
| Visualization | 실제 데이터 형태에 맞는 chart; source identity와 원래 aggregation 유지; percentile scalar로 histogram을 생성하지 않음 |
| UI / UX | Light / Dark, 1440px / 1024px / 390px에서 핵심 정보·unit·quality·동작 접근 가능; panel 내부 겹침과 축 잘림까지 확인 |
| Reliability | Optional source 장애 시 core 유지; Loading → Done / Error / empty, retry·route 왕복·fold / unfold 후 stale state 방지 |
| Test quality | 실제 결함의 실패 조건과 성공·오류·경계·복구 검증; 테스트 삭제·assertion 약화로 통과시키지 않음 |
| Maintainability | 기존 Scenes·canonical Dashboard·CLI·backend 재사용; 수정한 계약부터 작은 module로 분리 |
| Documentation | 사용자 명령·지원 범위·현재 구현·검증 결과 일치; 미실행 환경은 Pending |

일반 텍스트의 대비는 WCAG AA 4.5:1, 큰 텍스트는 3:1을 목표로 합니다. DOM typography / contrast 검사와 실제 chart·legend·tooltip·focus 캡처를 함께 확인하며 어느 한 검사만으로 전체 접근성을 인증하지 않습니다.

## Improvement Loop

1. **Audit:** baseline SHA·환경·기존 결과를 기록하고 public 경로에서 재현합니다. 기존 기능, 실제 결함, UX proposal, 실환경 검증 공백을 구분합니다.
2. **Prioritize:** 데이터 해석·회귀 → 사용자 workflow → visualization / layout 순으로 선택합니다. 변경 범위와 완료 조건은 [Quality Backlog](quality-backlog.md)에 기록합니다.
3. **Implement:** 실패하는 회귀 조건을 먼저 고정하고 최소 변경을 적용합니다. Query·URL·identity·운영 guard를 보존합니다.
4. **Verify:** 관련 검사 → 영향을 받는 전체 회귀 → 실제 Grafana / Browser → Theme / viewport 캡처를 수행합니다. 검증 후 수정하면 영향받는 검사를 다시 실행합니다.
5. **Evaluate:** 해결한 문제, 새 회귀, 남은 gate와 다음 우선순위를 기록합니다. 안정적인 영역에는 반복적인 디자인 변경을 하지 않습니다.

같은 agent가 구현·diff review·Browser 평가·test assertion review를 각각 수행할 수 있습니다. Reviewer 단계는 구현 의도보다 diff·관측 결과·실패 조건을 기준으로 판단합니다. Coverage 수치는 로드된 파일·subprocess·Browser 포함 범위를 명시하며 품질의 대리 지표로 사용하지 않습니다.

## Completion and Limits

현재 작업의 필수 gate를 충족하거나 남은 항목이 명시된 후속 개선이면 해당 loop를 종료합니다. 환경 제약·외부 의존성 또는 변경 위험 때문에 필수 gate를 검증하지 못하면 `Blocked / Pending`과 재개 조건을 남기고 통과로 처리하지 않습니다.

CPU / Synthetic / VM 검증은 실제 GPU / VERL·물리 multi-node·RDMA·3FS saturation을 입증하지 않습니다. 실환경 검증 계획은 Validation에 유지하며 매 loop의 결과에는 tests passed / failed / skipped, Browser 범위, capture 경로, cleanup 상태와 변경 commit을 기록합니다.
