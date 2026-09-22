# Application Metrics Guide

Application metric은 loss, step, throughput과 phase처럼 workload만 알 수 있는 상태를 기록합니다.
이 가이드는 application에서 metric snapshot을 만들고 node collector를 통해 Prometheus와 Grafana에 전달하는 가장 짧은 경로를 설명합니다.

Application은 local JSON file만 갱신합니다.
Prometheus 연결이 잠시 끊겨도 metric 기록 때문에 training이 중단되지 않으며, collector가 JSON을 읽어 system resource metric과 같은 dashboard에 표시합니다.

## Choose an Integration Path

먼저 application에 맞는 연결 방법을 선택합니다.

| Application | 연결 방법 | 시작할 절 |
| --- | --- | --- |
| Hugging Face `Trainer`, TRL `SFTTrainer` | callback 추가 | [Use the Hugging Face Callback](#use-the-hugging-face-callback) |
| 자체 training loop, Megatron integration | `MetricEmitter` 직접 호출 | [Instrument a Custom Loop](#instrument-a-custom-loop) |
| VERL | file logger wrapper 사용 | [Connect VERL](#connect-verl) |
| vLLM, Ray처럼 `/metrics`를 제공하는 service | native endpoint 등록 | [Use Native Exporters](#use-native-exporters) |

처음 연결할 때는 loss와 step처럼 값 하나부터 보내 collector와 dashboard 경로를 확인한 뒤 metric을 늘립니다.

## Prepare the Application

먼저 [Python package 사용법](../README.md#python-package-usage)에 따라 application에서 `post_training_telemetry`를 import할 수 있게 합니다.
Source checkout을 직접 사용하면 저장소 루트를 `PYTHONPATH`에 추가합니다.

```bash
export PYTHONPATH="/path/to/post-training-telemetry${PYTHONPATH:+:$PYTHONPATH}"
```

Package를 설치했다면 이 설정은 필요하지 않습니다.

각 실행에는 다음 두 환경변수를 함께 설정합니다.

```bash
export TELEMETRY_RUN_ID="training-001"
export TELEMETRY_METRICS_DIR="/path/to/output/telemetry-metrics"
```

| 환경변수 | 의미 |
| --- | --- |
| `TELEMETRY_RUN_ID` | dashboard와 실행 산출물을 연결하는 실행 식별자 |
| `TELEMETRY_METRICS_DIR` | worker별 최신 JSON snapshot을 저장하는 directory |
| `TELEMETRY_NODE` | 선택 사항인 logical node 이름이며 생략하면 hostname 사용 |

두 필수 환경변수가 모두 없으면 emitter는 비활성화되며 application은 그대로 실행됩니다.
둘 중 하나만 설정하면 경고를 출력하고 metric 기록을 비활성화합니다.

각 application과 해당 node collector는 같은 node-local metrics directory를 사용합니다.
여러 node가 shared storage의 같은 directory를 함께 읽으면 동일한 worker metric이 여러 Prometheus instance에 중복될 수 있습니다.

## Use the Hugging Face Callback

Hugging Face `Trainer` 또는 이를 사용하는 `SFTTrainer`에는 callback을 추가합니다.

```python
from post_training_telemetry.adapters.hf_trainer import make_trainer_callback

callback = make_trainer_callback(producer="my-training-app")
trainer_kwargs = {
    "model": model,
    "args": training_args,
    "train_dataset": train_dataset,
}
if callback is not None:
    trainer_kwargs["callbacks"] = [callback]

trainer = SFTTrainer(**trainer_kwargs)
```

Callback은 main process의 loss log에서 다음 metric을 기록합니다.

| Metric | 의미 |
| --- | --- |
| `training_loss` | Trainer가 마지막으로 log한 loss |
| `training_step` | snapshot을 기록한 global step |
| `training_step_time_seconds` | 이전 loss log 이후 지난 시간 |
| `training_tokens_per_second` | 같은 시간 동안 증가한 입력 token 수를 이용한 처리율 |

Logging interval에 여러 step이 포함되면 `training_step_time_seconds`도 여러 step의 경과 시간을 나타냅니다.
Trainer가 누적 입력 token을 보고하지 않거나 값이 증가하지 않으면 `training_tokens_per_second`는 기록하지 않습니다.

## Instrument a Custom Loop

Application 시작 시 emitter를 한 번 만들고 step이나 episode가 끝날 때 최신 snapshot을 기록합니다.

```python
from post_training_telemetry.metrics import Metric, MetricEmitter

emitter = MetricEmitter.from_env(
    producer="my-training-app",
    role="trainer",
    worker_id="0",
)

if emitter is not None:
    emitter.emit(
        step=global_step,
        samples=[
            Metric("training_loss", float(loss)),
            Metric("training_tokens_per_second", tokens_per_second),
            Metric(
                "agent_tool_call_errors_total",
                tool_error_count,
                kind="counter",
                labels={"tool": "python"},
            ),
        ],
    )
```

`kind`의 기본값은 `gauge`이며 계속 증가하는 누적값에는 `counter`를 사용합니다.
Metric 이름과 의미는 [Metrics Contract](metrics.md)를 따릅니다.
`request_id`, `episode_id`, prompt처럼 계속 새로운 값이 생기는 정보는 Prometheus label에 넣지 않고 event나 log에 기록합니다.

같은 metrics directory를 사용하는 worker에는 서로 다른 `worker_id`가 필요합니다.
`worker_id`를 생략하면 `RANK`가 있을 때 rank를 사용하고, 그렇지 않으면 `0`을 사용합니다.

| Application component | `producer` 예시 | 권장 `role` |
| --- | --- | --- |
| Policy training | application 이름 | `trainer` |
| Response generation | `vllm`, `sglang`, application 이름 | `rollout` |
| Reward evaluation | application 이름 | `reward` |
| Tool-using agent | application 이름 | `agent` |
| Scheduling | `ray`, application 이름 | `orchestrator` |

`run_id`, `producer`, `role`, `worker_id`에는 64자 이하의 영문자, 숫자, `.`, `_`, `-`만 사용합니다.
Snapshot 기록에 실패하면 emitter가 한 번 경고한 뒤 비활성화되며 application 실행은 계속됩니다.

## Connect VERL

VERL에는 Python callback을 추가하는 대신 기존 명령을 감싸는 wrapper를 사용할 수 있습니다.
Wrapper는 file logger output을 읽어 trainer metric으로 변환합니다.

처음 연결할 때는 [VERL Telemetry Quick Start](verl-quickstart.md)를 따릅니다.
Multi-node source, agent event와 3FS correlation은 [Agent RL Telemetry Guide](agent-rl.md)에서 확장합니다.

## Use Native Exporters

vLLM과 Ray처럼 자체 Prometheus endpoint를 제공하는 service의 metric은 `MetricEmitter`로 복제하지 않습니다.
Monitoring host의 `server` role에 `TELEMETRY_SOURCES_FILE`을 전달해 원래 metric 이름과 histogram을 그대로 수집합니다.

설정 형식과 실행 예시는 [Agent RL Telemetry Guide의 native endpoint 절](agent-rl.md#register-native-metric-endpoints)을 따릅니다.
Request latency histogram과 trace도 native exporter나 OpenTelemetry를 사용합니다.

## Start the Node Collector

Application이 JSON snapshot을 쓰기 시작하면 같은 compute node에서 collector를 실행합니다.
`TELEMETRY_METRICS_DIR`에는 application과 동일한 directory를 지정합니다.

```bash
cd /path/to/post-training-telemetry
NODE_ADDR='<node-management-address>' \
OUTPUT_DIR='<node-local-monitoring-state>' \
TELEMETRY_METRICS_DIR='/path/to/output/telemetry-metrics' \
  bash scripts/run_telemetry.sh node
```

Collector는 2초마다 worker JSON을 Node Exporter textfile 형식의 `application.prom`으로 변환합니다.
주기를 바꾸려면 `TELEMETRY_METRICS_INTERVAL`을 양의 초 단위 값으로 설정합니다.
`node` role은 종료 signal을 받을 때까지 실행되며 전체 monitoring server 구성은 [Distributed Run Monitoring](monitoring.md)을 따릅니다.

## Verify the Result

위 예시처럼 `telemetry-metrics`를 output directory 아래에 두었다면 collector 없이도 마지막 snapshot을 확인할 수 있습니다.

```bash
PYTHONPATH=. python -m post_training_telemetry.show_run /path/to/output
```

정상이라면 producer, role, worker와 마지막 metric이 다음과 같이 표시됩니다.

```text
[my-training-app/trainer worker 0] step 10: training_loss=1.25 training_tokens_per_second=420.0
```

Monitoring server를 시작한 뒤 Grafana의 Run Overview에서 `run_id`, `producer`, `role`, `worker_id`를 선택합니다.
Application metric과 같은 시간 범위·node의 GPU, host, network와 storage metric을 함께 확인합니다.

| 증상 | 확인할 항목 |
| --- | --- |
| JSON snapshot이 생성되지 않음 | 두 필수 환경변수, package import, callback 또는 `emit()` 호출 여부 |
| JSON은 있지만 `application.prom`이 없음 | application과 collector의 `TELEMETRY_METRICS_DIR`가 같은지 확인 |
| Prometheus에 metric이 없음 | node target 상태와 Node Exporter textfile directory 확인 |
| Dashboard에서 run이 보이지 않음 | `run_id` filter와 sample freshness 확인 |
| 같은 worker가 여러 node에 보임 | 여러 collector가 shared storage의 같은 metrics directory를 읽는지 확인 |

## Understand the Data Model

Application metric은 다음 경로로 전달됩니다.

1. Framework callback이나 custom loop가 `MetricEmitter`에 값을 전달합니다.
2. Emitter가 worker별 JSON snapshot을 atomic replace합니다.
3. Node collector가 snapshot을 Prometheus textfile metric으로 변환합니다.
4. Prometheus가 system resource metric과 함께 수집하고 Grafana가 같은 시간 범위에 표시합니다.

Snapshot은 worker별 최신값만 보존합니다.
전체 step history에는 Prometheus나 application log를 사용하고, request·trajectory별 상세 정보에는 event나 trace를 사용합니다.

`post_training_telemetry.metrics`는 framework를 import하지 않는 공용 SDK입니다.
`post_training_telemetry.adapters`는 framework별 연결 코드를 제공하며, workload 실행과 lifecycle은 application launcher가 관리합니다.
System resource metric과 application metric을 함께 해석하는 순서는 [Run Analysis](analysis.md)를 따릅니다.
