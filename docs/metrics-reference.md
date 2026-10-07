# Metrics Contract

> **Reference** · 기본 작업은 [metrics guide](metrics.md)에서 시작합니다. 아래에는 기존 운영·구현·해석 세부 정보와 기록을 보존합니다.

계층 간 비교에는 이름·단위·scope가 함께 필요합니다.
이 문서는 collector·adapter·query를 추가할 때의 기준이며, 기본 사용은 [Monitoring](monitoring.md)·[VERL Quickstart](verl-quickstart.md)를 따릅니다.
Scope를 분리하는 이유는 [설계 원칙](architecture-reference.md#design-principles)에 있습니다.

## What Is Actually Collected

VERL wrapper는 file logger를 읽는 bridge를 시작합니다.
GPU·host collector, native endpoint, custom span은 별도로 연결하며 아래 표에서 실제 producer와 확인할 metric을 구분합니다.
Endpoint 설정이나 run manifest가 있다는 사실만으로 metric 수집이 성공했다고 판단하지 않습니다.
등록한 native endpoint와 Node Exporter는 dashboard에 쓰는 metric만 골라 수집하지 않습니다.
Dashboard는 주요 신호를 요약하고, 나머지 exporter metric은 `xltel sources`의 Explore 링크에서 조회합니다.

| 계층 | 실제 signal 예 | Producer와 필요한 설정 | 측정 범위 |
| --- | --- | --- | --- |
| VERL trainer | `training_step`, `training_step_time_seconds`, `rl_stage_duration_seconds`, `reward_mean` | Wrapper의 file logger bridge; 원본 key가 기록된 경우만 변환 | Run / trainer worker / 완료 step |
| VERL rollout 요약 | `rollout_output_tokens_mean`, `agent_turns_mean`, `agent_tool_calls_mean` | VERL logger에 해당 평균값이 있을 때 bridge가 변환 | 완료 step의 평균; 개별 trajectory trace는 아님 |
| VERL reported MFU / policy version | `training_model_flops_utilization_ratio`, `policy_version` | [명시 scalar와 지원 범위](ui-telemetry-coverage.md#framework-reported-scalars); 실제 logger key가 있을 때만 변환 | Driver가 보고한 완료 stage MFU / trainer policy version; GPU util·step 번호에서 추론하지 않음 |
| Wrapped workload 상태 | `telemetry_wrapped_workload_state`, `telemetry_wrapped_workload_observed_timestamp_seconds`, `telemetry_wrapped_workload_exit_code` | Wrapper의 명시 health artifact와 동일 node의 application collector | [Wrapper가 실행한 command](ui-telemetry-coverage.md#wrapped-workload-status); 전체 async run 완료가 아님 |
| GPU | `telemetry_gpu_utilization_percent`, `telemetry_gpu_memory_used_bytes`, `telemetry_gpu_memory_total_bytes` | Node collector의 GPU sampler와 `nvidia-smi`; power·temperature·clock도 지원 값만 노출 | Device; run별 사용률 자동 귀속 없음 |
| CPU / memory / network / disk | `node_cpu_seconds_total`, `node_memory_MemAvailable_bytes`, `node_network_receive_bytes_total`, `node_disk_io_time_seconds_total` | Node Exporter; GPU 없는 node는 `ENABLE_GPU_METRICS=0` | Node / interface / device |
| RDMA | `node_infiniband_port_data_received_bytes_total`, `node_infiniband_port_data_transmitted_bytes_total` | 지원되는 host의 InfiniBand counter를 Node Exporter가 읽음 | Interface / port; NCCL 호출별 bytes는 아님 |
| vLLM | `vllm:num_requests_waiting`, `vllm:kv_cache_usage_perc`, `vllm:num_preemptions_total` | VERL/vLLM에서 native metric을 켜고 monitoring server에 endpoint 등록 | Serving engine; metric 이름은 version별 확인 |
| Mooncake KV storage | `vllm:mooncake_store_operation_*`, `master_allocated_bytes`, `mooncake_dfs_*` | [Store connector·master·client endpoint 연결](kv-storage.md); client HTTP는 기본 비활성 | Connector RPC / shared master / client batch; 물리 SSD I/O나 run 소유량이 아님 |
| Ray | `ray_tasks` 등 배포의 native metric | Ray endpoint 등록 | Ray component; Stage Correlation의 Ray row |
| SSD health | `smartctl_device_*` | 선택적 SMART exporter와 device 접근 권한 | SSD device; local sandbox와 3FS storage node를 구분 |
| 3FS service | ClickHouse distributions의 p99·mean과 raw counter의 identity·min/max/last·sample count·freshness | 기존 ClickHouse 설정으로 `xltel sources threefs` 조회 | Shared service; recorder reset·gauge가 섞여 rate나 누적 총량을 자동 계산하지 않음 |
| Tool / sandbox lifecycle | `tool.call`, `sandbox.exec`, `sandbox.resource_sample` | `EventRecorder` / `SandboxRecorder`를 integration 경계에서 호출 | Trace / span; JSONL이며 duration metric 자동 생성 아님 |
| Sandbox resource | `sandbox_io_write_bytes_total`, `sandbox_memory_pressure_ratio`, `sandbox_cpu_throttled_seconds_total` | Stable worker cgroup v2 경로를 sandbox sampler에 전달; CPU·memory·I/O와 압력·event counter | Worker cgroup; CPU quota 통계는 선택 cgroup 자체의 제한 |

GPU device memory는 `nvidia-smi`의 MiB를 bytes로 변환하며 지원하지 않는 field는 0 대신 생략합니다.
`telemetry_gpu_process_memory_bytes`는 `GPU_PROCESS_METRICS=1`일 때만 수집하는 별도 process 관측이며 UUID가 장치와 일치할 때만 `gpu` index label을 붙입니다.
기본값은 `0`이며 PID churn을 상시 Prometheus 시계열에 넣지 않습니다. 짧은 진단에서만 켜고 `GPU_MAX_PROCESSES`(기본 256, 최대 4096)로 snapshot별 행을 제한합니다.
`telemetry_gpu_process_collection_enabled`와 `telemetry_gpu_process_samples_truncated`로 설정·잘린 행 수를 구분합니다. 이 한도는 보존 기간 전체의 PID churn이나 nvidia-smi 응답 크기 자체의 한도가 아닙니다.
`telemetry_gpu_process_sample_timestamp_seconds`는 opt-in process memory 관측값이 하나 이상 있을 때만 노출하며, 실제 측정값 `0`과 일부 행 오류가 있어도 남은 유효한 관측은 보존합니다.
Process 수집 비활성화·전체 실패·관측값 없음에서는 이 시각을 생략하며 device 수집 성공 시각과 독립적으로 freshness를 판단합니다.
Compute & Communication의 process-memory panel은 이 시각을 우선하고, 이 metric이 없는 이전 collector·저장된 data에서만 device 시각으로 대체합니다.
Process 시각이 존재하지만 stale이면 fresh device 시각으로 우회하지 않으며, 두 경로 모두 기존 node·instance·GPU selector와 30초 freshness 조건을 유지합니다.
Device memory와 process memory를 합산하거나 run 소유량으로 해석하지 않습니다.

`telemetry_gpu_collection_success`는 가장 최근 device 수집의 성공 여부이며 GPU 수집 설정과 별개입니다.
`nvidia-smi` 실행·timeout·응답 decoding 실패는 JSONL의 `collection_success=false`와 `collection_error`에 기록하고 기존 interval 뒤에 재시도합니다.
명령이 정상 종료해도 유효한 identity를 가진 device 관측값이 없으면 `no_device_observations`로 기록하고 성공 시각을 노출하지 않으며, 별도 process query에서 얻은 유효한 opt-in 관측은 보존합니다.
실패 기록의 `timestamp`는 시도 시각이며, `gpu.prom`은 health 값으로 atomic 교체하여 이전 GPU·PID 값과 `telemetry_gpu_sample_timestamp_seconds`를 제거합니다.
수집이 복구되면 성공 시각과 관측값을 다시 노출하며, 일시적인 GPU 장애로 Node Exporter를 종료하지 않습니다.
JSONL에 함께 저장하는 선택적 `/proc/meminfo`는 읽을 수 있는 field만 보존하고 누락·잘못된 값은 생략합니다.
`host_memory_collection_success`, `host_memory_unavailable_fields`, `host_memory_error`와 `telemetry_gpu_host_memory_collection_success`로 이 상태를 구분하며, host-memory 실패는 유효한 GPU 관측을 버리지 않습니다.
이 health 값은 Node Exporter의 native host-memory metric을 대체하지 않으며, 설정·출력 파일 오류는 계속 실패로 처리합니다.

Sandbox pool의 `sandbox_active`, `sandbox_queued`, create/reset latency는 runtime이 제공해야 하는 optional contract입니다.
Docker를 실행했다는 이유만으로 이 값이 자동 생성되지는 않습니다.
KV offload metric도 해당 vLLM connector와 exporter가 제공하는 경우에만 나타납니다.
Mooncake DFS ops는 성공한 KV key 수이며 read/write latency histogram은 batch 단위 microseconds입니다.
Mooncake Store를 사용하는 구성에서는 connector·master·노출 가능한 client DFS metric을 기본 수집 대상으로 삼습니다.
Dashboard는 histogram을 seconds로 변환하며 write D2H staging을 별도로 표시합니다.
Master의 memory/file lookup hit counter로 rate를 계산하지만 현재 key 개수로 cache hit ratio를 만들지는 않습니다.
Master `PutStart` failure는 admission/RPC 요청 단위이며 client DFS 실패 key와 합산하지 않습니다.
`mooncake_ssd_*`는 별도 FileStorage 경로이므로 descriptor DFS·3FS USRBIO metric을 대체하지 않습니다.
수집 경로와 endpoint 등록 절차는 [Cross-Layer Integration](integration-reference.md#choose-the-next-source)에 있습니다.

수집 범위의 한계도 구분합니다.
RDMA port counter는 process·NCCL collective별 attribution을 제공하지 않고, GPU sampler는 kernel trace나 allocator eviction을 자동 수집하지 않습니다.
Tool span·sandbox pool 상태·KV offload 전용 signal은 해당 runtime의 계측이 필요하며, source가 없으면 다른 계층의 metric으로 대신 판정하지 않습니다.
실제 source와 누락 상태를 확인한 범위는 [Subsystem 검증 기록](validation/subsystem-telemetry-20261003.json)에 있습니다.

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

MFU·명시 policy version·wrapper 상태의 원본 key와 실제 phase hook 연결은 [UI Telemetry Coverage](ui-telemetry-coverage.md)에 정리합니다. `measured_phase()`는 호출자가 연결한 실제 entry/exit에서 `EventRecorder.span()`을 사용하며 file logger duration에서 phase interval을 재구성하지 않습니다.

Contract의 canonical name과 실제 exporter 이름은 다를 수 있습니다.
예를 들어 GPU contract의 `gpu_utilization_percent`는 sampler에서 `telemetry_gpu_utilization_percent`로 노출되며 disk 사용률은 `node_disk_io_time_seconds_total`의 rate로 계산합니다.
Dashboard나 diagnosis query를 추가할 때는 Prometheus에서 실제 metric 이름과 label을 먼저 확인합니다.

Workload 비교 field·sampling quality·sandbox major:minor는 JSONL/diagnosis의 evidence metadata로 저장하며 새 Prometheus label로 추가하지 않습니다.
여러 run 수집은 기존 `run_id` label을 재사용하고 종료·stale snapshot을 제외해 현재 export의 시계열 증가를 제한합니다.

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

[config/metrics.json](https://github.com/daegyu94/xlayer-telemetry/blob/main/config/metrics.json)은 공통 어휘·구현 기준입니다.
수집은 collector·adapter, 표시는 query가 담당하며 이름을 추가해도 시계열이나 panel이 자동 생성되지는 않습니다.
[Config 안내](https://github.com/daegyu94/xlayer-telemetry/blob/main/config/README.md)에 파일 역할·검증 명령을 정리했습니다.

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
`policy: "always"`는 해당 source를 사용할 때의 수집 권장 방식이며 built-in producer나 source 가용성을 보증하지 않습니다.
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

Scope 간 비교는 병목 후보이며 인과 확인은 [log·event·trace 조사](deep-dive.md)로 이어갑니다.
Derived metric은 원본·계산식·시간 창을 남기고 run ID 없는 시계열을 run별 사용량으로 표시하지 않습니다.

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

실행 조건·경로는 manifest, 개별 요청은 log·event·trace에 둡니다.
Sandbox Prometheus label은 `node`·`role`·`runtime`·`filesystem`·`deployment`를 사용하며 sandbox/container/trajectory/request ID와 SWE-Bench instance ID는 제외합니다.
Pool·lifecycle·failure metric은 선택적 runtime-provided 계약이고 내장 sampler는 cgroup I/O·CPU·memory·PSI를 생산합니다.
Memory PSI some/full·high/max event와 CPU quota counter의 의미 및 누락 조건은 [Sandbox cgroup 수집](integration-reference.md#sample-the-sandbox-worker-cgroup)을 따릅니다.
같은 label의 subtree는 공통 parent 하나로 집계하며 `.prom` 파일명만 나눠도 label 충돌은 해결되지 않습니다.
Target의 `cluster`·`nodename`은 별도로 붙을 수 있으므로 SDK·native exporter·dashboard의 실제 label을 확인합니다.

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

```bash
python -m pytest -q tests/test_schema.py
```

[Schema validator](https://github.com/daegyu94/xlayer-telemetry/blob/main/xlayer_telemetry/schema.py)는 필수 field, 허용된 이름·분류, 중복 등을 확인합니다.
검사 통과가 실제 endpoint의 가용성이나 값의 의미까지 보증하지는 않습니다.
기존 metric의 단위·의미를 바꾸면 consumer에 영향을 주므로 adapter와 query를 함께 검토합니다.

## Collector Health

Application textfile collector는 다음 counter를 `application.prom`에 함께 내보냅니다.
Prometheus target의 instance·cluster가 collector를 구분하며 파일명이나 worker ID를 추가 label로 사용하지 않습니다.

| Metric | 의미 |
| --- | --- |
| `telemetry_application_snapshot_reads_total` | Snapshot 파일 읽기 시도 |
| `telemetry_application_snapshot_cache_hits_total` | 변경되지 않은 snapshot의 파일 읽기·JSON parsing을 생략한 횟수 |
| `telemetry_application_snapshot_rejections_total` | 읽기·JSON·최상위 identity/schema 검증 실패 |
| `telemetry_application_sample_rejections_total` | Samples 구조·값·label·중복·metric definition 검증 실패 |

Counter는 collector 시작 이후의 처리 횟수이며 재시작하면 reset됩니다.
동일한 불량 파일을 매 poll마다 읽으면 계속 증가하므로 고유 record 손실 개수가 아닙니다.
Node/freshness 필터로 정상 제외한 snapshot은 오류로 집계하지 않습니다.
선택적 [time calibration](time-alignment.md)을 사용하면 application·GPU·sandbox freshness gauge는 reference time을 사용합니다.
Snapshot의 원본 local timestamp는 유지하며 보정이 불가능하면 timestamp gauge를 생략하여 정상 값 `0`으로 오해하지 않게 합니다.
이 이름들은 collector 전용이며 application snapshot이 같은 이름을 보내면 거부합니다.
`training_step`·`training_gpu_allocation`·`training_sample_timestamp_seconds`도 snapshot context에서만 생성하며, 같은 이름의 application sample은 거부합니다.
Sample label로 run·producer·role·worker·node·rank·local rank·GPU context를 추가하거나 덮어쓸 수 없으며, snapshot에 없는 context는 결측으로 남습니다.

CLI collector는 최대 1,024개 파일·원본 크기 합계 16 MiB의 decoded snapshot을 process-local cache에 보관합니다.
이 한도는 Python 객체의 실제 메모리 사용량 한도가 아니며, 초과 파일도 cache 없이 정상 수집합니다.
매 poll의 directory 탐색·metadata 확인·node/freshness 필터·sample 검증은 유지하고, 파일 identity·크기·mtime·ctime이 바뀌면 JSON을 다시 읽습니다.
Producer는 SDK처럼 atomic replace로 snapshot을 갱신하며, 삭제되거나 종료된 run의 cache는 제거합니다.
Identity·크기·mtime·ctime이 모두 같은 제자리 수정은 감지할 수 없으므로 외부 producer도 이 갱신 계약을 따릅니다.
Cache hit는 `snapshot_reads_total`에 포함하지 않고 rejection counter는 계속 검증 시도마다 증가합니다.


## Bounded Collection and Pressure Evidence

Host pressure·disk await/queue·network error 신호는 이미 실행하는 Node Exporter를 재사용합니다.
별도의 `/proc` polling collector를 추가하지 않습니다. `pressure`, `diskstats`, `netdev`,
`netstat`, `vmstat`, `filesystem`, `infiniband` collector가 host/kernel에서 실제 성공했는지
`node_scrape_collector_success`와 endpoint 원본으로 확인합니다. 없는 신호를 0으로 바꾸지 않습니다.
NVMe에서는 busy fraction만으로 포화라고 판단하지 않고 queue·operation latency·IOPS를 함께 봅니다.
네트워크 오류/retransmit은 node/port evidence이며 해당 run의 NCCL 전송 오류로 귀속하지 않습니다.

실제 collector용 generated server config는 host 2초/timeout 2초, native 5초/timeout 4초,
SMART 60초/timeout 10초로 조회합니다. 모든 job은 기본적으로 scrape당 100,000 samples,
1,024 targets/job, uncompressed body 16 MB, 40 labels/sample, label name 128 bytes,
label value 1,024 bytes 제한을 상속합니다. 한도 초과 시 일부 값만 성공으로 표시하지 않고
scrape 전체가 실패하므로 `up`, scrape duration·sample count와 Prometheus Targets 오류를 확인합니다.
필요한 경우 `PROMETHEUS_SAMPLE_LIMIT`, `PROMETHEUS_TARGET_LIMIT`,
`PROMETHEUS_BODY_SIZE_LIMIT_MB`, `NATIVE_SCRAPE_INTERVAL_SECONDS`,
`NATIVE_SCRAPE_TIMEOUT_SECONDS`를 bounded positive 값으로 조정합니다.
`0`으로 한도를 제거하는 설정은 허용하지 않습니다. Retention·label churn·전체 cluster TSDB
메모리 사용량은 별도 운영 예산이며 per-scrape 한도만으로 제한되지 않습니다.

Native source 등록은 1 MiB 파일, 1,024 endpoint, endpoint당 16개 사용자 label,
값당 256 UTF-8 bytes를 허용합니다. 내부 `__*` label과 같은 endpoint의 중복 등록을
거부해 scrape interval override나 exporter 이중 수집을 방지합니다. Path는 query/fragment 없는
metrics path만 받습니다. Prometheus의 metric 이름·label을 강제로 삭제해 서로 다른 시계열을
합치지 않습니다. 기존 외부 Prometheus 사용자는 같은 예산을 해당 설정에 적용합니다.

설정 필드의 의미는 [Prometheus scrape configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/),
host 수집 조건은 [Node Exporter collectors](https://github.com/prometheus/node_exporter#collectors)를 따릅니다.

Shared Prometheus range-query client는 응답을 JSON decoding 전에 8 MiB로 제한하고,
한 query 결과의 series 1,000개·points 200,000개를 초과하면 전체 query를 missing evidence로 처리합니다.
부분 표본으로 정상 결과를 만들지 않습니다. 초과 시 selector와 시간 창을 좁히거나 query step을 늘립니다.
이 예산은 Prometheus backend 자체의 query CPU/RAM 한도가 아니므로 backend 운영 한도도 별도로 둡니다.
