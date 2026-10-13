# Redesign status

이 표는 [전체 실행 계획](redesign-plan.md)의 WP 진행 상태를 기록합니다.
`done`은 구현 검증과 로컬 `main` 병합이 확인된 상태입니다. 실환경·원격 적용은 아래 별도 표에서 관리합니다.

- Source 기준: `13eb1a04d75c753d95fa399191a3f0c6d0f2585d` (WP-00 시작 시 로컬 `main`)
- 원문 진단 기준: `6ddce33a763d3de30e306980860d33aa88ab32f1`
- 현재 작업: WP-06 로컬 main 통합 완료; 사용자 요청으로 WP-10 시작 전 일시정지
- 다음 작업: WP-10 공용 fake (미시작; 재개 요청 후 진행)
- 최근 검증: WP-06 집중 gate 6 passed; Phase 0 전체 CPU 2179 passed·6 skipped·1 failed, 유일한 실패 parameter 단독 재실행 통과

| WP | 작업 | 상태 | 기준 SHA | 결과 SHA | 검증 | 병합 근거 | 장애물·후속 |
|---|---|---|---|---|---|---|---|
| WP-00 | 실행 계획 반입과 기준점 | done | `13eb1a0` | `b0351124495c3e8541ce6537987c0de9b57b9872` | 문서 test 10 passed, docs build 성공 | 로컬 main에 `b035112` fast-forward 병합 | — |
| WP-04 | 재현 가능한 개발 의존성 | done | `46b8290` | `302aa2fb0d3f2ae912eda45e2a4e8651c3728d07` | 새 venv 설치, 설치·script 14 passed, CPU 2116 passed/38 skipped, CI helper 52 passed, 문서 10 passed/build 성공 | 로컬 main에 `302aa2f` fast-forward 병합 | 38 skip의 실제 backend 검증은 후속 gate에서 확인 |
| WP-02 | Test 환경 격리 | done | `9995ccb` | `aec2ad667e3cf3f3839fd2b12bb16e2d7931de84` | clean/오염 CPU 각각 2117 passed·38 skipped, test별 상태 2155개 동일, CI helper 53 passed, compileall·shell 통과 | 로컬 main에 구현 `aec2ad6`·병렬 계획 `19f2add` fast-forward 병합 | backend-enabled 실검증은 후속 gate |
| WP-01 | Sandbox first-seen device 계약 확정 | done | `efde642` | `db2a3893be6f849b01346c1239f553b6a5dc39ca` | first-seen·unreadable·all/partial reset 통합 후 35 passed, compileall·diff 통과 | 최신 main merge commit 후 `4bf771f` fast-forward | 실제 cgroup device 검증은 별도 Pending |
| WP-03 | HTTP proxy 정책 정합 | done | `efde642` | `d933cc16c59b55e21528cd6a1716c7c1f7d9bd57` | 관련 HTTP·redirect·deadline 127 passed, compileall·diff 통과 | 로컬 main에 `d933cc1` fast-forward 병합 | — |
| WP-07 | Bounded shutdown과 ownership | done | `efde642` | `41115699a26206cb5f35110992234d0af14fcfaf` | ownership·signal·late child 통합 후 19 passed, shell·diff 통과 | 최신 main merge commit 후 `80e98e8` fast-forward | 실제 1,800-process host 미검증 |
| WP-08 | Pressure card의 evidence·단위 연결 | done | `a4cdd5e` | `a1154e509175fdd7fedb7bd8b2a27132d34c6454` | npm 206 passed, typecheck/build, Python helper 52 passed, 병합 후 격리 browser 10 checks/0 failures·390px 확인 | 최신 main merge commit 후 `365ce89` fast-forward | Synthetic·주입된 RDMA fixture; 실제 하드웨어 검증 Pending |
| WP-05 | 단일 gate와 hooks | done | `f42302a` | `003413cf53e13132dfd75c59a40d524154ce1012` | clean default 14 passed, test 수집 2183, hook·linked worktree 4 passed, shell·diff 통과 | WP-08 통합 후 `003413c` fast-forward, 활성 hook 설치 | full의 실제 backend/browser 결과는 Phase 경계·최종 gate에서 통합 확인 |
| WP-06 | CI gate 연결 | done | `5b51e26` | `6f21b5d97df76df2d3bf5caa50205120eaafd34a` | 집중 gate 6 passed, Git 변수 오염 재현 6 passed, YAML·Bash 통과; Phase 경계 결과는 아래 기록 | 복구 branch `6f21b5d` 로컬 main fast-forward | 원격 Actions·branch protection 미실행; Git 설정 복구 완료 |
| WP-10 | 공용 backend fake와 builders | todo | — | — | — | — | — |
| WP-11 | Golden characterization | todo | — | — | — | — | — |
| WP-12 | Layer 의존성 검사 | todo | — | — | — | — | — |
| WP-13 | 행동 기준 test 재배치 | todo | — | — | — | — | — |
| WP-20 | Observation·Status·Issue | todo | — | — | — | — | — |
| WP-21 | EntityRef·Scope·identity | todo | — | — | — | — | — |
| WP-22 | TimeWindow·clock 모델 | todo | — | — | — | — | — |
| WP-23 | Core mypy | todo | — | — | — | — | — |
| WP-30 | Registry v1 | todo | — | — | — | — | — |
| WP-31 | TS registry와 row schema | todo | — | — | — | — | — |
| WP-32 | Canonical panel key | todo | — | — | — | — | — |
| WP-33 | Registry 기반 문서 | todo | — | — | — | — | — |
| WP-34 | Registry 기반 demo metadata | todo | — | — | — | — | — |
| WP-40 | 구조화된 오류와 bounded retry | todo | — | — | — | — | — |
| WP-41 | Backend 계층과 transport 교체 | todo | — | — | — | — | — |
| WP-42 | Collect·align 경계 분리 | todo | — | — | — | — | — |
| WP-43 | Select·assess와 공통 EvidenceBundle | todo | — | — | — | — | — |
| WP-44 | Evaluate·cap·assemble | todo | — | — | — | — | — |
| WP-45 | Rule/LLM 공통 evidence 경로 | todo | — | — | — | — | — |
| WP-46 | Artifact·schema·공통 row projection | todo | — | — | — | — | — |
| WP-50 | 단일 config resolver와 snapshot | todo | — | — | — | — | — |
| WP-51 | Process identity와 ownership 공통화 | todo | — | — | — | — | — |
| WP-52 | Python supervisor와 얇은 launcher | todo | — | — | — | — | — |
| WP-60 | Native Router/Scenes 상태 통합 | todo | — | — | — | — | — |
| WP-61 | Domain view-model과 UI 분리 | todo | — | — | — | — | — |
| WP-62 | Reason code·readiness·품질 표시 | todo | — | — | — | — | — |
| WP-70 | Producer·contract·source 연결 | todo | — | — | — | — | — |
| WP-71 | Step·trajectory·async KPI | todo | — | — | — | — | — |
| WP-72 | Bottleneck rule과 end-to-end evidence | todo | — | — | — | — | — |
| WP-80 | 구 구현 제거·호환 경로·문서 정리 | todo | — | — | — | — | — |
| WP-81 | 지침 정리와 전체 goal 검증 | todo | — | — | — | — | — |

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

WP-10 이후 구현은 시작하지 않았습니다.
