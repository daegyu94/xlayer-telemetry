# Validation Records

이 디렉터리는 검증 당시의 입력·환경·결과와 실행하지 않은 범위를 보존합니다.
실행 코드와 설정은 [examples](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/README.md)에 있습니다.
기록된 `passed`는 현재 checkout이나 사용자의 host에서 모든 backend·hardware 조합을 다시 검증했다는 뜻이 아닙니다.

## Current Validation Scope

개발 단계 요약은 [Development Status](https://github.com/daegyu94/xlayer-telemetry#development-status)를 따릅니다. 아래 결과는 기록된 revision·환경에 한정하며, 기능 구현 완료를 실제 분산 Agent RL validation 완료로 해석하지 않습니다.

| Environment | Verified Scope | Limitations / Evidence |
| --- | --- | --- |
| CPU / Synthetic | Wrapper/parser·Run/baseline 격리·clock/sampling 품질·failure/recovery·Saved Runs 조사 흐름 | Model은 metadata이며 weights 실행 아님. [연결 검증](../system-review.md#agent-rl-연결-검증의-범위)·[실제 producer 회귀](../../tests/test_run_artifact_pipeline.py)·[동시 wrapper 회귀](../../tests/test_multi_job_concurrency.py) |
| Grafana 12.1 / Prometheus / Loki | Synthetic multi-job의 query/diagnosis → catalog → UI, 필터·비교 선택의 Back/reload/share와 keyboard focus | [Browser validation](../../grafana/xlayer-app/scripts/multi_job_validate.py). Physical GPU·NTP·storage 성능 검증 아님 |
| Single-Node GPU / Agent RL | 한 host·GPU 2개·Qwen2.5-1.5B의 짧은 SWE-Bench/GSM8K 실행과 mode별 관측 | 과거 실행·replay이며 최신 코드의 전 실환경 재검증 아님. [실측 범위](../real-verl-demo.md#recorded-agent-rl-run)·[Integration coverage](../integration-reference.md#real-validation-coverage) |
| VM / Backend-Specific | 독립 guest의 수집·clock 장애 및 일부 Mooncake/3FS I/O·ClickHouse 경로 | 아래 원본 기록 참고. 물리 multi-node Agent RL·shared-storage 포화·Run별 I/O attribution을 입증하지 않음 |

2026-10-11의 `d34b3de`에서 CPU regression **1,907 passed / 6 skipped**, frontend **161 passed**, typecheck·build·문서 build와 실제 loopback Grafana Synthetic journey를 확인했습니다. Skip 6개는 disposable ClickHouse 미설정에 따른 것으로 통과 범위에 포함하지 않습니다. 이번 문서 변경은 그 실행 기록과 현재 소스의 일치를 검토한 것이며 새로운 GPU 학습 실험을 수행하지 않았습니다.

## Planned Validation

다음은 모두 **Pending**이며 장비·workload·revision을 고정한 뒤 수행합니다. 상세 알고리즘·계측 연구 과제는 기존 [Future Work](../correlation-limitations.md#future-work-tbd)에 두고, 여기서는 실환경 validation의 완료 기준만 관리합니다.

| Milestone | Environment / Completion Criteria |
| --- | --- |
| Physical Multi-Node Collection & Correlation | Monitoring·trainer·rollout·service node의 clock offset/uncertainty/freshness와 identity를 기록. 수집 → query → Step/Phase 조사에서 누락·skew를 보류하며 node/run 혼합이 없는지 확인 |
| Concurrent Multi-Job & Rollout Replicas | 실제 여러 모델·Job과 sync/async·overlap·replica lifecycle/재시작을 실행. Run별 baseline·관측 집합을 보존하고 shared pressure를 특정 Job 원인으로 단정하지 않는지 확인 |
| Agent RL & Storage Integration | 실제 veRL/vLLM/Mooncake/3FS workload로 client·service·device의 시간 범위·단위·scope·coverage를 검증. 기존 개별 backend 실측을 통합 경로의 검증으로 대체하지 않음 |
| Distributed Recovery & Long-Running Operation | Source/backend 부분 장애·clock 변화·재시작과 장시간 실행에서 workload 종료 코드·정상 peer·artifact 복구·retention·query budget·CPU/memory/I/O overhead를 기록 |

각 validation은 hardware/topology·framework revision·model/mode·workload·입력·결과·missing evidence·cleanup을 원본 기록으로 남깁니다. Pass는 검증한 조건에만 적용하며 Correlation ≠ Attribution ≠ Causality를 유지합니다. 미구현 backend 계측과 Remote Sandbox 내부 관측은 별도 TBD이며 실환경 장비 준비만으로 지원 완료가 되지 않습니다.

## Historical Records

| 기록 | 검증 범위 |
| --- | --- |
| [배포·복구·producer 계약](prehardware-contracts-20261010.json) | CPU failure injection·실제 writer/launcher·Docker 3노드 Prometheus/Loki 수집과 부분 장애. 공유 kernel의 clock·GPU·RDMA·3FS 성능 검증 아님. |
| [Execution / Performance diagnosis](performance-diagnosis-20261010.json) | 독립 CPU fault oracle·기존 main과의 engine/SDK 비용 비교·bounded Graph/Path 측정. GPU·framework 자동 tracing·물리 multi-node는 제외. |
| [문서 Runbook / Reference](documentation-ux-20261009.json) | Public Pages·독자 경로·전용 CLI/synthetic smoke·6 viewport·기존 code block/anchor 보존 검증. |
| [Training·sandbox](sandbox/validation-20260930.json), [async follow-up](async-followup-20261001.json) | 실제 단일 host VERL·vLLM·Docker 실행 범위와 tool/cgroup 연결. 물리 multi-node 학습과 구분합니다. |
| [3FS·subsystem](subsystem-telemetry-20261003.json), [Mooncake](mooncake-telemetry-20261003.json) | 실제 3FS I/O·ClickHouse·native endpoint 경로와 수집하지 못한 signal. |
| [VM 검증](multinode/xltel-vm-validation-20261002.json), [시간 보정](userspace-time-alignment-20261004.json) | 독립 guest clock·collector와 userspace reference 보정. VM과 process 기반 검증은 다른 실행입니다. |
| [Metric coverage audit](metric-coverage-audit-20261003.md) | Contract별 producer·미수집 영역·cardinality·overhead 대조. |
| [Grafana App · Scenes](grafana-scenes-20261008.md) | 2026-10-08의 live synthetic UI·context 검증과 당시 화면 캡처. |
| [Live dashboard UX](dashboards/live-ui-20261003.md), [Fresh User Experience](e2e-user-experience.md) | 당시 browser navigation·filter·가독성의 before/after. |
| [GIF capture manifest](dashboards/recordings-20261001.json) | 2026-10-01 GIF의 context·재생 범위·checksum. 실측 데이터 replay와 synthetic fixture를 구분합니다. |
| [Failure regressions](tdd-regression-20261003.json), [runtime reliability](runtime-reliability-20261001.json) | 장애 주입·lifecycle·bounded query와 기록 비용 검증. |
| [D2 layout](diagram-layout-20261004.json) | D2 원본·SVG geometry·responsive browser 검증 당시 결과. |
| [Local LLM 검증](local-llm/history-20260930.md) | 실제 모델 응답·prompt version·latency·오판 및 원본 JSON. |

전체 JSON·screenshot은 [archive 디렉터리](https://github.com/daegyu94/xlayer-telemetry/tree/main/docs/validation)에 보존합니다.
실행별 테스트 수·환경·한계는 validation 기록에서 관리하며, README에는 개발 단계와 다음 목표만 요약합니다.

일부 기록은 이전 dashboard 구성이나 prompt version을 대상으로 합니다.
현재 사용 흐름은 [Dashboard Guide](../dashboards.md)와 [Local LLM Guide](../local-llm.md)를 따르며, 당시 기록의 version·limitations를 함께 읽습니다.

```{toctree}
:hidden:

e2e-user-experience
metric-coverage-audit-20261003
dashboards/live-ui-20261003
grafana-scenes-20261008
../grafana-ui-ux-review
local-llm/history-20260930
documentation-review-20261005
```

Metric audit의 당시 최종 범위는 [검증 원본](metric-audit-verification-20261003.json)에 있습니다.
