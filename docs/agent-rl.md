# Cross-Layer Integration for VERL

XLayer는 VERL의 서브시스템에서 생성하는 telemetry를 Collect하고, workload 문맥에서 Correlate한 뒤 Diagnose합니다.
[VERL 연결 가이드](verl-quickstart.md)에서 trainer와 GPU 지표를 확인했다면 이 문서의 절차로 필요한 계층을 하나씩 추가합니다.
이 문서는 vLLM·Ray endpoint, 여러 node의 배치 정보, 3FS 진단, custom tool event를 연결하는 방법을 설명합니다.
모든 기능을 켤 필요는 없으며 조사하려는 질문에 필요한 source부터 연결합니다.
기본 경로와 source별 저장 위치는 [구현 구조](architecture.md)에 있습니다.

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

3FS service latency > ClickHouse > Run diagnostics / show_run
Prometheus ----------------------> Run diagnostics / show_run
Tool call spans > Run event JSONL / show_run
Workload log files > Alloy > Loki > Grafana Run Logs
VERL step events > JSONL > Alloy > Loki > Grafana Step Explorer
```

Node collector는 trainer snapshot을 Node Exporter가 노출할 metric으로 변환하고, GPU sampler와 host 지표도 Node Exporter를 거쳐 Prometheus에 수집됩니다.
Prometheus는 vLLM·Ray endpoint도 직접 수집합니다.
3FS FUSE mount의 filesystem 지표는 node 자원 경로로 볼 수 있지만 3FS 서비스 latency는 ClickHouse를 조회하는 실행 진단에 기록됩니다.
Tool span은 JSONL event로 남고 workload log는 Alloy·Loki를 거쳐 Grafana Run Logs에 표시됩니다.
VERL step event는 별도로 Loki에 수집하면 Grafana Step Explorer의 목록과 상세 구간을 엽니다.

## Choose the Next Source

| 알고 싶은 내용 | 추가할 source | 결과를 볼 위치 |
| --- | --- | --- |
| Rollout queue·KV cache·KV offload 상태 | 배포한 vLLM의 Prometheus endpoint | Grafana의 native rollout·KV offload panel |
| Orchestration 상태 | 배포한 Ray의 Prometheus endpoint | Prometheus Explore, 선택적 diagnostics |
| 3FS 서비스 latency 변화 | 3FS가 기록한 ClickHouse distributions | 진단 JSON과 `show_run` |
| Storage node의 자원·SSD 상태 | Node Exporter와 SMART exporter | Resource·SSD dashboard |
| Workload log | Alloy와 Loki | 선택적인 Logs dashboard |
| Custom tool 호출의 대기 시간 | `EventRecorder`로 기록한 span | JSONL event와 `show_run` |

이 저장소는 VERL, vLLM, Ray, 3FS 자체를 설치하거나 endpoint를 자동으로 찾지 않습니다.
Native metric은 배포에서 제공하는 이름과 label에 따라 dashboard query를 맞춰야 할 수 있습니다.
자동 변환되는 VERL key와 별도 producer가 필요한 signal은 [실제 수집 범위](metrics.md#what-is-actually-collected)에 정리했습니다.
System resource와 shared service metric에는 application의 `run_id`가 자동으로 붙지 않습니다.
처음에는 vLLM·Ray·3FS를 한꺼번에 등록하지 말고, endpoint 하나의 target이 up인지 확인한 뒤 다음 source를 추가합니다.
Ray는 수집 target과 diagnostics query가 준비되어 있지만 전용 Grafana panel은 제공하지 않으므로 Prometheus Explore에서 실제 metric 이름을 먼저 확인합니다.

## Register Native Endpoints

먼저 monitoring host에서 접근 가능한 Prometheus 형식의 endpoint를 준비합니다.
VERL의 vLLM server에서 metric을 받으려면 VERL 명령에 `actor_rollout_ref.rollout.disable_log_stats=False`와 `actor_rollout_ref.rollout.prometheus.enable=True`를 함께 지정합니다.
vLLM wheel을 다시 빌드할 필요는 없습니다.
VERL은 기본적으로 `/tmp/ray/session_latest/metrics/prometheus/prometheus.yml`의 `rollout` job에 동적으로 정해진 `host:port`를 기록하므로, 실행 중 그 주소의 `/metrics`에서 `vllm:num_requests_waiting`과 `vllm:kv_cache_usage_perc`를 확인합니다.
VERL은 자신의 Prometheus 설정 파일 전체를 다시 쓰므로 `actor_rollout_ref.rollout.prometheus.file`에 XLayer server의 `prometheus.yml`을 지정하지 않습니다.
[예제 파일](../examples/verl/native-sources.json)을 복사한 뒤 실제 주소로 바꾸고 사용하지 않는 source는 제거합니다.
다음은 rollout node 한 개를 등록하는 최소 형태입니다.

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

이를 monitoring host의 `$HOME/telemetry/config/native-sources.json`으로 저장했다고 가정합니다.
주소는 실제 배포로 바꾸며 각 node collector는 먼저 실행되어 있어야 합니다.
단일 host [VERL config 경로](verl-quickstart.md#1-prepare-one-config-file)를 사용했다면 `verl-local.conf`에 `TELEMETRY_SOURCES_FILE="$HOME/telemetry/config/native-sources.json"`을 추가합니다.
기존 server process를 종료한 뒤 같은 config로 다시 시작합니다.
[VERL quickstart의 `up` 경로](verl-quickstart.md#2-start-server-node-and-verl)를 사용했다면 `down` 후 `up`을 실행합니다.

Monitoring Guide의 수동 경로를 사용했다면 `server.conf`에 같은 값을 넣고 `bash scripts/run_telemetry.sh server --config "$HOME/telemetry/config/server.conf"`로 다시 시작합니다.
시작 시 설정 형식을 검증하고 Prometheus의 `native` job에 사용할 target 파일을 만듭니다.
설정 검증 성공이 endpoint 접속 성공을 의미하지는 않으므로 Prometheus Targets에서 상태를 확인합니다.
`target`은 scheme과 path 없는 `host:port`이며 HTTPS라면 `scheme`을 `https`로 지정합니다.
Source 파일을 고치면 생성된 target 파일이 저절로 바뀌지 않으므로 monitoring server를 다시 시작합니다.
짧은 VERL 실행 중 server를 재시작하기 어렵다면, 이미 `TELEMETRY_SOURCES_FILE`로 `native` job을 켠 server에서 다음 명령으로 생성된 target 파일만 갱신합니다.

```bash
python -m xlayer_telemetry.source_discovery \
  --input "$HOME/telemetry/config/native-sources.json" \
  --output "$HOME/telemetry/state/server/native-targets.json"
