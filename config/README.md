# Shared Contracts

이 directory는 XLayer의 공통 어휘와 출력 계약을 관리합니다.
Monitoring 주소, VERL 실행 명령, diagnostics threshold를 지정하는 실행 설정은 [examples](../examples/README.md)에서 복사합니다.
보통 사용자는 이 directory를 수정할 필요가 없습니다.

| File | 역할 | 사용처 |
| --- | --- | --- |
| `metrics.json` | Metric의 canonical name·unit·scope·source·수집 용도와 권장 label/phase | 새 adapter·collector·SDK integration의 설계 기준 |
| `metrics.schema.json` | 위 metric 계약 파일의 JSON Schema | Editor·CPU test에서 구조 검증 |
| `diagnosis.schema.json` | Rule diagnosis report의 JSON Schema | `diagnostics/latest.json`과 누적 diagnosis report의 출력 검증 |

Metric 계약의 version 1과 SDK snapshot의 version 2는 다른 파일 형식입니다.
`metrics.json`은 snapshot을 검증하는 schema가 아니며, 실제 exporter의 native metric 이름을 자동 변환하지도 않습니다.
예를 들어 canonical `gpu_utilization_percent`와 GPU sampler의 `telemetry_gpu_utilization_percent`는 source query에서 연결합니다.
자세한 생산자·설정·scope는 [Metrics Contract](../docs/metrics.md#what-is-actually-collected)를 확인합니다.

## Collection Policy and Labels

`policy: "always"`는 해당 signal을 사용할 때 권장하는 수집 방식입니다.
모든 node에서 자동 생성되거나 built-in producer가 있다는 의미는 아닙니다.
Source가 비활성화되었거나 metric을 제공하지 않으면 missing/N/A 상태를 유지합니다.

권장 label은 filtering에 쓸 후보이며 모든 metric에 붙이는 allowlist가 아닙니다.
`runtime`, `filesystem`, `deployment`, `status`, `tool`은 제한된 종류의 값에만 사용합니다.
여기서 `filesystem`은 `overlayfs`·`ext4` 같은 종류이고 전체 경로는 `filesystem_path` 등 manifest metadata에 보관합니다.
이전 manifest에 기록한 `filesystem` field는 그대로 사용할 수 있으며 manifest에 허용된 field를 제한하는 변경은 아닙니다.
`sandbox_id`, `container_id`, `trajectory_id`, `request_id`, `prompt_id`, `trace_id`, `span_id`, SWE-Bench instance ID는 event attribute에 보관합니다.
Run label은 application metric에 사용할 수 있지만 node/device/shared-service 값에 특정 run의 사용량이라는 의미를 부여하지 않습니다.

Phase vocabulary도 권장 어휘입니다.
`environment`는 sandbox lifecycle의 공통 phase이며, framework별 추가 phase를 SDK에서 사용하는 것은 가능합니다.

## Validate Changes

Checkout의 telemetry Python 환경에서 실행합니다.

```bash
python -m xlayer_telemetry.schema config/metrics.json
python -m pytest -q tests/test_schema.py tests/test_config_contracts.py tests/test_diagnosis_analysis.py
```

첫 명령은 dependency 없는 Python validator로 metric 계약의 구조·중복 이름·label 정책을 검사합니다.
테스트는 표준 Draft 2020-12 validator로 두 JSON Schema 자체와 실제 metric 계약·진단 출력을 확인합니다.
`jsonschema`는 `scripts/setup.sh`가 설치하는 test dependency이며 SDK나 collector의 runtime dependency는 아닙니다.
Metric name의 유일성, label/manifest 어휘의 분리 같은 field 사이의 의미 검사는 Python validator가 보완합니다.

Diagnosis schema는 rule engine 출력의 계약이며 optional LLM 응답의 schema와는 다릅니다.
Counter evidence에도 signal·측정값·observation scope를 보존하고, span이나 sampled metric의 window·boundary accuracy는 supporting evidence에 남깁니다.
원래 event 시각을 모르는 replay에서는 interval boundary가 null/unknown일 수 있습니다.
Schema 통과는 구조의 일관성만 확인하며 timestamp 순서, source freshness, entity 일치나 인과관계를 보증하지 않습니다.
이 판단은 기존 correlation·diagnosis 로직에서 수행합니다.
기존 report의 추가 field는 계속 허용하므로 additive extension을 위해 schema version을 올리지는 않습니다.
