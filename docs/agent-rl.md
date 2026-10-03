# Cross-Layer Integration for VERL

[VERL Quickstart](verl-quickstart.md)에서 trainer·GPU 연결을 확인한 뒤 vLLM·Ray·multi-node·3FS·tool event를 추가합니다.
조사에 필요한 source부터 하나씩 연결하며 공통 데이터 경로는 [Architecture](architecture.md)에 있습니다.

## How the Signals Flow

VERL 실행에서는 trainer metric과 node 자원 지표를 Prometheus에서 시간 기준으로 비교합니다.
추가 source는 수집 방식에 따라 Grafana metric, log, run 진단 결과로 나뉩니다.

```text
Agent RL / VERL                          GPU / host node
+-----------------------------+          +-----------------------------------+
| VERL trainer                |          | GPU > GPU sampler > gpu.prom       |
| stage / reward / step time   |          | CPU / network / filesystem        |
+-------------+---------------+          | (including 3FS FUSE mount)        |
              | file logger              +----------------+------------------+
              v                                           |
     logs/verl-metrics.jsonl                              |
              | bridge                                    |
              v                                           |
     application snapshot                                 |
              |                                           |
              v                                           v
        Node collector ---------------------------> Node Exporter (:19100)
        (application.prom)                                |
                                                          | scrape
                                                          v
vLLM / Ray metrics endpoints ----------------------> Prometheus
                                                          |
                                                          v
                                                  Grafana metric dashboards

3FS service latency > ClickHouse > diagnostics / standalone threefs query
Prometheus ----------------------> Run diagnostics / show_run
Tool call spans > Run event JSONL / show_run
Workload log files > Alloy > Loki > Grafana Run Logs
VERL step events > JSONL > Alloy > Loki > Grafana Step Explorer
```

Node collector는 trainer snapshot을 Node Exporter가 노출할 metric으로 변환하고, GPU sampler와 host 지표도 Node Exporter를 거쳐 Prometheus에 수집됩니다.
Prometheus는 vLLM·Ray endpoint도 직접 수집합니다.
3FS FUSE mount의 filesystem 지표는 node 자원 경로로 보고, 3FS 서비스 latency는 ClickHouse 단독 조회 또는 실행 진단에서 확인합니다.
Tool span은 JSONL event로 남고 workload log는 Alloy·Loki를 거쳐 Grafana Run Logs에 표시됩니다.
VERL step event는 별도로 Loki에 수집하면 Run Overview의 Step Explorer 목록에서 Timeline의 상세 구간을 엽니다.

## Choose the Next Source

| 알고 싶은 내용 | 추가할 source | 결과를 볼 위치 |
| --- | --- | --- |
| Rollout queue·KV cache·KV offload 상태 | 배포한 vLLM의 Prometheus endpoint | Grafana의 native rollout·KV offload panel |
| Orchestration 상태 | 배포한 Ray의 Prometheus endpoint | Stage Correlation의 Ray row, Explore |
| 3FS 서비스 latency 변화 | 3FS가 기록한 ClickHouse distributions | 단독 `threefs` 조회, 진단 JSON과 `show_run` |
| Storage node의 자원·SSD 상태 | Node Exporter와 SMART exporter | Resource·SSD dashboard |
| Workload log | Alloy와 Loki | 선택적인 Logs dashboard |
| Custom tool 호출의 대기 시간 | `EventRecorder`로 기록한 span | JSONL event와 `show_run` |