```

Prometheus의 file discovery가 기본 30초 안에 새 target을 읽습니다.
Server의 `OUTPUT_DIR`를 기본값에서 바꿨다면 `--output`도 해당 directory의 `native-targets.json`으로 바꿉니다.
학습 종료 후 endpoint가 사라지면 Prometheus Targets의 현재 상태는 down으로 바뀌어도 과거 표본은 남습니다.

`kind`는 수집된 metric의 `telemetry_source`, `name`은 `component` label이 됩니다.
`labels.node`는 `TELEMETRY_TARGETS` 왼쪽 이름과 맞춰야 같은 node로 비교할 수 있습니다.
Native endpoint의 metric에는 VERL `run_id`가 자동으로 붙지 않으므로 Grafana에서 run을 선택해도 공유 vLLM·Ray 수치가 run별로 분리되지는 않습니다.
동적으로 endpoint가 바뀌는 배포는 배포 측의 discovery를 함께 관리해야 합니다.

3FS에도 Prometheus endpoint가 있다면 같은 방법으로 등록할 수 있습니다.
다만 endpoint 등록만으로 3FS 서비스 전용 dashboard가 생기지는 않습니다.
ClickHouse에 저장하는 3FS metric은 다음 진단 경로를 사용합니다.

## Map Multiple Nodes to a Run

Wrapper의 기본 manifest는 driver node에서 시작하는 단순한 배치를 기록합니다.
실제 trainer·rollout이 다른 node에 있다면 role 배치를 별도 manifest에 기록해 분석 시 참조합니다.
먼저 각 node에서 `node` role을 실행하고 monitoring server의 `TELEMETRY_TARGETS`에 모든 node를 등록합니다.
Native vLLM·Ray endpoint는 별도 source 파일에 등록하며, 역할 manifest는 이 두 설정을 대체하지 않습니다.

```text
trainer-0 > node exporter :19100 --------+
rollout-0 > node exporter :19100 --------+--> Prometheus > Grafana
rollout-0 > vLLM /metrics ---------------+
             | labels.node=rollout-0
