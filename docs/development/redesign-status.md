# Redesign status

이 표는 [반복 버그 예방을 위한 최소 실행 계획](redesign-plan.md)의 진행 상태를 기록합니다. `done`은 구현 검증과 로컬 main 병합이 확인된 상태이며, 후속 과제로 옮긴 기존 WP는 완료를 뜻하지 않습니다.

- 최초 Source 기준: `13eb1a04d75c753d95fa399191a3f0c6d0f2585d` (WP-00 시작 시 로컬 main)
- 원문 진단 기준: `6ddce33a763d3de30e306980860d33aa88ab32f1`
- 최소 범위 전환 기준: `5b51e268970f87d76f691c4d492a7a182b48daca`
- 현재 작업: 최소 계획 R-00–R-05 완료; 구현과 검증을 로컬 main에 통합
- 다음 실행: 없음. 별도 실환경·원격 검증과 deferred 과제는 자동 확장하지 않음
- 기존 최근 검증: WP-05 clean default 14 passed, 전체 2183개 test 수집, 실제 hook·linked worktree gate 4 passed. 범위 전환 시 제품 검증을 다시 실행한 결과가 아님

## 최소 계획

| 작업 | 내용 | 상태 | 기준 SHA | 결과 SHA | 검증·병합 근거 |
| --- | --- | --- | --- | --- | --- |
| R-00 | WP-06 마무리와 계약 baseline | done | `f15590d` | `e671396` | 대표 6 시나리오와 optional ClickHouse failure; 집중 28 passed·통합 문서/fixture 17 passed; 이 상태 커밋까지 main fast-forward |
| R-01 | 진단 도메인 계약 | done | `8dc10af` | `62bc290` | 238 passed, narrow strict mypy 통과; Rule/LLM window·measurement 검증 연결; main fast-forward |
| R-02 | 현재 signal 정의의 단일 기준 | done | `62bc290` | `e21a844` | 통합 176 passed, catalog 83·LLM/artifact 154 passed, narrow mypy·checkout 밖 wheel smoke·drift mutation 통과; main fast-forward |
| R-03 | 공통 evidence와 단계별 진단 | done | `e21a844` | `11c09dc` | 22 Rule ID·entity/quality/cap·LLM 경계 검증; 초기 통합 `fc3dc46` 전체 CPU 2257 passed·0 skip, 리뷰 수정 후 집중 99 passed·strict mypy; main fast-forward |
| R-04 | Frontend 계약 경계 | done | `dda036e` | `3680d28` | 통합 frontend 215 passed·typecheck/build, helper/catalog/docs 71 passed; 실제 clock/상태/URL/cache Light/Dark·390px 통과; main fast-forward |
| R-05 | 중복 제거와 완료 증명 | done | `76bb823` | `9eac5a1` | 아래 최종 검증 모두 통과; 구현 main fast-forward·소유 서비스/작업 worktree 정리 |

## 기존 Phase 0 이력

아래 완료 기록은 범위 전환 전에 기록된 구현·검증 이력입니다. WP-06 완료는 최신 main에서 확인했습니다. 과거 제품 검증과 이번 작업의 검증 결과를 구분합니다.

