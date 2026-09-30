# Cross-Layer Diagnosis

Diagnosis는 XLayer의 **Collect → Correlate → Diagnose** 흐름에서 VERL 실행의 느린 구간을 조사하는 단계입니다.
Trainer·vLLM·Ray·sandbox·GPU·host·storage 등 연결한 source의 관측치를 run·step·phase 문맥과 측정 범위로 해석해 검토 가능한 bottleneck candidate를 만듭니다.
수집 경로와 correlation 원리는 [구현 구조](architecture.md#why-xlayer-exists)에 설명합니다.

이 문서는 기본 rule diagnosis를 설명합니다.
수집된 메트릭을 local open-weight 모델이 직접 읽고 진단하도록 하려면 [Optional Local LLM Diagnosis](local-llm.md)를 사용합니다.
LLM 경로는 rule catalog와 기존 판정을 입력에 넣지 않는 별도 선택 기능입니다.

## Investigation Workflow

```text
Run Overview
  |
  +> Slow step / interval
       |
       +> Bottleneck Summary
            |  symptom + same-run baseline
            |  candidate + supporting/counter/missing evidence
            |
            +> Cross-Layer Timeline
                 |  exact EventRecorder spans
                 |  approximate VERL step window
                 |  sampled Prometheus metrics
                 |
                 +> Step Detail / Compute / Storage / Logs
                 +> targeted PyTorch Profiler / Nsight or NCCL baseline
```

`ENABLE_LOGS=1`인 monitoring server에 `04 · Bottleneck Summary`와 `05 · Cross-Layer Timeline`이 provision됩니다.
Node collector의 Alloy가 run root의 `diagnostics/investigation/*.jsonl`과 `telemetry-events/*.jsonl`을 Loki에 보내야 후보와 span 행이 채워집니다.
Loki 없이도 완전한 진단은 `diagnostics/latest.json`과 `python -m xlayer_telemetry.show_run "$RUN_ROOT"`에서 읽을 수 있습니다.
[진단 설정](agent-rl.md#add-diagnostics)을 붙이지 않았다면 새 화면의 후보 표는 비어 있습니다.

### Practice with a Synthetic Candidate

실제 VERL·3FS 없이 rule과 Grafana 탐색 경로를 익히려면 Alloy가 읽는 `TELEMETRY_LOG_ROOTS` 아래에 새 run root를 만듭니다.
다음 예시는 node collector의 root가 `verl=$HOME/telemetry-runs`이고 `ENABLE_LOGS=1`인 경우입니다.

```bash
python -m xlayer_telemetry.diagnosis_demo \
  --output "$HOME/telemetry-runs/diagnosis-demo-001/telemetry" \
  --run-id diagnosis-demo-001
```

Loki가 파일을 받은 뒤 Grafana의 Bottleneck Summary에서 Run을 `diagnosis-demo-001`로 선택합니다.
이 결과의 `data_origin=synthetic`과 `synthetic-node`는 설명용 수치이고 Prometheus의 현재 resource 그래프와 같은 실측값이 아닙니다.
실제 GPU·3FS source를 확인하려면 뒤의 진단 설정으로 VERL run을 실행합니다.

1. Run Overview에서 target과 sample freshness를 확인하고 느린 step의 시간을 찾습니다.
2. Step Explorer에서 완료된 step을 선택해 `record_id`와 추정 시간 범위를 확인합니다.
3. Bottleneck Summary의 candidate 상태와 scope, missing evidence를 함께 읽습니다.
   Evidence details 표에서 supporting·counter·missing 행의 수치, source, entity를 확인합니다.
4. Cross-Layer Timeline에서 exact span, approximate step band, sampled metric의 시간 관계를 봅니다.
5. 관련된 기존 dashboard나 profiler artifact를 열어 후보를 반증하거나 뒷받침합니다.

## What the Signals Mean

| 개념 | 의미 | 예 |
| --- | --- | --- |
| Metric | 반복 측정한 수치 | GPU utilization, 3FS p99 latency |
| Event | 특정 시점에 발생한 일 | checkpoint start, tool call |
| Span | 실제 시작과 끝을 기록한 작업 | EventRecorder의 rollout span |
| Trace | 관련 span을 묶는 ID와 관계 | `trace_id`, `span_id` |
| Profile | 짧은 구간의 상세 실행 자료 | PyTorch Profiler trace, Nsight report |
| Topology | 구성 요소와 연결 정보 | trainer node, NIC, storage service |
| Correlation | 같은 시간·문맥에서 함께 변한 신호 | step time 증가와 3FS latency 증가 |
| Attribution | 특정 run이 자원을 실제로 사용했다는 증거 | client별 byte counter가 있을 때만 가능 |
| Diagnosis candidate | 조건을 만족한 조사 가설 | `storage_queue_saturation` |

`Correlation is not attribution.`
Trainer duration은 application/run 범위이고 GPU utilization은 device 범위, node disk I/O는 node/device 범위, 3FS service latency는 shared-service 범위입니다.
GPU·host·disk 지표를 특정 run의 병목 근거로 해석하는 실험은 해당 run이 자원을 단독 사용하거나 다른 workload의 부하를 통제한 환경에서 가장 신뢰할 수 있습니다.
같은 시간에 관측됐다는 이유만으로 3FS 전체 latency나 NIC traffic을 특정 run에 귀속하지 않습니다.
Candidate의 각 evidence는 `source`, `observation_scope`, `window`, `boundary_accuracy`, current/baseline 값을 보존합니다.

Baseline은 같은 run·node·worker·boundary scope에서 관측 시각이 이전인 유효 구간만 사용합니다.
파일 기록 순서와 무관하게 최근 5개를 고른 뒤 duration median에 가장 가까운 구간을 비교 대상으로 삼습니다.
`gen`과 canonical phase `rollout`이 함께 있으면 `rollout`을 한 번만 사용하며, 없을 때만 `gen`으로 대체합니다.
vLLM source 하나가 빠져도 나머지 신호의 engine identity를 대조하고, 다른 engine의 queue·KV 신호를 한 후보에 섞지 않습니다.

## Semantic Model and Adapter Boundary

```text
Primary integration: VERL                 XLayer core
  update / rollout / reward ------------> run > step/iteration > phase > span/event
  worker / rank / node / GPU ------------> resource identity + observation scope
  vLLM / Ray / sandbox / storage --------> related subsystem evidence
                                          |
                                          +> comparison > candidate > evidence
```

현재 자동 step adapter는 VERL file logger bridge입니다.
`diagnosis_analysis.py`는 framework 이름을 모르는 signal과 participant map을 입력으로 받으므로 다른 framework에도 adapter로 이식할 수 있습니다.
새 adapter는 원래의 step/iteration와 phase 이름을 보존하면서 이 공통 모델에 대응시키며, 모든 framework의 integration을 제공한다는 뜻은 아닙니다.
`EventRecorder`의 기존 `trace_id`·`span_id`를 재사용하며 OpenTelemetry Collector는 필수 요소가 아닙니다.

## Baseline and Rule State

진단은 같은 run, worker, boundary scope의 이전 유효 step 다섯 개 중 duration 중앙값에 가장 가까운 step을 baseline으로 고릅니다.
3FS를 설정한 run은 ClickHouse ingest 지연을 고려해 step 종료 후 기본 30초(`threefs.settle_seconds`)가 지난 뒤 첫 조회를 수행하고 wrapper도 마지막 step에 대해 이 시간을 기다립니다.
첫 조회에서 표본이 없거나 backend 오류가 발생하면 진단은 `analysis_status=provisional`로 남고 기본 10초 간격으로 최대 60초 동안 다시 조회합니다.
`retry_interval_seconds`와 `retry_seconds`로 이 범위를 조정할 수 있으며, wrapper는 종료 시 남은 진단을 한 번 더 조회해 `final`로 확정합니다.
재시도 중에는 `diagnostics.jsonl`에 같은 `trigger_record_id`의 revision이 누적되지만, Loki용 investigation 파일은 최종 결과에 대해서만 생성됩니다.
따라서 잠시 비어 있는 Bottleneck Summary는 `diagnostics/latest.json`의 `analysis_status`와 `missing_sources`를 먼저 확인합니다.
Source 자체가 없는 경우에도 재시도는 제한 시간에 끝나며, `step_event_time`이 없는 replay record는 재시도하지 않습니다.
그 baseline의 시간 구간으로 Prometheus와 3FS를 다시 조회하고, `comparison.signals`에 current, baseline, delta, delta percent를 기록합니다.
3FS latency는 양쪽 구간에 모두 있는 같은 `metricName`만 비교합니다.
GPU utilization은 같은 `gpu` label의 양쪽 표본을 비교하며, device별 짝을 만들 수 없으면 node aggregate로 scope를 낮춥니다.
vLLM의 KV usage·waiting·preemption은 Prometheus가 engine identity를 제공할 때 같은 engine의 series끼리 비교합니다.
여러 engine이 있으나 공통 identity를 확인할 수 없으면 `vllm:shared_engine_identity`를 누락 근거로 남기고 이를 강한 후보로 조합하지 않습니다.
동일한 이전 step이나 해당 표본이 없으면 baseline을 만들어 내지 않고 `missing_evidence`에 표시합니다.
Delta가 크다는 사실은 원인 증명이 아닙니다.

`weak_signal`은 rule의 주요 증상만 관측한 상태, `supporting_signal`은 둘 이상의 독립 조건을 관측했지만 필수 조건이 없거나 반대 근거가 있는 상태, `strong_signal`은 모든 필수 조건이 실제 측정으로 충족된 상태입니다.
Storage rule의 필수 조건이 모두 있어도 run별 3FS client bytes가 없으면 attribution에 필요한 자료를 별도 `missing_evidence`로 남깁니다.
수치 confidence를 계산하지 않습니다.
강한 상태도 시간적 상관을 뜻하며 실행별 사용량이나 인과관계를 뜻하지 않습니다.

| Rule | `strong_signal`에 필요한 evidence | 주의 |
| --- | --- | --- |
| `storage_queue_saturation` | 같은 3FS metric의 latency 증가, storage device busy 증가, storage throughput 정체 | Storage device와 throughput은 명시적으로 설정한 source여야 합니다. |
| `device_limited_storage` | 3FS latency 증가, storage device busy, 측정된 network headroom | GPU node의 local disk busy로 대체하지 않습니다. |
| `network_limited_storage` | 3FS latency 증가, network utilization, 측정된 storage device headroom | Link capacity 없는 RDMA bytes/s만으로 saturation을 판단하지 않습니다. |
| `small_io_pressure` | 신뢰할 수 있는 request size와 3FS latency 증가 | IOPS만으로 request size를 추론하지 않습니다. |
| `gpu_starvation` | step slowdown, GPU utilization 하락, vLLM waiting | Device 수치에 다른 workload가 섞일 수 있습니다. |
| `gpu_memory_pressure` | GPU memory ratio와 실제 eviction delta | GPU memory 사용률만으로 allocator 압력을 단정하지 않습니다. |
| `rollout_queue_backlog` | rollout duration 증가, vLLM waiting | vLLM은 shared service일 수 있습니다. |
| `kv_cache_pressure` | KV cache 사용률, preemption, waiting | 세 signal이 모두 필요합니다. |
| `communication_bound` | collective/weight sync duration 증가, RDMA activity 증가, GPU utilization 하락 | NIC 전체 수치는 run별 traffic이 아닙니다. |
| `host_memory_pressure` | available memory 부족과 swap/paging activity | Memory 부족만으로 강한 결론을 내리지 않습니다. |
| `straggler` | 세 명 이상 participant의 duration, 한 participant만 peer median보다 1.5배 이상 느림, peer spread 20% 이하 | Cluster 평균만으로 판단하지 않습니다. |
| `sandbox_local_storage_pressure` | Tool duration이 baseline보다 1.5배 증가하고 sandbox cgroup I/O PSI가 0.2 이상이며 지정한 local device busy가 0.9 이상 | Cgroup·device의 동시 관측이므로 최대 `supporting_signal`입니다. Tool duration만 있으면 나머지는 `missing_evidence`로 남깁니다. |

`storage_device_busy_ratio`, `network_utilization_ratio`, `threefs_throughput_bytes_per_second`, `storage_request_bytes`, `gpu_memory_usage_ratio`, `gpu_evictions_delta`는 기본 query에 없습니다.
Prometheus에서 가져오는 값은 실제 source와 해당 scope를 확인한 뒤 `prometheus.queries`에 명시적으로 넣습니다.
3FS distributions에 신뢰할 수 있는 request size metric이 있다면 `threefs.request_size_metric`에 그 **정확한** `metricName`을 지정해 `storage_request_bytes`를 만들 수도 있습니다.
특히 storage device metric이 어떤 storage node/device를 보는지, network utilization의 분모가 어떤 link capacity인지 확인해야 합니다.
기본 Node Exporter의 `disk_busy_ratio`는 trainer node의 local device이고 3FS storage SSD를 뜻하지 않습니다.
Sandbox rule은 설정의 `sandbox.enabled=true`일 때만 sandbox node와 명시한 backing `device`를 조회합니다.
`sandbox_io_pressure_ratio`는 sandbox cgroup 안의 여러 block device를 합친 대기이며, `device` busy는 선택한 host block device 전체의 값입니다.
실제 Docker container가 sampler cgroup 아래에 있는지, `io.stat`의 major:minor가 선택한 backing device와 맞는지 확인하지 않았다면 두 값의 일치를 근거로 귀속을 주장하지 않습니다.
`sandbox.node`는 dedicated 배치의 node로 지정하고, colocated 배치에서는 생략해 trainer node를 사용합니다.
Tool duration은 `agent_tool_call_duration_seconds`의 해당 run 표본을 사용합니다.
이 metric이 없고 `sandbox.events_dir`를 설정했다면 같은 step 시간 구간의 정상 종료 `tool.call` span을 사용하며, baseline도 같은 tool 이름으로 비교합니다.
이 query는 `run_id`로 tool metric을 고르므로 trainer·rollout node가 달라도 사용할 수 있지만, 여러 rollout worker의 표본이 같은 구간에 섞일 수 있습니다.
두 scope가 겹쳐도 특정 trajectory의 SSD 사용량이라는 인과 주장은 하지 않습니다.

## Clock and Node Selection

Multi-node diagnosis에서는 [설정 예제](../examples/multinode/diagnostics.json)를 복사하여 실제 cluster·node·storage device를 지정합니다.
`compute_node`는 GPU source, `rollout_node`는 native vLLM/Ray source, `storage_node`와 `storage_device`는 storage host의 특정 block device를 선택합니다.
생략한 node는 현재 step observer의 node를 사용합니다.
한 설정이 cluster의 모든 GPU를 자동 집계하는 것은 아니며, 다른 compute/rollout node 조합을 조사하려면 해당 node를 선택한 설정을 사용합니다.
Step Explorer의 manifest 기반 조회는 여러 node를 각각 보여줍니다.

`cluster`를 지정하면 기본 query는 cluster/job으로 제한되고 clock check도 기본 활성화됩니다.
사용자가 제공한 `prometheus.queries`는 그대로 사용하므로 각 selector에 `cluster="{cluster}"`와 source·node/device 조건을 명시해야 합니다.
지원 placeholder는 `{node}`, `{run_id}`, `{cluster}`, `{compute_node}`, `{rollout_node}`, `{storage_node}`, `{storage_device}`, `{sandbox_node}`, `{sandbox_device}`입니다.
Host 전체 disk busy와 별도 storage node의 `storage_device_busy_ratio`는 다른 signal이며 shared 3FS latency와 동일한 사용량으로 합치지 않습니다.

`clock_quality`는 current 구간의 node별 offset·sample age·kernel sync status를 보존하며 baseline이 있으면 그 구간도 검사합니다.
`aligned`는 설정한 screening 조건을 만족했다는 뜻이고, `unsafe`는 skew·staleness·unsynchronized 상태, `unknown`은 필요한 clock source 부족, `unchecked`는 기존 설정에서 검사하지 않았다는 뜻입니다.
Current 또는 baseline이 `unsafe`/`unknown`이면 `verdict=insufficient_data`, 빈 `candidates`와 빈 resource comparison을 기록하고 `missing_sources`에 clock 상태를 남깁니다.
Raw `evidence`와 workload duration은 inspect할 수 있으며 기존 bounded retry를 적용합니다.
Clock 상태를 나중에 고쳤다고 이미 끝난 step의 과거 timestamp가 복원되지는 않습니다.

기본 `clock.require_sync=true`, `max_skew_seconds=1`, `max_sample_age_seconds=30`입니다.
`clock.require_sync=false`는 offset/freshness만 확인하는 제한된 조사 모드이고 `clock.enabled=false`는 검사 자체를 제외합니다.
두 경우 모두 물리 node 동기화의 증거로 사용하지 않습니다.
기존 cluster 없는 config는 호환을 위해 `unchecked`로 실행되므로 multi-node 운영 전 cluster를 추가해야 합니다.

ClickHouse endpoint의 server clock만 확인해서 3FS distribution timestamp를 검증할 수는 없습니다.
`threefs.clock_nodes`에 timestamp를 만드는 실제 3FS producer node 목록을 넣고 해당 node의 host collector를 `TELEMETRY_TARGETS`에 등록합니다.
목록이 없으면 3FS candidate에 `threefs_producer_clock_alignment`가 missing evidence로 남고 `strong_signal`을 `supporting_signal`로 제한합니다.
이 clock 검사는 step별 3FS 사용량의 attribution을 제공하지 않습니다.

## Data and UI Boundaries

`diagnostics/latest.json`의 `schema_version=1`, `findings`, `evidence`, `missing_sources`는 기존 소비자를 위해 유지합니다.
새 `diagnosis_schema_version=1`, `symptom`, `comparison`, `candidates`는 추가 필드입니다.
필드 계약은 [Diagnosis JSON Schema](../config/diagnosis.schema.json)에 있습니다.
`candidates[].evidence`와 `counter_evidence`, `missing_evidence`가 판단 근거이고 `related_nodes`, `related_devices`, `related_spans`는 확인된 연결만 담습니다.
Loki용 `diagnostics/investigation/*.jsonl`은 이 결과의 평면 projection이며 원본보다 정보가 적습니다.

VERL file logger에 event timestamp가 없어서 bridge가 이미 존재하는 로그를 처음 읽거나 종료 후 남은 record를 replay하면, 해당 record의 `analysis_window`는 `unknown`으로 남습니다.
이 경우 step duration과 stage 이름은 보존하지만 현재 시각의 GPU·network·storage 수치를 과거 step에 연결하지 않습니다.
Bridge가 파일 끝을 따라가며 새 record를 읽은 경우에만 관측 시각에서 reported duration을 뺀 approximate window를 사용합니다.

VERL file logger는 stage duration만 주고 stage별 실제 시작·끝은 주지 않습니다.
Timeline은 file logger에서 추정한 전체 step band만 `approximate`로 표시하며 rollout·reward·update 순서를 추정해 그리지 않습니다.
`EventRecorder`가 만든 span은 producer node의 start/end timestamp로 `exact` bar에 표시하고, Prometheus 선은 sampling 간격의 관측치입니다.
Wall clock jump가 감지된 `clock_discontinuity` span은 exact bar에서 제외되며 duration 자체는 monotonic clock 값으로 보존합니다.
Span 표의 `span_id`에서 같은 시간 창의 Step Detail·Logs·Diagnosis로 이동하고, `trace_id`로 기록한 관련 event를 아래 표에서 필터링할 수 있습니다.
Clock skew, scrape 간격, shared resource의 다른 사용자 때문에 눈으로 겹친 구간도 추가 확인이 필요합니다.

### Follow the Data Path

```text
Trainer > rollout / vLLM > GPU node NIC > 3FS service > storage node > SSD
             step/span          network        shared-service        device
```

Bottleneck Summary의 `Inspect declared topology and storage path` 링크는 기존 Data & Storage 화면의 component·edge 표로 이동합니다.
그 edge는 사용자가 제공한 topology 관계이며 throughput·latency·error·queue가 실제로 측정된 edge만 해당 값을 붙일 수 있습니다.
Node 전체 network byte를 특정 trainer-to-storage edge의 traffic이라고 표시하지 않습니다.

## Targeted Deep Dive

GPU 또는 communication candidate가 있으면 [profiler 실행 예제](dashboards.md#capture-a-short-trace)로 짧은 rank 구간을 기록합니다.
Communication candidate는 [NCCL baseline](dashboards.md#measure-a-communication-baseline)과 같은 hardware 경로에서 비교합니다.
Profile 파일은 native PyTorch Profiler/Nsight 도구로 열며, XLayer는 profiler 자체를 구현하거나 상시 full profiling을 켜지 않습니다.
`show_run`은 run 아래의 확인된 profiler/NCCL artifact 경로를 출력합니다.