driver run > topology manifest -----------> local Step Explorer
```

아래 명령은 기존 wrapper manifest를 보존하면서 topology 참고 파일을 만듭니다.

```bash
. .venv/bin/activate
PYTHONPATH=. python -m xlayer_telemetry.manifest \
  --output "$HOME/telemetry-runs/grpo-001/topology-manifest.json" \
  --run-id grpo-001 \
  --role trainer=trainer-0 \
  --role rollout=rollout-0 \
  --source vllm=http://10.0.0.11:8000/metrics \
  --artifact profiles="$HOME/telemetry-runs/grpo-001/profiles" \
  --set algorithm=grpo
```

Manifest는 실행 조건과 위치를 기록하는 파일입니다.
`--source`를 기록하는 것만으로 Prometheus 수집이 활성화되지는 않으며 `show_run`은 기본적으로 `telemetry-manifest.json`을 읽습니다.
기본 manifest의 role 정보를 갱신하려면 기존 settings·sources·artifacts를 보존하면서 실제 배치에 맞게 수정합니다.
Grafana Step Explorer의 `Resource node` 선택은 Prometheus에 등록된 node를 기반으로 하므로 manifest 파일만 만들어도 새로운 node의 그래프가 생기지 않습니다.

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

진단 process는 완료 step, Prometheus signal과 선택적인 3FS ClickHouse 조회를 조합해 병목 후보를 기록합니다.
이 process는 wrapper의 선택적 sidecar입니다.
`ENABLE_LOGS=1`과 Alloy/Loki도 연결하면 결과를 Bottleneck Summary와 Cross-Layer Timeline에서 볼 수 있습니다.
[diagnostics.json](../examples/verl/diagnostics.json)을 별도 파일로 복사하고 Prometheus 주소를 실제 주소로 수정합니다.
3FS를 사용하지 않으면 `threefs` object를 제거합니다.
단일 host에서는 `verl-local.conf`에 `DIAGNOSTICS_CONFIG="$HOME/telemetry/config/diagnostics.json"`을 추가하고 새 `RUN_ID`로 `run`을 실행합니다.

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
    --diagnostics-interval 10 \
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

다음 코드는 기존 tool 호출 위치에 넣는 형태입니다.
`call_tool()`은 application의 실제 함수로 바꿉니다.

```python
from pathlib import Path

from xlayer_telemetry.events import EventRecorder

events = EventRecorder.from_env(producer="agent", role="rollout")
if events is None:
    result = call_tool()
else:
    with events.span("tool.call", phase="tool_interaction", step=1):
        result = call_tool()
