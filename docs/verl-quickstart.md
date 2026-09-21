# VERL Telemetry Quick Start

이 문서는 VERL Python 코드를 수정하지 않고 기존 학습 명령에 integrated telemetry를 붙이는 가장 짧은 경로입니다.
처음 사용하는 사람은 이 순서대로 실행하고, multi-node·3FS·custom tool trace가 필요할 때만 [상세 가이드](agent-rl.md)로 이동하면 됩니다.

## What You Get

한 run을 시작하면 다음 정보가 공통 `run_id`와 시간축으로 연결됩니다.

- VERL의 rollout, reward, actor update, weight sync 완료 시간
- vLLM/Ray native metric을 등록한 경우 queue, KV cache, task 상태
- GPU, CPU, memory, NIC/RDMA, NVMe 상태
- 실제 role·node 배치와 metric endpoint를 기록한 run manifest
- Grafana의 stage-first 병목 분석 dashboard

기본 사용 경로에서 VERL Python source 수정은 없습니다.
Wrapper가 기존 VERL 명령에 `file` logger 설정만 추가하고 metric bridge를 sidecar process로 실행합니다.

## Try the Dashboard without VERL

VERL 환경을 준비하기 전에 dummy Agent run으로 전체 수집 경로와 dashboard를 먼저 확인할 수 있습니다.

```bash
TOOLS_DIR='<controller-local-tools>' \
OUTPUT_DIR='<controller-local-monitoring-state>' \
DEMO_LIVE=1 \
  bash scripts/run_telemetry.sh server
```

