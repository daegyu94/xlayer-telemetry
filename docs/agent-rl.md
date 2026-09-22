# Agent RL Telemetry with VERL

Agent RL 관측의 핵심은 지표 수를 늘리는 것이 아니라 `run → stage → worker/node/device → evidence`를 같은 시간축으로 연결하는 것입니다.
이 가이드는 VERL, Ray, vLLM, agent loop, reward, GPU·host, network, storage telemetry를 공통 `run_id`로 묶고 느린 단계부터 원인을 좁히는 방법을 설명합니다.

처음 연결하는 사용자는 먼저 [VERL Telemetry Quick Start](verl-quickstart.md)를 따라 wrapper 기반 경로를 확인합니다.
이 문서는 multi-node source, custom event와 focused profiling을 포함한 상세 운영 기준입니다.

## Architecture

각 데이터는 성격에 맞는 원본 저장소를 사용하고 run manifest가 endpoint와 artifact를 연결합니다.
Prometheus label에는 반복 가능한 낮은 cardinality 차원만 넣고 request, trajectory, session ID는 event·trace·log에 남깁니다.

| 계층 | 기본 source | 이 프로젝트의 역할 |
| --- | --- | --- |
| VERL trainer | `trainer.logger=file` 또는 RL-Insight | `timing_s/*`와 주요 scalar를 canonical metric으로 변환 |
| Rollout engine | vLLM/SGLang native `/metrics`, RL-Insight | 원본 metric endpoint 등록, run·replica topology 기록 |
| Agent/reward | RL-Insight state trace, `EventRecorder` | tool wait·error와 reward span을 JSONL evidence로 기록 |
| Ray orchestration | Ray metrics/dashboard | endpoint를 native source discovery에 등록 |
| GPU·host·NIC·NVMe | GPU sampler, Node Exporter | 기존 node collector로 수집 |
| 3FS | `monitor_collector`, client/server metric | endpoint 또는 ClickHouse 위치를 manifest에 기록 |
| Profile | VERL global profiler, PyTorch/Nsight | 선택한 step·rank의 artifact 경로를 manifest에 기록 |

VERL의 현재 공식 integration은 `rl_insight` logger를 통해 trainer scalar를 보내고 rollout engine, TransferQueue, agent-loop state trace를 RL-Insight에 등록합니다.
이 프로젝트의 bridge는 RL-Insight를 대체하지 않으며, 자체 Prometheus와 system metric을 함께 볼 때 필요한 작은 canonical subset만 복제합니다.

## Prepare One Run

모든 process와 collector에 같은 `TELEMETRY_RUN_ID`를 전달합니다.
Metrics와 event directory는 각 node의 local storage를 사용해야 하며, 여러 node가 같은 worker file을 읽지 않도록 분리합니다.

```bash
RUN_ROOT='/local/runs/grpo-001'
export TELEMETRY_RUN_ID='grpo-001'
export TELEMETRY_METRICS_DIR="$RUN_ROOT/telemetry-metrics"
export TELEMETRY_EVENTS_DIR="$RUN_ROOT/telemetry-events"
export VERL_FILE_LOGGER_PATH="$RUN_ROOT/logs/verl-metrics.jsonl"
export RL_INSIGHT_SERVER_URL='http://monitor.internal:18080'
```

실행 전에 실제 role 배치와 source·artifact 위치를 manifest로 기록합니다.
Model, dataset, batch와 topology처럼 비교 조건에 영향을 주는 값은 Prometheus label이 아니라 `--set`으로 남깁니다.

```bash
python -m post_training_telemetry.manifest \
  --output "$RUN_ROOT/telemetry-manifest.json" \
  --role trainer=trainer-0 \
  --role rollout=rollout-0 \
  --role rollout=rollout-1 \
  --source vllm=http://rollout-0.internal:8000/metrics \
  --source 3fs_clickhouse=http://storage-monitor.internal:8123 \
  --artifact profiles="$RUN_ROOT/profiles" \
  --set model_id=Qwen/Qwen2.5-0.5B-Instruct \
  --set algorithm=grpo
```

