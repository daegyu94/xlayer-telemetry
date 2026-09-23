# Connect an Existing VERL Run

이 가이드는 이미 실행 가능한 VERL 학습 명령과 NVIDIA GPU가 있는 사용자를 위한 단일 host 연결 절차입니다.
아직 VERL 명령이 없다면 [synthetic demo](monitoring.md#try-the-demo)로 화면과 수집 경로부터 확인합니다.
Process와 파일의 역할은 [구현 구조와 설계 원칙](architecture.md)에, 여러 node·vLLM·Ray·3FS 확장은 [Cross-Layer Integration](agent-rl.md)에 있습니다.

## What This Adds

VERL wrapper는 기존 명령에 file logger를 붙이고 bridge를 함께 실행합니다.
Bridge는 완료된 step을 최신 metric snapshot과 누적 step event로 나눠 기록합니다.
Node collector는 snapshot·GPU·host 정보를 Node Exporter로 노출하고 monitoring server가 이를 Prometheus와 Grafana에 연결합니다.

```text
VERL > file logger > bridge > snapshot > application.prom --+
GPU sampler > gpu.prom --------------------------------------+--> Node Exporter :19100 --> Prometheus --> Grafana
CPU / memory / network / disk -------------------------------+

VERL bridge > step event JSONL > optional Alloy > Loki > Grafana Step Explorer
```

완료 step 하나가 실제로 어떤 이름의 metric과 event가 되는지는 [step 7 예제](architecture.md#follow-one-completed-step)를 확인합니다.
기본 연결만으로는 vLLM queue·Ray task·3FS service latency·tool event가 생기지 않습니다.

## 1. Prepare One Config File

[README의 checkout 준비](../README.md#prepare-a-checkout)를 마친 뒤 저장소 루트에서 시작합니다.
Telemetry용 `.venv`에는 VERL이나 CUDA PyTorch가 설치되지 않으므로 이미 동작하는 VERL 환경의 Python을 따로 사용합니다.
아래 예제는 server·node collector·VERL이 같은 machine에서 실행되는 경우입니다.

```bash
mkdir -p "$HOME/telemetry/config"
cp -n examples/verl-local.conf "$HOME/telemetry/config/verl-local.conf"
```

복사한 파일에서 `RUN_ID`와 `VERL_COMMAND`만 자신의 실행에 맞게 수정합니다.
`VERL_COMMAND`는 Bash 배열이며, 첫 값은 VERL이 설치된 Python 또는 기존 launcher이고 나머지는 평소 사용하던 model·data·batch·GPU 설정입니다.
예제의 `/path/to/verl-env/bin/python`을 실제 경로로 바꾸고 실행 가능한 VERL recipe의 인자를 모두 넣습니다.
이 파일은 Bash로 읽히므로 자신이 관리하는 파일만 사용합니다.
Wrapper가 `verl.trainer.main_ppo` 또는 `verl.experimental.fully_async_policy.fully_async_main`을 명령 인자에서 찾으면 `trainer.logger=["console","file"]`을 추가합니다.
별도 Bash launcher 뒤에 VERL 명령을 숨기는 경우에는 launcher가 `VERL_FILE_LOGGER_PATH`를 VERL process에 전달하고 `trainer.logger`에 `file`을 포함하도록 설정해야 합니다.
직접 `trainer.logger`를 지정한 경우에도 `file`이 빠지면 wrapper가 실행을 거부합니다.

```bash
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" install
```

`install`은 `$HOME/telemetry/tools`에 node와 server 도구를 준비합니다.
처음 한 번만 실행하면 되고, NVIDIA driver·VERL·3FS는 설치하지 않습니다.

## 2. Start Server, Node, and VERL

서로 다른 terminal 세 개에서 저장소 루트로 이동하고 `. .venv/bin/activate`를 실행합니다.
각 terminal에서 같은 config 파일을 지정합니다.
Server와 node collector는 foreground에서 계속 실행되는 것이 정상이며, 먼저 두 process가 준비된 뒤 VERL을 시작합니다.

첫 번째 terminal에서 monitoring server를 실행합니다.

```bash
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" server
```

두 번째 terminal에서 GPU node collector를 실행합니다.

```bash
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" node
```

세 번째 terminal에서 기존 VERL 명령을 telemetry wrapper와 함께 실행합니다.

```bash
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" run
```

Config의 기본값은 `NODE_NAME=gpu-local`, `CLUSTER_NAME=training-cluster`, `RUN_ROOT=$HOME/telemetry-runs/<RUN_ID>`입니다.
Script가 server target, collector의 snapshot 경로, wrapper의 run ID·node 이름을 같은 값으로 맞춥니다.
`run`은 VERL process가 끝나면 bridge의 마지막 기록을 반영하고 원래 VERL의 종료 코드를 반환합니다.
이미 실행한 `RUN_ROOT`는 재사용할 수 없으므로 새 학습마다 `RUN_ID`를 바꾼 뒤 node collector를 새 설정으로 다시 시작합니다.

## 3. Check the First Completed Step

같은 host의 browser에서 `http://127.0.0.1:13000`을 엽니다.
원격 browser라면 [SSH tunnel](monitoring.md#open-the-dashboards)을 사용합니다.
Run Overview의 target 상태를 보고 Agent RL Stage Correlation에서 `cluster=training-cluster`, `node=gpu-local`, `run_id=grpo-001`을 선택합니다.
`grpo-001`은 예제 값이므로 config의 `RUN_ID`를 바꿨다면 그 값을 선택합니다.
Stage 시간과 VERL이 기록한 reward·throughput은 step이 완료된 뒤 갱신되고, GPU·host는 별도 주기로 갱신됩니다.

Dashboard 없이도 run 파일을 확인할 수 있습니다.
아래 경로의 `grpo-001` 역시 자신의 `RUN_ID`로 바꿉니다.
`RUN_ROOT`를 직접 지정했다면 그 경로를 전달합니다.

```bash
python -m xlayer_telemetry.show_run "$HOME/telemetry-runs/grpo-001"
```

```text
$HOME/telemetry-runs/grpo-001/     one VERL run
  logs/verl-metrics.jsonl          original completed step records
  logs/telemetry-bridge.log        bridge errors
  telemetry-metrics/*.json         latest step snapshot
  telemetry-events/*.jsonl         completed step events
  telemetry-manifest.json          run context

$HOME/telemetry/state/node/        collector state
  textfile/application.prom        translated application metrics
  textfile/gpu.prom                latest GPU metrics
  node-exporter.log                exporter errors
```

값이 보이지 않으면 위 순서대로 파일을 확인하고, `application.prom`까지 값이 있다면 Prometheus Targets와 Grafana의 시간·cluster·node·run 선택을 확인합니다.
Snapshot은 최신 step 하나를 덮어쓰고, 원본 file logger와 event JSONL은 이력을 보관합니다.
긴 step 동안 완료된 값이 유지되는 것은 정상일 수 있으므로 [Dashboard Guide](dashboards.md#agent-rl-stage-correlation)의 sample age를 함께 봅니다.

## Add Sources When Needed

설정 파일에는 자주 쓰는 선택 값이 주석으로 들어 있습니다.
기본 연결이 동작한 뒤 필요한 값만 켜고 영향을 받는 process를 다시 시작합니다.

| 원하는 기능 | Config에서 추가할 값 | 이어서 읽을 문서 |
| --- | --- | --- |
| Run Logs와 Grafana Step Explorer | `ENABLE_LOGS=1`; server·node 재시작 | [Loki 연결](monitoring.md#add-run-logs-with-loki) |
| vLLM·Ray endpoint | `TELEMETRY_SOURCES_FILE`; server 재시작 | [Native endpoint](agent-rl.md#register-native-endpoints) |
| 자동 진단과 선택적 3FS ClickHouse | `DIAGNOSTICS_CONFIG`; 새 run 시작 | [Diagnostics](agent-rl.md#add-diagnostics) |
| 다른 저장 위치·node 이름 | 절대 경로 `RUN_ROOT`·`TELEMETRY_HOME`, `NODE_NAME` | [구현 구조](architecture.md#what-each-file-is-for) |

`verl_local.sh`는 단일 host 편의 스크립트입니다.
다른 host에 collector를 배치할 때는 [Monitoring Guide](monitoring.md#expand-to-multiple-nodes)와 [node mapping](agent-rl.md#map-multiple-nodes-to-a-run)의 주소·port·label 설정을 사용합니다.
Wrapper의 자세한 옵션은 `bash scripts/run_verl_with_telemetry.sh --help`에서 확인합니다.
Step event를 Grafana에서 보려면 Loki가 필요하며, 로컬 JSONL 확인에는 필요하지 않습니다.

## If Data Is Missing

| 증상 | 먼저 확인할 곳 |
| --- | --- |
| Server가 시작되지 않음 | 설치 결과, `$HOME/telemetry/state/server/startup-summary.json`, 사용 중인 port |
| GPU도 보이지 않음 | `nvidia-smi`, node terminal, Prometheus `telemetry` target |
| GPU는 보이지만 run이 없음 | Config의 `RUN_ID`, wrapper log, collector가 읽는 `telemetry-metrics` 경로 |
| JSON은 있지만 panel이 비어 있음 | `application.prom`, Prometheus target, 시간·cluster·node·run filter |
| Stage 값이 없음 | 첫 step 완료 여부, VERL `file` logger 지원, `telemetry-bridge.log` |
| Step Explorer만 비어 있음 | `ENABLE_LOGS`, step event 파일, Alloy·Loki 수집과 보존 기간 |
| vLLM·Ray·3FS panel이 `N/A` | 해당 source를 추가했는지와 배포의 실제 metric 이름 |

학습 종료 후에도 server와 node terminal은 실행됩니다.
관측을 마치면 각 terminal에서 `Ctrl+C`로 종료합니다.
