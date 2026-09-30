# Metrics Contract

Metric을 여러 계층에서 함께 해석하려면 이름뿐 아니라 단위와 측정 범위도 일치해야 합니다.
이 문서는 새로운 collector·adapter·dashboard를 추가할 때 사용할 공통 기준을 설명합니다.
기존 dashboard를 사용하기만 한다면 [Monitoring Guide](monitoring.md)와 [VERL 연결 가이드](verl-quickstart.md)부터 시작합니다.
왜 application과 shared resource의 scope를 구분하는지는 [설계 원칙](architecture.md#design-principles)에 설명합니다.

## What Is Actually Collected

VERL wrapper는 file logger를 읽는 bridge를 시작합니다.
GPU·host collector, native endpoint, custom span은 별도로 연결하며 아래 표에서 실제 producer와 확인할 metric을 구분합니다.
Endpoint 설정이나 run manifest가 있다는 사실만으로 metric 수집이 성공했다고 판단하지 않습니다.

| 계층 | 실제 signal 예 | Producer와 필요한 설정 | 측정 범위 |
| --- | --- | --- | --- |
| VERL trainer | `training_step`, `training_step_time_seconds`, `rl_stage_duration_seconds`, `reward_mean` | Wrapper의 file logger bridge; 원본 key가 기록된 경우만 변환 | Run / trainer worker / 완료 step |
| VERL rollout 요약 | `rollout_output_tokens_mean`, `agent_turns_mean`, `agent_tool_calls_mean` | VERL logger에 해당 평균값이 있을 때 bridge가 변환 | 완료 step의 평균; 개별 trajectory trace는 아님 |
| GPU | `telemetry_gpu_utilization_percent`, GPU memory·power | Node collector의 GPU sampler와 `nvidia-smi` | Device; run별 사용률 자동 귀속 없음 |
| CPU / memory / network / disk | `node_cpu_seconds_total`, `node_memory_MemAvailable_bytes`, `node_network_receive_bytes_total`, `node_disk_io_time_seconds_total` | Node Exporter; GPU 없는 node는 `ENABLE_GPU_METRICS=0` | Node / interface / device |
| RDMA | `node_infiniband_port_data_received_bytes_total`, `node_infiniband_port_data_transmitted_bytes_total` | 지원되는 host의 InfiniBand counter를 Node Exporter가 읽음 | Interface / port; NCCL 호출별 bytes는 아님 |
| vLLM | `vllm:num_requests_waiting`, `vllm:kv_cache_usage_perc`, `vllm:num_preemptions_total` | VERL/vLLM에서 native metric을 켜고 monitoring server에 endpoint 등록 | Serving engine; metric 이름은 version별 확인 |
| Ray | `ray_tasks` 등 배포의 native metric | Ray endpoint 등록 | Ray component; 전용 Grafana panel 없음 |
| SSD health | `smartctl_device_*` | 선택적 SMART exporter와 device 접근 권한 | SSD device; local sandbox와 3FS storage node를 구분 |
| 3FS service | ClickHouse distribution의 p99·mean 등 | Diagnostics의 ClickHouse 설정 | Shared service; 자동 Prometheus 변환 아님 |
| Tool / sandbox lifecycle | `tool.call`, `sandbox.exec`, `sandbox.resource_sample` | `EventRecorder` / `SandboxRecorder`를 integration 경계에서 호출 | Trace / span; JSONL이며 duration metric 자동 생성 아님 |
| Sandbox resource | `sandbox_io_write_bytes_total`, `sandbox_io_pressure_ratio`, `sandbox_memory_bytes` | Stable worker cgroup v2 경로를 sandbox sampler에 전달 | Worker cgroup; node SSD와의 correlation은 별도 |

Sandbox pool의 `sandbox_active`, `sandbox_queued`, create/reset latency는 runtime이 제공해야 하는 optional contract입니다.
Docker를 실행했다는 이유만으로 이 값이 자동 생성되지는 않습니다.
KV offload metric도 해당 vLLM connector와 exporter가 제공하는 경우에만 나타납니다.
수집 경로와 endpoint 등록 절차는 [Cross-Layer Integration](agent-rl.md#choose-the-next-source)에 있습니다.

### Inspect the VERL Mapping

실행 코드가 지원하는 전체 logger key와 metric 이름은 다음 명령으로 확인합니다.
VERL 설치나 GPU 없이 사용할 수 있습니다.

```bash
python -m xlayer_telemetry.adapters.verl --describe-metrics
```

`timing_s/gen`은 `rl_stage_duration_seconds{phase="rollout",verl_stage="gen"}`으로, `timing_s/update_weights`는 `phase="weight_sync"`로 변환합니다.
`perf/time_per_step`이 없으면 `timing_s/step`으로 step duration을 채우며 `timing_per_token_ms/*`는 milliseconds를 seconds로 변환합니다.
`perf/throughput`은 `training_tokens_per_second_per_gpu`이므로 전체 cluster의 tokens/s로 읽지 않습니다.
Actor·critic loss를 임의로 합쳐 `training_loss`로 변환하지 않으며 지원하지 않는 scalar는 원본 `logs/verl-metrics.jsonl`에 남습니다.
추가 application metric은 [SDK 예제](application-metrics.md)처럼 의미와 scope를 정해 직접 기록합니다.

Contract의 canonical name과 실제 exporter 이름은 다를 수 있습니다.
예를 들어 GPU contract의 `gpu_utilization_percent`는 sampler에서 `telemetry_gpu_utilization_percent`로 노출되며 disk 사용률은 `node_disk_io_time_seconds_total`의 rate로 계산합니다.
Dashboard나 diagnosis query를 추가할 때는 Prometheus에서 실제 metric 이름과 label을 먼저 확인합니다.

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
| Sandbox lifecycle | Run, step, trajectory ID, sandbox ID, trace/span | 높은 cardinality의 ID는 event JSONL에만 보관 |
| Sandbox worker cgroup | Node, role, runtime, filesystem, deployment, 시간 | Worker subtree 집계이며 개별 sandbox나 tool의 I/O가 아님 |
| Local NVMe | Node, device, 시간 | Device 전체 부하이며 3FS service metric과 별개 |

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
Sandbox metric의 Prometheus label은 `node`, `role`, `runtime`, `filesystem`, `deployment`만 사용합니다.
`sandbox_id`, `container_id`, `trajectory_id`, request ID, SWE-Bench instance ID는 label로 사용하지 않습니다.
`sandbox_active`·`sandbox_queued`·lifecycle latency·failure metric은 계약상 선택적 runtime-provided signal이며 현재 내장 Docker grader adapter는 생산하지 않습니다.
내장 cgroup sampler가 생산하는 것은 worker subtree의 I/O·CPU·memory·PSI metric입니다.
같은 label 조합의 worker subtree를 여러 sampler가 내보내면 중복 시계열이 되므로 하나의 공통 parent로 집계합니다.
서로 다른 `.prom` 파일명만 지정해도 label 충돌이 해결되는 것은 아닙니다.
Node Exporter target의 `cluster`·`nodename` label은 별도로 추가될 수 있습니다.
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