### Multi-node Scope

Wrapper가 설정한 환경 변수는 우선 VERL driver process에 적용됩니다.
기본 `file` logger와 bridge는 driver에서 동작하므로 trainer stage metric만 수집할 때는 remote worker가 이 저장소를 import할 필요가 없습니다.

Remote Ray worker나 custom agent에서 `EventRecorder` 또는 `MetricEmitter`를 사용할 때는 launcher의 Ray `runtime_env.env_vars`나 각 node의 service environment로 다음 값을 명시적으로 전달합니다.

- 모든 node에 동일한 `TELEMETRY_RUN_ID`
- node마다 실제 이름이 다른 `TELEMETRY_NODE`
- node-local 경로인 `TELEMETRY_METRICS_DIR`와 `TELEMETRY_EVENTS_DIR`
- 해당 worker가 import할 수 있는 package 또는 checkout 경로

여러 node가 shared storage의 같은 snapshot directory에 쓰게 하지 않습니다.
각 node collector가 자기 local directory만 읽게 하고, logical node 이름은 `TELEMETRY_TARGETS`의 이름과 native source의 `labels.node`에서 동일하게 사용합니다.
Wrapper의 자동 manifest는 single-driver 시작점이므로 multi-node에서는 위 `manifest` 명령으로 실제 role 배치를 추가 기록합니다.

## Enable VERL Native Telemetry

VERL 실행에는 console과 함께 `file`, 가능하면 `rl_insight` logger를 활성화합니다.
Rollout 통계와 TransferQueue를 사용하는 구성에서는 해당 native metric도 켭니다.

```bash
python -m verl.trainer.main_ppo \
  trainer.logger='["console","file","rl_insight"]' \
  trainer.project_name='agent-rl' \
  trainer.experiment_name="$TELEMETRY_RUN_ID" \
  actor_rollout_ref.rollout.disable_log_stats=False \
  transfer_queue.metrics.enabled=True \
  ...
```

RL-Insight를 사용하지 않는 환경에서도 `file` logger만 있으면 trainer 단계 metric을 bridge할 수 있습니다.
VERL이 log file을 만든 뒤 다음 process를 같은 node에서 실행합니다.

```bash
python -m post_training_telemetry.adapters.verl \
  --input "$VERL_FILE_LOGGER_PATH" \
  --metrics-dir "$TELEMETRY_METRICS_DIR" \
  --run-id "$TELEMETRY_RUN_ID" \
  --follow
```

Bridge는 다음 공식 VERL key를 canonical metric으로 변환합니다.

| VERL key | Canonical metric | Correlation |
| --- | --- | --- |
| `timing_s/gen` | `rl_stage_duration_seconds` | `phase=rollout` |
| `timing_s/reward` | `rl_stage_duration_seconds` | `phase=reward` |
| `timing_s/update_actor` | `rl_stage_duration_seconds` | `phase=actor_update` |
| `timing_s/update_weights` | `rl_stage_duration_seconds` | `phase=weight_sync` |
| `perf/throughput` | `training_tokens_per_second_per_gpu` | run·worker |
| `critic/score/mean` | `reward_score_mean` | run·step |
| `num_turns/mean` | `agent_turns_mean` | run·step |

변환하지 않은 VERL scalar도 RL-Insight나 VERL file log에 그대로 남습니다.
Metric 이름은 VERL 버전에 따라 바뀔 수 있으므로 upgrade 시 `tests/test_verl_adapter.py`와 실제 file log 한 줄을 함께 확인합니다.

### Update Latency and Current Phase

VERL `file` logger bridge는 log file을 tail하지만 원본 `logger.log(...)` 호출 자체가 training step 끝에 발생합니다.
따라서 file write 직후 수 초 안에 dashboard로 전달되더라도 한 step이 오래 걸리면 stage duration·reward·throughput은 그 step이 끝날 때까지 갱신되지 않습니다.