```

파일은 기본적으로 `<producer>-<role>-<worker>.jsonl`로 생성됩니다.
식별자 자체에 `-`가 있으면 파일명에서 해당 문자를 `%2D`로 인코딩해 서로 다른 producer tuple의 파일이 충돌하지 않도록 합니다.
같은 directory의 worker는 서로 다른 ID를 사용해야 하며 기본 worker ID는 `RANK`, 없으면 `0`입니다.
Event는 그 자체로 Prometheus metric이 되지 않습니다.
Loki를 켜고 Alloy가 해당 run root를 읽으면 `xlayer_event` stream으로 전달되어 Cross-Layer Timeline의 exact span에 나타납니다.

## Observe an Agent Sandbox

SWE-Bench·Terminal-Bench·code execution에서는 tool 호출 시간이 sandbox 준비, 명령 실행, filesystem 작업을 포함할 수 있습니다.
XLayer는 외부 Docker·containerd·SWE-ReX·custom runtime의 sandbox 생성과 배치를 맡지 않으며, 그 실행 지점에서 lifecycle span을 기록하고 sandbox worker cgroup을 읽습니다.
기본 예시는 OverlayFS 위의 local SSD/NVMe이지만 `filesystem` 값은 `btrfs`, `zfs`, plain workspace, VM filesystem 등 실제 배포에 맞춥니다.

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

Sandbox runtime이 호출하는 코드에 아래처럼 계측을 넣습니다.
`sandbox_id`·`trajectory_id`는 event attribute에만 저장되고 Prometheus label이 아닙니다.
`parent_span_id`와 `trace_id`를 기존 `tool.call` span에서 넘기면 tool과 sandbox lifecycle을 같은 trace에서 찾을 수 있습니다.
RPC로 dedicated node에 전달할 때도 두 ID를 sandbox worker에 전달합니다.

```python
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
        "exec", step=127, trajectory_id="trajectory-17", sandbox_id="sandbox-17",
        trace_id=tool_span.trace_id, parent_span_id=tool_span.span_id,
        attributes={"tool": "pytest"},
        cgroup=Path("/sys/fs/cgroup/your-sandbox-container"),
    ):
        run_command_in_existing_sandbox()
