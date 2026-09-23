# VERL Quick Start

이미 실행 가능한 VERL 학습 명령에 GPU·host 관측과 trainer metric을 연결합니다.
첫 연결은 GPU node 한 대에서 진행하고, 성공한 다음 [여러 node와 외부 서비스 연결](agent-rl.md)로 확장합니다.
학습 환경이 아직 없다면 [synthetic demo](monitoring.md#try-the-demo)로 dashboard부터 확인할 수 있습니다.

## What You Will See

Wrapper는 VERL의 file logger를 읽는 bridge를 함께 실행합니다.
Bridge가 완료된 step의 stage 시간과 scalar를 JSON으로 기록하면 node collector가 이를 Prometheus에 노출하고 Grafana가 GPU·host 지표와 함께 보여 줍니다.

```text
VERL file logger > bridge > application snapshot > node collector
GPU / host -------------------------------------> node collector
                                                       |
                                                       |
                                    Prometheus > Grafana
```

처음부터 모든 panel이 채워지지는 않습니다.
vLLM queue, Ray 상태, tool event와 3FS 진단은 각각 source를 추가해야 사용할 수 있습니다.

## 1. Prepare the Environments

[README의 checkout 준비](../README.md#prepare-a-checkout)를 마친 뒤 저장소 루트에서 시작합니다.
GPU driver와 실행 가능한 VERL 환경은 별도로 준비되어 있어야 합니다.
Telemetry용 `.venv`에는 VERL이나 CUDA PyTorch가 설치되지 않습니다.

```bash
export TOOLS_DIR="$HOME/telemetry/tools"
bash scripts/install_telemetry_tools.sh
bash scripts/install_telemetry_tools.sh server
```

아래 세 terminal은 모두 같은 machine의 같은 checkout에서 실행합니다.
각 예제는 필요한 경로를 다시 지정하므로 다른 terminal의 환경 변수에 의존하지 않습니다.
실행마다 새로운 run 이름과 directory를 사용합니다.

## 2. Start the Node Collector

첫 번째 terminal에서 학습을 관측할 process를 실행합니다.
`RUN_ROOT`는 다음 단계의 wrapper와 같은 경로여야 합니다.

```bash
. .venv/bin/activate
export RUN_ROOT="$HOME/telemetry-runs/grpo-001"
TOOLS_DIR="$HOME/telemetry/tools" \
NODE_ADDR='127.0.0.1' \
NODE_NAME='gpu-local' \
OUTPUT_DIR="$HOME/telemetry/state/node" \
TELEMETRY_METRICS_DIR="$RUN_ROOT/telemetry-metrics" \
  bash scripts/run_telemetry.sh node
```

명령이 계속 실행되는 것이 정상입니다.
GPU sampler는 기본적으로 시간 제한 없이 동작하며, `Ctrl+C`로 collector를 종료합니다.
Run directory에 아직 application snapshot이 없어도 학습이 시작되면 읽을 수 있습니다.

## 3. Start Prometheus and Grafana

두 번째 terminal에서 실행합니다.
`TELEMETRY_TARGETS`의 왼쪽 이름을 앞 단계의 `NODE_NAME`과 맞춥니다.

```bash
. .venv/bin/activate
TOOLS_DIR="$HOME/telemetry/tools" \
CLUSTER_NAME='training-cluster' \
TELEMETRY_TARGETS='gpu-local=127.0.0.1' \
OUTPUT_DIR="$HOME/telemetry/state/server" \
  bash scripts/run_telemetry.sh server
```

같은 machine의 browser에서 `http://127.0.0.1:13000`을 엽니다.
원격 machine에서 실행했다면 [접속 안내](monitoring.md#open-the-dashboards)를 따릅니다.
앞서 demo를 실행했다면 port가 겹치므로 먼저 종료합니다.

## 4. Wrap Your VERL Command

세 번째 terminal에서 기존 VERL 실행 명령 앞에 wrapper를 붙입니다.
아래 `/path/to/verl-env/bin/python`은 VERL이 설치된 Python으로 바꾸고, `...`는 평소 사용하던 model·data·batch·GPU 설정 전체로 바꿉니다.
이 예제는 telemetry 연결 형태를 보여 주며 독립적인 학습 recipe는 아닙니다.

```bash
export RUN_ROOT="$HOME/telemetry-runs/grpo-001"
TELEMETRY_PYTHON="$PWD/.venv/bin/python" \
  bash scripts/run_verl_with_telemetry.sh \
    --output "$RUN_ROOT" \
    --run-id 'grpo-001' \
    --node 'gpu-local' \
    --set algorithm=grpo \
    -- /path/to/verl-env/bin/python -m verl.trainer.main_ppo \
      ...
```

`TELEMETRY_PYTHON`은 bridge 등 telemetry process에만 적용됩니다.
`--` 뒤의 workload는 지정한 Python으로 실행되므로 VERL 환경을 바꾸지 않습니다.

Wrapper는 기존 명령에 없는 경우 아래 logger 설정을 추가합니다.
직접 `trainer.logger`를 지정했다면 반드시 `file`을 포함합니다.

```text
trainer.logger=["console","file"]
trainer.project_name=agent-rl
trainer.experiment_name=<run-id>
```

Wrapper는 기존 manifest나 metric log가 있는 output directory의 재사용을 거부합니다.
새 실행에서는 `--run-id`와 `--output`을 바꾸고 node collector가 새 metric directory를 읽도록 재시작합니다.

## 5. Confirm the First Completed Step

Grafana에서 `Agent RL Stage Correlation`을 열고 `cluster=training-cluster`, `node=gpu-local`, `run_id=grpo-001`을 선택합니다.
GPU·host 값은 수집 주기에 따라 갱신되고 stage 시간·reward·throughput은 VERL step이 끝난 뒤 나타납니다.
긴 step 동안 완료 지표가 유지되는 것은 정상일 수 있으므로 `Worker sample age`를 함께 확인합니다.

Dashboard 없이도 최신 기록을 확인할 수 있습니다.

```bash
. .venv/bin/activate
PYTHONPATH=. python -m xlayer_telemetry.show_run \
  "$HOME/telemetry-runs/grpo-001"
```

기본 실행이 만드는 주요 파일은 다음과 같습니다.
`diagnostics/`는 [선택적인 자동 진단](agent-rl.md#add-diagnostics)을 켰을 때만 생성됩니다.

```text
grpo-001/
  telemetry-manifest.json
  telemetry-metrics/
    verl-trainer-driver.json
  telemetry-events/
    verl-steps.jsonl
  logs/
    verl-metrics.jsonl
    telemetry-bridge.log
```

Snapshot은 최신 상태 한 개이며 전체 step 이력이 아닙니다.
Stage별 원본 scalar는 `logs/verl-metrics.jsonl`, 완료 경계 event는 `telemetry-events/verl-steps.jsonl`에서 확인합니다.

## If Data Is Missing

| 증상 | 확인 순서 |
| --- | --- |
| GPU도 보이지 않음 | node collector log, `nvidia-smi`, Prometheus target 상태 |
| GPU는 보이지만 run이 없음 | wrapper와 collector의 metric 경로 일치, 첫 snapshot 생성 여부 |
| Stage 값이 없음 | 첫 step 완료 여부, `file` logger 지원, `telemetry-bridge.log` |
| 한 step 동안 stage 값이 유지됨 | sample age와 live GPU 지표 확인; file logger는 현재 phase를 실시간으로 알려 주지 않음 |
| vLLM·tool panel이 `N/A` | 해당 source 연결 여부; 미수집은 0과 다름 |
| Logger 설정 오류 | 사용 중인 VERL의 logger 지원과 직접 지정한 `trainer.logger` 확인 |

학습 종료 후에도 별도로 실행한 node·server terminal은 남아 있습니다.
관측을 마치면 각 terminal에서 `Ctrl+C`로 종료합니다.
다음 단계는 [vLLM·Ray·3FS 연결](agent-rl.md), [Loki log 수집](monitoring.md#add-run-logs-with-loki), [병목 분석](analysis.md)입니다.
