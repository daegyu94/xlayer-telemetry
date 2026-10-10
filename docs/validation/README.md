# Validation Records

이 디렉터리는 검증 당시의 입력·환경·결과와 실행하지 않은 범위를 보존합니다.
실행 코드와 설정은 [examples](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/README.md)에 있습니다.
기록된 `passed`는 현재 checkout이나 사용자의 host에서 모든 backend·hardware 조합을 다시 검증했다는 뜻이 아닙니다.

| 기록 | 검증 범위 |
| --- | --- |
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
개별 변경의 테스트 수·개발 단계는 이 기록에서만 확인하고 기본 사용 안내에는 반복하지 않습니다.

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