| Signal | 일반적인 갱신 기준 | 진행 중 상태를 보여 주는가? |
| --- | --- | --- |
| GPU·host·NIC·NVMe exporter | Prometheus scrape 주기, 기본 2초 | 예 |
| vLLM·Ray native metric | endpoint scrape 주기 | 예 |
| RL-Insight rollout·agent state trace | state 진입·종료 event와 backend ingestion | 예 |
| VERL `file` logger bridge | training step 완료 | 아니요 |
| Profile artifact | 선택한 profile 구간 완료 | 아니요 |

`Agent RL Stage Correlation` dashboard는 이 차이를 숨기지 않습니다.
`Completed RL stage duration (step boundary)`와 reward panel은 마지막 step-complete 값을 다음 update까지 유지합니다.
Worker sample age는 계속 증가하므로 유지된 값이 최신인지, long-running 또는 stalled step인지, 이미 끝난 run인지 함께 판단할 수 있습니다.
현재 rollout queue·KV pressure는 `Live rollout engine signals` panel이나 RL-Insight dashboard에서 확인합니다.

Actor update와 weight sync의 진행 중 phase를 별도 timeline에 표시하려면 VERL 내부 `marked_timer` 진입·종료 지점에서 직접 state event를 보내는 integration이 필요합니다.
외부 file log만 보고 현재 phase를 추측하면 잘못된 진단이 되므로 이 프로젝트는 완료 전 stage를 합성하지 않습니다.

## Instrument Agent and Tool Boundaries

Request나 trajectory ID는 Prometheus label로 넣지 않습니다.
`EventRecorder` span의 attribute와 `trace_id`를 사용하면 rollout, tool, reward evidence를 한 trajectory로 연결할 수 있습니다.

```python
from post_training_telemetry.events import EventRecorder

events = EventRecorder.from_env(
    producer="my-agent",
    role="agent",
    worker_id="0",
)

if events is not None:
    with events.span(
        "tool.call",
        phase="tool_interaction",
        step=global_step,
        trace_id=trajectory_id,
        attributes={
            "tool": tool_name,
            "request_id": request_id,
        },
    ):
        result = call_tool()
```

각 worker는 `TELEMETRY_EVENTS_DIR`에 producer·role·worker별 JSONL file을 append합니다.
실패한 span은 `status=error`와 exception type을 남기되 예외를 삼키지 않습니다.

## Register Native Metric Endpoints

vLLM, Ray, 3FS exporter처럼 Prometheus 형식의 endpoint가 이미 있으면 metric 이름을 바꾸지 않고 source discovery에 등록합니다.
[`examples/verl/native-sources.json`](../examples/verl/native-sources.json)을 배포 주소에 맞게 복사하고 `TELEMETRY_SOURCES_FILE`로 전달합니다.

```bash
TELEMETRY_SOURCES_FILE='/path/to/native-sources.json' \
CLUSTER_NAME='agent-rl-cluster' \
TELEMETRY_TARGETS='trainer-0=10.0.0.10,rollout-0=10.0.0.11' \
OUTPUT_DIR='/local/monitoring' \
TOOLS_DIR='/local/telemetry-tools' \
  bash scripts/run_telemetry.sh server
```

Source entry의 `target`은 scheme이나 path가 없는 `host:port` 형식입니다.
`labels.node`는 `TELEMETRY_TARGETS` 왼쪽의 logical node 이름과 정확히 맞춰야 dashboard의 node filter가 두 source를 함께 선택합니다.
`kind`, `component`, `role`, `node`, `replica` label은 고정 topology를 나타내며 request ID 같은 동적 값은 허용하지 않습니다.
VERL async rollout이 port를 동적으로 관리하면 VERL의 Prometheus 설정 또는 RL-Insight 등록을 우선하고, manifest에는 실제 endpoint discovery 위치를 기록합니다.

