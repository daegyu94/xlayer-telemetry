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

## Greenfield redesign · 2026-10-12

`019eea5`에서 기존 UI를 읽기 전에 사용자 의사결정과 외부 observability 사례를 정리했습니다. Run을 찾고 진행·지연을 확인한 뒤 Candidate → Evidence → Metric/Timeline으로 이동하는 흐름을 기준으로 네 기본 메뉴와 네 핵심 KPI를 구현했습니다. 상세 설명은 Tooltip·Source disclosure에 배치하고, Grafana theme·UI·Emotion으로 product shell과 공통 컴포넌트를 구성했습니다.

실제 화면을 여섯 차례 검토하면서 KPI 밀도, 후보별 metric 진입, 진단 없음과 후보 없음, 모바일 root 폭과 메뉴 focus, Dark topology 대비, replay polling 경합과 만료 URL의 theme/timezone 보존을 수정했습니다. 13개 route의 52개 캡처, 24개 가독성 캡처, 33개 native panel gate에서 최종 failure는 0입니다. 전체 CPU 1,935개와 frontend 203개가 통과했으며 ClickHouse 6개는 환경 미설정으로 skip했습니다.

Scenario Explorer는 하나의 Grafana에서 Run / Stop / Reset / Replay를 수행합니다. 일곱 시나리오의 입력 서명·예상 duration/candidate와 실제 query·diagnosis·UI를 분리 검사하고, generation별 store/Run ID와 이전 응답 차단을 확인했습니다. 수집 중 Stop과 Reset, expired URL도 실제 UI에서 검증했습니다. 자세한 조건과 남은 실장비·접근성 검증 범위는 [검증 JSON](../validation/dashboard-greenfield-20261012.json), 사용 방법과 Scenario Coverage Matrix는 [Live Demo](../demo.md#scenario-driven-live-demo)를 따릅니다.

최종 `ci_demo.py`도 원래의 validator별 180초 deadline 안에서 전체 순서를 통과했습니다. 지연 request fixture의 browser 재진입을 제거했고, 기존 old-Run KPI/Step assertion은 유지했습니다. 마지막 CI stack까지 종료·정리했으며 기존 사용자 demo와 remote는 변경하지 않았습니다.

## Final product review · 2026-10-12

`e4b68f9`의 clean `main`을 기준으로 제품·데이터 흐름·실제 Grafana를 다시 검토했습니다. 기존 통과 결과를 재사용해 완료 판정을 내리지 않고, fresh synthetic stack과 별도 HTTP/clock fixture에서 아래 문제를 재현했습니다. 이 리뷰의 상세 조건·결과·Pending 범위는 [Final Product Review 기록](../validation/product-release-review-20261012.json)에 있습니다.

| ID | Priority | 재현한 문제 | 수정·회귀 경로 |
| --- | --- | --- | --- |
| R01 | P1 | 다른 시나리오를 선택해도 이전 실행의 검증이 passed로 표시 | 선택한 실행에 stage·error·Stop·replay 진입을 연결; 미실행은 Not started |
| R02 | P2 | 5분 뒤 Scenario 상태 갱신 중단 | 한 번에 한 요청으로 계속 갱신; 실제 browser clock으로 122회 이후 확인 |
| R03 | P2 | 일시적인 replay 확인 실패 뒤 조사 화면 복구 중단 | 동일 generation 재확인·자동 복구; 알려진 controller를 legacy catalog로 대체하지 않음 |
| R04 | P2 | KPI query 진행 중 No data와 관측 없음 설명 | Loading을 구분하고 완료 전 no-observation 설명을 보류 |
| R05 | P2 | Metric library에서 Synthetic 표시 소실 | 검증된 replay 출처를 공통 chrome에서 유지 |
| R06 | P2 | 모바일 시나리오 목록 아래로 실행 버튼이 밀림 | 좁은 화면의 selector·첫 viewport action·URL 이력 보존 |
| R07 | P2 | 키보드로 모바일 메뉴를 열어도 focus는 바깥에 잔류 | 메뉴 focus 진입·Tab 순환·Escape 복귀 |
| R08 | P2 | Investigation 요약이 반쪽 폭이고 버튼이 영역을 넘음 | 실제 DOM과 맞지 않는 CSS 선택자 수정; theme/viewport별 geometry assertion |

첫 재리뷰에서 상태 복구를 확인한 뒤 출처·모바일 탐색 문제를 찾았고, 다음 실제 화면 리뷰에서 요약 배치의 결함을 추가로 확인했습니다. 수정된 동작은 `release_review_validate.py`와 `redesign_validate.py`에 남겨 `ci_demo.py`가 계속 실행하도록 연결했습니다. 지연 HTTP fixture 정리 중의 `Route is already handled`는 제품 실패와 구분했으며, 이미 취소된 route만 정리 과정에서 허용하고 Loading assertion은 유지했습니다.

네 기본 메뉴·네 핵심 KPI와 Candidate → Evidence → Subsystem 구조는 유지했습니다. Scope·단위·baseline·shared resource 해석과 canonical query 경로를 바꿀 근거는 발견하지 못했으며, 큰 Shell 모듈과 compatibility styling의 추가 분리는 후속 유지보수 후보입니다. Synthetic 통과는 실제 process liveness·GPU/VERL 학습·물리 multi-node·RDMA·3FS 성능·전체 접근성 인증을 뜻하지 않습니다.

최종 결과는 Python 3.12.3 / Node 20.20.0 / Grafana 13.1.7 / Playwright 1.56.0 기준입니다. 전체 CPU **1,935 passed / 0 failed / 6 skipped**(disposable ClickHouse 미설정), frontend **203 passed**, Grafana helper **47 passed**, 문서 계약 **10 passed**, typecheck·build·문서 build가 통과했습니다. Fresh stack의 전체 Grafana journey 두 번, 동일 Explorer의 일곱 scenario와 Normal 재생, 별도 세 Job·두 Replica 심층 journey가 통과했습니다. 최종 UI의 52개 route capture·24개 가독성 capture에서 실패는 없었고, native Step 요청 22개 모두 선택 window와 일치했습니다. 남은 재현 결함 없이 CPU/Synthetic 개발 범위를 완료했으며, 이 작업의 service·state는 정리하고 기존 사용자 demo는 보존했습니다.