```

지원하는 operation은 `queue`, `acquire`, `prepare`, `exec`, `reset`, `release`이며 runtime에서 실제 실행한 단계만 기록합니다.
`queue`는 pool에 빈 sandbox가 생기기를 기다리는 구간, `acquire`는 선택된 sandbox를 할당받는 구간입니다.
Queue가 없는 runtime은 두 구간을 만들어 내지 않고 실제 `exec`만 기록해도 됩니다.
`tool_span`은 AgentLoop의 기존 `tool.call` span에서 받은 ID이며, dedicated 배치라면 RPC로 전달합니다.
`cgroup`은 선택 사항이며, 개별 sandbox cgroup을 전달하면 span 전후의 I/O bytes·operations 차이와 PSI를 같은 trace의 `sandbox.resource_sample` event에 기록합니다.
이 event의 `sandbox_id`·`trajectory_id`와 cgroup 범위 값은 Prometheus label이나 worker 전체 집계에 섞이지 않습니다.
Dedicated worker의 `TELEMETRY_RUN_ID`는 rollout과 같고 `TELEMETRY_NODE`는 실제 sandbox node여야 합니다.
서로 다른 node의 JSONL을 동일 run root의 `telemetry-events` 아래에 전달하거나 각 node의 Alloy가 읽는 run root를 설정해야 Grafana의 Timeline에서 함께 보입니다.

veRL `function_tool_path`로 연결하는 실제 예시는 [verl-lab SWE-Bench adapter](../examples/sandbox/verl_lab_swebench_tools.py)입니다.
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
출력의 `run/telemetry-events`에서 `tool.call`과 `sandbox.exec`의 trace를 확인하고, 두 번째 step부터 같은 tool의 duration baseline을 비교할 수 있습니다.

`test_patch`는 완성된 unified diff를 받습니다.
작은 모델이 `type`처럼 스키마에 없는 인자를 넣거나 유효하지 않은 diff 조각을 만들 수 있으므로, 이 예제는 기존 grader를 호출하는 `edit_and_test(path, old, new)`도 제공합니다.
기존 실패에서는 veRL이 모델의 `{"patch": ..., "type": "add"}`를 함수에 그대로 전달해 `test_patch(patch: str)` 진입 전에 `TypeError`가 났고, 전달된 `patch`도 unified diff가 아니었습니다.
스키마 밖의 인자는 `invalid_tool_arguments` 응답과 오류 `tool.call` span으로 남기며 Docker grader를 실행하지 않습니다.
`old`는 원본 소스에서 정확히 한 번 나타나야 하며, adapter가 만든 patch에만 Docker grader를 실행합니다.
실측용 prompt에서 `read_source` 다음에 `edit_and_test`를 호출하도록 안내할 수 있지만, 이것이 모델의 SWE-Bench 해결률을 증명하지는 않습니다.
이 예제는 colocated Docker grader를 대상으로 하며 patch가 검증 단계에 도달했을 때만 `sandbox.exec` span이 생깁니다.
`docker run --rm` 호출만 감싸므로 개별 container cgroup 경로를 자동 발견하거나 trajectory ID를 만들어 내지는 않습니다.
그 정보가 있는 runtime은 위의 `SandboxRecorder.span(cgroup=...)`을 직접 호출해 개별 I/O event를 추가합니다.
Docker CLI를 호출하는 veRL worker와 Docker daemon이 만드는 grader container가 같은 cgroup subtree에 들어간다는 보장은 없습니다.
따라서 worker PID의 cgroup만 sampler에 주면 grader의 I/O·CPU·memory를 수집한다고 보장할 수 없습니다.
Docker grader를 별도의 parent 아래에 배치하려면 smoke 실행 전에 `XLAYER_SANDBOX_CGROUP_PARENT`를 지정합니다.

```bash
export XLAYER_SANDBOX_CGROUP_PARENT=xlayer-sandbox-worker.slice
bash examples/sandbox/run_verl_lab_smoke.sh
python -m examples.sandbox.validate_smoke "$RUN_ROOT" --require-sandbox
```

이 옵션은 grader의 `docker run`에 `--cgroup-parent`를 전달합니다.
호스트에서 실제 container PID의 `/proc/<pid>/cgroup`을 확인해 지정한 parent 아래에 생성됐는지 검증해야 합니다.
`docker run --rm` grader는 매우 짧게 실행될 수 있으므로, container가 없을 때 parent가 유지되는지도 확인하고 sampler interval만으로 모든 grader 호출이 포착된다고 가정하지 않습니다.
실제 Docker container 2개 이상의 parent I/O 합산을 별도로 검사하려면 로컬에 grader image가 있는 호스트에서 `python -m examples.sandbox.validate_docker_cgroup`을 실행합니다.
이 검사는 임시 container를 만들고 종료하며, veRL smoke의 개별 trajectory 귀속까지 검증하지는 않습니다.

### Sample the sandbox worker cgroup

Sandbox worker와 하위 container가 속한 **안정적인 cgroup v2 subtree**를 입력으로 지정합니다.
`/proc/<worker-pid>/cgroup`의 `0::` 뒤 경로를 호스트의 `/sys/fs/cgroup` 아래에서 확인하되, Docker daemon이 만든 container가 그 경로의 자손인지도 확인합니다.
안정적으로 유지되는 sandbox parent를 Prometheus 대상으로 사용하고, runtime이 sandbox를 만들 때마다 새로 생성하는 개별 container cgroup은 사용하지 않습니다.
하나의 node·runtime·filesystem·deployment 조합에 textfile producer 하나를 두어 같은 시계열이 충돌하지 않게 합니다.
한 node에 둘 이상을 수집한다면 `--textfile-name`을 서로 다른 `.prom` basename으로 설정합니다.

```bash
python -m xlayer_telemetry.sandbox_sampler \
  --cgroup /sys/fs/cgroup/your-sandbox-worker \
  --textfile-dir "$HOME/telemetry/state/node/textfile" \
  --node sandbox-0 \
  --runtime containerd \
  --filesystem overlayfs \
  --deployment dedicated