3FS `monitor_collector`가 ClickHouse에 기록하는 배포에서는 데이터를 Prometheus로 복제하지 않습니다.
Manifest의 ClickHouse endpoint, client/server role과 storage topology를 사용해 선택한 시간 창의 operation latency를 조회합니다.

Wrapper의 `--diagnostics-config`를 사용하면 별도 진단 process가 이 ClickHouse source를 read-only로 조회합니다.
비동기 실행에서는 3FS activity를 trainer step에 귀속하지 않고 현재 시간 창과 직전 동일 길이 창을 비교하며, 결과를 shared storage evidence로 기록합니다.
설정과 실행 예시는 [VERL Telemetry Quick Start](verl-quickstart.md#enable-automatic-bottleneck-diagnosis)를 따릅니다.

## Collect and Analyze

각 compute node에서는 기존 node role로 system metric과 application snapshot을 함께 수집합니다.

```bash
NODE_ADDR='10.0.0.10' \
NODE_NAME='trainer-0' \
TELEMETRY_METRICS_DIR="$TELEMETRY_METRICS_DIR" \
OUTPUT_DIR='/local/monitoring-node' \
  bash scripts/run_telemetry.sh node
```

Grafana의 `Agent RL Stage Correlation` dashboard에서 다음 순서로 확인합니다.

1. `run_id`와 느린 `phase`, worker를 선택합니다.
2. 같은 node·GPU의 utilization, HBM, CPU, NIC/RDMA, NVMe를 비교합니다.
3. Rollout이면 vLLM queue·TTFT·KV cache·preemption과 tool wait를 확인합니다.
4. Actor update이면 GPU/HBM, dataloader wait, collective, checkpoint I/O를 확인합니다.
5. Weight sync이면 전송 시간·bytes, replica ready와 policy version lag를 확인합니다.
6. 동시 변화는 원인 후보로만 취급하고 JSONL event, log 또는 선택한 step/rank profile로 검증합니다.

미수집 값은 0이 아니라 N/A입니다.
Host-wide resource metric은 다른 workload의 사용량을 포함할 수 있으므로 manifest의 role·rank·GPU mapping과 worker sample freshness를 먼저 확인합니다.

## Focused Profiling

상시 profiler는 overhead와 artifact 양을 키우므로 느린 stage를 찾은 뒤 선택한 step과 rank에만 켭니다.
VERL의 `global_profiler.steps`, role별 profiler 설정과 `finish_hook_cmd`를 사용하고 생성된 trace 경로를 manifest `artifacts`에 추가합니다.
비교 run은 model, prompt, batch, concurrency, cache state와 topology를 같게 유지합니다.

## Local Validation

CPU 단위 테스트는 외부 framework 없이 실행됩니다.

```bash
bash scripts/setup.sh
. .venv/bin/activate
python -m pytest -q
```

CUDA PyTorch가 설치된 host에서는 작은 REINFORCE policy로 모든 phase와 artifact 경로를 확인할 수 있습니다.
이 smoke test는 telemetry 동작 검증용이며 VERL 또는 LLM 성능 결과가 아닙니다.

```bash
PYTHONPATH=. python examples/verl/gpu_smoke.py \
  --output /tmp/agent-rl-telemetry-smoke \
  --steps 4 \
  --device cuda
```

결과는 `telemetry-metrics/`, `telemetry-events/`, `telemetry-manifest.json`에 기록됩니다.

## Upstream References

- [VERL RL-Insight integration](https://verl.readthedocs.io/en/latest/advance/rl_insight.html)
- [VERL rollout Prometheus and Grafana](https://verl.readthedocs.io/en/latest/advance/grafana_prometheus.html)
- [VERL PyTorch profiling](https://verl.readthedocs.io/en/latest/perf/torch_profiling.html)
- [VERL built-in tracking implementation](https://github.com/volcengine/verl/blob/main/verl/utils/tracking.py)
