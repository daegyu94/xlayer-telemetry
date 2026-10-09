# Correlation Limitations & Future Work

**읽는 목적:** 현재 XLayer가 설명할 수 있는 것과 추가 계측이 필요한 것을 구분합니다. Backend가 달라도 latency·bytes·operation의 의미를 보존하는 후속 연구개발 기준입니다.

> **Reference / TBD** · 2026-10-08 소스 조사. 현재 구현과 Future Work를 구분합니다. Common Storage·기존 3FS는 XLayer 내부 구현이며 pNFS·operation-level linkage는 TBD입니다. Upstream 수정·물리 multi-node·pNFS 배포 검증은 수행하지 않았습니다.

## 현재 지원과 완료된 P0

| 현재 지원 | 유지하는 해석 경계 |
| --- | --- |
| 완료 Step/Trainer update·native scalar·SDK phase/span | Reported duration·approximate boundary·exact/calibrated span을 구분 |
| Run/worker/node/device와 current/baseline | 실제 entity·declared workload 조건이 맞는 비교만 사용 |
| Clock inventory·`doctor --correlation` | Monitoring·resource·producer host의 sampled clock screening; continuous clock proof가 아님 |
| Selected-entity sampling quality | 다른 engine/device의 sample을 합쳐 strong을 만들지 않음; known stale/future 값은 raw로만 유지 |
| Mooncake connector/master/DFS native metric | RPC·batch·key·shared memory 관측; 실제 replica 선택이나 SSD I/O로 바꾸지 않음 |
| Common Storage coverage / DFS candidate | ClickHouse 없이 source·unit·entity·quality를 보존. Backend 미보고·모든 batch 실패·다른 client 오류를 구분하며 최대 supporting |
| Bounded 3FS collection time-series | Producer host·report 시각·reset/gauge 계약 보존; 미검증 clock이면 finding/candidate/delta 보류 |
| Supporting / Counter / Missing·저장 artifact·선택적 profiling | Mixed storage path가 미확인이면 최대 supporting; 운영·query 예산과 workload lifecycle 분리 |
| Compact signature·local parent/child relation delta | 선택된 worker/boundary 내부의 observed parent만 집계; cross-worker propagation·전역 critical path는 아님 |

사용·설정은 [Diagnosis contract](diagnosis-reference.md), [Storage correlation](storage-correlation.md), [Clock prerequisites](time-alignment.md), [기존 signature/triggered profiling](behavior-signature-research.md)을 따릅니다. 위 P0를 다시 구현하거나 새 dashboard·telemetry backend를 만드는 것은 후속 과제가 아닙니다.