| WP | 작업 | 상태 | 기준 SHA | 결과 SHA | 검증 | 병합 근거 | 장애물·후속 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| WP-00 | 실행 계획 반입과 기준점 | done | `13eb1a0` | `b0351124495c3e8541ce6537987c0de9b57b9872` | 문서 test 10 passed, docs build 성공 | 로컬 main에 `b035112` fast-forward 병합 | — |
| WP-04 | 재현 가능한 개발 의존성 | done | `46b8290` | `302aa2fb0d3f2ae912eda45e2a4e8651c3728d07` | 새 venv 설치, 설치·script 14 passed, CPU 2116 passed/38 skipped, CI helper 52 passed, 문서 10 passed/build 성공 | 로컬 main에 `302aa2f` fast-forward 병합 | 38 skip의 실제 backend 검증은 후속 gate에서 확인 |
| WP-02 | Test 환경 격리 | done | `9995ccb` | `aec2ad667e3cf3f3839fd2b12bb16e2d7931de84` | clean/오염 CPU 각각 2117 passed·38 skipped, test별 상태 2155개 동일, CI helper 53 passed, compileall·shell 통과 | 로컬 main에 구현 `aec2ad6`·병렬 계획 `19f2add` fast-forward 병합 | backend-enabled 실검증은 후속 gate |
| WP-01 | Sandbox first-seen device 계약 확정 | done | `efde642` | `db2a3893be6f849b01346c1239f553b6a5dc39ca` | first-seen·unreadable·all/partial reset 통합 후 35 passed, compileall·diff 통과 | 최신 main merge commit 후 `4bf771f` fast-forward | 실제 cgroup device 검증은 별도 Pending |
| WP-03 | HTTP proxy 정책 정합 | done | `efde642` | `d933cc16c59b55e21528cd6a1716c7c1f7d9bd57` | 관련 HTTP·redirect·deadline 127 passed, compileall·diff 통과 | 로컬 main에 `d933cc1` fast-forward 병합 | — |
| WP-07 | Bounded shutdown과 ownership | done | `efde642` | `41115699a26206cb5f35110992234d0af14fcfaf` | ownership·signal·late child 통합 후 19 passed, shell·diff 통과 | 최신 main merge commit 후 `80e98e8` fast-forward | 실제 1,800-process host 미검증 |
| WP-08 | Pressure card의 evidence·단위 연결 | done | `a4cdd5e` | `a1154e509175fdd7fedb7bd8b2a27132d34c6454` | npm 206 passed, typecheck/build, Python helper 52 passed, 병합 후 격리 browser 10 checks/0 failures·390px 확인 | 최신 main merge commit 후 `365ce89` fast-forward | Synthetic·주입된 RDMA fixture; 실제 하드웨어 검증 Pending |
| WP-05 | 단일 gate와 hooks | done | `f42302a` | `003413cf53e13132dfd75c59a40d524154ce1012` | clean default 14 passed, test 수집 2183, hook·linked worktree 4 passed, shell·diff 통과 | WP-08 통합 후 `003413c` fast-forward, 활성 hook 설치 | full의 실제 backend/browser 결과는 Phase 경계·최종 gate에서 통합 확인 |
| WP-06 | CI gate 연결 | done | `5b51e26` | `6f21b5d97df76df2d3bf5caa50205120eaafd34a` | 집중 gate 6 passed, Git 변수 오염 재현 6 passed, YAML·Bash 통과; Phase 경계 결과는 아래 기록 | 복구 branch `6f21b5d` 로컬 main fast-forward | 원격 Actions·branch protection 미실행; Git 설정 복구 완료 |

## 기존 미완료 WP 재분류

전체 계획의 기존 todo 목록을 아래 범위로 교체합니다. 모든 옛 WP를 done으로 만드는 조건은 폐기하고, 최소 계획 G.4를 새 완료 기준으로 사용합니다.

| 기존 WP | 처리 | 이어지는 작업 |
| --- | --- | --- |
| WP-10–12 | 관련 경로만 축소 통합 | R-00·R-05 |
| WP-20–23 | 진단 계약과 좁은 타입 검사로 통합 | R-01·R-03 |
| WP-30–32 | 현재 signal metadata와 소비자 drift 검사로 축소 | R-02·R-04 |
| WP-40–45 | 공통 evidence·오류 경계·pipeline으로 통합; transport 전면 교체는 후속 | R-03 |
| WP-46 | 필요한 row 경계와 old artifact 검증으로 축소 | R-01·R-03·R-04 |
| WP-60–62 | 상태/reason/identity 경계로 축소 | R-04 |
| WP-80–81 | 이번 변경 정리와 최종 검증으로 축소 | R-05 |
| WP-13·33–34·50–52·70–72 | deferred; 이번 완료 조건에서 제외 | 계획의 후속 과제와 재개 조건 참조 |

## 별도 실환경·원격 검증

| 검증 | 상태 | 재개 조건 |
|---|---|---|
| 실제 GPU/VERL 단일 노드 | Pending | 전용 workload·환경과 측정 승인 |
| 물리 multi-node·RDMA·운영 3FS | Pending | 격리 가능한 실제 cluster·source 준비 |
| 실제 LLM 추론 | Pending | 모델 endpoint와 입력 정책 준비 |
| GitHub Actions 활성화·branch protection·원격 게시 | Pending | 명시적 원격 작업 요청과 권한 |

## Phase 0 경계 검증

WP-06 통합 source는 `6f21b5d97df76df2d3bf5caa50205120eaafd34a`입니다.
Python 3.13.3과 실제 Prometheus/promtool 3.5.0에서 전체 CPU suite를 한 번 실행했습니다.

- 전체 결과: 2,179 passed, 6 skipped, 1 failed.
- Skip 6개는 소유한 ClickHouse 인스턴스가 필요한 실제 SQL 검사입니다.
- 초기 실패: `test_wide_info_metric_scrape_obeys_generated_label_budget[40-0]`에서 scrape 완료 직후 `up` sample 조회가 빈 결과였습니다.
- 같은 parameter의 단독 재실행은 1 passed입니다. 당시 테스트와 telemetry 설정 경로는 기준 commit `13eb1a0` 이후 불변이었으며, readiness 타이밍 한계로 기록했습니다. R-05에서 TSDB에 `up` sample이 commit될 때까지 같은 deadline 안에서 기다리도록 교정했고 기존 label-budget assertion은 유지했습니다.
- 전체 suite를 다시 실행한 것으로 표현하지 않습니다. Ubuntu/Python 3.10·3.12 CI와 원격 Actions는 미실행입니다.
- WP-06 격리 Git 테스트의 hook 환경변수 누출을 수정했습니다. 공유 Git의 `core.bare`, `user.name`, `user.email`을 원래 값으로 복구했고 `main` 작업 트리를 확인했습니다.

