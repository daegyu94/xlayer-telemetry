# Connect an Existing VERL Run

실행 가능한 VERL 명령·NVIDIA GPU가 있는 사용자를 위한 단일 host 연결입니다.
아직 없다면 [synthetic demo](monitoring.md#try-the-demo)부터 확인합니다.
Process·파일은 [Architecture](architecture.md), 추가 subsystem은 [Cross-Layer Integration](agent-rl.md)을 참고합니다.

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

| 연결 방법 | 기본적으로 얻는 정보 | 추가 연결이 필요한 정보 |
| --- | --- | --- |
| `run`의 VERL wrapper | 완료 step·stage duration과 file logger에 있는 reward·token/throughput scalar | vLLM queue, GPU, host, storage 지표 |
| `up`의 node collector | GPU utilization·power·temperature와 host CPU·memory·network·disk·filesystem | Native vLLM/Ray endpoint, 3FS service latency |
| 선택적 SDK / EventRecorder | 직접 기록한 worker metric·tool span | Application의 직접 계측과 실행 환경에 SDK 설치 |

File logger 연결은 VERL 환경에 SDK를 설치하지 않아도 됩니다.
Custom worker·tool에서 SDK를 import하면 해당 환경에도 설치합니다.
실제 producer·scope는 [수집 목록](metrics.md#what-is-actually-collected)을 확인합니다.
GPU 없는 collector는 `ENABLE_GPU_METRICS=0`을 사용하며 이 값이 VERL training 방식을 바꾸지는 않습니다.

## 1. Prepare One Config File

[Telemetry 환경](index.md#prepare-a-checkout)의 `.venv`에는 VERL이나 CUDA PyTorch가 설치되지 않으므로 이미 동작하는 VERL 환경의 Python을 따로 사용합니다.
아래 예제는 server·node collector·VERL이 같은 machine에서 실행되는 경우입니다.

```bash
mkdir -p "$HOME/telemetry/config"
cp -n examples/verl-local.conf "$HOME/telemetry/config/verl-local.conf"
```

`RUN_ID`와 `VERL_COMMAND`를 수정합니다.
`VERL_COMMAND`는 Bash 배열로 VERL Python 또는 기존 launcher 뒤에 평소 model·data·batch·GPU 인자를 넣습니다.
Config는 Bash로 실행하므로 자신이 관리하는 파일을 사용합니다.

Wrapper가 명령에서 `verl.trainer.main_ppo` 또는 `verl.experimental.fully_async_policy.fully_async_main`을 찾으면 `trainer.logger=["console","file"]`을 추가합니다.
Bash launcher는 `VERL_FILE_LOGGER_PATH` 전달과 `trainer.logger`의 `file` 설정을 담당하며 `file`이 빠진 명시적 logger는 거부됩니다.
Trainer mode를 숨긴 launcher는 `EXECUTION_MODE=async`로 지정합니다.

기본 `auto`는 trainer mode를 감지하고 rollout server의 `mode=async`만으로 판정하지 않습니다.

```bash
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" install
```

`install`은 `$HOME/telemetry/tools`에 node와 server 도구를 준비합니다.
처음 한 번만 실행하면 되고, NVIDIA driver·VERL·3FS는 설치하지 않습니다.

## 2. Start Server, Node, and VERL

Script가 monitoring server와 GPU node collector를 background로 시작하고 둘 다 준비됐는지 확인합니다.
실패하면 시작한 process를 정리하고 `$HOME/telemetry/state/verl-local/`의 log 경로를 알려줍니다.

```bash
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" up
```

`Monitoring ready`가 출력되면 같은 terminal에서 기존 VERL 명령을 telemetry wrapper와 함께 실행합니다.

```bash
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" run
```

기본값은 `NODE_NAME=gpu-local`, `CLUSTER_NAME=training-cluster`, `RUN_ROOT=$HOME/telemetry-runs/<RUN_ID>`입니다.
Script가 target·snapshot 경로·run/node 이름을 맞춥니다.
`run`은 마지막 telemetry export 후 원래 VERL exit code를 반환합니다.

Run 경로는 재사용할 수 없어 매 학습의 `RUN_ID`를 바꿉니다.
Collector는 같은 parent의 새 run을 발견하므로 ID만 바뀌면 재시작할 필요가 없습니다.
Server·collector는 학습 종료 후에도 유지되고 새 terminal에서도 같은 config로 `down`할 수 있습니다.

Process 기록·log는 `$HOME/telemetry/state/verl-local/`에 있습니다.
Run parent나 collector 입력을 바꿨다면 `down` → `up`으로 갱신합니다.

`status`는 process 생존만 확인하며 telemetry freshness는 Grafana에서 별도로 확인합니다.

```bash
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" status
```

`up`·`down`은 Linux `flock`으로 동시 실행을 막고, 새 PID 기록에는 process 시작 시각과 boot ID를 함께 저장합니다.
예전 두 필드 PID 기록은 이전 방식대로 읽을 수 있지만 boot ID 검사는 새로 시작한 process부터 적용됩니다.

## 3. Check the First Completed Step

`http://127.0.0.1:13000`을 열고 원격 접속은 [SSH tunnel](monitoring.md#open-the-dashboards)을 사용합니다.
Run Overview의 target을 확인한 뒤 Stage Correlation에서 config의 Cluster·Node·Run을 고릅니다(예제: `training-cluster`·`gpu-local`·`grpo-001`).
Stage·reward·throughput은 step 완료 시, GPU·host는 별도 주기로 갱신됩니다.

`inspect`는 같은 config의 run 경로와 등록 설정을 보여 주고 저장된 metric·event·진단 결과를 읽습니다.
Native endpoint의 실시간 접속 상태는 조회하지 않으므로 Prometheus Targets에서 별도로 확인합니다.

```bash
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" inspect
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

위 파일 순서로 확인하고 `application.prom`에 값이 있으면 target·시간·Cluster/Node/Run filter를 봅니다.
Snapshot은 최신 step, logger·event JSONL은 이력을 보존합니다.
원래 시각 없는 replay는 `unknown`으로 남아 시간 기반 목록에서 제외됩니다.
새 step이 완료될 때까지 live bridge가 유지되는지 확인합니다.
긴 step 동안 이전 값이 보일 수 있으므로 [sample age](dashboards.md#agent-rl-stage-correlation)를 함께 봅니다.

### Check Telemetry Completeness

Wrapper는 workload의 exit code와 별도로 `telemetry-health.json`을 기록합니다.
Bridge·diagnostics의 생존, 마지막 output 갱신 시각, 최종 export 결과와 진단의 missing source를 `show_run`에서 함께 확인합니다.
`complete`는 연결한 telemetry 처리의 완료 상태이며 모든 subsystem의 관측이나 workload correctness를 보증하지 않습니다.

`pending`은 첫 output 이전, `delayed`는 오래된 output입니다.
긴 step도 간격이 늘 수 있어 workload stall로 단정하지 않습니다.
기본 age 기준 300초는 `TELEMETRY_HEALTH_MAX_AGE_SECONDS`로 바꿉니다.
`partial`은 sidecar 종료·최종 export 실패/누락·evidence 부족이며 workload exit code는 유지합니다.

## Add Sources When Needed

설정 파일에는 자주 쓰는 선택 값이 주석으로 들어 있습니다.
기본 연결이 동작한 뒤 필요한 값만 켜고 영향을 받는 process를 다시 시작합니다.

| 원하는 기능 | Config에서 추가할 값 | 이어서 읽을 문서 |
| --- | --- | --- |
| Run Logs와 Run Overview의 완료 step 목록 | `ENABLE_LOGS=1`; server·node 재시작 | [Loki 연결](monitoring.md#add-run-logs-with-loki) |
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
| GPU도 보이지 않음 | `nvidia-smi`, `$HOME/telemetry/state/verl-local/node.log`, Prometheus `telemetry` target |
| GPU는 보이지만 run이 없음 | Config의 `RUN_ID`, wrapper log, collector가 읽는 `telemetry-metrics` 경로 |
| JSON은 있지만 panel이 비어 있음 | `application.prom`, Prometheus target, 시간·cluster·node·run filter |
| Stage 값이 없음 | 첫 step 완료 여부, VERL `file` logger 지원, `telemetry-bridge.log` |
| Step Explorer만 비어 있음 | `ENABLE_LOGS`, step event 파일, Alloy·Loki 수집과 보존 기간 |
| vLLM panel이나 Ray·3FS 진단 근거가 없음 | vLLM·Ray의 native target과 실제 metric 이름; 3FS는 ClickHouse 설정·데이터 |

```bash
bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" down
```

Server와 node를 따로 조사해야 할 때에는 기존 `server`·`node` 명령을 각각 foreground로 실행할 수도 있습니다.
