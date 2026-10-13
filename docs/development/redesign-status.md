# Redesign status

이 표는 [반복 버그 예방을 위한 최소 실행 계획](redesign-plan.md)의 진행 상태를 기록합니다. `done`은 구현 검증과 로컬 main 병합이 확인된 상태이며, 후속 과제로 옮긴 기존 WP는 완료를 뜻하지 않습니다.

- 최초 Source 기준: `13eb1a04d75c753d95fa399191a3f0c6d0f2585d` (WP-00 시작 시 로컬 main)
- 원문 진단 기준: `6ddce33a763d3de30e306980860d33aa88ab32f1`
- 최소 범위 전환 기준: `5b51e268970f87d76f691c4d492a7a182b48daca`
- 현재 작업: R-03 공통 evidence 통합 검증 완료; R-04 UI 통합 준비
- 다음 실행: R-00 대표 계약 fixture 검증 → R-01
- 기존 최근 검증: WP-05 clean default 14 passed, 전체 2183개 test 수집, 실제 hook·linked worktree gate 4 passed. 범위 전환 시 제품 검증을 다시 실행한 결과가 아님

## 최소 계획

| 작업 | 내용 | 상태 | 기준 SHA | 결과 SHA | 검증·병합 근거 |
| --- | --- | --- | --- | --- | --- |
| R-00 | WP-06 마무리와 계약 baseline | done | `f15590d` | `e671396` | 대표 6 시나리오와 optional ClickHouse failure; 집중 28 passed·통합 문서/fixture 17 passed; 이 상태 커밋까지 main fast-forward |
| R-01 | 진단 도메인 계약 | done | `8dc10af` | 이 상태 커밋 | 238 passed, narrow strict mypy 통과; Rule/LLM window·measurement 검증 연결; main fast-forward |
| R-02 | 현재 signal 정의의 단일 기준 | done | `62bc290` | `e21a844` | 통합 176 passed, catalog 83·LLM/artifact 154 passed, narrow mypy·checkout 밖 wheel smoke·drift mutation 통과; main fast-forward |
| R-03 | 공통 evidence와 단계별 진단 | done | `e21a844` | `11c09dc` | 22 Rule ID·entity/quality/cap·LLM 경계 검증; 초기 통합 `fc3dc46` 전체 CPU 2257 passed·0 skip, 리뷰 수정 후 집중 99 passed·strict mypy; main fast-forward |
| R-04 | Frontend 계약 경계 | todo | — | — | — |
| R-05 | 중복 제거와 완료 증명 | todo | — | — | — |

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
- 같은 parameter의 단독 재실행은 1 passed입니다. 테스트와 telemetry 설정 경로는 기준 commit `13eb1a0` 이후 불변이며, readiness 타이밍 한계로 기록합니다.
- 전체 suite를 다시 실행한 것으로 표현하지 않습니다. Ubuntu/Python 3.10·3.12 CI와 원격 Actions는 미실행입니다.
- WP-06 격리 Git 테스트의 hook 환경변수 누출을 수정했습니다. 공유 Git의 `core.bare`, `user.name`, `user.email`을 원래 값으로 복구했고 `main` 작업 트리를 확인했습니다.

옛 전면 계획 대신 최소 계획 R-00–R-05를 수행합니다.