소스 기준은 [SDK context/span](https://github.com/daegyu94/xlayer-telemetry/blob/47d8fa0a6e284b7b43b106b5fcabe00d250d6970/xlayer_telemetry/events.py), [window/clock/entity 진단](https://github.com/daegyu94/xlayer-telemetry/blob/47d8fa0a6e284b7b43b106b5fcabe00d250d6970/xlayer_telemetry/analysis/diagnostics.py), [signature relation 모델](https://github.com/daegyu94/xlayer-telemetry/blob/47d8fa0a6e284b7b43b106b5fcabe00d250d6970/xlayer_telemetry/analysis/behavior_signature.py), [native query profiles](https://github.com/daegyu94/xlayer-telemetry/blob/47d8fa0a6e284b7b43b106b5fcabe00d250d6970/xlayer_telemetry/analysis/metric_queries.py)입니다.

## Limitations

### Multi-job 관측과 자원 소유권

| 구분 | 현재 가능한 것 | 보장하지 않는 것 |
| --- | --- | --- |
| Application | Run·worker·Step·span/event identity로 기록·baseline·drill-down 구분 | 모든 async rollout을 하나의 trainer update가 소유한다는 관계 |
| Native engine/service | Endpoint·model·device label이 있는 관측을 그대로 보존; 명시 query selector 활용 | 설정된 endpoint가 실제 모든 요청을 처리했다는 operation 관계 |
| Shared node/storage | GPU/host/network/Ray/Mooncake pressure와 선택 window의 중첩 조사 | 특정 Job의 resource 사용량·I/O bytes·확정 원인 |
| Quality | Missing/stale/no-data·clock·sampling·unverified attribution 표시 | 관측되지 않은 source의 정상 상태나 수치 `0` |

Resource를 사용하는 candidate는 `resource_attribution=not_established`와 `run_resource_attribution_unverified`를 보존합니다. `strong_signal`은 실제 관측된 pressure 조건의 강도이며 attribution·causality를 확정하지 않습니다. 기존 [Multi-job demo](demo.md#multi-job-live-demo)는 서로 다른 모델 metadata·동일 Step/worker ID·겹치는 시간·missing/stale 입력을 실제 query/diagnosis 경로로 재현합니다. 실장비·framework-level execution propagation과 동일한 검증은 아닙니다.

### Time-window와 execution 관계

| 질문 | 현재 근거 | 아직 설명하지 못하는 것 |
| --- | --- | --- |
| 어느 구간이 느렸나? | 완료 duration·계측된 span·같은 구간의 metric | Logger observation 시각으로 exact 시작·실행 순서를 복원 |
| 같은 phase에서 무엇이 변했나? | 검증한 clock/node의 query evaluation과 span window | Raw collection 시각이 없는 metric의 정확한 phase 소비량 |
| Async update가 어느 rollout을 사용했나? | 명시 identity·기존 parent 관계·reported policy context | 시간 중첩·같은 Step 번호만으로 실제 sample/trajectory dependency 생성 |
| 여러 worker 중 어디가 다르나? | 비교 가능한 worker/call duration·fingerprint·resource identity | 독립 실행·다른 concurrency·token/tool workload를 동일한 peer로 취급 |
| 이 Run이 resource를 얼마나 썼나? | Node/device/shared-service 변화 | Run별 GPU·RDMA·SSD 사용량, shared cache 비용의 독점 귀속 |

veRL fully-async 경로는 rollout sample ID와 자체 `global_steps`를 사용하며 trainer update와 sample 소비·policy 적용은 별도 과정입니다. vLLM `request_id`는 generation 요청 식별자입니다. 어느 쪽도 그 자체로 Mooncake batch/key나 3FS I/O의 공통 operation ID가 되지 않습니다. 근거는 [async rollouter](https://github.com/volcengine/verl/blob/704a9f01bda8b9863c02c113547191bf167bc88f/verl/experimental/fully_async_policy/fully_async_rollouter.py)와 [vLLM server 호출](https://github.com/volcengine/verl/blob/704a9f01bda8b9863c02c113547191bf167bc88f/verl/workers/rollout/vllm_rollout/vllm_async_server.py)입니다.

Clock alignment는 시간축을 맞추며 missing execution edge를 생성하지 않습니다. Scrape 사이의 clock jump·짧은 phase·mutable collection cadence·rolling lookback·partial capture는 남는 오차입니다. Query evaluation 두 개가 실제 scrape 두 개나 전체 window coverage를 뜻하지 않습니다.

### Replica와 KV operation

최신 Mooncake의 [선택 함수](https://github.com/kvcache-ai/Mooncake/blob/dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2/mooncake-store/include/replica_selection.h)는 locality·complete 상태·설정한 remote scoring을 사용합니다. MEMORY 외에도 NOF·LOCAL_DISK·DFS·DISK가 있으므로 backend 이름이나 cache hit 감소만으로 실제 read tier를 정하지 않습니다.

[Descriptor 조회](https://github.com/kvcache-ai/Mooncake/blob/dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2/mooncake-store/src/real_client.cpp)는 후보 replica 목록을 반환합니다. vLLM의 [tier logging helper](https://github.com/vllm-project/vllm/blob/194da61de1769c28bfad2b6642c60cc7eca544dd/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/store/worker.py)는 첫 descriptor를 memory/disk/unknown으로 분류하며 DFS 선택·fallback·completion을 확정하지 않습니다. 이런 hint와 실제 `selected_replica`는 별도 evidence입니다.

KV key는 여러 request·Run에서 재사용될 수 있고 batch 하나에 여러 request/key가 묶입니다. GET 성공·DFS successful key 수·SSD batch·block/NFS RPC 수는 다른 모집단입니다. 같은 key나 timestamp가 있다는 이유만으로 one-to-one ownership을 만들지 않습니다.

### Diagnosis의 의미

- Candidate는 관측한 가설입니다. `strong_signal`도 cause probability·critical path·exclusive ownership이 아닙니다.
- Current/baseline의 workload 조건과 entity가 같아도 숨은 concurrency·cache state·background I/O가 남습니다. Missing source는 원인 부재의 증거가 아닙니다.
- Shared service와 SSD/interface가 같은 path라는 관계가 없으면 healthy 값도 그 path의 headroom/counter evidence가 아닙니다.
- 서로 다른 구간의 p95를 더해 end-to-end latency를 만들지 않습니다. Reported p99 max·histogram estimate·batch latency·device mean도 서로 대체하지 않습니다.
- Clock/coverage가 부족한 raw 값은 조사에는 남지만 정밀 비교·과도한 판정은 보류합니다. 이런 보류는 false positive를 줄이는 대신 false negative와 unknown을 늘릴 수 있습니다.

```{admonition} 핵심 경계
:class: important

Correlation ≠ Attribution ≠ Causality. 정확한 span은 그 계측 경계의 정확도입니다. 특정 storage I/O가 그 span의 요청 때문에 발생했거나 bottleneck이었다는 별도 증명이 아닙니다.
```

## Storage Backend Coverage

### 최신 upstream와 현재 활용 상태

| 영역 | 현재 충분한 관측 / 활용 | 확인한 공백 / 이번 처리 |
| --- | --- | --- |
| veRL | Reward·step/rollout duration·token throughput·MFU·정책 보고·SDK phase | Async rollouter의 stale-sample/resource-utilization은 trainer update 소유 관계가 아님. Native metric/trace adapter 호환 연결은 TBD |
| GPU / CPU / Memory | Sampled GPU/device·host memory, opt-in DCGM·PSI·fault evidence | MFU와 GPU/tensor activity를 대체하지 않음. 새 collector·임의 metric 추가 없음 |
| Network / RDMA | Interface별 bytes·errors/drops·retransmit·hardware wait ticks | NIC와 remote storage path 관계는 미계측. Wait tick을 임의 ms로 변환하지 않음 |
| Ray | Session tasks·object-store spill/restore·memory eviction | Byte gauge에 counter rate를 적용하지 않음. Worker/sample와 Ray task의 정확한 부모 관계는 TBD |
| vLLM / KV | Engine별 queue·TTFT/TPOT/E2E·preemption·cache/offload·Store RPC | Native operation/status는 RPC/batch 경계; request→selected replica→I/O의 공통 ID 아님 |
| Mooncake | Canonical DFS latency·delivered bytes/keys·failed/skipped keys·Master memory | 비교표에서만 사용하던 DFS signal을 backend 공통 supporting candidate와 coverage에 연결 |
| 3FS | Existing distribution·reset report·collection series·host clock | 기존 deep query를 유지. Common Overview와 source availability를 분리하고 path 소유권을 추가하지 않음 |
| Sandbox | Worker/cgroup queue·tool span·PSI·resource evidence | Host SSD와 cgroup을 같은 소유 자원으로 묶지 않음. Runtime/scheduler 추가 없음 |

veRL [async metric report](https://github.com/volcengine/verl/blob/704a9f01bda8b9863c02c113547191bf167bc88f/verl/experimental/fully_async_policy/fully_async_rollouter.py), vLLM [native metrics](https://github.com/vllm-project/vllm/blob/194da61de1769c28bfad2b6642c60cc7eca544dd/vllm/v1/metrics/loggers.py), Ray [metric 정의](https://github.com/ray-project/ray/blob/07197d0cae70cdfb75f4e3f97f3c0b14f8ba05b3/src/ray/raylet/metrics.h)와 [spill/restore 실제 기록](https://github.com/ray-project/ray/blob/07197d0cae70cdfb75f4e3f97f3c0b14f8ba05b3/src/ray/raylet/local_object_manager.cc)를 대조했습니다. Definition comment만으로 state 지원을 판단하지 않고 call site도 확인했습니다. 기존 opt-in profile은 version-dependent이며 배포 endpoint에 이름·label이 없는 경우 missing을 유지합니다.

최신 Mooncake의 transfer interface metric도 native에 있으나 DFS/connector metric에 합산하지 않습니다. 최소 추가 가치가 검증되지 않은 transfer profile과 이번 범위에서 제외한 Local SSD Deep Dive는 추가하지 않았습니다.

### Backend 이름보다 실제 adapter와 소유 host

| 구성 | 소스로 확인한 path | 조건 / 미검증 범위 |
| --- | --- | --- |
| Mooncake + 3FS | Descriptor DFS → `DistributedStorageBackend` → `Hf3fsAdapter` → USRBIO submit/wait → 3FS service/storage target | `USE_3FS`와 실제 mount·client 설정 필요. Client의 local disk busy를 remote service I/O로 사용하지 않음 |
| Mooncake + Local SSD | 별도 FileStorage → Bucket/File-per-key/OffsetAllocator 등의 storage backend → POSIX 또는 build/config에 따른 io_uring → 소유 host filesystem/device | `LOCAL_DISK` replica의 소유 node가 remote이면 offload RPC로 읽음. Local SSD라는 이름이 요청 worker의 local SSD를 뜻하지 않음 |
| Mooncake + pNFS | 조건부 제안: descriptor DFS → `PosixFsAdapter` → Linux NFS client → 협상한 layout의 DS 또는 MDS fallback | 전용 `pnfs` adapter는 확인하지 못함. POSIX mount 접근은 가능해도 실제 pNFS deployment·layout·shared namespace 동작은 TBD |

근거: [backend factory](https://github.com/kvcache-ai/Mooncake/blob/dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2/mooncake-store/src/storage_backend.cpp), [HF3FS adapter](https://github.com/kvcache-ai/Mooncake/blob/dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2/mooncake-store/src/storage/distributed/hf3fs_adapter.cpp), [POSIX adapter](https://github.com/kvcache-ai/Mooncake/blob/dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2/mooncake-store/src/storage/distributed/posix_fs_adapter.cpp), [remote LOCAL_DISK restore](https://github.com/kvcache-ai/Mooncake/blob/dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2/mooncake-store/src/real_client.cpp).

`posix`를 node-local directory에 지정하는 것과 distributed shared filesystem으로 쓰는 것은 다릅니다. 여러 client가 같은 descriptor path를 열어야 하는 DFS 구성에서 local mount를 shared namespace처럼 가정하지 않습니다. FileStorage path가 NFS mount인 구성도 descriptor DFS와 별도 경로입니다. Path만으로 backend를 분류하지 않습니다.

이번 비교는 세 환경에 한정합니다. 최신 factory에는 NVMe KV·OSS 등의 다른 경로도 있으므로 실제 build/config를 확인해야 합니다. POSIX write 완료는 해당 syscall 경계이며 이 adapter에 없는 fsync·remote durability를 완료 시각에서 추정하지 않습니다.

### Native telemetry와 XLayer 소비 범위

| 계층 / source | 실제 제공 범위 | 현재 XLayer / 제한 |
| --- | --- | --- |
| vLLM Store connector | `vllm:mooncake_store_operation_*`: operation/status별 RPC duration histogram·calls·keys·bytes·failed keys | 기존 Stage/Deep Dive·profile 재사용. Replica/backend/Run별 physical I/O가 아님 |
| Mooncake master | Shared allocation/capacity·lookup/admission 등 | 기존 explicit Master profile. Lookup 결과를 실제 selected/completed tier로 바꾸지 않음 |
| Mooncake descriptor DFS client | `mooncake_dfs_*`: successful key/bytes, batch latency microseconds, failed/skipped key, 별도 D2H staging | 기존 `mooncake`·`mooncake_storage`. Adapter가 hf3fs/posix여도 metric 경계는 client batch |
| Mooncake FileStorage | `mooncake_ssd_*`: successful batch key/bytes·read/write latency histogram/summary | Native에 있음. 현재 canonical DFS panel/profile로 대체하지 않으며 backend 전용 활용은 TBD |
| 최신 Mooncake transfer API | `mooncake_transfer_{read,write}_operation_*`, `op_name`별 interface count/bytes/latency | Native에 있음. XLayer 자동 diagnosis profile 소비는 미구현. Request·selected replica별 trace는 아님 |

근거: [connector metric](https://github.com/vllm-project/vllm/blob/194da61de1769c28bfad2b6642c60cc7eca544dd/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/store/metrics.py), [Mooncake metric 정의](https://github.com/kvcache-ai/Mooncake/blob/dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2/mooncake-store/include/client_metric.h), [DFS observe call site](https://github.com/kvcache-ai/Mooncake/blob/dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2/mooncake-store/src/client_service.cpp), [FileStorage call site](https://github.com/kvcache-ai/Mooncake/blob/dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2/mooncake-store/src/file_storage.cpp).

DFS ops는 successful key 수이며 physical IOPS가 아닙니다. FileStorage read/write도 성공 batch/key accounting으로, SSD controller latency가 아닙니다. Client metric 활성화와 HTTP endpoint 공개는 별도 조건이므로 정의가 있어도 embedded client의 `/metrics` 노출을 가정하지 않습니다.

### Backend service / device evidence

| Backend | Latency / throughput / IOPS 관측 범위 | Client / Server와 남는 공백 |
| --- | --- | --- |
| 3FS | ClickHouse distribution의 producer/entity별 reported p99 max·weighted mean, 검증한 reset report의 bytes/ops 합계. Storage node의 diskstats/RDMA는 별도 source | Client/server operation·node scope를 유지. 1초 DateTime은 정확한 collection interval이 아니므로 exact B/s/IOPS를 만들지 않음. Service→특정 SSD/interface/request edge 없음 |
| Local SSD | FileStorage batch histogram·successful key/bytes rate. Diskstats의 device bytes/s·completed read/write ops/s·time/ops mean·in-flight/weighted I/O time | Request host와 SSD owner를 분리. Page cache·D2H/H2D·RPC·filesystem·device는 별도 경계. Native device p99·Run별 I/O 없음 |
| pNFS / NFS client | Mountstats의 mount/export/operation별 누적 queue/response/request 시간·requests/transmissions/timeouts·bytes·pNFS events | Delta로 얻는 RPC mean/rate이며 p99가 아님. Mount namespace·remount/reset을 유지. 실제 MDS/DS 선택·RPC별 소속을 자동으로 얻지 못함 |
| pNFS server / DS | Linux kernel nfsd의 RPC/method counts·aggregate bytes/errors/threads. DS host에 exporter가 있다면 별도 diskstats/network | Kernel nfsd용 source. 미지정 userspace/appliance server의 native 지원은 미확인. 표준 collector에 DS별 service p99 없음 |

Node Exporter 1.9.1의 [diskstats](https://github.com/prometheus/node_exporter/blob/v1.9.1/collector/diskstats_linux.go), [mountstats](https://github.com/prometheus/node_exporter/blob/v1.9.1/collector/mountstats_linux.go), [nfs](https://github.com/prometheus/node_exporter/blob/v1.9.1/collector/nfs_linux.go), [nfsd](https://github.com/prometheus/node_exporter/blob/v1.9.1/collector/nfsd_linux.go)를 확인했습니다. XLayer launcher는 default collector와 textfile을 사용하므로 nfs/nfsd는 proc source가 있는 경우 대상입니다. **mountstats는 default disabled이며 managed launcher에서 현재 명시적으로 켜지 않습니다.** NFS 전용 diagnosis profile도 없습니다.

3FS의 [recorder](https://github.com/deepseek-ai/3FS/blob/22fca04564c7cc230fd8b9523b8b92864e1dad47/src/common/monitor/Recorder.cc), [SQL schema](https://github.com/deepseek-ai/3FS/blob/22fca04564c7cc230fd8b9523b8b92864e1dad47/deploy/sql/3fs-monitor.sql), [USRBIO API](https://github.com/deepseek-ai/3FS/blob/22fca04564c7cc230fd8b9523b8b92864e1dad47/src/lib/api/UsrbIo.md)와 XLayer의 현재 [bounded query 계약](storage-correlation.md)을 함께 확인합니다. Reset report의 raw ops와 application key count를 같은 IOPS로 취급하지 않습니다.

`node_mountstats_nfs_operations_queue_time_seconds_total`·`response_time_seconds_total`·`request_time_seconds_total`은 operation별 누적 시간입니다. 대응하는 requests의 delta로 mean을 구해도 queue·RTT·total execution을 구분하며 p99나 SSD latency로 부르지 않습니다. App/syscall bytes와 NFS server와 주고받은 bytes도 구분합니다.

`event_pnfs_read_total`/`write_total`은 [Linux pNFS dispatch](https://github.com/torvalds/linux/blob/47324d3a5b3abd781295044d01d92d09f184e872/fs/nfs/pnfs.c)에서 layout driver가 `PNFS_NOT_ATTEMPTED` 외의 값을 반환할 때 증가합니다. DS 성공 I/O·KV GET 수가 아닙니다. File/flexfile layout·retry·MDS resend가 있으므로 mountaddr를 실제 DS identity로 취급하지 않습니다. 구체적인 server/layout과 관측 방법은 PoC에서 확정합니다.

NFS data가 container 안에 mount되어 있다면 exporter의 `/proc/self/mountstats`가 같은 mount namespace를 보는지도 확인해야 합니다. Host/node·mountaddr·export가 같아 보여도 remount와 namespace가 다른 data를 섞지 않습니다. Layout 정보와 DS별 latency가 없는 구성에서는 server/backend 내부 원인을 구분할 evidence가 부족합니다.

### 3FS 의존성의 현황

| 3FS가 없는 경우 | 유지 / 사라지는 evidence |
| --- | --- |
| 유지 | Framework scalar/span·GPU/host/network·connector RPC·사용 가능한 Mooncake client/master·local device·log/profiler |
| 사라짐 | 3FS ClickHouse의 producer/operation/entity distribution·storage-client/server report·reset counter 계약·3FS host-clock mapping 비교 |
| 대체 불가 | NFS RPC mean·diskstats mean·FileStorage batch p95를 `threefs_p99_latency`나 같은 baseline 통계로 변환하지 않음 |

Prometheus·SDK·기본 window/quality 모델과 `storage_overview`의 common source coverage는 backend 공통입니다. `ThreeFSClient`·`storage_queries.py`·`threefs_*` rule·collection projection은 3FS 전용으로 유지합니다. ClickHouse가 없거나 실패해도 검증된 Mooncake source의 공통 관측·후보를 제공하지만 같은 backend diagnosis coverage를 약속하지 않습니다.

현재 coverage는 기존 조회 결과·quality·설정 여부를 요약하는 작은 additive model입니다. 실제 backend/adapter discovery나 generic provider registry는 아닙니다. `threefs.status=observed`여도 `backend.status=not_reported`일 수 있습니다. Verified adapter/mount/DS identity와 pNFS capability provider는 후속 과제입니다. [Common / 3FS / pNFS 흐름](storage-correlation.md)을 따릅니다.

## Future Work (TBD)

**우선순위:** 기존 source 활용 → capability/identity 선언 → local execution link → 실제 replica/completion → operation/resource path 순서입니다. 아래 overhead는 비용의 구조에 대한 예상이며 측정값이 아닙니다.

Rollout Replica는 VERL request를 처리하는 논리 실행 배치이며 Mooncake KV data replica와 다릅니다. [선언형 Rollout inventory](diagnosis-reference.md#declared-rollout-replica-inventory)는 endpoint·여러 node의 clock·engine별 부분 metric을 보존합니다. 배치 선언은 request routing, TP/DP device 소유권, trainer의 sample consumption 관계를 증명하지 않습니다. 같은 Run/Step 번호를 가진 async worker call은 원래 monotonic duration을 조사할 수 있지만 trainer-owned rollout이라고 해석하지 않습니다.

Replica별 percentile·throughput의 단순 합계/평균 또는 Job 간 workload-mismatched straggler 판정은 제공하지 않습니다. 실제 request→rollout replica→applied policy→consumed sample 관계와 동적 scale/restart generation은 native/framework hook 검증이 필요한 TBD이며, 현재 CPU synthetic inventory를 물리 VERL multinode 검증으로 간주하지 않습니다.

선택적 [serving observations](diagnosis-reference.md#router-membership-and-serving-lifecycle)은 read-only Router snapshot과 완료 hook의 lifecycle·workload·applied policy를 읽습니다. Endpoint up와 요청 수용 가능성을 구분하지만 Router membership도 엔진 readiness를 보장하지 않습니다. Sticky routing은 `get_status()` in-flight 불균형만으로 확인할 수 없으며 prompt mix·실제 routing strategy·request 관계가 없으면 원인을 보류합니다.

PD disaggregation은 [검토한 VERL revision의 vLLMPDReplica](https://github.com/verl-project/verl/blob/6afd1f5d1feee75a6982250ae8438daee21c29b2/verl/workers/rollout/vllm_rollout/vllm_pd_replica.py)가 single-node·prefill 1개 구성을 제한하는 P2/TBD입니다. 향후 fixture는 prefill TTFT/queue, KV-transfer bytes/latency/errors, decode TPOT/KV를 분리해야 하며 이를 하나의 vLLM latency나 일반적인 multi-node PD 지원으로 표시하지 않습니다. Ray scheduling/spilling과 engine 내부 대기, replica request→KV→storage의 공통 ID, routing/affinity cause, 미계측 scale/restart·shared storage ownership도 별도 검증이 필요합니다.

### 우선순위와 최소 변경 범위

| 우선 | 제안 / 기대효과 | 최소 변경과 upstream 경계 |
| --- | --- | --- |
| P1-A | 현재 coverage에 verified backend/adapter·mount/DS identity 확장 | 기존 `storage_overview`·quality·source discovery를 재사용. 공통 metadata·3FS collection을 다시 구현하지 않음 |
| P1-B | pNFS native evidence와 backend-specific Deep Dive | Optional canonical query·mountstats 명시 설정·export/MDS/DS identity. 이번에는 Runtime/UI 미구현; Local SSD 확장은 별도 범위 |
| P1-C | Native sample/request ID를 기존 SDK identity에 연결. Async framework 경계의 오결합 방지 | Existing context/span·local relation 집계를 재사용하고 framework-native trace/ID의 호환 변환만 추가. 기존 local parent relation·phase wrapper 재구현 금지 |
| P1-D | vLLM KV enqueue/start/completion과 request↔batch/key link | KV connector module/plugin으로 XLayer decorator 우선 평가. Callback 공개/override 가능한 버전만 무수정 PoC; 안정적 callback이 없으면 최소 vLLM patch TBD |
| P2-A | Actual selected replica·fallback·completion | `SelectBestReplica`→execution plan/completion의 작은 callback/structured event. 비공개 C++ 경계는 최소 Mooncake 변경 필요 |
| P2-B | Storage path / process·operation evidence. Client→DS/server→device 귀속 검증 | 기존 profiler/eBPF를 triggered 구간만 사용. USRBIO/RDMA는 POSIX syscall만으로 추적할 수 없어 native event/RPC metadata의 최소 변경은 TBD |
| P2-C | Observed cross-process edge로 E2E linkage와 relation delta 확장 | 현재 signature의 local child-count/cost delta는 이미 구현. 새로 관측한 queue/RPC edge에서만 확장하며 plugin만으로 자동 전파를 약속하지 않음; 복수 framework의 작은 변경·호환성 검증 필요 |

### 기존 hook과 소스 근거

- **veRL:** [`RLInsightLogger.trace_state/trace_span`](https://github.com/volcengine/verl/blob/704a9f01bda8b9863c02c113547191bf167bc88f/verl/utils/tracking.py)은 이미 있습니다. Wrapper도 optional rl-insight logger를 선택하지만 XLayer schema·Run/worker·parent·clock/reference가 자동으로 맞는다고 가정하지 않습니다. 호환 취입을 먼저 검증합니다.
- **vLLM:** [`KVConnectorFactory` module path](https://github.com/vllm-project/vllm/blob/194da61de1769c28bfad2b6642c60cc7eca544dd/vllm/distributed/kv_transfer/kv_connector/factory.py)와 [Store connector](https://github.com/vllm-project/vllm/blob/194da61de1769c28bfad2b6642c60cc7eca544dd/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/store/connector.py)를 사용하는 안입니다. Store의 `wait_for_layer_load`/`save_kv_layer`는 no-op이므로 실측 layer I/O로 만들지 않습니다. `start_load_kv`·`wait_for_save`·`get_finished`·실제 worker operation callback을 구분하고 enqueue/return을 completion으로 오인하지 않는 검증이 필요합니다.
- **Mooncake:** Native DFS/SSD/transfer metric은 이미 있어 같은 counter/histogram을 새로 제안하지 않습니다. 부족한 selected replica·retry·completion만 `real_client.cpp` execution plan과 `client_service.cpp`/`FileStorage` 경계에서 추가하는 안입니다.
- **pNFS:** Client mountstats, kernel nfsd server counter, DS host exporter를 먼저 활용합니다. Layout/DS edge는 [Linux file layout](https://github.com/torvalds/linux/blob/47324d3a5b3abd781295044d01d92d09f184e872/fs/nfs/filelayout/filelayout.c)·[flexfile layout](https://github.com/torvalds/linux/blob/47324d3a5b3abd781295044d01d92d09f184e872/fs/nfs/flexfilelayout/flexfilelayout.c)에 의존하므로 일반 Node Exporter가 자동 제공한다고 하지 않습니다.

### Capability / 공통 interface 제안

| 선언할 것 | Backend별로 보존할 내용 |
| --- | --- |
| Source / layer | Connector·replica service·DFS client·NFS client/server/DS·block device 구분 |
| Backend/config identity | 실제 adapter/build revision·mount/export·namespace·device/owner host. Path명만으로 판정하지 않음 |
| Metric contract | 단위·counter/gauge/reset·batch/key/RPC/block population·histogram/mean/reported p99 max |
| Quality / coverage | Source availability、clock/reference/uncertainty、resolution、freshness、partial capture、entity/mapping quality |
| 상태 | unsupported·disabled·query failure·no data·stale·ambiguous·measured zero 구분 |
| Relation | Observed execution edge·configured topology·temporal coincidence를 다른 종류로 기록; 미관측 edge는 unknown |

이것은 **TBD 설계안**입니다. 통일할 것은 조회/해석 계약이며 모든 backend를 하나의 `storage_latency_p99`나 IOPS로 변환하지 않습니다. 실제 source/schema/probe 결과로 capability를 결정하며 backend 이름만으로 허용하지 않습니다. Storage 전용 rule은 필요한 layer/statistic/relation이 맞을 때만 사용하고 부족하면 limited/missing evidence로 제한합니다.

### 난이도·overhead·PoC

| 제안 | 난이도 / 예상 비용 | 검증 방법과 판정 |
| --- | --- | --- |
| P1-A capability | 낮음–중간. 작은 metadata·provider lookup; 선택 source만 query | 같은 증상을 세 backend fixture로 재생. Unsupported를 0/healthy로 만들지 않고 이종 통계·entity·clock delta 거부 |
| P1-B native 활용 | 낮음–중간. 추가 selected query/scrape series; mount/operation 수에 따른 mountstats 비용 | Version별 `/metrics`·mount namespace·idle/reset/remount·cache-hit/direct-I/O 확인. RPC mean과 block/DFS 통계를 분리 |
| P1-C native ID 연결 | 중간. Native ID/trace 변환·queue payload와 SDK bounded event; ID를 Prometheus label로 넣지 않음 | 기존 local relation 테스트와 native sample/request를 대조. Async/overlap/retry·동일 Step/다른 Run에서 없는 parent를 만들지 않음 |
| P1-D KV plugin | 중간. Python callback·request↔batch link·async completion 추적; 전 key 기록 회피 | Cold/warm KV·batch merge·partial failure·background save. Native elapsed와 span을 대조하고 no-op/enqueue를 I/O로 만들지 않음 |
| P2-A replica outcome | 중간–높음. C++ event/IPC·thread간 token·backend 전환 path 유지보수 | Memory/DFS/FileStorage·multi-replica·retry/fallback에서 실제 선택 대조. Lookup hint와 completion의 불일치 검출 |
| P2-B storage path | 높음. Kernel/version/driver·probe rate·event volume; triggered capture만 사용 | Local background I/O·MDS/DS delay·pNFS fallback·3FS 계층별 delay 독립 주입. PID/fd/operation link 없는 귀속은 unknown |
| P2-C E2E / relation delta | 높음. RPC/queue payload/schema·cross-language propagation·missing edge 관리 | Concurrent Run/shared key·partial capture/skew/drop·batch fan-in/out. Cross-Run 오결합 0 목표와 end-to-end duration·알려진 fault 대조 |

Overhead는 no-telemetry / 현재 lightweight / 추가안 / triggered capture에서 같은 workload로 비교합니다. CPU/RSS·step/rollout p50/p95·query latency/bytes·series/event/log volume·drop/coverage를 측정합니다. 비용의 수치 보장과 탐지 정확도 개선은 실측까지 TBD입니다.

## 조사 revision과 미검증 사항

| Source | 조사 revision |
| --- | --- |
| XLayer main 조사 시작 | `4734fcee5d2f408b252b4a54cb85904e20c12163`; common-storage 개선은 현행 checkout |
| veRL main | `704a9f01bda8b9863c02c113547191bf167bc88f` |
| vLLM main | `194da61de1769c28bfad2b6642c60cc7eca544dd` |
| Ray main | `07197d0cae70cdfb75f4e3f97f3c0b14f8ba05b3` |
| Mooncake main | `dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2` |
| 3FS main | `22fca04564c7cc230fd8b9523b8b92864e1dad47` |
| Node Exporter | XLayer 사용 버전 `v1.9.1`; 최신 master `1271bc244266457fd225dcef9b8a33641b582c64`도 비교 |
| Linux pNFS | `47324d3a5b3abd781295044d01d92d09f184e872` |

Source에 있다는 사실은 배포 binary·Python wheel·실제 build/endpoint의 지원을 보장하지 않습니다. XLayer의 query 계약은 현행 [Metrics contract](metrics-reference.md)를 우선합니다. pNFS server 제품/FSAL/layout이 미지정이므로 전용 exporter·service p99·DS identity 제공 여부는 TBD입니다. 세 backend의 실환경 성능·호환성·attribution 정확도는 이번 문서 조사에서 검증하지 않았습니다.
