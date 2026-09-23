# Cross-Layer Integration for VERL

[VERL 연결 가이드](verl-quickstart.md)에서 trainer와 GPU 지표를 확인했다면 필요한 계층을 하나씩 추가합니다.
이 문서는 vLLM·Ray endpoint, 여러 node의 배치 정보, 3FS 진단, custom tool event를 연결하는 방법을 설명합니다.
모든 기능을 켤 필요는 없으며 조사하려는 질문에 필요한 source부터 연결합니다.
기본 경로와 source별 저장 위치는 [구현 구조](architecture.md)에 있습니다.

## How the Signals Flow

VERL 실행에서는 trainer metric과 node 자원 지표를 Prometheus에서 시간 기준으로 비교합니다.
추가 source는 수집 방식에 따라 Grafana metric, log, run 진단 결과로 나뉩니다.

```text
Agent RL / VERL                          GPU / host node
+-----------------------------+          +-----------------------------------+
| VERL trainer               |          | GPU > GPU sampler > gpu.prom       |
| loss / reward / step time   |          | CPU / network / filesystem        |
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
System resource와 shared service metric에는 application의 `run_id`가 자동으로 붙지 않습니다.
처음에는 vLLM·Ray·3FS를 한꺼번에 등록하지 말고, endpoint 하나의 target이 up인지 확인한 뒤 다음 source를 추가합니다.
Ray는 수집 target과 diagnostics query가 준비되어 있지만 전용 Grafana panel은 제공하지 않으므로 Prometheus Explore에서 실제 metric 이름을 먼저 확인합니다.

## Register Native Endpoints

먼저 monitoring host에서 접근 가능한 Prometheus 형식의 endpoint를 준비합니다.
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
기존 server terminal을 종료한 뒤 같은 config로 다시 시작합니다.

```bash
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" server
```

Monitoring Guide의 수동 경로를 사용했다면 `server.conf`에 같은 값을 넣고 `bash scripts/run_telemetry.sh server --config "$HOME/telemetry/config/server.conf"`로 다시 시작합니다.
시작 시 설정 형식을 검증하고 Prometheus의 `native` job에 사용할 target 파일을 만듭니다.
설정 검증 성공이 endpoint 접속 성공을 의미하지는 않으므로 Prometheus Targets에서 상태를 확인합니다.
`target`은 scheme과 path 없는 `host:port`이며 HTTPS라면 `scheme`을 `https`로 지정합니다.
Source 파일을 고치면 생성된 target 파일이 저절로 바뀌지 않으므로 monitoring server를 다시 시작합니다.

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
이 process는 wrapper의 선택적 sidecar이며 Grafana dashboard를 추가하지 않습니다.
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

3FS latency 비교는 현재 창과 직전 동일 길이 창에서 관측한 `max_observed_p99`를 사용합니다.
전체 요청을 합친 global p99로 해석하지 않습니다.
이 결과를 Grafana의 내장 ClickHouse datasource로 보여 주는 기능은 제공하지 않습니다.

## Understand Asynchronous Runs

Wrapper는 `actor_rollout_ref.rollout.mode=async`와 fully async entrypoint를 감지하며 필요하면 `--execution-mode async`로 지정할 수 있습니다.
동기 실행은 `rl_step`, 비동기 실행은 `trainer_update` 완료 경계를 기록합니다.

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
from xlayer_telemetry.events import EventRecorder

events = EventRecorder.from_env(producer="agent", role="rollout")
if events is None:
    result = call_tool()
else:
    with events.span("tool.call", phase="tool_interaction", step=1):
        result = call_tool()
```

파일은 `<producer>-<role>-<worker>.jsonl`로 생성됩니다.
같은 directory의 worker는 서로 다른 ID를 사용해야 하며 기본 worker ID는 `RANK`, 없으면 `0`입니다.
Event는 그 자체로 Prometheus metric이나 Loki log가 되지 않으므로 JSONL에서 읽거나 별도 변환 경로를 구현합니다.

## Optional Integrations

이미 운영 중인 RL-Insight server가 있다면 wrapper에 `--rl-insight-url <URL>`을 추가할 수 있습니다.
Wrapper는 `rl_insight` logger를 추가하며 실제 logger 지원과 서비스 연결은 사용하는 VERL 배포에서 확인해야 합니다.
이 저장소가 RL-Insight server를 설치하지는 않습니다.

상세 실행 구간이 필요하면 [Run Analysis](dashboards.md#run-analysis)의 선택적 profiling을 사용합니다.
[CUDA smoke example](../examples/verl/gpu_smoke.py)은 telemetry 경로를 확인하는 작은 workload이며 실제 VERL 학습 성능을 측정하는 도구가 아닙니다.
