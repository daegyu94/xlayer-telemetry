# Validation Records

이 디렉터리는 검증 당시의 입력·환경·결과와 실행하지 않은 범위를 보존합니다.
실행 코드와 설정은 [examples](../../examples/README.md)에 있습니다.
기록된 `passed`는 현재 checkout이나 사용자의 host에서 모든 backend·hardware 조합을 다시 검증했다는 뜻이 아닙니다.

| 기록 | 검증 범위 |
| --- | --- |
| [examples-20261001.json](examples-20261001.json) | CPU SDK 예제·공통 Compose dashboard·target 설정·문서 링크와 기존 테스트 검증 |
| [sandbox/validation-20260930.json](sandbox/validation-20260930.json) | 실제 VERL Agent RL의 workload·trainer mode matrix와 Docker tool·native metrics·span 연결 |
| [multinode/validation.json](multinode/validation.json) | 한 host의 GPU·logical node·clock screening; 물리 multi-node 학습과 구분 |
| [investigation/validation-20260930.json](investigation/validation-20260930.json) | Collector run discovery·freshness·별도 실제 VERL 및 explicit LLM investigation의 검증 범위 |
| [dashboards/investigation-20260930.json](dashboards/investigation-20260930.json) | Grafana·Prometheus·Loki fixture의 investigation navigation |
| [dashboards/consolidation-20260930.json](dashboards/consolidation-20260930.json) | Dashboard 통합, metrics-only provisioning, observer/resource context 유지 |
| [dashboards/recordings-20261001.json](dashboards/recordings-20261001.json) | 최신 GIF의 context·재생 시간·checksum; 실제 수집 데이터의 재생과 synthetic fixture 구분 |
| [local-llm/validation-summary.json](local-llm/validation-summary.json) | Optional Ollama diagnosis 시나리오와 evidence contract 검사 |
| [local-llm/input-optimization-validation.json](local-llm/input-optimization-validation.json) | Model 입력 축약과 evidence 보존 검증 |
| [local-llm/review-validation.json](local-llm/review-validation.json) | Semantic review, 한국어 응답, evidence 검증과 남은 한계 |

일부 기록은 이전 dashboard 구성이나 prompt version을 대상으로 합니다.
현재 사용 흐름은 [Dashboard Guide](../dashboards.md)와 [Local LLM Guide](../local-llm.md)를 따르며, 당시 기록의 version·limitations를 함께 읽습니다.
