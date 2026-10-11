# Quality Backlog

2026-10-11 첨부 코드·시각 리뷰를 remote `main` `3e48c0e`에서 다시 확인하는 기록입니다. Severity는 영향과 재현 근거를 기준으로 판단하며 원래부터 존재한 결함과 작업 중 발생한 regression을 구분합니다. 전체 제품 목표는 [Quality Goals](quality-goals.md), 실장비 검증 범위는 [Validation](../validation/README.md)을 따릅니다.

## Baseline

| Item | State / evidence |
| --- | --- |
| Revision | `3e48c0e` — investigation context recovery 및 regression safeguards |
| Earlier code review F1–F6 | Optional failure / invalid time / Step limit / Timezone / tab history / conflicting ID 보호가 baseline에 구현됨; 이번 실제 journey에서 재확인 |
| Frontend | 191 tests passed, typecheck / production build passed |
| Python / Grafana helper | Baseline 1,949 passed / 24 skipped; final actual promtool 포함 1,971 passed / ClickHouse 6 skipped |
| Browser | Grafana 12.1 baseline과 13.1.7 target의 격리 synthetic SDK → Prometheus / Loki → Scenes 검증 |
| Hardware | 실제 GPU / RDMA / 3FS / 물리 multi-node Agent RL은 Pending |

## Active Findings

| ID | Priority | Reproduction / acceptance | Status |
| --- | --- | --- | --- |
| Q01 | P1 | Query 없는 Native Text의 source 본문·context link | Verified — static Scene data와 실제 body/link assertion |
| Q02 | P1 | Prefix-cache / IOPS의 terminal 상태·identity | Verified — activation 전 subscription·cached frame reread·owning panel activation |
| Q03 | P1 | 1·4·16 Worker의 값·unit·identity; 평균 생성 금지 | Verified — raw labels → merge → table, 측정값 0·긴 이름·마지막 행; age 300/900 threshold 보존 |
| Q04 | P2 | Current / Baseline / Change / quality 접근 | Verified — 전체 폭·Scope 상세·명시 column role; 1440/1024/390px |
| Q05 | P2 | Lane과 하단 시간축 | Verified — 1/7/20 lane height 계약·실제 canvas bounds·24px overflow reserve |
| Q06 | P2 | Metric dimension과 resource dimension 구분 | Verified — `_field` / `_query` 보존, Source / Scope 상세와 선택 안내 |
| Q07 | P2 | 단일 / 다중 resource의 기본 mode·선택 metric | Verified — 첫 Ready에서 결정; Loading / Error / empty는 기본 선택을 확정하지 않음 |
| Q08 | P2 | Step-first label·full identity·Timezone | Verified — full tuple로 두 Cluster의 dropdown/table/window 검사; 전체 range 표시 |
| Q09 | P2 | Discrete 상태·gap·Unknown / zero 구분 | Verified — 5개 status-history; Ray count·XID·p99 points는 원래 의미 보존 |

## Loop Evidence

### Loop 1 — Rendering and Data Lifecycle

Baseline에서 Text 제목만 표시·Worker Stat 겹침·prefix-cache와 IOPS의 Loading 잔류를 캡처했습니다. Query 없는 Text의 readiness와 activation/subscription 순서를 수정하고 source body, link, populated native plot을 실제 Grafana에서 확인했습니다. 새 scene data 계약은 동기 activation·cached terminal frame·Error·unsubscribe를 검사합니다.

### Loop 2 — Reading and Investigation Flow

Raw Worker 표, 전체 폭 comparison, Timeline 높이, metric/resource 안내, 첫 Ready mode와 Step-first range 표시를 적용했습니다. Actual multi-worker journey와 13개 경로의 52개 after capture를 확인했으며 모든 canonical panel ID·query target은 baseline과 같았습니다.

### Loop 3 — Visualization and Regression Review

재평가에서 Worker frame selector·값 열 위치, Timeline canvas의 5px overflow, comparison header role, 제거된 age threshold와 기존 cluster 테스트의 label-prefix 의존을 추가로 발견해 수정했습니다. 제품의 full identity와 두 Cluster/window assertion은 유지했고 test는 display text 대신 full tuple로 선택합니다. 첫 validator의 잘못된 endpoint/node·초기 Loading 확인·저장 catalog 준비 누락은 test setup 문제로 구분해 수정했으며 assertion의 기대 결과를 낮추지 않았습니다.

최종 Node 20.20.0의 Frontend 196 tests·typecheck/build, Python 3.12.3의 actual promtool 포함 1,971 tests, single-worker CI journey·multi-worker·multi-job·context recovery가 통과했습니다. Panel gate 33개, readability 30개 캡처에서 failure는 0이며 DOM text의 기준 미달 contrast·12px 미만 글자도 0입니다. 동일 Grafana 13.1.7·backend·Step/window·viewport의 Before/After를 비교했고 검증한 범위에서 Browser page error는 0입니다. 상세 수치·조건·artifact 경로는 [Validation Record](../validation/dashboard-quality-20261011.json)에 있습니다.

## Gate Assessment

현재 수정 범위의 Functional / Telemetry / Regression hard gate와 Browser / Theme / viewport gate는 CPU / Synthetic 범위에서 Verified입니다. 실제 분산 Agent RL·hardware 성능·장시간 운영·전체 accessibility는 아래 Pending을 유지하며 제품 전체가 production-ready라고 선언하지 않습니다. GitHub Actions의 Node 22 결과는 이 기록의 로컬 Node 20 검증과 별개입니다.

## Deferred Validation

| Item | State / next evidence |
| --- | --- |
| 실제 VERL multi-job × multi-node × replica | Pending — 물리 배포·request / policy identity와 telemetry 연결 확인 |
| GPU / RDMA / shared Storage 정확도·overhead | Pending — 실장비 counter와 실제 부하의 비교 |
| 3FS collection points | Synthetic populated plot과 실제 ClickHouse / service population을 별도 검증 |
| 전체 accessibility | Browser / DOM / keyboard 표본 검증; screen reader·모든 canvas hover·색각 조건 인증은 별도 |
| Long history startup / RSS | 측정 필요; `_seen`을 무조건 축소해 restart deduplication을 약화하지 않음 |
| Histogram Heatmap / saved stage bars | 후속 개선 — 호환 bucket·population·query 비용 또는 명시 saved duration 조건을 확인한 뒤 구현 |
| XID change-history / compact phase columns | 후속 UX — 실제 사용 근거와 코드/health·clock/phase 의미를 보존하는 조건부터 검증 |
