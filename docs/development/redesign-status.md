# Redesign status

이 표는 [전체 실행 계획](redesign-plan.md)의 WP 진행 상태를 기록합니다.
`done`은 구현 검증과 로컬 `main` 병합이 확인된 상태입니다. 실환경·원격 적용은 아래 별도 표에서 관리합니다.

- Source 기준: `13eb1a04d75c753d95fa399191a3f0c6d0f2585d` (WP-00 시작 시 로컬 `main`)
- 원문 진단 기준: `6ddce33a763d3de30e306980860d33aa88ab32f1`
- 현재 작업: WP-00 완료, 다음 WP-04
- 현재 검증: `.venv/bin/python -m pytest -q tests/test_documentation_links.py tests/test_documentation_diagrams.py` — 10 passed; `bash scripts/docs.sh build` — 성공

| WP | 작업 | 상태 | 기준 SHA | 결과 SHA | 검증 | 병합 근거 | 장애물·후속 |
|---|---|---|---|---|---|---|---|
| WP-00 | 실행 계획 반입과 기준점 | done | `13eb1a0` | `b0351124495c3e8541ce6537987c0de9b57b9872` | 문서 test 10 passed, docs build 성공 | 로컬 main에 `b035112` fast-forward 병합 | — |
| WP-04 | 재현 가능한 개발 의존성 | todo | — | — | — | — | — |
| WP-02 | Test 환경 격리 | todo | — | — | — | — | — |
| WP-01 | Sandbox first-seen device 계약 확정 | todo | — | — | — | — | — |
| WP-03 | HTTP proxy 정책 정합 | todo | — | — | — | — | — |
| WP-07 | Bounded shutdown과 ownership | todo | — | — | — | — | — |
| WP-08 | Pressure card의 evidence·단위 연결 | todo | — | — | — | — | — |
| WP-05 | 단일 gate와 hooks | todo | — | — | — | — | — |
| WP-06 | CI gate 연결 | todo | — | — | — | — | — |
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
