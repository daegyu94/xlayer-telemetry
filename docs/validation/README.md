# Validation Records

이 디렉터리는 검증 당시의 입력·환경·결과와 실행하지 않은 범위를 보존합니다.
실행 코드와 설정은 [examples](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/README.md)에 있습니다.
기록된 `passed`는 현재 checkout이나 사용자의 host에서 모든 backend·hardware 조합을 다시 검증했다는 뜻이 아닙니다.

| 기록 | 검증 범위 |
| --- | --- |
| [Diagram layout review](diagram-layout-20261004.json) | 25개 그림의 설명 구조 재검토·6개 재설계, source를 관통하던 화살표 3곳 제거, 고정 Chromium 생성·실제 Sphinx 페이지 browser regression |
| [Figure styles and mobile rendering](figure-styles-20261004.json) | 원본 ASCII 의미 대조·log/event 경로 보정, D2/Excalidraw 25쌍·전역 선택기, 12개 페이지의 320–1440px 실제 Chrome 검사, 827개 Python test·5개 browser regression |
| [D2 documentation diagrams](d2-diagrams-20261004.json) | ASCII 그림 24개 교체, D2/SVG 25개·label 238개 실제 Chrome 확인, 11개 문서의 1440/900/390px·확대 링크·넘침 검사와 826개 test |
| [Userspace time alignment](userspace-time-alignment-20261004.json) | OS clock 변경 없이 reference 시간축 보정; 독립 process clock +12/-7초, 실제 Prometheus/Loki/Alloy 조회·Timeline, TDD 신규 36개 포함 820개 test와 cleanup |
| [Live dashboard UX](dashboards/live-ui-20261003.md) | 실제 Chrome navigation·baseline·optional row·stale/결측 화면, Timeline identity·policy lag 단위·숨겨진 filter 개선과 784개 test |
| [Mooncake telemetry](mooncake-telemetry-20261003.json) | 실제 client DFS read/write·master/client endpoint·Prometheus query·진단 입력, optional source 결측과 단위·engine 분리 검증 |
| [Metric collection coverage audit](metric-coverage-audit-20261003.md) | 133개 baseline 계약 전체 분류, 수집 경로·미수집 영역·cardinality/overhead 감사, native evidence·budget 개선과 검증 한계 |
| [Restricted Node Exporter smoke](metric-node-exporter-smoke-20261003.json) | 공식 1.9.1의 제한된 cloud Linux 관측; meminfo/vmstat/netstat/loopback/filesystem 확인, PSI/sysfs 및 physical hardware 미검증 |
| [Subsystem telemetry](subsystem-telemetry-20261003.json) | 실제 3FS O_DIRECT I/O·ClickHouse·diagnosis·Loki/Grafana, GPU 2개·cgroup, source coverage와 누락 상태 |
| [TDD failure regressions](tdd-regression-20261003.json) | 부분 저장·log 재작성·SDK 초기화·sidecar/child 종료·stale PGID 장애 재현, 신규 35개 포함 656개 test와 cleanup |
| [Greenfield architecture review](greenfield-review-20261003.json) | Fresh CLI·Grafana 실행, health/artifact 경계·event/query provenance 개선, 621개 test·source 조회 benchmark·cleanup |
| [Product user journey](product-user-journey-20261003.json) | Fresh clone·4회 loop, CLI run 문맥 보존·오류 안내, 실제 Grafana 9화면의 900/1440px 탐색·138 query·602개 test와 cleanup |
| [Synthetic coverage](dashboards/synthetic-coverage-20261003.json) | 10개 dashboard·138개 query의 실제 Prometheus/Loki 결과, 접힌 row 포함 117개 panel rendering, native/sandbox fixture·wheel 실행·599개 test |
| [Fresh User Experience](e2e-user-experience.md) | Fresh clone·CLI·Grafana 실제 클릭, 4회 개선 loop·591개 test·900/1440px 전체 panel 점검과 cleanup |
| [multinode/xltel-vm-validation-20261002.json](multinode/xltel-vm-validation-20261002.json) | Host server·KVM guest 두 개의 CLI lifecycle, 실제 metric·Loki event·Grafana datasource 조회, clock skew·VM 중단·collector 재시작·종료; 회귀 테스트 582개 |
| [review-20261002.json](review-20261002.json) | Log rotation·retry 시각·SDK 오류 격리, bounded snapshot cache, 회귀 테스트 490개·실제 Prometheus와 CPU 비용 측정 |
| [runtime-reliability-20261001.json](runtime-reliability-20261001.json) | Worker deadline·incremental cache·선택적 SDK background I/O, failure tests와 실제 VERL async·backend 실패 검증 |
| [async-followup-20261001.json](async-followup-20261001.json) | 실제 colocate/separate async 각 3 update, sandbox outcome·final export와 cgroup 관측 범위 검증 |
| [followup-20261001.json](followup-20261001.json) | Collector health·sandbox outcome, 실제 VERL sync 2 step·Docker cgroup·3FS query와 회귀 테스트 442개 |
| [review-20261001.json](review-20261001.json) | 전체 구조·실행 경로 리뷰, failure regression 10개, 실제 Prometheus·logical multi-node와 이력 scan 비용; 실학습 검증과 구분 |
| [core-refactor-20261001.json](core-refactor-20261001.json) | 공통 Prometheus parser·snapshot 선택·VERL bridge의 회귀 검증 |
| [examples-20261001.json](examples-20261001.json) | CPU SDK 예제·공통 Compose dashboard·target 설정·문서 링크와 기존 테스트 검증 |
| [sandbox/validation-20260930.json](sandbox/validation-20260930.json) | 실제 VERL Agent RL의 workload·trainer mode matrix와 Docker tool·native metrics·span 연결 |
| [multinode/vm-validation-20261001.json](multinode/vm-validation-20261001.json) | KVM guest 두 개의 실제 collector·clock 변경/복구·VM 중단·trace 연결; backend budget과 회귀 테스트 435개 |
| [multinode/validation.json](multinode/validation.json) | 한 host의 GPU·logical node·clock screening; 물리 multi-node 학습과 구분 |
| [investigation/validation-20260930.json](investigation/validation-20260930.json) | Collector run discovery·freshness·별도 실제 VERL 및 explicit LLM investigation의 검증 범위 |
| [dashboards/ux-20261002.json](dashboards/ux-20261002.json) | Baseline pivot·비교 표, 실제 Grafana 12.1.0의 Loki on/off·106 query·1600/1024px browser 검증 |
| [dashboards/investigation-20260930.json](dashboards/investigation-20260930.json) | Grafana·Prometheus·Loki fixture의 investigation navigation |
| [dashboards/consolidation-20260930.json](dashboards/consolidation-20260930.json) | Dashboard 통합, metrics-only provisioning, observer/resource context 유지 |
| [dashboards/recordings-20261001.json](dashboards/recordings-20261001.json) | 최신 GIF의 context·재생 시간·checksum; 실제 수집 데이터의 재생과 synthetic fixture 구분 |
| [local-llm/validation-summary.json](local-llm/validation-summary.json) | Optional Ollama diagnosis 시나리오와 evidence contract 검사 |
| [local-llm/input-optimization-validation.json](local-llm/input-optimization-validation.json) | Model 입력 축약과 evidence 보존 검증 |
| [local-llm/review-validation.json](local-llm/review-validation.json) | Semantic review, 한국어 응답, evidence 검증과 남은 한계 |

일부 기록은 이전 dashboard 구성이나 prompt version을 대상으로 합니다.
현재 사용 흐름은 [Dashboard Guide](../dashboards.md)와 [Local LLM Guide](../local-llm.md)를 따르며, 당시 기록의 version·limitations를 함께 읽습니다.

```{toctree}
:hidden:

e2e-user-experience
metric-coverage-audit-20261003
dashboards/live-ui-20261003
```

- [Metric audit final verification](metric-audit-verification-20261003.json): 775 passed, zero failed/skipped; hardware validation boundaries are explicit.
