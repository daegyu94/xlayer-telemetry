# Metrics Contract

Metric을 여러 계층에서 함께 해석하려면 이름뿐 아니라 단위와 측정 범위도 일치해야 합니다.
이 문서는 새로운 collector·adapter·dashboard를 추가할 때 사용할 공통 기준을 설명합니다.
기존 dashboard를 사용하기만 한다면 [Monitoring Guide](monitoring.md)와 [VERL 연결 가이드](verl-quickstart.md)부터 시작합니다.
왜 application과 shared resource의 scope를 구분하는지는 [설계 원칙](architecture.md#design-principles)에 설명합니다.

## Understand a Metric

Metric은 시간에 따라 기록하는 수치입니다.
예를 들어 GPU 사용률은 특정 GPU의 현재 상태이고 stage duration은 application이 측정한 완료 구간의 시간입니다.
숫자가 같아도 대상·단위·수집 시점이 다르면 직접 비교할 수 없습니다.

| 개념 | 의미 | 예 |
| --- | --- | --- |
| Name | 무엇을 측정했는가 | `training_loss` |
| Unit | 숫자의 단위 | Seconds, bytes, tokens/s |
| Scope | 값이 속한 대상 | Run, worker, node, GPU, device |
| Label | 시계열을 구분하는 속성 | `node=trainer-0` |
| Gauge | 현재 값 | Loss, queue 길이 |
| Counter | 시작 이후 누적값 | 전송 bytes, error 횟수 |

Counter의 현재 값 자체는 초당 처리량이 아닙니다.
시간 구간의 증가량으로 rate를 계산하고 process 재시작에 따른 reset도 고려해야 합니다.
SDK counter를 기록할 때는 application이 누적값을 관리합니다.

## Read the Contract File

기준 파일은 [config/metrics.json](../config/metrics.json)입니다.
이 파일은 공통 어휘와 구현 기준이며 metric을 자동 수집하거나 exporter 이름을 자동 변환하는 registry는 아닙니다.
실제 수집에는 collector·adapter가, 화면 표시에는 해당 metric을 읽는 query가 필요합니다.
따라서 계약에 이름을 추가한 것만으로 Prometheus 시계열이나 Grafana panel이 생기지는 않습니다.

| 최상위 field | 내용 |
| --- | --- |
| `schema_version` | 계약 파일 형식의 version |
| `metrics` | Canonical metric 정의 |
| `recommended_labels` | Filtering·비교에 사용할 label 후보 |
| `manifest_only_fields` | 실행 metadata로 보관할 정보 |
| `phase_vocabulary` | 공통 실행 단계 이름 |

각 metric entry는 다음 여섯 field를 포함합니다.
아래는 application 처리량을 정의한 예입니다.

```json
{
  "name": "training_tokens_per_second",
  "category": "training",
  "unit": "tokens/s",
  "scope": "run",
  "source": "framework adapter",
  "policy": "always"
}
```

| Field | 작성할 내용 |
| --- | --- |
| `name` | 안정적으로 사용할 canonical name |
| `category` | Training, GPU, network, storage 같은 관측 영역 |
| `unit` | Dashboard 표시 변환 이전의 단위 |
| `scope` | 측정값이 속한 범위 |
| `source` | Exporter, framework timer, trace 등 원본 |
| `policy` | 상시, workload별, diagnostic 등 수집 시점·용도 |

`category`와 `source`는 다릅니다.
NIC counter와 application collective timer는 모두 network 분석에 도움이 되지만 생산자와 측정 범위가 다릅니다.
새 항목을 쓸 때 기존 entry의 단위와 표현을 먼저 확인합니다.

## Connect Layers with Context

Application metric은 `run_id`·worker·phase를 기준으로 조사할 구간을 정하는 데 사용합니다.
System resource metric은 같은 시간대의 node·device 상태를 설명합니다.
Shared service metric은 해당 서비스의 관측 범위를 유지하면서 비교합니다.

| 데이터 | 연결 기준 | 주의할 점 |
| --- | --- | --- |
| VERL 완료 stage | Run, 완료 step, driver | 현재 진행 중인 phase와 다름 |
| GPU·host | 시간, node, GPU | 다른 process의 부하가 포함될 수 있음 |
| vLLM·Ray native metric | 시간, source, node, replica | Run label이 자동 생성되지 않음 |
| 3FS 서비스 | 시간 창, host·mount 등 filter | Shared storage activity를 특정 step에 귀속하지 않음 |
| Tool event·trace | Run, worker, span 시간 | 별도 상세 증거이며 자동 Prometheus 시계열이 아님 |

서로 다른 scope의 값을 비교한 결과는 병목 후보입니다.
인과관계가 필요한 경우 [Run Analysis](dashboards.md#run-analysis)에 따라 log·event·trace를 확인합니다.
Derived metric에는 원본, 계산식과 시간 창을 함께 기록합니다.
특히 `run_id`가 없는 node·native service 시계열을 run별 사용량으로 표시하는 query를 만들지 않습니다.

## Choose Labels Carefully

Label 값의 조합마다 별도 시계열이 생깁니다.
`run_id`처럼 실행 구분에 필요한 값은 사용하되, run 수가 늘어나면 보존 기간 내 시계열 수도 증가한다는 점을 고려합니다.
모든 권장 label을 모든 metric에 붙일 필요는 없습니다.

| Label에 적합한 정보 | 별도 기록에 적합한 정보 |
| --- | --- |
| Cluster, node, GPU, device | Model·dataset·checkpoint URI |
| Producer, role, worker, rank | Git commit, image digest |
| Phase, operation, replica | Batch·sequence·precision, 전체 rank map |
| 제한된 종류의 interface·tool 이름 | Prompt, request ID, trace ID, 개별 timestamp |

실행 조건과 경로는 manifest에, 개별 요청의 상세 정보는 log·event·trace에 둡니다.
SDK에서 추가하는 label과 native exporter가 제공하는 label은 같다고 가정하지 않습니다.
현재 dashboard의 filter와 query에 실제로 쓰이는 label을 확인합니다.

## Name Phases by the Work

`phase_vocabulary`는 계층 사이에서 같은 작업 구간을 설명하기 위한 이름입니다.
Application이 실제로 측정한 구간에만 붙이고 관측하지 않은 phase를 추정해서 기록하지 않습니다.

| 작업 | Phase 예 | 함께 볼 지표 |
| --- | --- | --- |
| Dataset·model 준비 | `dataset_loading`, `model_loading` | Storage read, host staging |
| Training 입력·연산 | `training_input`, `forward_backward` | Data wait, GPU, collective |
| Optimizer | `optimizer_step` | Compute·rank synchronization |
| Checkpoint | `checkpoint_save`, `checkpoint_restore` | I/O 시간·bytes, device 상태 |
| VERL rollout·update | `rollout`, `actor_update`, `weight_sync` | Engine queue, GPU, transfer |
| 외부 tool | `tool_interaction` | Span duration, error |

단계의 시작·종료를 직접 기록할 수 있으면 run·worker와 성공 여부도 함께 남깁니다.
VERL bridge가 완료 시 기록하는 stage duration과 직접 계측한 span은 시간 정확도가 다를 수 있습니다.
전체 vocabulary와 metric 목록은 기준 JSON을 확인합니다.

## Add and Validate a Metric

1. 실제 source에서 값을 얻을 수 있는지 확인하고 단위·scope·수집 주기를 정합니다.
2. 기존 metric으로 표현할 수 있는지 확인한 뒤 필요한 정의를 `config/metrics.json`에 추가합니다.
3. Collector 또는 adapter를 구현하고 필요한 dashboard query·분석 코드를 연결합니다.
4. 계약 검사와 해당 구현의 검증을 실행합니다.

Checkout의 [Python 환경](../README.md#prepare-a-checkout)을 준비한 뒤 다음 검사를 실행합니다.

```bash
python -m pytest -q tests/test_schema.py
```

[Schema validator](../xlayer_telemetry/schema.py)는 필수 field, 허용된 이름·분류, 중복 등을 확인합니다.
검사 통과가 실제 endpoint의 가용성이나 값의 의미까지 보증하지는 않습니다.
기존 metric의 단위·의미를 바꾸면 consumer에 영향을 주므로 adapter와 query를 함께 검토합니다.