VERL·vLLM·Ray·3FS 설치와 endpoint discovery는 외부 배포가 담당합니다.
Native metric 이름·label은 실제 exporter와 맞추고 [수집 범위](metrics.md#what-is-actually-collected)를 확인합니다.
System·shared-service metric에 `run_id`가 자동으로 붙지는 않습니다.
Endpoint 하나의 target부터 확인한 뒤 Stage Correlation의 vLLM·Ray row를 사용합니다.

## Inspect One Subsystem

Cross-layer diagnosis나 완료 step 없이 subsystem 자체를 조사할 수 있습니다.
Start Here의 `Subsystem only`에서 GPU·network·storage·logs로 이동하고, vLLM은 Stage Correlation의 native panel에서 Resource node·engine을 선택합니다.
Native metric은 `run_id`로 나뉘지 않으므로 run 선택이 해당 engine의 단독 사용량을 뜻하지 않습니다.

| Subsystem | 단독 조회 경로 | 필요한 연결 |
| --- | --- | --- |
| VERL | Run Overview의 training 지표, Stage Correlation의 stage·reward·throughput | File logger bridge |
| vLLM | Stage Correlation의 queue·KV·offload·token throughput·request latency p95 | Native endpoint 등록 |
| Ray | Stage Correlation의 task·actor state, logical CPU/GPU, object store, OOM eviction | Native endpoint 등록 |
| Mooncake | Stage Correlation의 Mooncake row: connector RPC·master cache·client DFS 지표 | [Mooncake 연결](#observe-mooncake-kv-storage)과 지원 버전 |
| 기타 native exporter | `sources`의 endpoint별 Explore 링크 | Native endpoint 등록 |
| GPU / host / NIC / local disk / SSD | Compute & Communication / Data & Storage | Node collector, SSD는 선택적 SMART exporter |
| 3FS service metrics | `threefs` 명령의 시간 구간별 distributions·raw counters·freshness | 기존 `DIAGNOSTICS_CONFIG`의 ClickHouse 연결 |
| Subsystem log | Run Logs의 Workload·Node·Log directory | `ENABLE_LOGS=1`과 실제 log 파일 등록 |

GPU sampler의 device memory used/total은 지원될 때만 bytes로 노출됩니다.
GPU를 선택하면 UUID가 해당 장치와 일치하는 process memory도 함께 볼 수 있으며, `N/A`인 device field나 장치를 매핑하지 못한 process를 0이나 다른 GPU의 값으로 대체하지 않습니다.
실제 metric 이름과 측정 범위는 [Metrics Contract](metrics.md#what-is-actually-collected)에서 확인합니다.

```bash
xltel sources
xltel sources threefs
```

`sources`는 Prometheus Targets를 한 번 조회해 등록한 endpoint별 `up`, `down`, 첫 scrape 전 `unknown`, `not_discovered`, backend `unavailable`을 구분합니다.
출력의 `metrics_url`을 열면 cluster·component·instance가 선택된 Explore에서 현재 metric을 볼 수 있습니다.
처음에는 instant table로 열리며 필요한 metric을 골라 range query로 바꿉니다.
`up`은 마지막 scrape 성공을 뜻하며 workload health는 아닙니다. `last_scrape`도 함께 확인합니다.

`threefs`는 기본 30초의 ingestion 여유를 두고 직전 5분을 조회하며 run·step·baseline·diagnosis를 만들지 않습니다.
기존 `threefs.filters`, `settle_seconds`, timeout과 credential 환경변수를 그대로 사용합니다.
Distributions는 기존 metricName별 sample count·weighted mean·max·max_observed_p99를 유지하고, raw counters는 producer identity별 sample count·min/max/last를 반환합니다.
두 종류의 마지막 관측 시각과 source age는 `freshness`로 확인합니다.

Counters에는 recorder가 reset하는 값과 gauge가 섞여 있으므로 rate·delta·누적 operation 총량으로 자동 변환하지 않습니다.
같은 초에 여러 표본이 기록되면 `last`만으로 그 안의 세부 순서를 구분할 수 없습니다.
Counter source의 table·schema 오류나 지원하지 않는 `method` 필터는 `counter_status=unavailable`과 `missing_sources`로 표시하고 사용 가능한 distribution 결과는 유지합니다.
Counter identity가 1,000개를 넘거나 query 응답이 8 MiB를 넘으면 source 필터나 시간 구간을 좁힙니다.

출력은 shared-service 범위이고 `max_observed_p99`는 global p99가 아니며 단위는 3FS producer 정의를 따릅니다.
ClickHouse는 여기서 **3FS metric 저장소**입니다.
ClickHouse 자체의 query 성능·DB 운영 지표를 수집하는 기능은 별도 exporter 연결이 필요합니다.

Subsystem별 log가 필요하면 같은 config에서 기존 Alloy log root를 추가합니다.
아래 root들은 `<root>/<session>/logs/**/*.log` 구조이며 XLayer가 Ray·vLLM의 내부 log 경로를 자동 변경하거나 Docker log를 가져오지는 않습니다.
각 runtime의 log 출력 또는 배포 측 archive를 이 구조에 맞춘 뒤 node collector를 재시작합니다.

```bash
ENABLE_LOGS=1
TELEMETRY_LOG_ROOTS="verl=$HOME/telemetry-runs,ray=$HOME/ray-log-archives"
```

Run Logs에서 `Workload=ray`를 선택하고 `Log directory`는 해당 session 또는 `.*`로 지정합니다.
VERL stdout에 섞인 vLLM/Ray log는 별도 source label이 없으므로 component별로 자동 분리되지 않습니다.

## Register Native Endpoints

먼저 monitoring host에서 접근 가능한 Prometheus 형식의 endpoint를 준비합니다.
VERL의 vLLM server에서 metric을 받으려면 VERL 명령에 `actor_rollout_ref.rollout.disable_log_stats=False`와 `actor_rollout_ref.rollout.prometheus.enable=True`를 함께 지정합니다.
vLLM wheel을 다시 빌드할 필요는 없습니다.
VERL은 기본적으로 `/tmp/ray/session_latest/metrics/prometheus/prometheus.yml`의 `rollout` job에 동적으로 정해진 `host:port`를 기록하므로, 실행 중 그 주소의 `/metrics`에서 `vllm:num_requests_waiting`과 `vllm:kv_cache_usage_perc`를 확인합니다.
VERL은 자신의 Prometheus 설정 파일 전체를 다시 쓰므로 `actor_rollout_ref.rollout.prometheus.file`에 XLayer server의 `prometheus.yml`을 지정하지 않습니다.
[예제 파일](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/verl/native-sources.json)을 복사한 뒤 실제 주소로 바꾸고 사용하지 않는 source는 제거합니다.

```json
{
  "schema_version": 1,
  "sources": [
    {
      "name": "rollout-0",
      "kind": "vllm",
      "target": "10.0.0.11:8000",
      "metrics_path": "/metrics",
      "labels": {
        "role": "rollout",
        "node": "rollout-0",
        "replica": "0"
      }
    }
  ]
}
```

DCGM Exporter가 이미 있으면 `kind="dcgm"`과 정확한 `node` label로 해당 endpoint를 한 번 등록합니다.
해당 GPU node의 `ENABLE_GPU_METRICS=0`을 설정하면 nvidia-smi device collector를 중복 실행하지 않습니다.
Compute/Communication의 DCGM row는 실제 노출되는 GPU/tensor/DRAM activity, framebuffer,
PCIe throughput/replay, XID와 power/thermal throttling 신호만 표시합니다. Field 지원은 DCGM 설정·GPU에 따라 다르고
XID는 마지막 error code gauge이며 발생 횟수 counter가 아닙니다.
DCGM 설치·field 활성화와 exporter 접근 권한은 기존 배포에서 준비해야 합니다.
예제의 3FS endpoint도 기존 exporter를 등록하는 자리이며 XLayer가 3FS exporter를 만들거나 시작하지 않습니다.

Source 파일 경로는 `$HOME/telemetry/config/native-sources.json`을 사용합니다.
Node collector도 실행되어 있어야 합니다.
`verl-local.conf`에 `TELEMETRY_SOURCES_FILE="$HOME/telemetry/config/native-sources.json"`을 지정한 뒤 `down` → `up`으로 재시작합니다.

Monitoring Guide의 수동 경로를 사용했다면 `server.conf`에 같은 값을 넣고 `bash scripts/run_telemetry.sh server --config "$HOME/telemetry/config/server.conf"`로 다시 시작합니다.
시작 시 설정 형식을 검증하고 Prometheus의 `native` job에 사용할 target 파일을 만듭니다.
설정 검증 성공이 endpoint 접속 성공을 의미하지는 않으므로 Prometheus Targets에서 상태를 확인합니다.
`target`은 scheme과 path 없는 `host:port`이며 HTTPS라면 `scheme`을 `https`로 지정합니다.
Source 파일 수정 후 이미 `native` job이 활성화된 server에서는 같은 config로 target 파일만 갱신합니다.
최초 연결로 `native` job을 추가할 때는 monitoring server를 재시작합니다.

```bash
xltel sources refresh
```

Prometheus의 file discovery가 기본 30초 안에 새 target을 읽습니다.
`refresh-sources`는 config의 `SERVER_OUTPUT_DIR`를 사용하고, 이어서 `sources`로 실제 scrape 상태를 확인합니다.
수동 server 배포는 기존 `python -m xlayer_telemetry.source_discovery --input FILE --output SERVER_OUTPUT_DIR/native-targets.json`을 사용할 수 있습니다.
학습 종료 후 endpoint가 사라지면 Prometheus Targets의 현재 상태는 down으로 바뀌어도 과거 표본은 남습니다.

`kind`는 수집된 metric의 `telemetry_source`, `name`은 `component` label이 됩니다.
`labels.node`는 `TELEMETRY_TARGETS` 왼쪽 이름과 맞춰야 같은 node로 비교할 수 있습니다.
Native endpoint의 metric에는 VERL `run_id`가 자동으로 붙지 않으므로 Grafana에서 run을 선택해도 공유 vLLM·Ray 수치가 run별로 분리되지는 않습니다.
동적으로 endpoint가 바뀌는 배포는 배포 측의 discovery를 함께 관리해야 합니다.

3FS에도 Prometheus endpoint가 있다면 같은 방법으로 등록할 수 있습니다.
다만 endpoint 등록만으로 3FS 서비스 전용 dashboard가 생기지는 않습니다.
ClickHouse에 저장하는 3FS metric은 [3FS 조회](#inspect-one-subsystem)와 기존 diagnosis 설정을 사용합니다.

## Observe Mooncake KV Storage

VERL + vLLM + Mooncake + 3FS 구성에서는 Mooncake를 기본 관측 대상에 포함합니다.
`MooncakeStoreConnector`를 사용하는 vLLM endpoint의 connector metric은 기존 수집에 함께 들어오며 master/client endpoint도 native source에 등록합니다.
별도의 Mooncake 활성화 옵션은 없고, 등록된 endpoint는 기존 Prometheus native job으로 수집합니다.
Connector RPC 지연, cache lookup, client DFS I/O를 추가하면 rollout 지연이 KV 조회·전송·DFS 단계 중 어디와 함께 변했는지 조사할 수 있습니다.
Prefill/decode 간 전송용 `MooncakeConnector`와 Store connector는 다른 경로이므로 Store 전용 metric이 양쪽에서 나온다고 가정하지 않습니다.

```text
VERL rollout -> vLLM MooncakeStoreConnector -> Mooncake client -> DFS / 3FS
                       |                          |                 |
                connector RPC metrics       client DFS metrics  ClickHouse
                       |                          |                 |
                       +------ Prometheus --------+-------- XLayer --+
```

### Register the Endpoints

기존 native source 파일에 [Mooncake 예제](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/mooncake/native-sources.json)의 실제 endpoint를 추가합니다.
이미 등록된 vLLM endpoint는 다시 등록하지 않고, 배포에 존재하지 않는 endpoint는 제거합니다.
Mooncake의 `kind`는 `mooncake`, `labels.node`는 해당 process가 실행되는 node 이름으로 지정합니다.
Master가 dedicated node에 있어도 동일한 schema를 사용하며 native source에 `run_id`를 붙이지 않습니다.

| Endpoint | 활성화 방법 | 확인할 신호 |
| --- | --- | --- |
| vLLM `/metrics` | 기존 VERL Prometheus 설정과 `MooncakeStoreConnector` | `vllm:mooncake_store_operation_time_seconds`, operation·bytes·failed keys counter |
| Mooncake master `/metrics` | Master의 `--metrics_port=9003` 또는 실제 설정 port | RAM capacity·cache lookup·`master_put_start_failures_total` |
| Mooncake client `/metrics` | Client의 `setup()`에서 HTTP endpoint 활성화 | `mooncake_dfs_*` bytes·successful keys·batch latency·errors |

Programmatic client가 HTTP 옵션을 지원한다면 초기화 호출에 다음 인자를 전달합니다.
여러 worker가 같은 host에 있으면 client별로 다른 port를 지정합니다.

```python
store.setup(
    # Existing connection and storage arguments ...
    enable_client_http_server=True,
    client_http_port=9300,
)
```

`MC_STORE_CLIENT_METRIC=0`이면 client metric 수집이 비활성화되며 HTTP endpoint만 켜도 `/metrics`는 503을 반환할 수 있습니다.
`MOONCAKE_ENABLE_CLIENT_HTTP_SERVER=true`는 standalone `mooncake_store_service`의 설정으로, embedded vLLM client에 자동 적용되는 옵션이 아닙니다.
검토한 vLLM 0.24.0의 Store worker는 위 HTTP 인자를 전달하지 않으므로 Mooncake wheel이 지원하더라도 배포 측 connector가 이를 전달해야 DFS endpoint가 열립니다.
이 경우 vLLM connector와 master metric부터 연결하고, client DFS panel은 `N/A`로 유지합니다.

```bash
xltel sources refresh
xltel sources
```

이미 native job이 활성화되어 있으면 refresh로 갱신하며, 첫 연결은 `xltel restart --role server`가 필요합니다.
`xltel sources`의 endpoint별 Explore 링크와 Stage Correlation의 **Mooncake / KV storage** row에서 단독으로 조사할 수 있습니다.
Row는 초기 화면의 정보 밀도를 줄이기 위해 접혀 있으며, Mooncake를 사용하는 배포에서는 기본 조사 항목입니다.
Resource node를 rollout/client 또는 master node로 바꿔 보며, `vLLM engine`은 connector panel에만 적용됩니다.
Mooncake log도 [Subsystem log](#inspect-one-subsystem)의 기존 log root 방식으로 연결합니다.

### Interpret the Evidence

DFS bytes와 ops는 client가 성공한 KV key를 처리한 양이며 물리 SSD bytes·block IOPS가 아닙니다.
DFS latency는 batch 단위 microseconds이고 dashboard는 seconds로 변환합니다.
검토한 client exporter는 histogram의 `_count`에 bucket과 같은 client label을 붙이지 않으므로 dashboard는 동일 label의 `+Inf` bucket으로 관측 여부를 확인합니다.
Write의 GPU→host staging은 별도 histogram으로 표시하며 DFS write latency에 포함하지 않습니다.
실패한 key와 미시도 skipped write는 따로 확인하며, 성공 latency만 정상이어도 실패가 없다고 판단하지 않습니다.
`master_put_start_failures_total`은 DFS I/O 전 admission에서도 증가하므로 client DFS error와 별도로 봅니다.

Master memory는 Store 전체의 RAM tier이며 vLLM GPU KV cache와 다릅니다.
Memory/file cache hit counter는 lookup hit rate로 읽고 현재 저장된 key gauge를 분모로 hit ratio를 만들지 않습니다.
검토한 Mooncake revision의 file hit counter는 descriptor DFS hit를 포함하지 않으므로 3FS cache hit rate로 읽지 않습니다.
`mooncake_ssd_*`나 legacy master file capacity를 descriptor 기반 DFS·USRBIO의 사용량으로 대체하지 않습니다.
Client에 DFS metric이 없으면 다른 storage metric으로 채우지 않으며 [Metrics Contract](metrics.md#what-is-actually-collected)에서 source와 단위를 확인합니다.

Selected step의 Timeline 시간 범위를 유지해 Stage Correlation으로 이동한 뒤 connector·client·3FS service 신호를 비교합니다.
공유 master/client 신호는 해당 run의 소유량이나 3FS가 원인이라는 증명이 아닙니다.
Optional LLM은 [Mooncake query 예제](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/mooncake/prometheus.json)의 cluster·node·component와 시간 구간을 실제 조사 대상에 맞춰 [직접 수집](local-llm.md#diagnose-collected-metrics-directly)할 수 있습니다.
`--collect-only`로 metric·label·missing source를 먼저 확인하고 필요할 때만 모델에 전달합니다.
새 rule을 자동 활성화하거나 training 중 LLM을 호출하지 않습니다.

[실측 기록](validation/mooncake-telemetry-20261003.json)은 설치된 Mooncake의 DFS read/write, Prometheus query, 진단 입력과 미검증 범위를 구분합니다.

설정·metric 지원은 [Mooncake observability](https://kvcache-ai.github.io/Mooncake/getting_started/observability.html), [DFS 계측 소스](https://github.com/kvcache-ai/Mooncake/blob/e88aacf20cd83461ac4e6ff18a8b50a1e2b3f349/mooncake-store/include/client_metric.h), [vLLM Store metrics](https://docs.vllm.ai/en/latest/api/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/store/metrics/)를 기준으로 하며 배포 버전의 `/metrics`에서 실제 존재 여부를 확인합니다.

## Map Multiple Nodes to a Run

기본 manifest는 driver node의 단순 배치입니다.
분리된 trainer·rollout의 role 배치는 별도 manifest로 기록합니다.
각 node collector와 `TELEMETRY_TARGETS`, native source 파일은 각각 설정하며 role manifest로 대체되지 않습니다.

```text
trainer-0 > node exporter :19100 --------+
rollout-0 > node exporter :19100 --------+--> Prometheus > Grafana
rollout-0 > vLLM /metrics ---------------+
             | labels.node=rollout-0
driver run > topology manifest -----------> declared role / node placement
```

아래 명령은 기존 wrapper manifest를 보존하면서 topology 참고 파일을 만듭니다.

```bash
. .venv/bin/activate
PYTHONPATH=. python -m xlayer_telemetry.manifest \
  --output "$HOME/telemetry-runs/grpo-001/topology-manifest.json" \
  --run-id grpo-001 \
  --role trainer=trainer-0 \
  --role rollout=rollout-0
```

Manifest는 실행 조건·위치의 기록입니다.
여러 node의 같은 role은 `--role` 반복, endpoint·profile 경로는 `--source`·`--artifact`로 남깁니다.
`--source`는 수집을 켜지 않으며 `show_run`은 기본 `telemetry-manifest.json`을 읽습니다.
기본 manifest를 수정할 때 settings·sources·artifacts를 보존합니다.
Grafana의 Resource node는 Prometheus target 목록을 사용하므로 manifest만으로 그래프가 생기지는 않습니다.

기본 file logger bridge는 driver에서 동작합니다.
Remote worker에서 SDK나 `EventRecorder`를 직접 호출할 때만 해당 worker가 package를 import할 수 있도록 설치하고 다음 환경을 전달합니다.

| 설정 | 각 worker에 전달할 값 |
| --- | --- |
| `TELEMETRY_RUN_ID` | 실행 전체에서 같은 식별자 |
| `TELEMETRY_NODE` | 실제 실행 node의 논리 이름 |
| `TELEMETRY_METRICS_DIR` | 해당 node의 run별 local snapshot directory |
| `TELEMETRY_EVENTS_DIR` | 해당 node의 run별 local event directory |
| `RANK` / `LOCAL_RANK` | 실행기가 부여한 process 번호 |

Ray를 통해 실행한다면 worker의 runtime environment에도 전달해야 합니다.
Driver의 환경 변수를 설정하는 것만으로 remote worker에 모두 전달된다고 가정하지 않습니다.
각 collector는 자기 node의 snapshot을 읽고, shared storage의 동일한 snapshot을 여러 node에서 중복 수집하지 않습니다.
Remote worker의 snapshot을 Grafana에 표시하려면 그 worker node에도 collector를 실행하고 해당 snapshot directory를 `TELEMETRY_METRICS_DIR`로 전달합니다.

## Add Diagnostics

진단은 wrapper의 선택적 sidecar로 step·Prometheus·3FS ClickHouse를 비교합니다.
[diagnostics.json](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/verl/diagnostics.json)을 복사해 endpoint를 바꾸고 사용하지 않는 `threefs`는 제거합니다.
`verl-local.conf`에 `DIAGNOSTICS_CONFIG="$HOME/telemetry/config/diagnostics.json"`을 지정하고 새 `RUN_ID`로 실행합니다.
`ENABLE_LOGS=1`과 Alloy·Loki를 연결하면 Bottleneck Summary와 Timeline에서도 결과를 확인할 수 있습니다.

3FS를 연결하려면 ClickHouse에 `distributions` 데이터가 있어야 합니다.
Database와 `mount_name` 등 filter를 실제 배포에 맞추고, 인증이 필요하면 `THREEFS_CLICKHOUSE_USER`와 `THREEFS_CLICKHOUSE_PASSWORD` 환경 변수로 전달합니다.
설정의 endpoint는 조회 가능한 HTTP 주소여야 합니다.

수동 wrapper를 사용하는 배치에서는 다음처럼 새 run에 `--diagnostics-config`를 전달합니다.
`...`와 Python 경로는 기존 VERL 명령으로 대체하고, node collector도 새 run의 `telemetry-metrics` directory를 읽도록 시작합니다.

```bash
TELEMETRY_PYTHON="$PWD/.venv/bin/python" \
  bash scripts/run_verl_with_telemetry.sh \
    --output "$HOME/telemetry-runs/grpo-002" \
    --run-id grpo-002 \
    --node gpu-local \
    --diagnostics-config "$HOME/telemetry/config/diagnostics.json" \
    -- /path/to/verl-env/bin/python -m verl.trainer.main_ppo \
      ...
```

결과는 `diagnostics/diagnostics.jsonl`에 누적되고 최신 결과는 `diagnostics/latest.json`과 `show_run`에서 확인합니다.
`diagnostics/investigation/*.jsonl`은 Loki가 읽는 작은 projection이며 완전한 evidence는 `latest.json`에 있습니다.
진단 오류는 `logs/telemetry-diagnostics.log`에서 확인하며 workload의 종료 코드를 바꾸지 않습니다.

| 결과 | 의미 |
| --- | --- |
| `bottleneck_suspected` | 설정한 기준에 따라 병목 후보를 발견함 |
| `no_anomaly_observed` | 조회한 데이터와 기준에서 이상을 찾지 못함 |
| `insufficient_data` | 판단에 필요한 데이터가 부족함 |
| `missing_sources` | 누락된 source 목록; 정상이라는 뜻이 아님 |

기본 query와 실제 exporter의 metric 이름·label이 다르면 `prometheus.queries`를 수정합니다.
VERL file logger에 원본 timestamp가 없으므로 완료 step 시간 범위는 근사치입니다.
3FS 결과는 같은 시간대의 shared storage 상태이며 특정 step의 I/O만 분리한 값이 아닙니다.
진단에 필요한 source를 연결하지 않았다면 `missing_sources`를 먼저 해결하고 `no_anomaly_observed`만으로 병목이 없다고 판단하지 않습니다.

기존 `findings`의 3FS latency 비교는 현재 창과 직전 동일 길이 창에서 관측한 `max_observed_p99`를 사용합니다.
새 `candidates`는 같은 run·worker의 이전 step 창에서 **같은 3FS metricName**을 비교하며, 해당 baseline이 없으면 강한 storage 판정을 만들지 않습니다.
전체 요청을 합친 global p99로 해석하지 않습니다.
Grafana는 ClickHouse를 직접 query하지 않고 Loki에 전달된 diagnosis projection을 보여 줍니다.

추가 rule에 필요한 source는 실제 배포의 metric과 측정 대상을 확인한 뒤 `prometheus.queries`에 지정합니다.
예를 들어 `storage_device_busy_ratio`는 **3FS storage device**의 busy ratio이고 기본 `disk_busy_ratio`는 trainer node의 local disk이므로 서로 바꾸어 사용할 수 없습니다.
`network_utilization_ratio`도 측정한 NIC의 link capacity로 정규화한 실제 비율이어야 합니다.
이 값이나 request size가 없으면 해당 strong rule은 `missing_evidence`를 표시합니다.
전체 조건과 baseline 선택법은 [Cross-Layer Diagnosis](diagnosis.md)에 있습니다.

## Understand Asynchronous Runs

`actor_rollout_ref.rollout.mode`는 vLLM rollout server 방식이고 `trainer.v1.trainer_mode`는 training step의 scheduling 방식입니다.
현재 VERL의 vLLM rollout은 `async` server를 쓰지만 `trainer.v1.trainer_mode=sync`인 학습도 가능합니다.
이 환경의 VERL `0.10.0.dev`에서는 `actor_rollout_ref.rollout.mode=sync`가 제거됐으므로 synchronous trainer를 원해도 rollout mode는 `async`로 둡니다.
XLayer wrapper는 직접 전달된 `trainer.v1.trainer_mode=colocate_async`·`separate_async` 또는 fully async entrypoint를 비동기 trainer로 감지하며, 기본 trainer mode는 `sync`로 처리합니다.
별도 Bash launcher나 YAML config가 실제 trainer mode를 감추면 wrapper에 `--execution-mode sync` 또는 `--execution-mode async`를 명시합니다.
동기 trainer는 `rl_step`, 비동기 trainer는 `trainer_update` 완료 경계를 기록합니다.

비동기 rollout·Ray·storage activity는 trainer update와 별도로 계속될 수 있습니다.
진단은 주기적인 시간 창을 비교하며 같은 창의 activity 전체를 특정 update에 귀속하지 않습니다.
직접 수집한 policy version lag나 event가 있을 때 연결 근거로 함께 읽습니다.

## Record a Custom Tool Span

Tool 호출처럼 시작·종료 시간이 필요한 작업은 JSONL span으로 기록할 수 있습니다.
Application 환경에 [SDK를 설치](application-metrics.md#1-prepare-the-python-environment)하고 run별 환경 변수를 설정합니다.

```bash
export TELEMETRY_RUN_ID='grpo-001'
export TELEMETRY_NODE='rollout-0'
export TELEMETRY_EVENTS_DIR="$HOME/telemetry-runs/grpo-001/telemetry-events"
```

```python
from xlayer_telemetry.events import EventRecorder

events = EventRecorder.from_env(producer="agent", role="rollout")
if events is None:
    result = call_tool()
else:
    with events.span(
        "tool.call", phase="tool_interaction", step=1,
        attributes={"tool": "pytest"},
    ) as tool_span:
        result = call_tool()
```

Event 경로는 `events.path`에서 확인합니다.
파일명은 `<producer>-<role>-<worker>@NODE@RUN.jsonl`이며 식별자의 `-`는 `%2D`로 인코딩해 충돌을 피합니다.
같은 run·node·producer·role의 worker는 서로 다른 ID를 사용합니다(기본 `RANK`, 없으면 `0`).
`attributes.tool`에 실제 이름을 기록해야 같은-tool baseline을 비교할 수 있습니다.
하위 sandbox에 `tool_span`의 trace/span ID를 전달하면 parent 관계가 연결됩니다.
Event는 Prometheus metric이 아니며 Alloy·Loki로 보내면 Timeline에 exact span으로 표시됩니다.

## Observe an Agent Sandbox

Agent tool latency에는 sandbox 준비·실행·filesystem 작업이 포함될 수 있습니다.
XLayer는 외부 runtime의 lifecycle을 기록하고 cgroup을 읽으며 생성·배치는 담당하지 않습니다.
기본 예시는 OverlayFS + local SSD/NVMe이고 `filesystem`은 btrfs·zfs·plain workspace·VM 등 실제 배포로 지정합니다.

```text
Colocated
GPU / rollout node
+-- AgentLoop > tool.call > sandbox worker > OverlayFS > local NVMe
+-- Node collector > Node Exporter > Prometheus

Dedicated
GPU / rollout node > sandbox RPC > sandbox node
                                 +-- sandbox worker > OverlayFS > local NVMe
                                 +-- Node collector > Node Exporter > Prometheus
```

두 배치는 `role=sandbox`와 같은 metric 이름을 사용합니다.
`node`와 `deployment`만 실제 위치에 맞게 달라지고, dedicated 배치에서는 sandbox node에도 [node collector](monitoring.md)를 실행해 monitoring server의 target에 등록합니다.
Manifest의 role mapping에도 `sandbox=sandbox-0`을 추가할 수 있지만 manifest만으로 collector가 시작되지는 않습니다.

### Record lifecycle spans

`sandbox_id`·`trajectory_id`는 event attribute에만 저장되고 Prometheus label이 아닙니다.
`parent_span_id`와 `trace_id`를 기존 `tool.call` span에서 넘기면 tool과 sandbox lifecycle을 같은 trace에서 찾을 수 있습니다.
RPC로 dedicated node에 전달할 때도 두 ID를 sandbox worker에 전달합니다.

```python
from pathlib import Path

from xlayer_telemetry.events import EventRecorder
from xlayer_telemetry.sandbox import SandboxRecorder

events = EventRecorder.from_env(producer="sandbox", role="sandbox", worker_id="worker-0")
if events is None:
    run_command_in_existing_sandbox()
else:
    sandbox = SandboxRecorder(
        events, runtime="containerd", filesystem="overlayfs",
        deployment="dedicated", sandbox_node="sandbox-0",
    )
    with sandbox.span(
        "exec", step=1, trajectory_id="trajectory-17", sandbox_id="sandbox-17",
        trace_id=tool_span.trace_id, parent_span_id=tool_span.span_id,
        attributes={"tool": "pytest"},
        cgroup=Path("/sys/fs/cgroup/your-sandbox-container"),
    ):
        run_command_in_existing_sandbox()
```

Operation은 `queue`, `acquire`, `prepare`, `exec`, `reset`, `release`이며 실제 단계만 기록합니다.
`queue`는 pool 대기, `acquire`는 선택된 sandbox 할당입니다.

Colocated는 `tool_span`을 전달하고 dedicated는 RPC로 `run_id`·trace/parent ID를 넘깁니다.
RPC 전파·worker 환경 설정은 외부 runtime이 담당합니다.

선택적 `cgroup`은 span 전후 I/O·PSI를 같은 trace의 `sandbox.resource_sample`에 기록하며 worker Prometheus 집계와 분리합니다.
Dedicated worker의 `TELEMETRY_RUN_ID`는 rollout과 같고 `TELEMETRY_NODE`는 실제 sandbox node입니다.
각 node의 JSONL을 Alloy가 읽는 run root에 두어야 Timeline에서 함께 볼 수 있습니다.

veRL `function_tool_path`로 연결하는 실제 예시는 [verl-lab SWE-Bench adapter](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/sandbox/verl_lab_swebench_tools.py)입니다.
XLayer 저장소 루트에서 아래 경로를 설정하고 기존 `verl-lab` 명령을 XLayer wrapper로 실행하면, 원본 tool·reward 코드를 바꾸지 않고 `read_source`·`test_patch`·`edit_and_test`의 `tool.call`과 실제 Docker grader 호출의 `sandbox.exec`를 기록합니다.

```bash
export VERL_LAB_ROOT="$HOME/workspace/verl-lab"
export VERL_LAB_TOOLS_PATH="$VERL_LAB_ROOT/scripts/swebench_agent_tools.py"
export FUNCTION_TOOL_PATH="$PWD/examples/sandbox/verl_lab_swebench_tools.py"
export CUSTOM_REWARD_FUNCTION_PATH="$FUNCTION_TOOL_PATH"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
```

기존 `verl-lab`의 SWE-Bench POC가 준비된 단일 GPU host라면 다음 smoke script가 데이터를 별도 경로에 복사하고 2-step veRL+vLLM 학습을 실행합니다.
원본 dataset·도구 파일은 수정하지 않으며, Docker grader image는 미리 local cache에 있어야 합니다.
이 4-sample smoke는 `DATALOADER_NUM_WORKERS=0`을 기본값으로 사용하므로 `verl-lab` benchmark runner가 해당 환경 변수를 `data.dataloader_num_workers`에 전달해야 합니다.
이전 runner에서 환경 변수를 지원하지 않으면 DataLoader 기본 worker 8개가 유지될 수 있습니다.

```bash
export VERL_LAB_ROOT="$HOME/workspace/verl-lab"
export MODEL_PATH="/path/to/local/model-snapshot"
export RUN_ROOT="$HOME/telemetry-runs/swebench-sandbox-smoke"
bash examples/sandbox/run_verl_lab_smoke.sh
```

이 smoke dataset은 문제 문장에 이미 있는 `self.logger.error` 수정 방향을 prompt에 명시하므로 agent의 SWE-Bench 해결률 평가에는 사용하지 않습니다.
`XLAYER_SWE_EDIT_ONLY=1`로 `test_patch`를 모델의 도구 목록에서 제외하되 원래 reward와 grader는 재사용합니다.
출력의 `telemetry/telemetry-events`에서 `tool.call`과 `sandbox.exec`의 trace를 확인하고, 두 번째 step부터 같은 tool의 duration baseline을 비교할 수 있습니다.
Trainer log는 `logs/training.log`에 기록하므로 [Alloy의 기본 run 경로](monitoring.md#add-run-logs-with-loki)와 맞습니다.
`validate_smoke`는 이전 `run/telemetry-events` 구조도 읽습니다.

기본 trainer mode는 `sync`입니다.
`TRAINER_MODE=colocate_async` 또는 `TRAINER_MODE=separate_async`로 기존 `verl-lab` launcher의 mode를 전달하며, XLayer wrapper에도 비동기 update 경계를 명시합니다.
`separate_async`는 GPU 두 개가 필요하고, 기본 visible device는 `0,1`입니다.
장치 배치가 다르면 `CUDA_VISIBLE_DEVICES`를 지정합니다.

```bash
RUN_ROOT="$HOME/telemetry-runs/swebench-separate-async" \
  TRAINER_MODE=separate_async TOTAL_TRAINING_STEPS=3 \
  bash examples/sandbox/run_verl_lab_smoke.sh
```

기본값은 2 step이며 `TOTAL_TRAINING_STEPS`로 바꿀 수 있습니다.
진단까지 연결하려면 monitoring server를 먼저 실행하고 `DIAGNOSTICS_CONFIG`에 [진단 설정](#add-diagnostics)의 경로를 전달합니다.
`VERL_PROMETHEUS_ENABLE=1`은 이를 지원하는 `verl-lab` launcher에서 vLLM native endpoint를 활성화하며, endpoint 등록은 별도로 필요합니다.
이 옵션 자체가 XLayer collector에 endpoint를 자동 등록하지는 않습니다.

`test_patch`는 완성된 unified diff를 받습니다.
스키마 밖 인자는 `invalid_tool_arguments`·오류 span으로 남기고 grader를 실행하지 않습니다.
유효하지 않은 diff는 원래 tool의 patch 검증 결과로 반환되며 인자 오류와 구분합니다.
예제의 `edit_and_test(path, old, new)`는 원본에서 `old`가 한 번만 일치할 때 patch를 만들어 grader로 보냅니다.
Guided prompt는 integration 검증용이며 해결률을 증명하지 않습니다.

실제 Docker grader에 도달해야 `sandbox.exec`가 생기고, 개별 cgroup·trajectory는 자동 발견하지 않습니다.
해당 정보가 있으면 `SandboxRecorder.span(cgroup=...)`으로 기록합니다.
Docker daemon이 만든 container는 worker PID의 cgroup 아래에 있다고 보장할 수 없습니다.
Sampler가 grader resource를 보려면 전용 parent를 설정하고 실제 container 위치를 확인합니다.

```bash
export XLAYER_SANDBOX_CGROUP_PARENT=xlayer-sandbox-worker.slice
bash examples/sandbox/run_verl_lab_smoke.sh
python -m examples.sandbox.validate_smoke "$RUN_ROOT" --require-sandbox
```

최근 Docker adapter의 outcome과 async update 기록도 확인하려면 다음을 사용합니다.
`--require-results`는 각 `sandbox.exec`에 같은 run/trace/span의 outcome이 하나씩 있는지 검사합니다.
Nonzero exit가 기록돼도 trace 연결 검증은 통과할 수 있으며, 실행 성공 여부는 outcome을 별도로 확인합니다.

```bash
python -m examples.sandbox.validate_smoke "$RUN_ROOT" \
  --require-sandbox --require-results --execution-mode async --min-updates 3
```

Sync run에는 `--execution-mode sync`를 사용합니다.
Async의 경계는 `trainer_update`이며, 모든 rollout/tool span이 해당 시간 구간 안에 포함된다고 가정하지 않습니다.

이 옵션은 grader의 `docker run`에 `--cgroup-parent`를 전달합니다.
호스트에서 실제 container PID의 `/proc/<pid>/cgroup`을 확인해 지정한 parent 아래에 생성됐는지 검증해야 합니다.
`docker run --rm` grader는 매우 짧게 실행될 수 있으므로, container가 없을 때 parent가 유지되는지도 확인하고 sampler interval만으로 모든 grader 호출이 포착된다고 가정하지 않습니다.
실제 Docker container 2개 이상의 parent I/O 합산을 별도로 검사하려면 로컬에 grader image가 있는 호스트에서 `python -m examples.sandbox.validate_docker_cgroup`을 실행합니다.
이 검사는 임시 container를 만들고 종료하며, veRL smoke의 개별 trajectory 귀속까지 검증하지는 않습니다.
실행 중 parent/child counter를 동시에 읽을 수 없으므로, child scan 전후의 parent 값을 사용해 합산 범위를 검사합니다.

### Real validation coverage

2026-09-30에는 Qwen2.5-1.5B-Instruct로 아래 6개 조합을 각각 3 step 실행했습니다.
두 workload 모두 실제 veRL GRPO 학습·vLLM generation·외부 Docker tool 실행을 사용했습니다.
버전·span 수·누락 source·검증 범위는 [검증 결과](validation/sandbox/validation-20260930.json)에 기록했습니다.

| Workload | `sync` | `colocate_async` | `separate_async` |
| --- | --- | --- | --- |
| SWE-Bench guided patch + Docker grader | 3 step 완료 | 3 update 완료 | 3 update 완료 |
| GSM8K + Docker calculator | 3 step 완료 | 3 update 완료 | 3 update 완료 |

GSM8K 검증에는 [calculator tool](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/sandbox/calculator_tools.py)을 `FUNCTION_TOOL_PATH`로 지정하고, 기존 math prompt에 `calculate` 사용을 안내했습니다.
작은 integration fixture이며 SWE-Bench·GSM8K 해결률이나 학습 throughput 평가가 아닙니다.
Calculator의 인자 오류와 실제 Docker nonzero exit도 별도로 실행해 오류 span이 남는지 확인했습니다.

Prometheus에서 application·GPU·host·vLLM·Ray·sandbox cgroup 지표를, Loki에서 trainer log·step·span·diagnosis projection을 확인했습니다.
Grafana Step Explorer의 step 메뉴가 실제 run·record·node·시간 범위를 전달하고, Step Detail·Bottleneck Summary·Timeline·Logs에서 해당 자료를 읽는지 브라우저로 검증했습니다.
이 검증 이후 Step Explorer 목록은 Run Overview에, Step Detail은 Timeline에 통합했습니다.
`policy_version_lag`와 RDMA source는 없었으므로 missing evidence로 남겼습니다.
Optional Ollama 진단은 별도로 실제 관측 packet을 읽고 한국어 응답과 evidence validation을 통과했습니다.

`separate_async` 학습은 한 물리 host의 GPU 두 개를 분리해 실행했습니다.
별도로 Ubuntu 24.04 VM 두 개에서 기존 node collector·SDK·dedicated sandbox RPC의 context 전달을 검증했습니다.
두 node에 같은 worker ID를 사용해도 run/node별 metric과 Loki event가 구분됐고, remote `tool.call → sandbox.exec`의 trace/parent 연결과 실제 cgroup resource event를 확인했습니다.
Sandbox guest clock을 약 12초 이동하면 진단은 `unsafe`·`insufficient_data`로 보류됐으며, collector 중단·복구 시 clock evidence는 `unknown → aligned`로 변했습니다.

VM 검증 workload는 HTTP/file-I/O probe이며 VM 내부에서 distributed VERL 학습을 실행한 것은 아닙니다.
Guest는 독립 kernel·clock을 가지지만 물리 CPU·NIC·SSD를 공유하므로 독립 서버의 GPU·RDMA·storage contention 검증을 대신하지 않습니다.
일반 clock screening은 이 검증에서 `require_sync=false`로 수행했으며, 실제 운영에서는 [NTP/chrony와 clock check](monitoring.md#check-clock-alignment-before-diagnosing)를 구성합니다.
이번 matrix는 Terminal-Bench, 3FS KV offload, storage overload, full profiler capture를 재검증하지 않았습니다.
Async trainer update 구간에 모든 rollout/tool span이 포함되는 것은 아니므로, span은 해당 trace의 실제 시간 범위에서도 별도로 조회했습니다.

### Sample the sandbox worker cgroup

Worker와 하위 container를 포함하는 **안정적인 cgroup v2 subtree**를 지정합니다.
`/proc/<pid>/cgroup`의 `0::` 경로를 `/sys/fs/cgroup` 아래에서 확인하고 Docker container가 실제 자손인지 검사합니다.
매번 바뀌는 container cgroup 대신 공통 parent를 Prometheus 대상으로 사용합니다.
Node·runtime·filesystem·deployment 조합당 producer 하나로 집계합니다.
서로 다른 label 조합은 `--textfile-name`도 나누되 파일명만 달리해 같은 시계열을 중복 노출하지 않습니다.

```bash
python -m xlayer_telemetry.collectors.sandbox_sampler \
  --cgroup /sys/fs/cgroup/your-sandbox-worker \
  --textfile-dir "$HOME/telemetry/state/node/textfile" \
  --node sandbox-0 \
  --runtime containerd \
  --filesystem overlayfs \
  --deployment dedicated
```

Colocated라면 `--node`를 GPU/rollout node 이름으로, `--deployment`를 `colocated`로 바꾸고 그 node collector의 `textfile` directory를 지정합니다.
`io.stat`의 bytes·operations, `io.pressure`·`cpu.pressure`의 `some.total`, `memory.pressure`의 `some.total`·`full.total`, `cpu.stat`, `memory.current`·`memory.peak`·`memory.events`를 읽습니다.
파일 형식과 누적 counter의 의미는 [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html), PSI는 [Linux PSI](https://docs.kernel.org/accounting/psi.html)를 따릅니다.
PSI ratio는 직전 표본 이후 stall 시간 비율이므로 첫 표본·counter reset에서는 비어 있으며, 읽을 수 없거나 음수인 값도 0으로 대체하지 않고 생략합니다.

`sandbox_memory_pressure_ratio`는 일부 task가 memory에 막힌 시간, `sandbox_memory_full_pressure_ratio`는 모든 non-idle task가 동시에 막힌 시간의 비율입니다.
`sandbox_memory_high_events_total`은 `memory.high` 초과로 direct reclaim에 진입한 횟수이고, `sandbox_memory_max_events_total`은 `memory.max` 경계에 도달한 횟수입니다.
두 event 모두 실제 OOM kill을 뜻하지 않으며, `sandbox_oom_total`은 `memory.events`의 `oom`, `sandbox_oom_kill_total`은 실제 kill을 센 `oom_kill`입니다.

`sandbox_cpu_throttled_seconds_total`, `sandbox_cpu_throttled_periods_total`, `sandbox_cpu_periods_total`은 선택 cgroup 자체의 CPU bandwidth 제한 통계입니다.
하위 cgroup의 합계가 아니며 CPU controller가 이 field를 제공하지 않으면 생략합니다.
Dashboard는 throttled seconds/s와 throttled periods/periods를 구분하고, period 증가가 없으면 비율을 표시하지 않습니다.

Page cache hit처럼 block device에 도달하지 않은 작업은 `io.stat` bytes로 보이지 않을 수 있습니다.
Counter는 worker cgroup이 유지되는 동안에만 단조 증가합니다.
`sandbox_memory_peak_bytes`는 cgroup 생성 이후의 high-water mark이므로 선택한 step이나 Grafana 시간 범위의 peak로 해석하지 않습니다.
`sandbox_sample_timestamp_seconds`로 sampler의 마지막 갱신 시각을 확인하며, 오래된 textfile 표본은 현재 압력으로 해석하지 않습니다.

Sandbox I/O는 cgroup에 속한 여러 block device의 합계이고 local NVMe busy는 선택한 node/device 전체 범위입니다.
`sandbox.device`를 설정할 때는 container의 `io.stat`에 표시된 major:minor와 호스트의 backing device를 대조합니다.
대조하지 못했다면 두 수치가 같은 device를 나타낸다고 가정하지 않습니다.
3FS 서비스 latency나 공유 storage 지표와 local sandbox NVMe를 합산하지 않습니다.
특정 tool이 느리고 cgroup pressure와 NVMe busy가 동시에 증가해도 다른 sandbox·process의 부하가 섞일 수 있으므로 [진단 후보](diagnosis.md#baseline-and-rule-state)로만 해석합니다.
원인을 더 좁혀야 하면 해당 구간에서 VFS syscall, OverlayFS copy-up, fsync 등을 선택적으로 profile할 수 있으며 eBPF는 기본 의존성이 아닙니다.

### Preserve Device Evidence in Events

`SandboxRecorder.span`은 `io.stat`의 major:minor별 counter delta를 `sandbox.resource_sample.attributes.io_devices`에 보존합니다.
Prometheus는 기존 cgroup 합계와 낮은 cardinality label을 유지합니다.
Backing device를 운영자가 확인한 경우 `device_major_minor`를 함께 전달합니다.

```python
with sandbox.span("exec", cgroup=cgroup_path, device_major_minor="259:0"):
    run_tool()
```

`device_mapping.status=observed`는 지정한 major:minor가 그 cgroup의 관측 device에 있었다는 뜻입니다.
`unmatched`는 관측 목록에 없고, `unconfigured`는 매핑을 지정하지 않은 경우입니다.
이 상태만으로 OverlayFS backing path나 특정 tool의 device ownership이 입증되지는 않습니다.
Host에서 `lsblk -o NAME,MAJ:MIN`과 실제 workspace backing device를 확인한 뒤 diagnostics의 `sandbox.device_major_minor`에도 같은 값을 설정합니다.
Diagnostics는 해당 run·sandbox node·시간 구간의 event를 찾아 `sandbox_device_mapping`을 표시하며, 겹치는 cgroup/span의 delta를 합산하지 않습니다.

### Enable the optional diagnosis rule

기존 [diagnostics 설정](#add-diagnostics)에 sandbox section을 추가합니다.
`node`는 sandbox worker가 있는 node이고 `device`는 해당 local SSD의 Node Exporter device label입니다.
Colocated에서 `node`를 생략하면 rollout/trainer record의 node를 사용하지만, dedicated 배치에서는 반드시 실제 sandbox node를 지정합니다.

```json
"sandbox": {
  "enabled": true,
  "node": "sandbox-0",
  "device": "nvme0n1",
  "events_dir": "/path/to/run/telemetry-events"
}
```

`events_dir`는 진단 process가 읽을 수 있는 선택적 경로입니다.
Producer 이름과 무관하게 EventRecorder JSONL의 정상 종료 `tool.call` 중 해당 run·구간에 완전히 포함된 최대 duration을 사용하며 baseline은 같은 tool끼리 비교합니다.
Span이 없으면 `agent_tool_call_duration_seconds`를 사용하고 첫 step은 이전 baseline이 없어 slowdown을 판단하지 않습니다.
Event 조회는 설정된 incremental cache를 재사용하며, cache를 끄거나 한도를 넘으면 scan하므로 장시간 run의 비용을 확인합니다.
Event fallback의 evidence는 span boundary와 trace/span을 보존하며 Prometheus query·sampling metadata를 사용하지 않습니다.
Exact span도 approximate step 귀속은 correlation이며, cgroup PSI와 지정 local device busy는 별도 scope입니다.
개별 sandbox·trajectory 문맥은 trace/span attribute에서 확인합니다.

## Optional Integrations

이미 운영 중인 RL-Insight server가 있다면 wrapper에 `--rl-insight-url <URL>`을 추가할 수 있습니다.
Wrapper는 `rl_insight` logger를 추가하며 실제 logger 지원과 서비스 연결은 사용하는 VERL 배포에서 확인해야 합니다.
이 저장소가 RL-Insight server를 설치하지는 않습니다.

상세 실행 구간이 필요하면 [Run Analysis](dashboards.md#run-analysis)의 선택적 profiling을 사용합니다.
[CUDA smoke example](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/verl/gpu_smoke.py)은 telemetry 경로를 확인하는 작은 workload이며 실제 VERL 학습 성능을 측정하는 도구가 아닙니다.

## Record Sandbox Execution Outcomes

`sandbox.exec`의 `status=ok`는 Python 호출이 예외 없이 반환됐다는 뜻입니다.
Grader가 성공했거나 model의 답이 맞았다는 의미는 아닙니다.
Runtime adapter는 같은 trace/span에 별도 `sandbox.exec_result`를 기록할 수 있습니다.

```python
with sandbox.span("exec") as identity:
    result = run_external_tool()
    sandbox.execution_result(
        identity,
        outcome="completed" if result.returncode == 0 else "nonzero_exit",
        exit_code=result.returncode,
    )
```

VERL-lab Docker adapter는 exit code, timeout, 실행 시작 오류를 자동 기록하며 기존 반환값과 예외를 그대로 전달합니다.
`oom`, `infra_failure`, `test_failure`, `model_failure`는 runtime이 명시적인 증거로 구분한 경우에만 전달합니다.
Exit code 137만으로 OOM을 추정하거나 nonzero exit를 model failure로 자동 분류하지 않습니다.
Result는 event JSONL/Run Logs에서 span ID로 연결하며 stdout·stderr·명령 인자는 저장하지 않습니다.
Reward, retry, trajectory 제외 정책은 외부 runtime/trainer가 결정합니다.
