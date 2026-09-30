# How XLayer Telemetry Works

이 문서는 [README의 순서](../README.md#start-here)를 따라 VERL을 연결할 때 각 process가 왜 필요한지 설명합니다.
먼저 한 node의 기본 경로를 이해한 뒤 vLLM·Ray·3FS·Loki를 추가하면, 화면의 빈 값이 설정 문제인지 아직 연결하지 않은 source인지 구분할 수 있습니다.
실행 명령은 [VERL 연결 가이드](verl-quickstart.md)에, 추가 source 설정은 [Cross-Layer Integration](agent-rl.md)에 있습니다.

## Why XLayer Exists

XLayer의 주된 역할은 VERL과 함께 동작하는 여러 서브시스템의 telemetry를 **Collect → Correlate → Diagnose**하는 것입니다.
VERL trainer·vLLM·Ray·tool/sandbox와 GPU·host·network·storage source를 기존 관측 도구로 연결하고, run·step·phase 문맥에서 느린 구간의 bottleneck candidate와 누락된 근거를 제시합니다.
Prometheus·Grafana·Loki는 저장·query·시각화를 담당하고 XLayer는 workload 문맥, baseline 비교, evidence 기반 investigation을 담당합니다.

```text
1. Collect: VERL and its subsystem telemetry
  VERL trainer / AgentLoop   vLLM / Ray   tool / sandbox
  GPU / host   network / RDMA   filesystem / 3FS / SSD
  configured metrics / logs / events + optional profile artifacts
  |
  +--> 2. Correlate: XLayer workload context
       run > step/iteration > phase > span/event
       + node / role / worker / rank / GPU / topology / observation scope
       |
       +--> 3. Diagnose: XLayer investigation
            symptom > baseline comparison > candidate > evidence / missing evidence
            |
            +--> Existing detail tools: Grafana / Loki / PyTorch Profiler / Nsight / NCCL
```

**Collect**는 연결한 source에서 신호를 수집하거나 조회하는 단계입니다.
VERL wrapper는 file logger bridge를 시작하며 native endpoint, node collector, log 경로와 ClickHouse는 각각 설정해야 합니다.
XLayer가 VERL의 모든 서브시스템을 자동으로 발견하거나 설치하는 것은 아닙니다.

**Correlate**는 같은 workload interval에 속하는 신호의 시간·identity·측정 범위를 맞추는 단계입니다.
계측된 event/span에는 `trace_id`와 parent span 관계를 보존하고, system·shared-service metric에는 node·device·topology와 observation scope를 유지합니다.
Step 경계의 정확도와 multi-node clock 상태도 확인하며, 시간상 동시 변화만으로 특정 run의 자원 사용량을 단정하지 않습니다.

**Diagnose**는 current/baseline 비교와 관측 근거에서 조사할 후보를 만드는 단계입니다.
기본 rule diagnosis와 선택적 local LLM diagnosis는 각각 검토 가능한 evidence와 missing evidence를 남깁니다.
Logs와 profile은 관련 상세 증거로 탐색하며, 모든 log·profile 내용이 자동으로 diagnosis engine에 입력되는 것은 아닙니다.

Grafana·Prometheus는 시계열 저장·query·시각화에 쓰지만 training step, rollout, weight sync, worker, rank, KV offload, storage path를 자동으로 같은 실행 단위로 해석하지 않습니다.
GPU utilization 40%, 3FS p99 15 ms, RDMA 250 Gbps가 같은 시간에 보여도 어느 run의 어느 phase가 왜 느려졌는지는 사용자가 별도로 조사해야 합니다.
XLayer는 이 수치를 workload interval과 측정 scope에 연결해 조사 가설을 만들고, 공유 자원 사용량을 특정 run에 자동 귀속하지 않습니다.

OpenTelemetry의 metric·trace·log·event·profile 및 resource 개념은 interoperability의 기반입니다.
XLayer의 기존 `trace_id`·`span_id`는 이를 고려해 유지하지만 OpenTelemetry 규격만으로 storage path가 병목이라는 판단이 자동으로 생기지는 않습니다.
DeepFlow의 eBPF·network/service path visibility는 환경에 있을 때 소비할 수 있는 유용한 signal source이며, XLayer가 그 수집 stack을 다시 만들지는 않습니다.
Nsight Systems·PyTorch Profiler 같은 전문 profiler는 저수준 상세 분석 도구이므로 XLayer는 상시 저비용 관측에서 의심 구간을 고르고 필요한 때 그 도구로 이동합니다.
DCGM 또는 기존 GPU sampler, Node Exporter, Loki도 기존 역할 그대로 사용합니다.

이 분리는 코드에도 반영됩니다.
`diagnosis_analysis.py`는 framework 이름을 모르는 측정값·scope·baseline·participant를 입력으로 받아 rule을 평가하고, `diagnostics.py`는 VERL step 이력과 Prometheus·3FS source를 연결합니다.
[공통 Prometheus client](../xlayer_telemetry/prometheus.py)는 rule diagnosis·LLM input·clock check·recorded step 조회가 같은 방식으로 series label과 유효 sample을 읽도록 합니다.
기존 `diagnostics.PrometheusClient` import는 호환되며, backend 파싱은 diagnosis rule과 분리합니다.
Sandbox runtime은 별도 구현하지 않고 `SandboxRecorder`가 외부 runtime의 lifecycle을 기존 EventRecorder span으로 남깁니다.
`sandbox_sampler.py`는 sandbox worker의 안정적인 cgroup v2 subtree를 읽어 Node Exporter textfile에 기록하며, local NVMe의 node/device 지표와는 scope가 다릅니다.
Grafana용 projection은 완전한 JSON 진단 결과에서 파생되고 Loki를 사용하지 않는 실행에서도 원본 결과를 읽을 수 있습니다.
구체적인 schema와 rule, 조사 순서는 [Cross-Layer Diagnosis](diagnosis.md)에 있습니다.
현재 integration과 사용 가이드는 VERL을 중심으로 제공하지만 SDK와 core의 run·phase·resource·evidence 모델은 다른 framework의 adapter에도 사용할 수 있습니다.

### When to Revisit eBPF

현재 XLayer의 기본 수집·진단 경로에는 eBPF가 필요하지 않습니다.
Node Exporter의 network·disk 값은 node 또는 device 전체이므로 process별 사용량이 필요할 수 있지만, PID별 CPU·memory·disk I/O 합계는 먼저 [OpenTelemetry Host Metrics의 process scraper](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/receiver/hostmetricsreceiver/README.md) 같은 기존 source로 확인할 수 있습니다.
반면 [RDMA userspace fast path](https://docs.kernel.org/infiniband/user_verbs.html)와 [3FS USRBIO](https://github.com/deepseek-ai/3FS/blob/main/src/lib/api/UsrbIo.md)는 일반 socket·read/write probe만으로 run별 I/O를 자동 귀속할 수 없습니다.
VERL의 run·step·phase 의미도 application 계측에서 계속 받아야 합니다.

실제 느린 run에서 process별 file I/O 지연, network flow 또는 scheduler wait가 반복해서 진단의 missing evidence로 남을 때만 eBPF를 재검토합니다.
그때는 한 사례에 기존 profiler나 eBPF 도구를 짧게 적용해 evidence가 채워지는지와 학습 성능 영향을 확인합니다.
DeepFlow는 지속적인 flow·service 조사에, Beyla는 tool·reward·inference RPC 조사에 필요한 경우 optional source로 평가합니다.
외부 PID를 run·worker와 연결하지 못하면 관측 scope를 process 또는 node로 유지하고 특정 run의 사용량으로 표시하지 않습니다.
Sandbox filesystem의 syscall·OverlayFS copy-up 원인이 계속 누락될 때에도 profiler를 해당 구간에만 선택적으로 붙입니다.

## The Basic Path

Node collector와 monitoring server는 별도 process입니다.
Wrapper는 VERL과 bridge를 함께 시작하고, node collector는 같은 node의 snapshot을 Prometheus 형식으로 노출하며, monitoring server는 exporter를 조회합니다.

```text
GPU node                                                      Monitoring host
VERL > file logger > bridge > snapshot > application.prom --+
GPU > GPU sampler > gpu.prom --------------------------------+--> Node Exporter :19100 --> Prometheus --> Grafana
CPU / memory / network / disk -------------------------------+
```

Bridge는 VERL의 `logs/verl-metrics.jsonl`에서 완료된 step record를 읽고 알려진 scalar를 공통 metric 이름으로 변환합니다.
예를 들어 `timing_s/gen`은 `rl_stage_duration_seconds{phase="rollout"}`으로, `perf/time_per_step`은 `training_step_time_seconds`로 옮깁니다.
Bridge는 새 snapshot으로 이전 snapshot을 교체하고, 별도로 `telemetry-events/verl-steps.jsonl`에 완료 step event를 누적합니다.
Node collector의 변환 process는 기본 2초마다 snapshot을 읽어 `application.prom`을 갱신합니다.
GPU sampler가 만드는 `gpu.prom`과 host 지표도 Node Exporter의 `:19100/metrics`에서 함께 노출되며, Prometheus는 기본 2초마다 이를 조회합니다.
Grafana는 Prometheus에 저장된 시계열을 query하므로 JSON 파일을 직접 읽지 않습니다.

Application snapshot과 topology collector는 손상된 파일·sample을 건너뛰고 정상 producer의 지표를 계속 내보냅니다.
JSONL 조회도 malformed JSON, UTF-8 오류와 object가 아닌 행을 건너뜁니다.
누락된 값을 0으로 채우지는 않으므로 source와 freshness를 함께 확인합니다.
Snapshot 교체에는 쓰기마다 별도의 임시 파일을 사용해 같은 process의 동시 export도 파일을 충돌 없이 교체합니다.

## Follow One Completed Step

예를 들어 VERL file logger가 step 7의 `timing_s/gen=2.4`와 `perf/time_per_step=8.0`을 기록했다고 가정합니다.
아래 숫자는 변환 경로를 설명하기 위한 예시이며 실제 학습 측정값이 아닙니다.

```text
VERL record: step=7, timing_s/gen=2.4, perf/time_per_step=8.0
  |
  +> bridge
       |
       +> latest snapshot: step=7, rollout=2.4s, step_time=8.0s
       |      |
       |      +> application.prom > Node Exporter > Prometheus
       |                                    |
       |                                    +> Grafana Agent RL panel
       |
       +> verl-steps.jsonl > Alloy > Loki > Grafana Step Explorer
```

Bridge는 `timing_s/gen`을 `rl_stage_duration_seconds`의 `phase="rollout"` 값으로 바꾸고, `perf/time_per_step`을 `training_step_time_seconds`로 바꿉니다.
Node collector가 새 snapshot을 읽은 뒤 Prometheus가 scrape해야 Grafana의 metric panel이 바뀝니다.
다음 step이 완료되기 전까지 panel이 step 7의 마지막 값을 유지하는 것은 이 구조에서 정상입니다.
Step event는 별도로 JSONL에 쌓이며, Alloy·Loki를 켠 경우에만 Run Overview에 통합된 Step Explorer 목록에 나타납니다.
VERL logger가 보고한 stage 소요 시간은 실제 stage의 시작·종료 timestamp가 아니므로 Step Explorer의 시간 구간은 추정치입니다.

## What Each File Is For

`RUN_ROOT`는 한 VERL 실행의 증거이고 `OUTPUT_DIR`은 collector 한 instance의 상태입니다.
두 경로가 달라도 되지만 node collector의 `TELEMETRY_METRICS_DIR`은 wrapper의 `$RUN_ROOT/telemetry-metrics`를 가리켜야 합니다.
`verl_local.sh node`는 기본적으로 `RUN_ROOT`의 부모 아래 `*/telemetry-metrics`를 찾아 run이 바뀌어도 collector를 유지합니다.
직접 시작하는 collector는 기존 `TELEMETRY_METRICS_DIR` 또는 새 `TELEMETRY_RUNS_ROOT`를 선택할 수 있습니다.
여러 run을 발견할 때는 기본 300초 freshness와 `telemetry-health.json`의 종료 상태를 확인하며, 원본 파일은 삭제하지 않습니다.

```text
$RUN_ROOT/                              $OUTPUT_DIR/
  telemetry-manifest.json                 textfile/
  logs/                                     application.prom
    verl-metrics.jsonl                     gpu.prom
    telemetry-bridge.log                 gpu-*.jsonl
  telemetry-metrics/                     node-exporter.log
    verl-trainer-driver@NODE@RUN.json      alloy-data/         (logs enabled)
  telemetry-events/
    verl-steps.jsonl
  telemetry-health.json                  (workload outcome + telemetry status)
  diagnostics/
    latest.json
    diagnostics.jsonl
    investigation/*.jsonl          (Loki projection, diagnostics enabled)
```

JSON snapshot은 최신 상태를 빠르게 노출하기 위한 것이므로 모든 step의 이력이 아닙니다.
원본 step scalar 이력은 VERL file logger에, 관측된 완료 경계는 event JSONL에 남습니다.
Prometheus에는 scrape에 잡힌 시계열만 남고 기본 보존 기간은 1일입니다.
Run artifact와 collector state는 별도 로컬 파일이므로 두 경로의 용량과 보존 기간을 각각 관리합니다.

## Add One Source at a Time

기본 경로가 정상인 뒤 추가 source를 연결합니다.
Source마다 저장소와 화면이 달라서 endpoint를 하나 등록하는 것만으로 모든 패널이 채워지지 않습니다.

| Source | 수집 경로 | 주로 확인할 곳 |
| --- | --- | --- |
| VERL 완료 step | File logger > bridge > snapshot > Node Exporter > Prometheus | Run Overview, Agent RL |
| GPU·CPU·memory·network·disk | GPU sampler·Node Exporter > Prometheus | Run Overview, Compute, Data & Storage |
| vLLM·Ray native metrics | 각 `/metrics` endpoint > Prometheus `native` job | vLLM: Agent RL, Ray: Prometheus query 화면 또는 Grafana Explore·진단 |
| Workload log·step event | File > Alloy > Loki | Run Logs, Grafana Step Explorer |
| 3FS FUSE mount·SSD | Node Exporter·선택적 SMART exporter > Prometheus | Data & Storage |
| 3FS service latency | ClickHouse > 선택적 diagnostics process | `diagnostics/latest.json`, `show_run` |
| Custom tool span | Application SDK > JSONL event > 선택적 Alloy/Loki | `show_run`, 원본 event, Cross-Layer Timeline |
| 진단 후보·timeline | Diagnostics JSON > Alloy > Loki, EventRecorder JSONL > Alloy > Loki | Bottleneck Summary, Cross-Layer Timeline; Loki 없이 JSON·`show_run` |

3FS의 POSIX/FUSE mount 용량과 node disk I/O는 host 관측치입니다.
3FS service latency는 ClickHouse 진단 경로이고, Grafana의 Data & Storage 패널에 자동 표시되지 않습니다.
vLLM KV offload rate도 vLLM endpoint의 지표이며 filesystem tier나 특정 3FS write량을 직접 뜻하지 않습니다.
각 source를 켜는 명령과 전제 조건은 [확장 가이드](agent-rl.md#choose-the-next-source)와 [Monitoring Guide](monitoring.md#add-run-logs-with-loki)에 있습니다.

## Match Identity and Time

Application snapshot에는 `run_id`·worker·node가 들어갑니다.
Prometheus의 node target 이름은 server 설정의 `TELEMETRY_TARGETS` 왼쪽 값이고, native source의 node는 `labels.node`, Loki의 node는 collector의 `NODE_NAME`입니다.
같은 machine을 가리키는 값은 동일한 논리 이름으로 맞춥니다.
Log의 `Run`은 디렉터리 이름에서 추출하므로 telemetry `run_id`와 다를 수 있습니다.

System metric과 shared vLLM·Ray·3FS service metric에는 특정 VERL `run_id`가 자동으로 붙지 않습니다.
같은 시간대와 node·role·device를 선택해 비교하고, 여러 node에서는 clock을 동기화합니다.
VERL file logger에는 원본 step 시작·종료 timestamp가 없어서 Step Explorer는 bridge가 관측한 완료 시각에서 보고된 step 시간을 빼 분석 구간을 추정합니다.
Bridge 시작 전에 존재하던 기록이나 종료 후 재생한 기록은 원래 실행 시각을 복원할 수 없으므로 시간 구간을 `unknown`으로 남기고 외부 resource metric과 연결하지 않습니다.
`ingested_at`은 파일을 읽은 시각이며 `source_event_time`을 대신하지 않습니다.
Async mode에서는 이 구간이 trainer update를 나타내며, 동시에 실행된 rollout이나 storage I/O가 해당 update에 속한다고 보장하지 않습니다.
구간 해석은 [Step Explorer](dashboards.md#read-a-step)에 자세히 설명합니다.

### Multi-node Correlation Boundary

GPU 번호 `0`과 worker 번호 `0`은 node마다 반복됩니다.
Resource identity는 cluster·node·device, application identity는 run·node·producer·role·worker를 함께 사용하며, snapshot/event 파일명에도 node와 run을 포함합니다.
같은 directory에 여러 node의 snapshot을 저장해도 node collector는 자신의 `NODE_NAME`과 일치하는 snapshot만 노출합니다.
기존 파일은 계속 읽을 수 있지만 SDK upgrade 이후 고정 파일명을 읽는 외부 script는 JSON의 identity나 `*.json` 목록을 사용해야 합니다.
Log directory는 각 node에서 실제로 생성한 파일만 Alloy가 읽도록 분리합니다.
Shared directory 전체를 여러 Alloy가 동시에 tail하면 중복 수집이나 잘못된 collector node label이 생길 수 있습니다.

Prometheus scrape timestamp와 application/span의 node wall clock은 서로 다른 시간 source입니다.
`EventRecorder`의 duration은 monotonic clock으로 계산하고 wall clock jump가 감지된 span은 `clock_discontinuity`로 표시합니다.
이는 duration을 보호하지만 서로 다른 node의 절대 timestamp를 맞추지는 않습니다.
Trace ID와 parent span ID는 clock 차이가 있어도 관계를 보존하며, resource window와의 시간 overlap은 별도 clock 검증이 필요합니다.

Cluster를 지정한 diagnosis는 기존 Node Exporter의 `node_time_seconds`와 `node_timex_sync_status`로 current/baseline의 clock 상태를 검사합니다.
차이가 크거나 source가 없거나 오래됐거나 kernel이 unsynchronized를 보고하면 raw evidence는 남기고 cross-layer candidate와 resource delta를 보류합니다.
Scrape-relative offset에는 network/collection delay가 포함되므로 NTP 측정이나 timestamp 자동 보정으로 사용하지 않습니다.
Clock 검사를 통과해도 polling 오차, 1분 rate window, shared-service attribution 제한은 남습니다.
실행 순서는 [Multi-node Monitoring](monitoring.md#monitor-gpu-and-storage-nodes-together)에 설명합니다.

## Design Principles

1. **Workload와 수집기를 분리합니다.** Wrapper는 기존 VERL 명령을 실행하고 file logger와 bridge를 붙이며 VERL source를 고치지 않습니다.
   수집기나 선택적 diagnostics 오류를 조사할 수 있도록 별도 log를 남기고 workload의 종료 코드를 보존합니다.
2. **신호의 생산자와 측정 범위를 유지합니다.** Trainer duration은 application 값이고 GPU·disk는 node 값이며 3FS latency는 shared service 값입니다.
   같은 시각의 변화는 병목 후보이지 run별 정확한 사용량이나 인과관계가 아닙니다.
3. **작은 연결부터 검증합니다.** Synthetic 화면, 실제 node target, 첫 완료 VERL step, native endpoint, Loki·3FS 순서로 확인합니다.
   각 단계에서 `show_run`, exporter `/metrics`, Prometheus Targets, Grafana를 순서대로 점검할 수 있습니다.
4. **누락과 0을 구분합니다.** 연결하지 않은 source나 오래된 표본을 0으로 채우지 않습니다.
   Target 상태와 sample age를 먼저 확인하고, diagnostics의 `missing_sources`를 정상 판정으로 읽지 않습니다.
5. **상시 지표와 상세 증거를 나눕니다.** 저비용 metric은 계속 수집하고 step event·log·짧은 profiler trace로 의심 구간을 확인합니다.
   Request ID·prompt·trace ID처럼 계속 달라지는 값은 Prometheus label 대신 event·log·manifest에 둡니다.

새 metric의 단위·scope·label을 정할 때는 [Metrics Contract](metrics.md), custom application snapshot을 만들 때는 [Application Metrics Guide](application-metrics.md)를 따릅니다.
