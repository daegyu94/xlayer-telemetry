# Application Metrics Guide

VERL의 기본 trainer 연결은 [file logger wrapper](verl-quickstart.md)를 사용합니다.
이 가이드는 custom worker·tool·다른 application에 metric을 직접 추가하는 SDK 경로입니다.
Run·worker snapshot을 collector가 읽으므로 application이 Prometheus와 직접 통신하지 않습니다.
[데이터 경로](architecture.md#the-basic-path)에서 snapshot과 전체 step 이력의 차이를 확인합니다.

## Choose Your Path

| Application | 할 일 |
| --- | --- |
| Custom loop, Megatron integration | 아래 최소 예제를 확인한 뒤 `emit()`을 loop에 연결 |
| Hugging Face Trainer / TRL | [Callback 연결](#use-a-trainer-callback) |
| VERL | [VERL 연결 가이드](verl-quickstart.md)의 wrapper 사용 |
| vLLM·Ray 등 native metric service | [Endpoint 등록](agent-rl.md#register-native-endpoints); SDK 환경변수는 필요 없음 |

아래 절차는 SDK로 JSON snapshot을 만드는 application용입니다.
먼저 CPU에서 metric 하나를 기록하고 확인한 뒤 실제 학습에 연결합니다.
복사 없이 실행할 수 있는 [CPU SDK 예제](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/README.md#cpu-sdk-quickstart)는 metric과 `tool.call → sandbox.exec`의 trace/parent 연결을 함께 보여 줍니다.

## 1. Prepare the Python Environment

Application의 Python 환경에 checkout의 SDK를 설치합니다.
Source 개발 중에는 `-e`를 추가하며 script·dashboard는 checkout에서 실행합니다.

```bash
python -m pip install /path/to/xlayer-telemetry
python -c "from xlayer_telemetry.metrics import MetricEmitter"
```

각 run에 새로운 directory를 사용하면 이전 snapshot과 섞이지 않습니다.

```bash
export TELEMETRY_RUN_ID='metrics-demo-001'
export TELEMETRY_METRICS_DIR="$PWD/artifacts/metrics-demo-001/telemetry-metrics"
export TELEMETRY_NODE='gpu-local'
```

`TELEMETRY_NODE`는 dashboard에서 사용할 logical node 이름입니다.
Monitoring server의 `TELEMETRY_TARGETS`에 `gpu-local=주소`로 등록하면 node 이름이 맞습니다.
생략하면 hostname을 사용합니다.

`TELEMETRY_RUN_ID`와 `TELEMETRY_METRICS_DIR`를 함께 설정합니다.
없거나 하나만 있으면 emitter가 비활성화되며 application은 계속 실행됩니다.
Remote worker에는 별도로 전달하고, JSON이 없으면 두 변수와 `emit()` 호출부터 확인합니다.

## 2. Write and Inspect One Snapshot

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
예제에서는 `telemetry-metrics/demo-trainer-0@gpu-local@metrics-demo-001.json`에 최신 snapshot이 저장됩니다.
식별자에 `-`가 포함되면 파일명에서는 `%2D`로 인코딩되며, snapshot 안의 원래 식별자는 유지됩니다.
별도 summary나 manifest가 없다는 안내가 나와도 이 예제에서는 정상입니다.

`show_run`에는 snapshot directory의 부모 run 경로를 전달합니다.
`emit()`은 같은 worker의 최신 snapshot을 교체하므로 scrape 사이의 모든 step을 보존하지 않습니다.
전체 이력은 application log에, 호출 start/end는 [event span](agent-rl.md#record-a-custom-tool-span)에 기록합니다.

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

기본 worker ID는 `RANK`, 없으면 `0`이며 같은 producer·role의 process마다 다르게 지정합니다.
Run별 directory를 나누고 application·collector의 논리 node 이름을 맞춥니다.
Application snapshot은 해당 node의 collector가 읽는 directory에 기록합니다.
파일명의 `@NODE@RUN`은 node별 worker 번호 충돌을 막고 collector의 `--node`는 자기 node만 고릅니다.

기존 snapshot도 읽으며 identity가 중복되면 최신 `observed_at`을 선택합니다.
외부 script는 고정 basename 대신 반환 경로나 JSON identity를 사용합니다.

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

Collector는 application과 같은 node에서 실행합니다.

```bash
TOOLS_DIR="$HOME/telemetry/tools" \
NODE_ADDR='127.0.0.1' \
NODE_NAME='gpu-local' \
OUTPUT_DIR="$HOME/telemetry/state/node" \
TELEMETRY_METRICS_DIR="$PWD/artifacts/metrics-demo-001/telemetry-metrics" \
  bash scripts/run_telemetry.sh node
```

이 예제의 loopback 주소는 monitoring server가 같은 host에 있을 때 사용합니다.
분산 배치에서는 monitoring host가 접근할 수 있는 node 주소를 지정합니다.
GPU metric도 수집하려면 동작하는 `nvidia-smi`가 필요합니다.
CPU application이나 GPU 없는 sandbox/storage node에서는 같은 명령에 `ENABLE_GPU_METRICS=0`을 추가하여 host·application metric만 노출할 수 있습니다.

Collector는 기본 2초마다 `OUTPUT_DIR/textfile/application.prom`을 갱신합니다.
`TELEMETRY_METRICS_INTERVAL`로 주기를 바꿀 수 있습니다.
Launcher는 `NODE_NAME`에 해당하는 snapshot만 읽으므로 application의 `TELEMETRY_NODE`와 일치시킵니다.
같은 논리 node를 여러 exporter로 중복 등록하면 별도 중복 시계열이 생길 수 있습니다.
JSON 생성은 되지만 Grafana가 비어 있다면 JSON 경로, `application.prom`, Node Exporter target, dashboard filter 순서로 확인합니다.

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

Loss나 step time 변화가 보이면 같은 시간의 자원 지표를 [Run Analysis](dashboards.md#run-analysis)에 따라 비교합니다.
SDK는 workload의 실행과 종료를 관리하지 않으며, 이 연결 방식은 특정 launcher나 다른 저장소를 요구하지 않습니다.

## Optional Background I/O

기본 SDK는 동기 기록입니다.
Application 내부의 파일 쓰기 지연을 줄이려면 `from_env()`로 만드는 emitter와 recorder에 다음 설정을 적용합니다.
VERL file logger bridge는 이미 별도 process이므로 이 옵션의 주 사용처는 application/tool instrumentation입니다.

```bash
export TELEMETRY_ASYNC_IO=1
```

Constructor에서는 `async_io=True`를 사용할 수 있습니다.
Event의 timestamp·span duration·JSON serialization은 호출 시점에 처리하고 filesystem 쓰기만 background thread로 보냅니다.
Async `emit()`의 반환 경로는 queue 수락을 의미하므로 파일 생성 완료를 확인하려면 `flush()`를 호출합니다.

```python
from xlayer_telemetry.events import EventRecorder

recorder = EventRecorder.from_env(producer="agent", role="rollout")
if recorder is not None:
    try:
        with recorder.span("tool.call", phase="environment"):
            run_tool()
    finally:
        drained = recorder.close()  # 기본 최대 1초
        print("drained:", drained, "I/O:", recorder.io_status())
```

| 대상 | Queue 정책 |
| --- | --- |
| Event/span | FIFO; queue가 차거나 record가 byte 한도를 넘으면 새 record를 drop |
| Metric snapshot | 기록 중인 snapshot은 완료하고 대기 중인 값은 최신 값으로 coalesce |

기본 queue 한도는 256 record·4 MiB이며 in-flight record 하나는 별도입니다.
필요하면 `TELEMETRY_IO_QUEUE_CAPACITY`, `TELEMETRY_IO_QUEUE_BYTES`, `TELEMETRY_IO_FLUSH_TIMEOUT`을 바꿉니다.
Constructor의 대응 인자는 `queue_capacity`, `max_queue_bytes`, `flush_timeout`입니다.
Metric coalescing은 `dropped`에 포함되며 snapshot은 원래 모든 step의 이력을 보존하지 않습니다.

`flush()`는 대기 작업이 모두 처리됐는지를 반환합니다.
`io_status()`의 `written`, `dropped`, `write_errors`, `queued_bytes`, `flush_timeouts`로 실제 기록·누락을 확인합니다.
파일 오류는 해당 SDK writer를 비활성화하며 application exception으로 전달하지 않습니다.
`close()`는 새 수락을 중단하고 제한 시간에 남은 queue를 취소하지만, 이미 filesystem 안에서 대기 중인 write는 나중에 끝날 수 있습니다.
자동 shutdown flush는 전체 최대 1초의 best-effort이며 SIGKILL·process crash 시에는 실행되지 않으므로 정상 worker 종료 경계에서 직접 `close()`합니다.

Recorder/emitter는 worker 시작 시 만들어 재사용합니다.
Fork한 자식에서는 부모의 대기 기록과 writer lock을 재사용하지 않으며, 별도 worker identity의 SDK를 새로 만드는 것이 권장됩니다.
비동기 옵션은 filesystem 대기를 옮기지만 JSON serialization 비용이나 filesystem 자체의 부하는 줄이지 않습니다.