Grafana에서 `Agent RL Stage Correlation`을 열고 `cluster=demo-b300`, `node=gpu-node-0`, `run_id=verl-agent-demo`를 선택합니다.
6초마다 완료 step 지표가 갱신되며 tool latency와 policy lag는 scrape마다 움직입니다.
화면 예시와 각 값의 의미는 [Synthetic Live Demo](monitoring.md#synthetic-live-demo)에서 확인합니다.

## Before You Start

다음 항목이 필요합니다.

- 실행 가능한 VERL 환경과 기존 `python -m verl.trainer.main_ppo ...` 명령
- 이 저장소 checkout
- GPU node에서 쓸 node-local run directory
- Dashboard까지 사용할 경우 node exporter, Prometheus, Grafana를 둘 local tool directory

Telemetry Python package를 VERL 환경에 설치할 필요는 없습니다.
Wrapper는 이 checkout을 `PYTHONPATH`로 사용하고 workload는 사용자가 전달한 Python command로 그대로 실행합니다.

## 1. Validate This Checkout

저장소 루트에서 telemetry 자체 테스트를 먼저 확인합니다.

```bash
bash scripts/setup.sh
. .venv/bin/activate
python -m pytest -q
```

VERL은 별도 virtual environment를 사용해도 됩니다.
그 경우 wrapper를 호출할 때 `--` 뒤에 그 환경의 Python 경로를 전달합니다.

## 2. Prepare the Monitoring Processes

Dashboard가 이미 운영 중이면 이 단계를 건너뜁니다.
처음 한 host에서 확인할 때는 local tool directory를 만들고 node·server 도구를 준비합니다.

```bash
export TOOLS_DIR='/local/telemetry-tools'
bash scripts/install_telemetry_tools.sh
bash scripts/install_telemetry_tools.sh server
```

첫 번째 terminal에서 GPU·host collector를 시작합니다.
Wrapper와 동일한 `RUN_ROOT`를 사용해야 application metric도 함께 수집됩니다.

```bash
export RUN_ROOT='/local/runs/grpo-quickstart'
NODE_ADDR='127.0.0.1' \
NODE_NAME='gpu-local' \
OUTPUT_DIR='/local/monitoring/node' \
TELEMETRY_METRICS_DIR="$RUN_ROOT/telemetry-metrics" \
DURATION=3600 \
TOOLS_DIR="$TOOLS_DIR" \
  bash scripts/run_telemetry.sh node
```

두 번째 terminal에서 Prometheus와 Grafana를 시작합니다.
각 terminal에서 저장소 루트로 이동하고 `export TOOLS_DIR='/local/telemetry-tools'`를 실행합니다.
다른 terminal의 환경 변수는 자동으로 전달되지 않습니다.

```bash
CLUSTER_NAME='agent-rl-local' \
TELEMETRY_TARGETS='gpu-local=127.0.0.1' \
OUTPUT_DIR='/local/monitoring/server' \
TOOLS_DIR="$TOOLS_DIR" \
  bash scripts/run_telemetry.sh server
```

정상 시작되면 Grafana 주소는 `http://127.0.0.1:13000`입니다.
원격 node라면 [SSH port forwarding 절차](monitoring.md#5-open-the-dashboards)를 사용합니다.

이 첫 연결 경로는 single driver와 한 node를 기준으로 합니다.
Multi-node에서는 node마다 collector와 local metric directory를 두고 [Multi-node scope](agent-rl.md#multi-node-scope)에 따라 Ray worker 환경과 실제 role 배치를 기록합니다.

## 3. Wrap the Existing VERL Command

세 번째 terminal에서 평소 쓰던 VERL 명령 앞에 wrapper만 추가합니다.
먼저 `export RUN_ROOT='/local/runs/grpo-quickstart'`를 설정하고 VERL 환경을 활성화하거나 해당 Python의 절대 경로를 사용합니다.
Telemetry 테스트용 `.venv`에는 VERL이 설치되어 있지 않습니다.
기존 run의 로그를 덮어쓰지 않도록 실행마다 새로운 output directory를 사용합니다.
`--` 뒤의 model, data, batch, GPU 설정은 기존 명령을 그대로 사용합니다.

```bash
bash scripts/run_verl_with_telemetry.sh \
  --output "$RUN_ROOT" \
  --run-id 'grpo-quickstart' \
  --node 'gpu-local' \
  --set model_id=Qwen/Qwen2.5-0.5B-Instruct \
  --set algorithm=grpo \
  -- python -m verl.trainer.main_ppo \
    algorithm.adv_estimator=grpo \
    data.train_files=/path/to/train.parquet \
    data.val_files=/path/to/test.parquet \
    actor_rollout_ref.model.path=Qwen/Qwen2.5-0.5B-Instruct \
    trainer.n_gpus_per_node=1 \
    trainer.nnodes=1 \
    ...
```

Wrapper가 자동으로 추가하는 VERL override는 다음 세 개입니다.

```text
trainer.logger=["console","file"]
trainer.project_name=agent-rl
trainer.experiment_name=<run-id>
```

기존 명령에 `trainer.logger`가 있으면 `file`을 포함해야 합니다.
RL-Insight server가 있으면 `--rl-insight-url http://monitor.internal:18080`을 추가할 수 있으며 wrapper가 `rl_insight` logger도 활성화합니다.

실행 중 다음 artifact가 생성됩니다.

```text
<RUN_ROOT>/
  telemetry-manifest.json
  telemetry-metrics/
    verl-trainer-driver.json
  telemetry-events/
  logs/
    verl-metrics.jsonl
    telemetry-bridge.log
```

## 4. Read the Dashboard Correctly

Grafana에서 `Agent RL Stage Correlation` dashboard를 열고 `run_id=grpo-quickstart`를 선택합니다.

- GPU·host와 등록한 vLLM native metric은 scrape 주기마다 갱신됩니다.
- `Completed RL stage duration (step boundary)`, reward와 throughput은 VERL step이 끝날 때 갱신됩니다.
- 완료 지표는 다음 step이 끝나거나 run이 정리될 때까지 마지막 값을 유지하므로 `Worker sample age`와 함께 읽습니다.
- `Worker sample age`가 계속 증가하면 long-running step, stalled worker 또는 metric export 실패를 구분해 조사합니다.
- 진행 중 rollout phase는 `Live rollout engine signals`와 RL-Insight state timeline을 사용합니다.

File log가 append되고 있다는 이유만으로 모든 panel이 초 단위로 갱신되는 것은 아닙니다.
특히 actor update와 weight sync의 현재 phase는 VERL step 완료 전에는 file logger만으로 알 수 없습니다.
등록하지 않은 tool event나 native vLLM source의 panel은 `N/A`가 정상이며 0으로 해석하지 않습니다.

## First Bottleneck Check

먼저 느린 stage를 고른 뒤 같은 시간·node의 live resource signal을 비교합니다.

| 관찰 | 먼저 확인할 후보 |
| --- | --- |
| Rollout 완료 시간이 증가하고 request waiting이 증가 | replica 부족, 긴 response, scheduler queue |
| Rollout이 느리고 KV usage·preemption이 증가 | KV pressure, concurrency, cache configuration |
| Rollout이 느리지만 engine queue는 낮고 tool event가 김 | external tool 또는 environment wait |
| Actor update가 느리고 GPU utilization이 높음 | compute·memory-bound update |
| Actor update가 느리고 GPU utilization이 낮음 | input, host pressure, collective wait |
| Weight sync가 느리고 NIC/RDMA traffic·error가 변함 | transfer 또는 network path |
| 모든 stage가 느리고 worker sample age가 동시에 증가 | node contention, Ray scheduling, stalled process |
| 3FS latency와 NVMe queue가 같은 시간에 증가 | client→service→device storage path |

함께 변한 값은 원인 후보이며 인과관계의 증명은 아닙니다.
원인이 남으면 선택한 step·rank만 profile하고 run manifest의 동일한 model·batch·topology 조건으로 재현합니다.

## Add Native vLLM, Ray, or 3FS Metrics

고정 endpoint가 있으면 [native source 예시](../examples/verl/native-sources.json)를 복사해 실제 `host:port`로 바꿉니다.
Monitoring server 시작 시 `TELEMETRY_SOURCES_FILE`을 추가하면 source를 검증하고 `native` Prometheus job으로 수집합니다.

```bash
TELEMETRY_SOURCES_FILE='/path/to/native-sources.json' \
CLUSTER_NAME='agent-rl-local' \
TELEMETRY_TARGETS='gpu-local=127.0.0.1' \
OUTPUT_DIR='/local/monitoring/server' \
TOOLS_DIR="$TOOLS_DIR" \
  bash scripts/run_telemetry.sh server
```

VERL async rollout처럼 endpoint port가 동적으로 바뀌면 고정 파일 대신 VERL의 Prometheus registration 또는 RL-Insight를 사용합니다.
Source의 `labels.node`는 `TELEMETRY_TARGETS`에 사용한 logical node 이름과 같아야 dashboard에서 함께 필터링됩니다.

## Troubleshooting

### Dashboard에 stage가 보이지 않음

`logs/verl-metrics.jsonl`이 존재하고 각 줄에 `{"step":...,"data":...}`가 있는지 확인합니다.
`telemetry-bridge.log`에 오류가 없고 `telemetry-metrics/verl-trainer-driver.json`이 생성됐는지 확인합니다.
첫 stage 값은 첫 training step이 끝난 뒤 나타납니다.

### GPU는 보이지만 run filter가 비어 있음

Node collector의 `TELEMETRY_METRICS_DIR`와 wrapper의 `--output/telemetry-metrics`가 같은 node-local 경로인지 확인합니다.
공유 NFS directory를 여러 node collector가 동시에 읽지 않습니다.

### VERL 명령이 logger 설정 오류로 종료됨

현재 VERL 버전이 `file` logger를 지원하는지 확인합니다.
사용자가 직접 `trainer.logger`를 지정했다면 `["console","file"]` 또는 `["console","file","rl_insight"]`처럼 `file`을 포함합니다.

### 한 step 동안 dashboard stage가 멈춰 보임

정상일 수 있습니다.
Step-complete panel 대신 GPU·host, native rollout panel, RL-Insight state timeline과 worker sample age를 확인합니다.

## Next Steps

Multi-node role 배치, Ray runtime environment, RL-Insight, 3FS ClickHouse, custom tool span과 focused profiling은 [Agent RL Telemetry 상세 가이드](agent-rl.md)를 따릅니다.
Metric 이름·단위·label을 확장할 때는 [Metrics Contract](metrics.md)를 먼저 확인합니다.