옛 전면 계획 대신 최소 계획 R-00–R-05를 수행합니다.

## 최소 계획 최종 검증

구현은 `9eac5a1afa21cb48ac397cd4ded4acae72e2f414`까지 로컬 main에 fast-forward 병합했습니다. 아래 CPU 검증 source는 `16b7d0fcdb6e8db5d562d470d5348c10ad2b6a89`이며, 이후 변경은 RDMA browser fixture의 structured identity 한 줄입니다. 해당 fixture 변경은 관련 helper/catalog/UI 검사 69 passed와 같은 실제 Grafana assertion으로 확인했습니다.

| 검증 | 환경·범위 | 결과 |
| --- | --- | --- |
| 최종 CPU | Linux x86_64·standalone CPython 3.10.22, 실제 Prometheus/promtool 3.5.0·ClickHouse 25.1.5.31 | 2273 passed·0 failed·0 skipped |
| 최종 CPU | 독립 venv·standalone CPython 3.12.15, 같은 backend와 fixture/assertion | 2273 passed·0 failed·0 skipped |
| Frontend | 전체 npm test, typecheck, production build | 215 passed; typecheck/build 성공 |
| CI helper·drift·문서 | 통합 helper/catalog/docs 71개; 마지막 fixture 영향 검사 69개 | 모두 통과 |
| Narrow mypy | 공통 observation·pure decision 모듈 strict; 기존 catalog 일반 검사 | 모두 통과 |
| 문서·generator | Sphinx build, pinned D2 0.9.0, 기존 contract drift mutation 검사 | build 성공; 26 diagrams checked·0 changed |
| 설치본 smoke | checkout 밖 독립 wheel/venv·CLI·94 signal catalog·alias·canonical assets·zero/no baseline·structured identity | 통과 |
| 실제 Grafana | Clock 제한/회복, error/stale/unknown/표본 없음/zero, native query, optional/required retry, Reload/Back/Forward, 공유·A/B 선택, cached Scene, Light/Dark·390px | Clock·context·cache·10 workflow 검사 모두 통과; browser error 없음 |
| 기본 gate·syntax | check.sh default, compileall, tracked shell bash -n, diff check | 모두 통과 |

최초 두 CPU matrix 실행은 각각 2271 passed·1 failed였습니다. 실패는 설치 fixture의 interpreter 복사 방식과 checkout `PYTHONPATH`가 새 설치 환경으로 전달되는 문제였으며, POSIX venv CLI와 같은 symlink 방식 및 fixture 안의 `PYTHONPATH` 제거로 교정했습니다. 기존 public installer assertion을 유지했고, 두 버전의 집중 검증 후 전체 matrix를 다시 실행한 결과가 위 표입니다.

Docker daemon 접근이 없어 disposable native ClickHouse의 loopback HTTP 경로로 같은 SQL fixture와 assertion을 실행했습니다. 실제 `ThreeFSClient` 요청을 사용했으며 기존 Docker 경로를 제거하지 않았습니다. Ubuntu 22.04/24.04의 원격 CI 실행을 검증한 것으로 표현하지 않습니다.

최초 최종 clock browser 재실행은 고정 demo 관측이 기본 5분 창 밖으로 나가 실패했습니다. 최종 source로 fresh owned demo를 다시 생성해 같은 assertion을 통과했습니다. Structured identity 추가 후 RDMA synthetic fixture가 이전 evidence의 identity 객체를 상속한 오류도 수정했으며, 기존 same-entity assertion을 약화하지 않았습니다.

WP-02의 clean/오염 환경 비교 이력은 위 Phase 0 표에 보존합니다. 명시적 test harness인 ClickHouse URL만 환경 격리의 허용 목록에 추가했으며 사용자 runtime 설정의 격리는 유지합니다. CPU fixture·synthetic Grafana·실제 query/protocol 검증은 GPU/VERL, 물리 multi-node·RDMA·운영 3FS, 실제 모델 추론을 대신하지 않습니다.

이번 작업의 Grafana·Prometheus·Loki·ClickHouse process를 종료하고 소유한 모든 port가 닫힌 것을 확인했습니다. 작업자 worktree와 main에 병합된 작업 브랜치만 정리했으며, 기존 사용자 브랜치·worktree와 과거 incident 보존 브랜치는 유지했습니다. 검증 XML/log·browser JSON/screenshot·설치본 smoke와 cleanup 근거는 `/tmp/xlayer-redesign-evidence/summary.json`과 같은 디렉터리에 보존합니다. Remote push·Actions·branch protection은 수행하지 않았습니다.
