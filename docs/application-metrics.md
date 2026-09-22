# Application Metrics Guide

VERL 사용자는 먼저 [VERL Quick Start](verl-quickstart.md)의 file logger wrapper를 사용합니다.
이 문서는 VERL의 custom worker·tool 또는 다른 application에 loss·step·처리량 계측을 직접 추가할 때 사용하는 SDK 가이드입니다.
이 값에 run과 worker 문맥을 붙이면 같은 시간·node의 GPU·network·storage 지표와 비교할 수 있습니다.
Application은 local JSON snapshot을 쓰고 collector가 별도로 읽으므로 Prometheus와 직접 통신할 필요가 없습니다.

## Choose Your Path

| Application | 할 일 |
| --- | --- |
| Custom loop, Megatron integration | 아래 최소 예제를 확인한 뒤 `emit()`을 loop에 연결 |
| Hugging Face Trainer / TRL | [Callback 연결](#use-a-trainer-callback) |
| VERL | [VERL Quick Start](verl-quickstart.md)의 wrapper 사용 |
| vLLM·Ray 등 native metric service | [Endpoint 등록](agent-rl.md#register-native-endpoints); SDK 환경변수는 필요 없음 |

아래 절차는 SDK로 JSON snapshot을 만드는 application용입니다.
먼저 CPU에서 metric 하나를 기록하고 확인한 뒤 실제 학습에 연결합니다.

## 1. Prepare the Python Environment

Application이 실행되는 Python 환경에서 [package 설치](../README.md#python-package-usage)를 마칩니다.
다음 명령이 성공하면 SDK를 사용할 수 있습니다.

```bash
python -c "from xlayer_telemetry.metrics import MetricEmitter"
```

같은 terminal에서 실행 식별자와 출력 위치를 정합니다.
각 run에 새로운 directory를 사용하면 이전 snapshot과 섞이지 않습니다.

```bash
export TELEMETRY_RUN_ID='metrics-demo-001'
export TELEMETRY_METRICS_DIR="$PWD/artifacts/metrics-demo-001/telemetry-metrics"
export TELEMETRY_NODE='gpu-local'
```

`TELEMETRY_NODE`는 dashboard에서 사용할 logical node 이름입니다.
Monitoring server의 `TELEMETRY_TARGETS`에 `gpu-local=주소`로 등록하면 node 이름이 맞습니다.
생략하면 hostname을 사용합니다.

`TELEMETRY_RUN_ID`와 `TELEMETRY_METRICS_DIR`는 함께 설정해야 합니다.
둘 다 없으면 emitter가 비활성화되며, 하나만 있으면 경고 후 비활성화됩니다.
환경변수는 다른 terminal이나 remote worker로 자동 전달되지 않습니다.

## 2. Write and Inspect One Snapshot

다음 예제는 GPU나 training framework 없이 실행되며 예시 loss 한 개를 기록합니다.

```bash
python - <<'PY'
from xlayer_telemetry.metrics import Metric, MetricEmitter

emitter = MetricEmitter.from_env(producer="demo", role="trainer")
if emitter is not None:
    path = emitter.emit(step=1, samples=[Metric("training_loss", 1.25)])
    print(path)
PY

python -m xlayer_telemetry.show_run "$PWD/artifacts/metrics-demo-001"
```

출력에서 `demo/trainer`, `step 1`, `training_loss=1.25`를 찾습니다.
`telemetry-metrics/demo-trainer-0.json`에는 최신 snapshot이 저장됩니다.
별도 summary나 manifest가 없다는 안내가 나와도 이 예제에서는 정상입니다.

`show_run`에는 `telemetry-metrics` directory 자체가 아니라 그 부모 run directory를 전달합니다.
다음 `emit()`은 같은 worker의 snapshot을 교체하므로 전체 step history를 저장하지 않습니다.
Prometheus도 scrape 사이에 교체된 모든 snapshot을 보존하지는 않으며, 모든 step이 필요하면 application log를 함께 남깁니다.

## 3. Connect Your Workload

Worker는 metric을 생산하는 application process입니다.
`producer`는 application 이름, `role`은 trainer·rollout 같은 역할, `worker_id`는 같은 producer·role 내 process 식별자입니다.
Emitter는 process 시작 시 한 번 만들고 step이 끝날 때 `emit()`을 호출합니다.

```python
from xlayer_telemetry.metrics import Metric, MetricEmitter

emitter = MetricEmitter.from_env(producer="my-training-app", role="trainer")

# 기존 loop에서 global_step과 loss를 계산한 뒤 호출합니다.
if emitter is not None:
    emitter.emit(
        step=global_step,
        samples=[Metric("training_loss", float(loss))],
    )
```

`worker_id`를 생략하면 `RANK`를 사용하고, `RANK`도 없으면 `0`을 사용합니다.
같은 directory의 동일 producer·role을 여러 process가 기록한다면 서로 다른 worker ID를 지정합니다.
Run마다 directory를 분리하고, 각 node의 application과 collector는 같은 node-local directory를 사용합니다.

### Use a Trainer Callback

이미 생성한 Hugging Face `Trainer` 또는 TRL trainer에 callback을 추가할 수 있습니다.
아래 `trainer`는 자신의 코드에서 만든 객체이며 callback을 연결한 뒤 학습을 시작합니다.

```python
from xlayer_telemetry.adapters.hf_trainer import make_trainer_callback

callback = make_trainer_callback(producer="my-training-app")
if callback is not None:
    trainer.add_callback(callback)
trainer.train()
```

Main process가 loss를 log할 때 snapshot이 갱신됩니다.
Loss logging을 하지 않으면 callback만 등록해도 metric이 나오지 않습니다.

| Metric | 의미 |
| --- | --- |
| `training_loss` | Log에 포함된 loss |
| `training_step` | Snapshot의 global step을 collector가 metric으로 변환 |
| `training_step_time_seconds` | 직전 loss log 이후 경과 시간; 여러 step이 포함될 수 있음 |
| `training_tokens_per_second` | 누적 입력 token 증가량 / 경과 시간; token 수가 증가할 때만 기록 |

### Add Other Metrics

현재 값에는 기본 `gauge`를, 누적 횟수에는 `counter`를 사용합니다.
Counter는 emitter가 더해 주지 않으므로 application이 유지하는 누적값을 전달합니다.

```python
Metric("agent_tool_call_errors_total", tool_error_count,
       kind="counter", labels={"tool": "python"})
```

`run_id`, `producer`, `role`, `worker_id`에는 64자 이하 영문자·숫자·`.`·`_`·`-`를 사용합니다.
Request ID나 prompt처럼 값이 계속 늘어나는 정보는 label 대신 [event](agent-rl.md#record-a-custom-tool-span)에 기록합니다.
Metric 단위와 label 기준은 [Metrics Contract](metrics.md)에 있습니다.
Snapshot 기록 중 처리되는 파일·값 오류는 경고 후 emitter를 비활성화하며 application은 계속 실행됩니다.

## 4. Publish Through the Node Collector

[Monitoring Guide](monitoring.md#monitor-one-gpu-node)에 따라 node 도구와 monitoring server를 준비합니다.
SDK가 설치된 것만으로 Node Exporter가 설치되지는 않습니다.
이미 실행 중인 `node` role이 있으면 종료한 뒤 아래 옵션을 포함해 다시 실행합니다.

Application과 같은 node의 별도 terminal에서 실행하며, 경로를 앞에서 만든 실제 directory로 바꿉니다.

```bash
TOOLS_DIR='/path/to/installed-node-tools' \
NODE_ADDR='127.0.0.1' \
NODE_NAME='gpu-local' \
OUTPUT_DIR='/path/to/local-monitoring-state' \
TELEMETRY_METRICS_DIR='/path/to/artifacts/metrics-demo-001/telemetry-metrics' \
  bash scripts/run_telemetry.sh node
```

이 예제의 loopback 주소는 monitoring server가 같은 host에 있을 때 사용합니다.
분산 배치에서는 monitoring host가 접근할 수 있는 node 주소를 지정합니다.
`node` role에는 동작하는 `nvidia-smi`가 필요하므로 CPU에서 JSON 확인만 할 때는 앞의 2단계까지만 실행합니다.

Collector는 기본 2초마다 `OUTPUT_DIR/textfile/application.prom`을 갱신합니다.
`TELEMETRY_METRICS_INTERVAL`로 주기를 바꿀 수 있습니다.
Shared storage의 같은 snapshot을 여러 node collector가 읽으면 중복 시계열이 생길 수 있습니다.

## 5. Check the Dashboard

Run Overview에서 `cluster`, `node`, `run_id`를 선택합니다.
`producer`, `role`, `worker_id`는 metric label이며 필요하면 Grafana Explore에서 조회 조건으로 사용합니다.
처음 만든 예시 snapshot은 계속 갱신되지 않으므로 시간이 지나면 freshness filter에 의해 숨겨질 수 있습니다.

| 증상 | 확인할 위치 |
| --- | --- |
| JSON 없음 | 환경변수, `emit()` 호출, callback의 loss logging |
| JSON만 있고 metric 파일 없음 | Collector 경로와 `OUTPUT_DIR/textfile/application.prom` |
| Metric 파일은 있지만 dashboard가 비어 있음 | Prometheus target, cluster·node·run 선택, sample age |
| 같은 worker가 여러 instance에 표시됨 | Shared directory를 여러 collector가 읽는지 확인 |

Loss나 step time 변화가 보이면 같은 시간의 자원 지표를 [Run Analysis](analysis.md)에 따라 비교합니다.
SDK는 workload의 실행과 종료를 관리하지 않으며, 이 연결 방식은 특정 launcher나 다른 저장소를 요구하지 않습니다.
