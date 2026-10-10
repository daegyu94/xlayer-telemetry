# Cross-Layer Diagnosis

> **Reference** · 기본 작업은 [diagnosis guide](diagnosis.md)에서 시작합니다. 아래에는 기존 운영·구현·해석 세부 정보와 기록을 보존합니다.

:::{container} xlayer-question-index

**찾으려는 질문부터 선택하세요**

| 질문 / 작업 | 바로 볼 절 |
| --- | --- |
| 진단 결과가 비어 있다면 | [확인 →](#investigation-workflow) |
| 비교 가능한 baseline을 고르려면 | [확인 →](#select-a-comparable-workload) |
| 짧은 Step / sampling / stale 영향을 보려면 | [확인 →](#read-sampling-quality) |
| Source·node·clock 조건을 확인하려면 | [확인 →](#clock-and-node-selection) |
| Query 비용과 timeout을 정하려면 | [확인 →](#backend-query-budget) |
| Stored artifact / Loki projection을 확인하려면 | [확인 →](#follow-the-data-path) |

:::

<details>
<summary>이 Reference의 범위와 전제</summary>

Diagnosis는 XLayer의 **Collect → Correlate → Diagnose** 흐름에서 VERL 실행의 느린 구간을 조사하는 단계입니다.
Trainer·vLLM·Ray·sandbox·GPU·host·storage 등 연결한 source의 관측치를 run·step·phase 문맥과 측정 범위로 해석해 검토 가능한 bottleneck candidate를 만듭니다.
수집 경로와 correlation 원리는 [구현 구조](concepts.md)에 설명합니다.

이 문서는 기본 rule diagnosis를 설명합니다.
수집된 메트릭을 local open-weight 모델이 직접 읽고 진단하도록 하려면 [Optional Local LLM Diagnosis](local-llm.md)를 사용합니다.
LLM 경로는 rule catalog와 기존 판정을 입력에 넣지 않는 별도 선택 기능입니다.

</details>

## Investigation Workflow

![Run·step·candidate·evidence·Timeline과 targeted profile의 조사 흐름](figures/diagrams/investigation-flow.svg)

| 읽으려는 결과 | 필요한 경로 | 비어 있으면 |
| --- | --- | --- |
| Grafana candidate / span | Server `ENABLE_LOGS=1`, Bottleneck Summary·Cross-Layer Timeline provisioning | [진단 설정](diagnosis.md#1-진단-연결) 확인 |
| Loki projection | Node Alloy가 `diagnostics/investigation/*.jsonl`·`telemetry-events/*.jsonl`을 Loki로 전송 | Source path·label·query window 확인 |
| 완전한 저장 diagnosis | `diagnostics/latest.json`, `python -m xlayer_telemetry.show_run "$RUN_ROOT"` | Loki 없어도 조회 가능 |

### Practice with a Synthetic Candidate

기본 CLI 설정에서 `ENABLE_LOGS = true`를 지정하고 `xltel restart`로 Loki·Alloy를 연결합니다.
GPU 없는 host는 `ENABLE_GPU_METRICS = false`도 지정합니다.
이미 `xltel up`으로 시작한 stack을 사용하므로 별도 demo server를 띄우지 않습니다.

```bash
python -m xlayer_telemetry.demos.diagnosis \
  --output "$HOME/telemetry/runs/diagnosis-demo-001" \
  --run-id diagnosis-demo-001 --node gpu-local
xltel inspect diagnosis-demo-001
```

Custom config에서는 output을 `TELEMETRY_RUNS_ROOT` 아래, node를 collector의 `NODE_NAME`과 동일하게 지정합니다.
Collector와 Alloy가 파일을 읽은 뒤 Start Here의 해당 Run → Run Overview의 Step 127 Duration → Bottleneck Summary로 이동합니다.
Snapshot은 기본 300초 후 live 목록에서 제외되지만 step·diagnosis artifact는 보존됩니다.
이 예제의 `data_origin=synthetic`과 `producer=synthetic`은 설명용 수치이며 host·GPU·3FS 실측값이 아닙니다.
Timeline의 실제 host metric과 예제의 storage evidence를 같은 실험 결과로 해석하지 않습니다.
실제 run에는 [진단 설정](diagnosis.md#1-진단-연결)을 연결하고 필요한 source를 등록합니다.

1. Run Overview에서 target과 sample freshness를 확인하고 느린 step의 시간을 찾습니다.
2. Run Overview의 완료 step 목록에서 step을 선택해 `record_id`와 추정 시간 범위를 확인합니다.
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
Prometheus evidence의 `query`는 context 변수를 치환한 실제 실행식이며 canonical signal 이름에 연결됩니다.
Tool event를 대신 사용하는 evidence는 trace/span과 span 경계를 보존하고 Prometheus sampling 품질을 이어받지 않습니다.

Baseline은 같은 run·node·worker·boundary scope의 이전 유효 구간에서 고릅니다(세부 조건은 아래).
Phase alias는 canonical을 우선해 중복 사용하지 않고, vLLM은 같은 engine의 signal끼리 비교합니다.

## Semantic Model and Adapter Boundary

![VERL 실행 개념을 framework-independent XLayer context로 mapping](figures/diagrams/framework-mapping.svg)

현재 자동 step adapter는 VERL file logger bridge입니다.
`diagnosis_analysis.py`는 framework 이름을 모르는 signal과 participant map을 입력으로 받으므로 다른 framework에도 adapter로 이식할 수 있습니다.
새 adapter는 원래의 step/iteration와 phase 이름을 보존하면서 이 공통 모델에 대응시키며, 모든 framework의 integration을 제공한다는 뜻은 아닙니다.
`EventRecorder`의 기존 `trace_id`·`span_id`를 재사용하며 OpenTelemetry Collector는 필수 요소가 아닙니다.

## Optional Exporter Metric Profiles

기본 진단의 12개 query는 유지합니다. 추가 evidence는 기존 exporter를 재사용하는
`prometheus.metric_profiles`에서 필요한 profile만 선택합니다. 별도 collector나
기본 scrape 주기를 추가하지 않습니다. `examples/verl/diagnostics.json`은
`["host", "disk", "vllm"]`을 선택한 예입니다.

```json
{
  "prometheus": {
    "url": "http://monitor.internal:19090",
    "metric_profiles": ["host", "disk", "vllm"]
  }
}
```

위 코드는 기존 diagnostics config의 `prometheus` 설정에 합칠 부분입니다.
전체 config의 `schema_version` 및 기존 source/clock 설정은 유지합니다.

| Profile | 추가 query 수 | 관측 항목 |
| --- | ---: | --- |
| `host` | 6 | CPU busy, CPU/memory/I/O PSI stall fraction, major faults/s, runnable processes |
| `disk` | 7 | read/write/flush mean latency, average queue depth, read/write IOPS, write bytes/s |
| `filesystem` | 4 | available bytes/inodes ratio, read-only, device error |
| `network` | 7 | RX/TX bytes/s, RX/TX error/drop rates, TCP retransmits/s |
| `rdma` | 3 | receive errors, transmit discards, transmit-wait ticks/s |
| `vllm` | 6 | per-entity TTFT/TPOT/queue/E2E p95, prompt/generated tokens/s |
| `kv_offload` | 5 | load/store bytes/s, allocation failures/s, sync/async lookup p95 |
| `mooncake` | 6 | Canonical connector RPC p95, DFS read/write/staging p95, read bytes/s, read failed keys/s |
| `mooncake_storage` | 5 + 선택적 Master 3 | Write bytes·성공 key ops·write error/skips. Master allocation/capacity/admission은 명시 node가 있을 때만 |
| `ray` | 5 | spilled bytes, disk-backed mmap bytes, pending spill/restore bytes, worker eviction rate |
| `dcgm` | 7 | GPU utilization, tensor/DRAM activity, PCIe RX/TX bytes/s, PCIe replays/s, last XID code |

`host`/`disk`/`filesystem`/`network`/`rdma`는 Node Exporter의 `instance={node}`를
조회합니다. Native profile은 `job="native"`, 등록된 `telemetry_source`와
`node={rollout_node}`(vLLM/Ray), `node={compute_node}`(DCGM)를 요구합니다.
노드·cluster labels를 실제 discovery와 맞춥니다. Storage service node의 독립적인
장치/네트워크 관측에는 기존 `storage_node`/`storage_device` 및 명시적
`prometheus.queries` override를 사용합니다. 이 profile들이 trainer node를 모든
3FS/pNFS storage node로 간주하지는 않습니다.

Mooncake connector/client도 `node={rollout_node}`의 실제 native endpoint를 사용합니다. `mooncake`를 선택할 때만 packaged canonical dashboard의 고정 panel/ref를 읽고 단위·매크로 계약을 검사합니다. RPC·client DFS·D2H staging은 별도의 관측이며 새 strong rule이나 계층 간 dependency를 추론하지 않습니다.

- `prometheus.queries`의 동일 signal override가 profile보다 우선합니다.
- Unknown/중복 profile은 오류로 거절합니다. 전체 profile은 최대 64개 추가 query이며 Master node가 없으면 3개를 조회하지 않습니다.
  예제의 3개 profile은 총 31개이며 baseline이 있으면 최대 62개 metric request가
  필요합니다. Clock/freshness query는 별도입니다. 전체 profile을 무조건 켜지 말고
  `query_execution`/`missing_sources`를 확인합니다. 기존 30초 query budget과
  60초 worker deadline을 늘리지 않습니다.
- `comparison.signals`에 단위, 실행 query, window statistic 및 선택한 entity labels를
  보존합니다. 현재 window에서 선택한 entity와 **동일한 labels**의 baseline만
  비교합니다. 장치/endpoint 교체나 label 불일치는 baseline을 누락으로 기록합니다.
  서로 다른 signal의 최대값이 같은 entity라는 의미는 아닙니다.
- Rate는 reset-aware `rate(...[1m])`입니다. 짧은 step에서는 앞선 구간이 포함될 수
  있으며 per-step byte total이 아닙니다. Idle/zero denominator의 latency/capacity
  ratio는 0으로 꾸미지 않고 제외합니다. Device busy fraction은 NVMe의 실제 포화나
  NAND writes/WAF를 증명하지 않습니다.
- vLLM p95는 각 endpoint/model/engine의 histogram으로 계산한 추정치입니다.
  TPOT는 `request_time_per_output_token_seconds`이며 inter-output latency와 다릅니다.
  KV offload byte query는 새 `kv_offload_{load,store}_bytes_total`을 우선하고
  legacy `kv_offload_total_bytes_total{transfer_type=...}`를 fallback으로 사용해
  둘 다 노출될 때 중복 합산하지 않습니다. Lookup/allocation metrics는 최신 connector
  구현에만 있을 수 있습니다. Connector가 어떤 offload medium을 쓰는지는 배포 설정으로
  확인해야 하며 byte metric만으로 NVMe/3FS 사용을 판단하지 않습니다.
- Ray `SPILLED`/`MMAP_DISK`는 현재 gauge 값으로 throughput이 아닙니다.
  Pending spill/restore는 raylet 구현의 version-dependent gauge입니다.
  Ray의 lifetime active-transfer throughput을 step-window throughput으로 사용하지 않습니다.
- DCGM 미지원 sentinel/range 밖 값은 query에서 제외합니다. PCIe profiling metric은
  이미 bytes/s인 gauge이고 XID는 마지막 error code라 `rate()`하지 않습니다.
  XID 발생 시점은 GPU log로 확인해야 합니다. DCGM은 기존 NVML series와 device identity를
  임의로 합치거나 대체하지 않고 독립적인 evidence로 남습니다.
- PSI와 step/actor update/critic update/checkpoint slowdown이 겹치거나 rollout slowdown과
  queue p95 증가가 겹치면 최대 `supporting_signal` 후보를 만듭니다. Resource window는
  전체 step이므로 특정 stage의 정확한 I/O attribution을 주장하지 않습니다.
  높은 busy percentage만으로 새로운 stall 후보를 만들지 않습니다.

### Source contracts and remaining gaps

Node Exporter는 bundled **v1.9.1**의
[PSI](https://github.com/prometheus/node_exporter/blob/v1.9.1/collector/pressure_linux.go),
[diskstats](https://github.com/prometheus/node_exporter/blob/v1.9.1/collector/diskstats_linux.go),
[InfiniBand](https://github.com/prometheus/node_exporter/blob/v1.9.1/collector/infiniband_linux.go)
계약을 확인했습니다. Newer mlx5 hardware ACK/ECN/retry counters, NVMe physical
writes/endurance, filesystem operation latency/errors, per-process/cgroup ownership,
NCCL per-collective stall 시간은 이 profile에 없습니다. 필요하면 별도 검증된 exporter,
profiler 또는 application spans를 연결합니다. Device I/O 값은 shared-device context이며
정확한 run별 WAF나 physical writes로 환산하지 않습니다.

vLLM은 commit `5f30fc7031cae49bf51073fc953d419b08f8887c`의
[request metrics](https://github.com/vllm-project/vllm/blob/5f30fc7031cae49bf51073fc953d419b08f8887c/vllm/v1/metrics/loggers.py)와
[offload metrics](https://github.com/vllm-project/vllm/blob/5f30fc7031cae49bf51073fc953d419b08f8887c/vllm/distributed/kv_transfer/kv_connector/v1/offloading/metrics.py),
DCGM은 commit `fafd151148052628061a80450b4ee037a5fa0c3c`의
[default counters](https://github.com/NVIDIA/dcgm-exporter/blob/fafd151148052628061a80450b4ee037a5fa0c3c/etc/default-counters.csv)를
기준으로 합니다. 설치 버전의 실제 `/metrics`에서 이름·labels·지원 여부를 확인합니다.
Ray의 documented object-store/eviction 계약은
[Ray 2.58 system metrics](https://docs.ray.io/en/latest/ray-observability/reference/system-metrics.html)를
확인했습니다. Pending spill/restore는 Ray commit `43b706d733c590cf497cc322c57b9fd610bf214f`의
[metric definitions](https://github.com/ray-project/ray/blob/43b706d733c590cf497cc322c57b9fd610bf214f/src/ray/raylet/metrics.h)와
[emission semantics](https://github.com/ray-project/ray/blob/43b706d733c590cf497cc322c57b9fd610bf214f/src/ray/raylet/local_object_manager.cc)를
확인했습니다. Source semantics/CPU fixture 검증이며 실제 GPU, RDMA, Ray, vLLM 및
3FS workload에서의 수집 또는 학습 성능 검증을 뜻하지 않습니다.

3FS distributions 조회도 byte cap 8 MiB 외에 최대 1,000 metric을 허용합니다.
`LIMIT 1001`로 초과를 확인해 누락으로 보고하며 부분 결과를 정상으로 내보내지 않습니다.
3FS의 raw counter table은 producer별 reset/gauge 의미를 모르므로 자동 rate를 만들지 않습니다.

## Backend Query Budget

`query_budget_seconds`는 한 번의 rule analysis가 backend 요청에 사용할 budget이며 기본 30초입니다.
Prometheus의 current·baseline·clock·freshness query와 3FS 조회가 budget을 공유합니다.
남은 시간보다 긴 HTTP timeout은 줄이고 budget이 소진되면 추가 요청을 생략합니다.

연결 오류·timeout·HTTP 429/5xx가 발생한 backend에는 같은 analysis에서 재요청하지 않습니다.
다른 backend 조회는 남은 budget으로 진행하며, 다음 analysis에서 다시 연결합니다.
빈 결과·잘못된 query·malformed JSON은 backend 전체의 연결 장애로 취급하지 않습니다.

`query_execution`에 backend별 `attempted`·`failed`·`skipped`와 `unavailable`, 소요 시간을 기록하고 `show_run`에도 표시합니다.
생략된 query는 `missing_sources`로 남아 기존 bounded retry 대상이 됩니다.
이 budget은 새 요청의 admission과 socket timeout을 제한하며 DNS·느리게 이어지는 응답·로컬 파일 I/O를 강제로 중단하는 hard deadline은 아닙니다.

CLI와 VERL wrapper는 별도로 `analysis_deadline_seconds`(기본 60초)를 적용합니다.
History/report를 읽는 scheduling 요청과 각 interval의 rule analysis는 재사용 가능한 별도 worker process에서 실행합니다.
제한 시간을 넘으면 worker를 TERM 후 필요시 KILL로 중단하고, 다음 요청에서 새 worker를 시작합니다.
`analysis_execution`에 상태·소요 시간·deadline을 기록하며 중단 결과는 후보 없이 `insufficient_data`, `missing_sources=["analysis:deadline_exceeded"]`로 남습니다.
Step 결과는 기존 bounded retry를 따르고 report·Loki projection 저장은 부모 process가 담당합니다.

설정은 scheduling 요청과 개별 analysis의 제한이며 run 전체나 batch 합계의 제한은 아닙니다.
Worker 정리에는 별도로 최대 0.5초의 grace를 사용하고, 부모의 report 저장 경로는 node-local filesystem을 권장합니다.
Kernel의 uninterruptible I/O는 즉시 종료를 보장할 수 없으며, 종료되지 않은 worker가 있으면 추가 worker 생성을 억제합니다.
`analysis_deadline_seconds=0`은 process 격리를 끄는 선택적 설정입니다.
Python에서 `DiagnosticEngine.analyze()`를 직접 호출하면 in-process로 실행되므로 이 deadline은 적용되지 않습니다.

History/report·tool/sandbox event는 process-local incremental cache로 새 newline까지 추가된 부분만 파싱합니다.
기본 `jsonl_cache` 한도는 100,000 record·원본 64 MiB·128 file이며 한 worker 내 파일들이 한도를 공유합니다.
File 교체·축소·감지된 rewrite는 다시 읽고, 한도를 넘는 파일은 전체 scan으로 처리해 evidence를 잘라내지 않습니다.
Producer는 append-only JSONL을 쓰거나 파일을 atomic replace해야 하며, 기존 파일 중간을 제자리에서 수정하는 방식은 지원하지 않습니다.
Cache는 재시작 후 원본에서 복원하고 `jsonl_cache` report field에 처리량을 표시합니다.
`jsonl_cache.enabled=false`로 끌 수 있으며, 이 cache는 시간 구간을 직접 찾는 persistent index가 아니므로 기존 record의 필터링 비용은 남습니다.

CPU fixture로 cold/warm parse 비용을 확인하려면 다음을 실행합니다.

```bash
python -m examples.investigation.validate_runtime --output artifacts/runtime-validation
```

같은 명령은 1,000-worker snapshot fixture의 cache 미사용·첫 poll·반복 poll 비용과 실제 파일 읽기 횟수도 비교합니다.
Local filesystem 측정이며 전체 collector latency나 학습 throughput 개선을 뜻하지 않습니다.

## Baseline and Rule State

### Baseline / 이력 선택

| 항목 | 계약 |
| --- | --- |
| 후보 구간 | 같은 Run·node·worker·boundary scope·execution mode의 최근 이전 유효 Step 5개 |
| 대표 baseline | Duration median에 가장 가까운 실제 interval. 별도 comparable history 전체를 복사하지 않음 |
| 명시 identity | Cluster·producer·role·rank·local rank·GPU도 같아야 함. 한쪽 누락을 다른 쪽 값으로 대신하지 않음 |
| 순서 / 제외 | 파일 기록 순서가 아닌 관측 시각. Future·unknown·clock-discontinuity 제외 |
| Stage slowdown | 같은 유효 history 5개 사용. Canonical phase alias 우선, 중복 사용 금지 |

### Retry / revision / 저장 복구

Optional `threefs`·`sandbox`의 누락이나 `null`은 비활성 source로 정규화합니다. 기본 진단은 계속 실행하며, 해당 source의 telemetry를 측정값 0으로 만들지 않습니다.

| 상황 | 동작 / 확인할 field |
| --- | --- |
| Retry 시작 | Batch 시작이 아닌 각 Step의 첫 분석 시작 시각 기준 |
| 다음 시도 | 분석 종료 후 `retry_interval_seconds`, 원래 retry deadline 이내. 분석이 기간을 소진하면 final로 남겨 즉시 반복 방지 |
| 3FS ingest 지연 | 기본 30초 `threefs.settle_seconds` 후 조회. Wrapper도 마지막 Step을 기다림 |
| 표본 부재 / backend 오류 | `analysis_status=provisional`, 기본 10초 간격·최대 60초 재시도 (`retry_interval_seconds`, `retry_seconds`) |
| 일부 source / baseline만 누락 | 같은 제한 시간 안에서 누락 query 재조회. 이미 수집한 current evidence는 버리지 않음 |
| 영구적으로 빈 optional source | Deadline까지 대기 후 종료. Provisional만으로 backend 장애라고 판단하지 않음 |
| Wrapper 종료 | 한 번 더 조회해 final 확정. `step_event_time` 없는 replay는 재시도하지 않음 |
| Revision / UI | `diagnostics.jsonl`은 같은 `trigger_record_id`의 revision 보존. Loki projection은 final만 생성 |
| Report 이후 파생 파일 쓰기 실패 | 다음 분석에서 저장 report로 `latest.json` / investigation 복구. Backend 재조회·revision 추가·기존 immutable investigation 재작성 없음 |

Retry와 settling의 경과시간은 관측 timestamp와 별도의 monotonic clock으로 계산합니다. Clock 보정 만료·복구가 대기를 갑자기 끝내거나 늘리지 않으며, `retry_timing`에는 boot별 clock ID와 경과시간 deadline을 보존합니다. 이전 journal이나 다른 boot의 경과시간은 추정하지 않고 새로 제한된 retry 기간을 시작합니다. `first_attempt_at`·`retry_at`은 기존 timestamp 표시를 유지하며 실제 예약 판단에는 사용하지 않습니다.
| 후보 표가 비어 있음 | 먼저 `diagnostics/latest.json`의 `analysis_status` / `missing_sources` 확인 |

### Current / Baseline 비교의 단위와 scope

| Source / 조건 | 비교 계약 | 해석 금지 |
| --- | --- | --- |
| Prometheus / 3FS window | `comparison.signals`에 current·baseline·delta·delta percent 기록 | 표본·baseline 생성 또는 큰 delta를 원인 증명으로 해석 |
| Step·rollout·actor/critic·checkpoint duration | 기존 seconds, `unit=s`, reported-duration statistic 유지. Communication은 보고된 stage 합계 | Approximate boundary를 exact span으로 변환·3FS raw latency의 단위 추정 |
| 3FS identity | 같은 `metricName` + 전체 producer identity. `host`·`tag`·`mount_name`·`instance`·`io`·`uid`·`method`·`pod`·`thread`·`statusCode`를 labels/entity에 보존 | 여러 client/node/operation을 하나로 결합 |
| `max_observed_p99` | 한 entity의 유효 report 구간별 p99 중 최대. `count=0`은 extrema·weighted mean·freshness에 기여하지 않음 | Global p99, p99의 재집계 |
| 3FS 조회 비용 | 기존 8 MiB·1,000 entity 상한. 초과 시 source filter 좁히기 | 조용한 truncation을 complete coverage로 취급 |
| Collection series | 정수 source timestamp별 값. Loki index time과 sample time 분리, reset/gauge·gap·clock coverage 보존 | 실제 interval / complete I/O coverage 없이 storage strong 승격 |
| Legacy artifact | 다른 legacy metric-only artifact끼리 비교. Latency/request-size도 metric 이름 외 producer identity 일치 필요 | 명시 identity와 혼합·partial/duplicate에서 첫/마지막 행 임의 선택 |
| GPU | 같은 device끼리 비교. 짝이 없으면 node aggregate scope로 낮춤. 표는 utilization 감소가 가장 큰 GPU 선택 | Run별 장치 사용량·node 평균으로 해석 |
| vLLM | 같은 engine signal 비교. 공통 identity가 없으면 `vllm:shared_engine_identity` missing | 다른 engine 조건을 합친 strong evidence |

Collection point와 reset-after-report counter의 상세는 [Storage correlation 계약](storage-correlation.md)을 따릅니다.

### Candidate 상태

| 상태 | 실제 근거 | 보류하는 해석 |
| --- | --- | --- |
| `weak_signal` | 주요 증상 관측 | 나머지 필수 조건을 관측했다고 가정 |
| `supporting_signal` | 둘 이상 독립 조건, 필수 조건 누락 또는 counter evidence | Scope/quality가 부족한 상태의 strong 승격 |
| `strong_signal` | Rule 필수 조건이 실제 측정으로 충족 | Resource attribution / causality 확정 |

```{admonition} 판정의 경계
:class: important

수치 confidence를 계산하지 않습니다. Storage 필수 조건을 충족해도 Run별 3FS client bytes가 없으면 attribution 자료를 별도 missing evidence로 남깁니다. Strong도 시간적 상관이며 실행별 사용량·원인은 아닙니다.

Prometheus/3FS의 resource evidence를 사용하는 candidate는 `resource_attribution=not_established`와 `run_resource_attribution_unverified`를 남깁니다. 이 marker는 required pressure sample 누락과 구분합니다. 모든 관측 조건이 충족돼 `strong_signal`이어도 공유 node/service가 특정 Run의 원인이라는 뜻은 아닙니다.

```

`signal_strength`는 기존 `state`의 최종 관측 강도이며, `run_relation`은 별도입니다. Candidate 카드에서 Evidence를 열기 전에도 관계 상태를 확인합니다.

| `run_relation` | 해석 |
| --- | --- |
| `configured` | 선택 Run에 대해 명시한 cluster·rollout node·endpoint와 일치. 요청 소유권·causality는 미검증 |
| `shared_unverified` | 해당 window의 shared/node/service context이며 Run 관계는 미확인 |
| `unlinked` | 명시한 Run endpoint와 관측된 native engine이 다름. 선택 Run의 병목으로 귀속하지 않음 |

기존 diagnostics JSON config에 아래 optional key를 사용할 수 있습니다. 최대 16개이며 `run_id`, `cluster`, `rollout_node`를 함께 선언합니다. Query filter나 모델 이름만으로 관계를 추정하지 않고, 설정된 endpoint도 operation attribution으로 해석하지 않습니다. `verified`는 현재 지원하지 않습니다.

```json
"run_id": "run-084",
"cluster": "lab",
"rollout_node": "gpu-node-0",
"run_engine_instances": ["vllm-node-0:8000"]
```

| Rule | 주요 evidence | 판정 범위·주의 |
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
| `host_cpu_stalls`, `host_memory_stalls`, `host_io_stalls` | Step slowdown과 해당 resource의 host PSI 증가 | Node 전체 대기와의 동시 관측이며 최대 `supporting_signal`입니다. |
| `actor_update_cpu_stalls`, `critic_update_cpu_stalls`, `checkpoint_io_stalls` | 해당 stage slowdown과 CPU 또는 I/O PSI 증가 | Resource window는 전체 step이며 최대 `supporting_signal`입니다. |
| `ray_object_store_disk_pressure` | Step slowdown과 Ray disk-backed mmap bytes | 현재 gauge이며 spill throughput이 아닙니다. 최대 `supporting_signal`입니다. |
| `rollout_queue_latency` | Rollout slowdown과 동일 entity의 queue p95 증가 | Histogram 추정치와의 상관이며 최대 `supporting_signal`입니다. |
| `straggler` | 세 명 이상 participant의 duration, 한 participant만 peer median보다 1.5배 이상 느림, peer spread 20% 이하 | Cluster 평균만으로 판단하지 않습니다. |
| `sandbox_local_storage_pressure` | Tool duration이 baseline보다 1.5배 증가하고 sandbox cgroup I/O PSI가 0.2 이상이며 지정한 local device busy가 0.9 이상 | Cgroup·device의 동시 관측이므로 최대 `supporting_signal`입니다. Tool duration만 있으면 나머지는 `missing_evidence`로 남깁니다. |

`gpu_memory_usage_ratio`는 GPU sampler의 device used/total bytes로 계산하며 capacity가 없거나 0이면 만들지 않습니다.
Memory 사용률만 높고 eviction evidence가 없다면 `gpu_memory_pressure`의 필수 조건을 충족하지 않습니다.
Custom eviction query는 memory와 같은 cluster·node 및 `gpu`/`gpu_uuid` 식별 field를 유지해야 합니다.
Identity가 다르거나 aggregate만 있거나 한 GPU에 여러 process series가 있어 결합이 모호하면 eviction 근거를 제외하고 `gpu_memory_entity_match`를 missing evidence로 남깁니다.
`storage_device_busy_ratio`, `network_utilization_ratio`, `threefs_throughput_bytes_per_second`, `storage_request_bytes`, `gpu_evictions_delta`는 기본 query에 없습니다.
Prometheus에서 가져오는 값은 실제 source와 해당 scope를 확인한 뒤 `prometheus.queries`에 명시적으로 넣습니다.
3FS distributions에 신뢰할 수 있는 request size metric이 있다면 `threefs.request_size_metric`에 그 **정확한** `metricName`을 지정해 `storage_request_bytes`를 만들 수도 있습니다.
특히 storage device metric이 어떤 storage node/device를 보는지, network utilization의 분모가 어떤 link capacity인지 확인해야 합니다.
기본 Node Exporter의 `disk_busy_ratio`는 trainer node의 local device이고 3FS storage SSD를 뜻하지 않습니다.

Ray pending task는 실행 node가 정해지기 전 owner가 기록할 수 있으므로 기본 query는 rollout node로 제한하지 않습니다.
`cluster`·`SessionName`별 `PENDING.*` 합계를 비교하며 `scheduling_backlog`는 cluster/session 범위의 supporting signal입니다.
여러 Ray cluster의 session 구분이 없는 exporter는 `prometheus.queries.ray_pending_tasks`로 배포에 맞는 query를 지정합니다.

Sandbox rule은 설정의 `sandbox.enabled=true`일 때만 sandbox node와 명시한 backing `device`를 조회합니다.
`sandbox_io_pressure_ratio`는 sandbox cgroup 안의 여러 block device를 합친 대기이며, `device` busy는 선택한 host block device 전체의 값입니다.
실제 Docker container가 sampler cgroup 아래에 있는지, `io.stat`의 major:minor가 선택한 backing device와 맞는지 확인하지 않았다면 두 값의 일치를 근거로 귀속을 주장하지 않습니다.
`sandbox.node`는 dedicated 배치의 node로 지정하고, colocated 배치에서는 생략해 trainer node를 사용합니다.
`sandbox.events_dir`에 유효한 `tool.call` span이 있으면 이를 우선 사용하고 baseline도 같은 tool 이름으로 비교합니다.
진단 process가 이 directory를 읽을 수 있어야 합니다.
파일명과 producer에 관계없이 같은 run의 정상 종료 `tool.call` 중 `attributes.tool`이 있고 분석 구간 안에서 시작·종료한 span을 사용합니다.
그 span이 없거나 directory를 설정하지 않았다면 `agent_tool_call_duration_seconds`의 해당 run 표본을 사용합니다.
Prometheus tool query는 `run_id`로 표본을 고르므로 trainer·rollout node가 달라도 사용할 수 있지만, 여러 rollout worker의 표본이 같은 구간에 섞일 수 있습니다.
두 scope가 겹쳐도 특정 trajectory의 SSD 사용량이라는 인과 주장은 하지 않습니다.

### Select a Comparable Workload

기존 baseline 선택은 유지하지만 기본 `workload_comparability`는 `unverified`입니다. 완료된 step·rollout·training·checkpoint·communication duration의 baseline 비교를 사용하는 candidate는 이 상태에서 최대 `supporting_signal`이며, `workload_comparability_unverified`를 missing evidence로 남깁니다. 현재 KV pressure처럼 duration baseline을 사용하지 않는 관측은 이 제한으로 낮추지 않습니다.

Token 수·evaluation·checkpoint 차이를 제외하려면 diagnostics config에 비교 조건을 추가합니다. Raw duration·baseline·delta는 그대로 남기며, workload가 다르다는 이유만으로 관측값을 없애지 않습니다.

```json
"baseline": {
  "match_fields": ["perf/total_num_tokens", "has_evaluation", "has_checkpoint"],
  "relative_tolerance": 0.1,
  "normalize_by": "perf/total_num_tokens"
}
```

Bridge는 logger에 존재하는 `perf/total_num_tokens`, `prompt_length/mean`, `response_length/mean`, `data/train_batch_size`, `train_batch_size`, `policy_version`을 history의 `workload`에 보존합니다.
`has_evaluation`·`has_checkpoint`는 보고된 stage timing에서 계산합니다.
Logger가 batch나 policy 값을 제공하지 않으면 만들어내지 않으며, 선택한 field가 어느 쪽에든 없으면 해당 baseline은 제외합니다.
Numeric field는 이전 값 기준 tolerance로 비교하고 boolean field는 정확하게 일치해야 합니다. `policy_version`과 `fully_async/count/current_param_version`을 `match_fields`에 넣으면 tolerance와 관계없이 같은 version만 비교합니다. 이 두 값은 연속적인 workload 크기가 아니라 식별자입니다.
조건을 만족하는 이전 step이 없으면 `baseline:comparable_workload`를 missing evidence로 남깁니다.

Normalization은 양쪽에 유효한 양수 token 수가 있을 때만 `step_seconds_per_token`을 추가합니다.
단위는 `seconds/token`이고 총 step duration의 기존 rule threshold를 대체하지 않습니다.
Token 수가 같아도 tool mix·sequence 분포·cache 상태까지 동일하다는 보장은 없으므로 `matched_configured_fields`도 비교 조건의 충족만 의미합니다.

### Robust Differential Diagnosis

`baseline.robust.enabled=true`는 같은 workload의 과거 application duration에서 Median/MAD와 cohort 부족 여부를 검사합니다. 분산이 큰 tail·중복 record·겹친 exposure를 강한 회귀 근거로 사용하지 않으며, resource query와 sampling·clock 제한은 그대로 유지합니다. 기본값은 disabled이고 사용 방법·통계 해석·독립 fault 평가 결과는 [Execution / Performance Diagnosis](performance-diagnosis.md)에 있습니다.

### Read Sampling Quality

Source freshness는 단일 source가 명확한 query에서만 확인합니다.
Custom query에 `offset`·`@`·subquery나 추가 bare metric이 있으면 원래 시점을 생략한 timestamp query를 만들지 않고 freshness를 `unknown`으로 남깁니다.
허용한 단순 aggregation·rate 이외의 복잡한 식도 자동 해석하지 않습니다.
Source timestamp가 분석 구간 종료보다 미래이면 `source_timestamp_in_future`를 표시하고 age를 0으로 보정하지 않습니다.

Evidence와 comparison은 `sampling_quality.current`·`baseline`에 interval 길이, query step, range window와 evaluation count를 보존합니다.
Entity를 선택한 evidence는 해당 entity의 evaluation count를 사용합니다. 다른 engine/device의 sample 수를 빌리지 않습니다. Source timestamp count는 반환된 source series별 고유 timestamp 수의 최솟값이며, 여러 entity의 sample을 합쳐 충분한 coverage처럼 표시하지 않습니다.
예를 들어 6초 step의 `[1m]` rate에는 `range_window_exceeds_interval`이 표시됩니다.
이 값은 step 전후 활동을 포함할 수 있으므로 해당 step의 정밀한 resource 사용량으로 해석하지 않습니다.

`query_result`는 해당 query 전체의 backend warning·info 수와 버린 sample·series 수를 보존하며 개별 engine의 품질로 해석하지 않습니다.
Warning·info 수는 각각 1,000에서 제한하고 backend 원문은 저장하거나 LLM에 전달하지 않습니다.
Info만으로 entity 누락을 단정하지 않으며, 버린 series 수는 응답에 있었지만 유효한 finite sample이 없어 제외한 series만 셉니다.
이 품질 제한은 `missing_sources`·candidate의 `missing_evidence`에도 구간별 안전한 code와 수로 남고, 관련 candidate는 최대 `supporting_signal`로 표시합니다.
유효한 값과 labels는 유지하며 NaN·Inf·잘못된 sample을 0으로 바꾸지 않습니다.
Known stale/future source 값은 raw evidence·comparison에 보존하지만 rule의 supporting/counter 입력과 legacy finding에서는 제외합니다. Application symptom은 독립적으로 남으므로 resource evidence가 없어도 missing을 가진 weak candidate가 보일 수 있습니다.

Source freshness를 확인하려면 diagnostics 또는 LLM source config에 다음 설정을 추가합니다.

```json
"sampling": {"check_source_freshness": true}
```

Freshness 검사는 단일 source query에 원본 `timestamp(metric{...})` 조회를 추가합니다.
Computed rate의 timestamp나 application event 시각과는 다릅니다.
반복 노출한 snapshot의 진행 여부는 `training_sample_timestamp_seconds`·telemetry health도 확인합니다.
`source_age_seconds`와 조회에서 발견한 고유 timestamp 수를 기록하지만 전체 scrape 횟수는 아닙니다.
복합 query·오류·비활성 검사는 `unknown`; `observed`도 age·clock·missing source와 함께 해석합니다.
Current/baseline마다 query당 최대 한 번 비용이 추가되므로 기본은 비활성화입니다.

## Clock and Node Selection

Multi-node diagnosis에서는 [설정 예제](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/multinode/diagnostics.json)를 복사하여 실제 cluster·node·storage device를 지정합니다.
`compute_node`는 GPU source, `rollout_node`는 native vLLM/Ray source, `storage_node`와 `storage_device`는 storage host의 특정 block device를 선택합니다.
생략한 node는 현재 step observer의 node를 사용합니다.
한 설정이 cluster의 모든 GPU를 자동 집계하는 것은 아닙니다. 다른 compute node는 해당 node를 선택한 설정으로 조사하고, 여러 rollout endpoint는 아래 선언형 Replica inventory로 조회합니다.
Grafana Timeline의 `Resource node`로 조사할 node를 바꿉니다.
Manifest만으로 target이나 metric을 등록하지는 않습니다.

### Declared Rollout Replica Inventory

대표 endpoint와 실제 배치 node를 별도로 선언합니다. Native source의 `cluster`·`node`·`instance` 값과 일치해야 하며 `instance`는 요청 API URL이나 credential이 아닌 Prometheus endpoint identity입니다.

```json
{
  "schema_version": 1,
  "run_id": "run-a",
  "cluster": "rl-cluster",
  "node": "trainer",
  "clock": {"monitoring_node": "monitor"},
  "sampling": {"check_source_freshness": true},
  "prometheus": {"url": "http://127.0.0.1:9090", "metric_profiles": ["vllm"]},
  "rollout_replicas": [
    {"id": "replica-0", "instance": "rollout-a:8000", "endpoint_node": "rollout-a", "nodes": ["rollout-a"]},
    {"id": "replica-1", "instance": "rollout-b:8000", "endpoint_node": "rollout-b", "nodes": ["rollout-b", "rollout-c"]}
  ]
}
```

**확인 결과:** `rollout_replicas`에는 각 선언 Replica와 endpoint의 engine별 signal·baseline·quality·candidate가 남습니다. Analyze의 **Rollout Replica Coverage**에서 누락·clock 상태와 선언 node를 확인한 뒤 **Inspect endpoint**로 기존 Stage dashboard에 이동합니다. Run·Step·observer·time을 유지하며 resource node와 endpoint만 변경합니다.

같은 engine의 GET/PUT·status가 다른 Mooncake series는 개별 `metric_observations`로 보존합니다. 각 raw baseline은 operation/status를 포함한 같은 label에만 연결하며, 여러 operation의 p95를 합치거나 평균하지 않습니다. 복수 관측을 하나의 signal로 축약하지 않으며 이 raw 목록의 delta는 보류합니다. Replica당 최대 64개를 보존하고 한도 도달은 missing source로 표시합니다.

| 상태 | 해석 / 다음 행동 |
| --- | --- |
| `observed` | 반환된 query/source coverage 검사 통과. 전체 scrape·실행 관계 보장은 아님 |
| `partial_evidence` | Metric·baseline·freshness·rolling window 등이 부족함. Raw 관측과 missing evidence 확인 |
| `clock_unverified` | 해당 Replica의 node 또는 trainer/monitor clock 미검증. Raw 값 유지, candidate·delta 보류 |
| `missing` | 해당 endpoint의 관측이 없음. Target DOWN·metric 미지원·query 실패를 별도로 확인; Down으로 단정하지 않음 |
| `identity_limit` | Replica당 engine identity 8개 초과. 임의 engine 선택 없이 상세 분석 보류 |

- 최대 16 Replica·Replica당 8 node이며 전체 clock inventory는 기존 32 node 상한을 적용합니다. `run_engine_instances`와 함께 설정하지 않습니다.
- 기본/opt-in vLLM·Connector expression은 node/endpoint regex로 조회합니다. Metric당 current/baseline range query 수는 Replica 수에 따라 늘지 않으며, 새 node당 clock 검사 5개가 구간별로 추가됩니다. 기존 budget·deadline을 공유합니다.
- `prometheus.queries` override는 그대로 유지합니다. Override가 inventory를 조회하지 않으면 해당 Replica는 missing으로 남습니다.
- Queue·KV·preemption은 같은 engine만 평가합니다. 부분 metric이 없는 혼잡 engine을 정상 engine의 0으로 채우지 않습니다. Replica별 percentile을 합하거나 평균하지 않습니다.
- `configured`는 배치 선언이며 resource ownership이 아닙니다. Async trainer update가 해당 Replica의 request/sample을 소비했다는 관계도 생성하지 않습니다.
- 전체 clock verdict가 미확인일 때 기존 Run-level 후보는 보류합니다. Replica별 결과는 trainer/monitor와 해당 Replica node의 검사만 통과한 경우 별도 관측 결과로 남습니다.

VERL의 [LLMServerManager](https://github.com/verl-project/verl/blob/05093df562b90f659385ec0a313d03fc22da2ba5/verl/workers/rollout/llm_server.py)와 [vLLM replica server](https://github.com/verl-project/verl/blob/05093df562b90f659385ec0a313d03fc22da2ba5/verl/workers/rollout/vllm_rollout/vllm_async_server.py)는 논리 Replica·대표 endpoint·node rank를 구분합니다. XLayer는 배치를 자동 추론하지 않으며 clock node 선언만으로 GPU/device 사용량을 연결하지 않습니다.

Dynamic scale/discovery, 같은 endpoint를 재사용한 process generation, 실제 routing/request linkage, applied-policy coverage, workload-matched replica straggler 판정은 TBD입니다. Counter reset 처리는 기존 client semantics를 유지하며 restart 여부를 추정하지 않습니다. Endpoint가 사라져도 나머지 Replica의 raw 관측은 보존합니다.

**검증 범위:** CPU fixture에서 metric 누락·stale·endpoint no-data·engine identity 변경·미등록 remote clock·calibration reference session 변경과 잘못된 metadata를 검사했습니다. 실제 Prometheus/Loki/Grafana Multi-job synthetic에서는 queue 0인 Replica와 queue 14·KV 98%인 부분 수집 Replica를 분리하고, endpoint drill-down/Browser Back 및 1440/390px 화면을 확인했습니다. Llama는 추가 두 node의 current/baseline clock 검사로 134→154 request/Run이며 metric query 수는 유지됐습니다. 물리 VERL/vLLM multinode·TP/DP, 실제 process restart/scale, routing/policy 적용과 GPU 소유 관계는 미검증입니다.

### Router Membership and Serving Lifecycle

Scrape 성공은 endpoint 접근 가능성입니다. Router 등록 여부와 실제 serving 상태는 별도 관측해야 합니다. 기본값은 **Unknown**이며 `up=1`, queue=0, GPU idle로 active/sleep/healthy를 추정하지 않습니다.

| 관측 | Source / 연결 | 해석 경계 |
| --- | --- | --- |
| Router membership·in-flight | 선택적 `get_status()` SDK adapter | Registered server의 count. 요청 수용 준비·routing strategy·Job 소유량은 아님 |
| Sleep / weight update / waking / serving | 실제 runtime 호출의 완료 hook | 마지막 명시 상태의 sampled coverage. 연속 상태 증명·장애 분류는 아님 |
| Applied policy | Owning rollout worker의 `policy_applied()` | Trainer version·step 번호로 대체하지 않음 |
| Workload / generation | Owning runtime의 명시 counts·incarnation | 미보고 항목은 unknown. 재구성·재시작을 timestamp로 추정하지 않음 |

**준비:** Replica inventory·native targets·clock 검사를 먼저 연결합니다. 별도 observer host를 사용하면 `clock.nodes`에 추가합니다. SDK event는 같은 Run·cluster·router namespace를 사용합니다.

```json
"rollout_observations": {
  "events_dir": "artifacts/run-a/telemetry-events",
  "router_id": "trainer-router",
  "max_age_seconds": 60
}
```

각 `rollout_replicas` entry에 실제 Router ID인 `"server_id": "rollout-a:8000"`를 추가합니다. Prometheus `instance`와 API server ID가 같다고 가정하지 않습니다. Mapping이 없으면 membership은 Unknown입니다.

```python
from pathlib import Path
from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.adapters.rollout import RolloutObserver

events = EventRecorder(Path("artifacts/run-a/telemetry-events"),
    CorrelationContext(run_id="run-a", node="trainer", producer="rollout_observer",
                       role="trainer", worker_id="driver"))
observer = RolloutObserver(events, cluster="rl-cluster", router_id="trainer-router")

# Inside the existing async coordinator; this reads only get_status.
ok = await observer.poll_router(lambda: router.get_status.remote(), timeout_seconds=1)
# After the runtime confirms a completed transition, never before the call:
observer.replica_state("replica-0", "rollout-a:8000", "sleeping", generation="verified-process-1")
```

**정상 결과:** SDK JSONL에 `rollout.router.snapshot`·`rollout.replica.state`가 추가됩니다. `ok`는 read/shape validation 성공이며 SDK flush·서비스 readiness를 보장하지 않습니다. Ray import·cluster 연결·poll scheduling은 기존 coordinator가 담당합니다. XLayer는 router/scheduler를 생성하거나 `add_servers`·`remove_servers`·sleep/wake를 호출하지 않습니다.

- `poll_router` getter는 즉시 awaitable을 반환해야 합니다. Timeout/invalid response는 Unknown 관측으로 기록하며 backend 상세 오류를 노출하지 않습니다. Poll 주기는 사용자가 정하고 요청당 timeout은 최대 10초입니다.
- Policy 적용은 **실제 owning worker**의 recorder로 `observer.policy_applied(...)`를 호출합니다. Router snapshot만으로 적용 version이나 KV reset 정합성을 만들지 않습니다.
- 선택 window 이전의 관측, window 안의 상태 변화, sample gap, observer clock·reference session을 검사합니다. Window 중간의 sleep/wake·membership 변경은 전체 Step 상태로 확장하지 않습니다.
- 명시 inactive engine은 강한 serving bottleneck으로 승격하지 않습니다. 같은 node의 GPU idle에는 mixed context limitation을 추가하며 다른 active engine의 pressure는 보존합니다.
- Known generation·workload·applied version·serving eligibility가 달라지면 raw current/baseline을 남기고 delta를 보류합니다. 미계측 restart와 실제 consumed-sample 관계는 여전히 TBD입니다.
- Current와 baseline 구간 내부에서 각각 policy나 workload가 바뀌어 양쪽 요약 값이 Unknown이 되어도 비교를 허용하지 않습니다. 변경·stale/gap·유효하지 않은 명시 관측은 `quality_issues`에 보존하며 해당 Replica signal의 delta를 보류합니다. 미설정 optional 관측을 같은 조건이 검증되었다는 뜻으로 해석하지 않습니다.
- 구간 직전의 유효한 inactive 관측에서 구간 중 serving으로 전환된 경우도 sampled inactive overlap으로 남깁니다. GPU idle을 강한 starvation으로 해석하지 않으며, stale 관측으로 휴면 상태를 추정하지 않습니다.
- 읽기는 최대 32 JSONL file·file당 1 MiB·8,192 record로 제한합니다. Limit/partial source는 Unknown이며 0으로 채우지 않습니다. Metric query 수는 추가하지 않습니다.

Worker Comparison은 같은 명시 fingerprint라도 보고된 prompt/output token·turn/tool·concurrency·applied policy·generation·execution mode·model·state가 다르면 같은 cohort로 묶지 않습니다. 미보고 차원은 검증하지 못한 조건이며 fingerprint 자체도 producer의 선언입니다.

### Separate Async Decision Context

| Logger key suffix (`separate_async/decision/`) | Canonical metric | 읽는 방법 |
| --- | --- | --- |
| `sampleable_count` | `training_async_sampleable_count` | 다음 trainer update에서 사용할 수 있다고 보고한 sample 수 |
| `remaining` | `training_async_samples_remaining` | 다음 update의 decision sample gap; 실제 wait duration 아님 |
| `should_switch_to_rollout` | `training_async_should_switch_to_rollout` | 보고된 0/1 전환 결정; 완료된 lifecycle 아님 |
| `effective_switch_cost_seconds` | `training_async_effective_switch_cost_seconds` | 보고된 cost estimate; unknown이면 metric 없음 |

VERL key가 출력될 때만 snapshot·Step history에 저장합니다. Overview의 **Reported Async Trainer Decision**에서 completed observation을 읽습니다. Source·단위·scope는 [Metric Coverage](subsystem-metrics.md)를 확인하며 이 context로 vLLM bottleneck이나 trainer의 현재 sample starvation을 확정하지 않습니다.

Source 기준은 VERL [`6afd1f5`](https://github.com/verl-project/verl/commit/6afd1f5d1feee75a6982250ae8438daee21c29b2)의 [Router](https://github.com/verl-project/verl/blob/6afd1f5d1feee75a6982250ae8438daee21c29b2/verl/workers/rollout/router.py), [Separate Async](https://github.com/verl-project/verl/blob/6afd1f5d1feee75a6982250ae8438daee21c29b2/verl/trainer/ppo/v1/trainer_separate_async.py), [Colocated Async](https://github.com/verl-project/verl/blob/6afd1f5d1feee75a6982250ae8438daee21c29b2/verl/trainer/ppo/v1/trainer_colocate_async.py)입니다. Native integration은 opt-in hook이며 실제 Ray/VERL actor 연결은 이번 CPU/mock 검증에 포함되지 않습니다.

`cluster`를 지정하면 기본 query는 cluster/job으로 제한되고 clock check도 기본 활성화됩니다.
사용자가 제공한 `prometheus.queries`는 그대로 사용하므로 각 selector에 `cluster="{cluster}"`와 source·node/device 조건을 명시해야 합니다.
지원 placeholder는 `{node}`, `{run_id}`, `{cluster}`, `{compute_node}`, `{rollout_node}`, `{storage_node}`, `{storage_device}`, `{sandbox_node}`, `{sandbox_device}`입니다.
Host 전체 disk busy와 별도 storage node의 `storage_device_busy_ratio`는 다른 signal이며 shared 3FS latency와 동일한 사용량으로 합치지 않습니다.

`clock_quality`는 current 구간의 node별 scrape 상대 offset·kernel NTP offset·maxerror·sample age·sync status를 보존하며 baseline이 있으면 그 구간도 검사합니다. [Correlation preflight](time-alignment.md#correlation-preflight)는 같은 관측 대상 목록을 사용하는 읽기 전용 doctor 검사입니다.
`aligned`는 설정한 screening 조건을 만족했다는 뜻이고, `unsafe`는 skew·staleness·unsynchronized 상태, `unknown`은 필요한 clock source 부족, `unchecked`는 기존 설정에서 검사하지 않았다는 뜻입니다.
Current 또는 baseline이 `unsafe`/`unknown`이면 `verdict=insufficient_data`, 빈 `candidates`와 빈 resource comparison을 기록하고 `missing_sources`에 clock 상태를 남깁니다.
Raw `evidence`와 workload duration은 inspect할 수 있으며 기존 bounded retry를 적용합니다.
Clock 상태를 나중에 고쳤다고 이미 끝난 step의 과거 timestamp가 복원되지는 않습니다.
선택적으로 [userspace calibration](time-alignment.md)을 설정하면 생성 시점에 보존한 reference window와 uncertainty로 판단합니다.
`clock.calibration_reference`가 맞고 uncertainty가 `min(max_skew_seconds, interval / 10)` 이내여야 하며 current·baseline 어느 쪽이든 부족하면 보류합니다.

기본 `clock.require_sync=true`, `max_skew_seconds=1`, `max_sample_age_seconds=30`입니다. Kernel uncertainty와 NTP offset의 허용 예산은 `min(max_uncertainty_seconds 또는 max_skew_seconds, interval / 10)`입니다. Clock sample이 조사 구간보다 오래되거나 충분한 query evaluation이 없으면 정밀 correlation을 보류합니다. Evaluation 수는 실제 scrape 수나 continuous clock coverage가 아닙니다.

멀티노드는 `clock.monitoring_node`와 `cluster`가 필요합니다. `clock.nodes`에는 별도 service/tool host를 추가하며, 실제 관측한 remote tool host가 목록 밖이면 `unknown`으로 보류합니다. 멀티노드에서는 `clock.enabled=false`나 `require_sync=false`가 검사를 우회하지 않습니다. Cluster·별도 resource mapping이 없는 기존 단일 노드 config는 `unchecked`로 raw context를 유지합니다. Userspace calibration은 raw 3FS clock이나 remote OS synchronization을 인증하지 않습니다.

ClickHouse endpoint의 server clock만 확인해서 3FS distribution timestamp를 검증할 수는 없습니다.
`threefs.clock_nodes`에 timestamp를 만드는 실제 3FS producer node 목록을 넣고 해당 node의 host collector를 `TELEMETRY_TARGETS`에 등록합니다.
Producer coverage가 부족하면 `threefs:producer_clock_alignment`가 missing source로 남으며 3FS를 사용한 regression finding·candidate·delta를 보류합니다.
이 clock 검사는 step별 3FS 사용량의 attribution을 제공하지 않습니다.

3FS regression finding과 candidate는 반환된 distribution의 실제 `labels.host`가 current·baseline의 raw clock screening에 포함될 때만 사용합니다. Host 이름이 Node Exporter identity와 다르면 기존 `threefs.time_series.host_clock_nodes` mapping을 사용하며 series 활성화는 필요하지 않습니다. 다른 host의 정상 clock이나 application calibration만으로 해당 producer를 인증하지 않습니다. 미검증이면 raw current/baseline은 남고 delta는 `producer_clock_unverified`로 보류합니다. Host 없는 legacy artifact도 정밀 time correlation을 인증하지 않습니다.

3FS service와 SSD/interface가 같은 I/O path라는 관계는 현재 telemetry에 없습니다. Mixed storage 후보는 최대 supporting이며 `storage_service_resource_relation_unverified`를 남깁니다. 별도 interface의 healthy 값으로 storage-path headroom을 확인하거나 다른 device의 busy 값으로 이를 반박하지 않습니다. 실제 GET/PUT→3FS→SSD/network 관계와 operation attribution은 기존 [TBD](storage-correlation.md)의 계측 과제입니다.

## Data and UI Boundaries

`diagnostics/latest.json`의 `schema_version=1`, `findings`, `evidence`, `missing_sources`는 기존 소비자를 위해 유지합니다.
새 `diagnosis_schema_version=1`, `symptom`, `comparison`, `candidates`는 추가 필드입니다.
필드 계약은 [Diagnosis JSON Schema](https://github.com/daegyu94/xlayer-telemetry/blob/main/config/diagnosis.schema.json)에 있습니다.
`candidates[].evidence`와 `counter_evidence`, `missing_evidence`가 판단 근거이고 `related_nodes`, `related_devices`, `related_spans`는 확인된 연결만 담습니다.
Loki용 `diagnostics/investigation/*.jsonl`은 이 결과의 평면 projection이며 원본보다 정보가 적습니다.

VERL file logger에 event timestamp가 없어서 bridge가 이미 존재하는 로그를 처음 읽거나 종료 후 남은 record를 replay하면, 해당 record의 `analysis_window`는 `unknown`으로 남습니다.
이 경우 step duration과 stage 이름은 보존하지만 현재 시각의 GPU·network·storage 수치를 과거 step에 연결하지 않습니다.
Bridge가 파일 끝을 따라가며 새 record를 읽은 경우에만 관측 시각에서 reported duration을 뺀 approximate window를 사용합니다.

VERL file logger는 stage duration만 주고 stage별 실제 시작·끝은 주지 않습니다.
Timeline은 file logger에서 추정한 전체 step band만 `approximate`로 표시하며 rollout·reward·update 순서를 추정해 그리지 않습니다.
`EventRecorder`가 만든 span은 producer node의 start/end timestamp로 `exact` bar에 표시하고, Prometheus 선은 sampling 간격의 관측치입니다.
Wall clock jump가 감지된 `clock_discontinuity` span은 exact bar에서 제외되며 duration 자체는 monotonic clock 값으로 보존합니다.
Timeline의 `Span and event records` 행을 펼쳐 span 표를 확인합니다.
`span_id`에서 같은 시간 창의 Timeline·Logs·Diagnosis로 이동하고, `trace_id`로 기록한 관련 event를 필터링할 수 있습니다.
Clock skew, scrape 간격, shared resource의 다른 사용자 때문에 눈으로 겹친 구간도 추가 확인이 필요합니다.

![같은 workload window의 cgroup·device·shared-service evidence를 scope별로 보존하는 조사 모델](figures/diagrams/evidence-scope.svg)

### Follow the Data Path

![Declared storage path와 workload·network·shared-service·device scope](figures/diagrams/storage-path.svg)

Bottleneck Summary의 `Inspect declared topology and storage path` 링크는 기존 Data & Storage 화면의 component·edge 표로 이동합니다.
그 edge는 사용자가 제공한 topology 관계이며 throughput·latency·error·queue가 실제로 측정된 edge만 해당 값을 붙일 수 있습니다.
Node 전체 network byte를 특정 trainer-to-storage edge의 traffic이라고 표시하지 않습니다.

## Targeted Deep Dive

GPU 또는 communication candidate가 있으면 [profiler 실행 예제](deep-dive.md#짧은-trace-수집)로 짧은 rank 구간을 기록합니다.
Communication candidate는 [NCCL baseline](dashboard-reference.md#measure-a-communication-baseline)과 같은 hardware 경로에서 비교합니다.
Profile 파일은 native PyTorch Profiler/Nsight 도구로 열며, XLayer는 profiler 자체를 구현하거나 상시 full profiling을 켜지 않습니다.
`show_run`은 run 아래의 확인된 profiler/NCCL artifact 경로를 출력합니다.

## Reproduce the Integration Checks

실제 local Prometheus에서 SDK fixture를 수집하여 새 run 발견·node 필터·종료/age 제외·source timestamp를 검증할 수 있습니다.
`--output`은 새 directory여야 하며 시작한 Prometheus와 HTTP exporter는 종료 시 정리합니다.

```bash
python -m examples.investigation.validate \
  --prometheus "$TOOLS_DIR/prometheus-3.5.0.linux-amd64/prometheus" \
  --output /path/to/new-validation-directory
```

Optional Ollama가 이미 실행 중이면 `--ollama`를 추가해 선택한 record의 실제 모델 응답·review·projection까지 확인합니다.
이 예제는 SDK fixture이며 실제 VERL 학습이나 물리 multi-node, storage 병목 검증을 대신하지 않습니다.
[검증 기록](validation/investigation/validation-20260930.json)은 별도 실제 VERL 두-step 실행과 model·Grafana·Loki 확인 범위를 구분합니다.