```

Colocated라면 `--node`를 GPU/rollout node 이름으로, `--deployment`를 `colocated`로 바꾸고 그 node collector의 `textfile` directory를 지정합니다.
`io.stat`의 bytes·operations, `io.pressure`·`cpu.pressure`의 `some.total` 증가량, `cpu.stat`, `memory.current`·`memory.peak`·`memory.events`를 읽습니다.
파일 형식과 누적 counter의 의미는 [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html), PSI의 `some.total` 의미는 [Linux PSI](https://docs.kernel.org/accounting/psi.html)를 따릅니다.
PSI ratio는 직전 표본 이후 stall 시간 비율이므로 첫 표본에서는 비어 있으며, 읽을 수 없는 source도 0으로 위조하지 않고 생략합니다.
Page cache hit처럼 block device에 도달하지 않은 작업은 `io.stat` bytes로 보이지 않을 수 있습니다.
Counter는 worker cgroup이 유지되는 동안에만 단조 증가합니다.
`sandbox_oom_total`은 `memory.events`의 `oom`, `sandbox_oom_kill_total`은 실제 kill을 센 `oom_kill`입니다.
`sandbox_memory_peak_bytes`는 cgroup 생성 이후의 high-water mark이므로 선택한 step이나 Grafana 시간 범위의 peak로 해석하지 않습니다.
`sandbox_sample_timestamp_seconds`로 sampler의 마지막 갱신 시각을 확인하며, 오래된 textfile 표본은 현재 압력으로 해석하지 않습니다.

Sandbox I/O는 cgroup에 속한 여러 block device의 합계이고 local NVMe busy는 선택한 node/device 전체 범위입니다.
`sandbox.device`를 설정할 때는 container의 `io.stat`에 표시된 major:minor와 호스트의 backing device를 대조합니다.
대조하지 못했다면 두 수치가 같은 device를 나타낸다고 가정하지 않습니다.
3FS 서비스 latency나 공유 storage 지표와 local sandbox NVMe를 합산하지 않습니다.
특정 tool이 느리고 cgroup pressure와 NVMe busy가 동시에 증가해도 다른 sandbox·process의 부하가 섞일 수 있으므로 [진단 후보](diagnosis.md#baseline-and-rule-state)로만 해석합니다.
원인을 더 좁혀야 하면 해당 구간에서 VFS syscall, OverlayFS copy-up, fsync 등을 선택적으로 profile할 수 있으며 eBPF는 기본 의존성이 아닙니다.

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

`events_dir`는 선택 사항입니다.
지정하면 해당 run의 `agent-*.jsonl`에 기록된 정상 종료 `tool.call` span 중 분석 구간 안에 완전히 포함된 호출의 최대 duration을 사용하고, baseline에서도 같은 tool 이름만 비교합니다.
선택한 run의 agent event 파일을 분석할 때마다 읽으므로 장시간·대규모 실행에서는 기존 Prometheus tool duration 지표를 우선 사용합니다.
해당 span이 없거나 이 설정이 없으면 기존 `agent_tool_call_duration_seconds` Prometheus 지표를 사용합니다.
첫 step에는 같은 run의 이전 baseline이 없어 tool slowdown을 판단하지 않습니다.
VERL step window가 approximate이면 span 자체가 exact여도 step 귀속은 시간상 겹침에 근거한 correlation입니다.
`sandbox_io_pressure_ratio`는 sandbox node의 cgroup, local device busy는 같은 node의 지정한 device에서 조회합니다.
개별 sandbox와 trajectory의 높은 cardinality 문맥은 EventRecorder의 `trace_id`·`span_id`·attribute에서 확인합니다.

## Optional Integrations

이미 운영 중인 RL-Insight server가 있다면 wrapper에 `--rl-insight-url <URL>`을 추가할 수 있습니다.
Wrapper는 `rl_insight` logger를 추가하며 실제 logger 지원과 서비스 연결은 사용하는 VERL 배포에서 확인해야 합니다.
이 저장소가 RL-Insight server를 설치하지는 않습니다.

상세 실행 구간이 필요하면 [Run Analysis](dashboards.md#run-analysis)의 선택적 profiling을 사용합니다.
[CUDA smoke example](../examples/verl/gpu_smoke.py)은 telemetry 경로를 확인하는 작은 workload이며 실제 VERL 학습 성능을 측정하는 도구가 아닙니다.
