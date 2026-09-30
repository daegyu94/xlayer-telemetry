# Monitoring Guide

이 문서는 VERL GPU node와 sandbox·storage host의 자원을 관측하고 Grafana에서 확인하는 방법을 설명합니다.
먼저 demo 화면을 확인하고, 실제 node 하나를 연결한 뒤 여러 node·log·storage 관측으로 확장합니다.
VERL trainer metric 연결은 [VERL 연결 가이드](verl-quickstart.md)에서 이어집니다.
전체 데이터 흐름과 경로의 역할은 [구현 구조](architecture.md#the-basic-path)에 설명합니다.

## Know the Roles

| 구성 | 하는 일 | 실행 위치 |
| --- | --- | --- |
| `node` role | GPU sampler와 Node Exporter로 GPU·host 지표 노출 | 각 GPU·sandbox·storage host |
| `server` role | Prometheus로 metric을 수집하고 Grafana로 표시 | Monitoring host |
| `storage` role | SMART exporter로 SSD 건강 상태 노출 | 전용 storage node |
| Alloy / Loki | File log 전송 / 저장; 선택 기능 | Log를 읽는 node / monitoring host |

Monitoring host는 monitoring process를 실행하는 machine을 뜻합니다.
처음에는 GPU node와 같은 machine을 사용해도 되며 별도 cluster 제어기는 필요하지 않습니다.
Prometheus가 exporter를 주기적으로 조회하는 것을 scrape라고 부릅니다.
Node collector는 metric을 노출하고 monitoring server가 이를 가져가는 구조이므로, 여러 node에서는 server에서 각 node의 `:19100` 주소에 접근할 수 있어야 합니다.

## Prepare the Host

[Checkout 준비](../README.md#prepare-a-checkout)를 마치고 저장소 루트에서 실행합니다.
Script는 ARM64·x86_64 Linux, Python 3.10 이상, Bash, `curl`, `tar`, `unzip`을 사용합니다.
기본 `node` role은 GPU도 수집하므로 NVIDIA driver와 동작하는 `nvidia-smi`가 필요합니다.
GPU 없는 sandbox·storage host는 `ENABLE_GPU_METRICS=0`으로 host metric만 수집할 수 있으며, 실제 자원 없이 화면을 익힐 때는 demo를 사용합니다.

아래 예제는 `$HOME/telemetry` 아래에 설치 도구(`tools`), 실행 상태(`state`), server 설정(`config`)을 나누어 저장합니다.
`state` 아래에서는 역할과 demo별로 `OUTPUT_DIR`을 분리합니다.
`run_telemetry.sh`에서 경로를 생략하면 `tools`와 `state/<role>-<hostname>`을 기본값으로 사용합니다.
자신의 경로로 바꿔도 되지만 설치 때와 실행 때 같은 `TOOLS_DIR`을 전달해야 합니다.
새 terminal에서도 저장소 루트로 이동하고 Python 환경을 활성화합니다.
서버의 `server.conf`는 monitoring host에서만 읽고, `node`·`storage` 역할의 값은 해당 machine의 실행 환경 변수로 전달합니다.
같은 이름의 `OUTPUT_DIR`을 모든 역할에 쓰면 상태 파일이 섞이므로 역할별 directory를 유지합니다.

## Try the Demo

GPU나 VERL 없이 synthetic metric으로 화면을 확인합니다.
먼저 [checkout과 Python 환경 준비](../README.md#prepare-a-checkout)를 마치고 저장소 루트에서 다음 명령을 실행합니다.

```bash
. .venv/bin/activate
export TOOLS_DIR="$HOME/telemetry/tools"
bash scripts/install_telemetry_tools.sh server
OUTPUT_DIR="$HOME/telemetry/state/demo" \
DEMO_LIVE=1 \
  bash scripts/run_telemetry.sh server
```

이 명령은 foreground에서 계속 실행되므로 terminal을 열어 둡니다.
[Start Here](http://127.0.0.1:13000/d/xlayer-start-here)를 열고 Run Overview로 이동해 `cluster=demo-b300`, `node=All`, `run_id=live-demo`를 선택합니다.
`Collector availability`의 해당 target이 Up이고 application/GPU sample age가 fresh이면 수집과 Grafana 연결을 확인한 것입니다.
GPU별 값은 Compute & Communication의 GPU matrix에서 확인합니다.
Agent RL Stage Correlation에서 `run_id=verl-agent-demo`, `node=gpu-node-0`을 선택하면 완료 step 값이 증가하는 것도 확인할 수 있습니다.
화면이 비어 있으면 먼저 실행 terminal의 오류와 `OUTPUT_DIR/startup-summary.json`을 확인합니다.
실제 학습 성능이 아닌 화면·수집 경로 확인용 값입니다.
이 demo의 target은 합성 exporter이며 실제 GPU driver, VERL 실행, 3FS 서비스가 연결되었다는 뜻은 아닙니다.

실제 node를 연결하기 전 `Ctrl+C`로 demo를 종료해 동일한 service port를 비웁니다.
상세한 가상 구성과 화면 예시는 [Demo Details](#demo-details)에 있습니다.

## Monitor One GPU Node

VERL을 같은 host에서 실행한다면 [config 기반 `up`](verl-quickstart.md#2-start-server-node-and-verl)으로 한 terminal에서 server·collector를 background로 시작하는 경로를 권장합니다.
아래는 VERL 없이 자원만 관측하거나 개별 process를 조사할 때 쓰는 수동 경로입니다.
각 명령은 foreground로 실행하므로 별도 terminal을 사용하고 `OUTPUT_DIR`도 분리합니다.

### 1. Install the Tools

```bash
export TOOLS_DIR="$HOME/telemetry/tools"
bash scripts/install_telemetry_tools.sh node
bash scripts/install_telemetry_tools.sh server
```

`node` 설치는 Node Exporter·SMART exporter·Alloy를 준비합니다.
`server` 설치는 Node Exporter·SMART exporter·Prometheus·Grafana·Loki를 준비합니다.
Driver, `smartmontools`, training framework는 별도입니다.

### 2. Start the Collector

첫 terminal에서 실행합니다.

```bash
. .venv/bin/activate
TOOLS_DIR="$HOME/telemetry/tools" \
NODE_ADDR='127.0.0.1' \
NODE_NAME='gpu-local' \
OUTPUT_DIR="$HOME/telemetry/state/node" \
  bash scripts/run_telemetry.sh node
```

Node Exporter는 `127.0.0.1:19100/metrics`에 host와 GPU metric을 노출합니다.
기본 실행 시간 제한은 없으며, `Ctrl+C`나 종료 signal 또는 자식 service 종료 시 함께 시작한 process를 정리합니다.
일회성 수집에는 `DURATION=3600`처럼 양의 초 값을 추가합니다.
GPU snapshot JSONL은 계속 누적되므로 장기 운영에서는 출력 directory 용량을 관리합니다.

### 3. Start the Server

두 번째 terminal에서 실행합니다.
처음 한 번만 예제 설정을 개인 경로로 복사하고 `TELEMETRY_TARGETS`를 실제 collector 주소로 수정합니다.
이후 server를 재시작할 때도 같은 설정 파일을 사용합니다.

```bash
. .venv/bin/activate
mkdir -p "$HOME/telemetry/config"
if [[ ! -f "$HOME/telemetry/config/server.conf" ]]; then
  cp examples/monitoring-server.conf "$HOME/telemetry/config/server.conf"
fi
bash scripts/run_telemetry.sh server --config "$HOME/telemetry/config/server.conf"
```

`TELEMETRY_TARGETS`는 `logical-node-name=address` 형식입니다.
이름은 dashboard에서 사용하고 주소는 exporter 접속에 사용합니다.
예제의 `gpu-local=127.0.0.1`은 node와 server가 같은 machine에 있을 때만 유효합니다.
다른 host에 server를 두면 node의 `NODE_ADDR`와 target 주소를 monitoring host에서 도달 가능한 주소로 함께 바꿉니다.
시작 시 HTTP 상태와 query를 검사하고 `OUTPUT_DIR/startup-summary.json`을 기록합니다.
설정 파일은 로컬 Bash 파일이므로 `$HOME`을 사용할 수 있으며, 파일에 적힌 값이 현재 terminal의 같은 이름 환경 변수보다 우선합니다.

## Monitor GPU and Storage Nodes Together

Collector와 correlation은 분산 실행을 지원하지만, GPU 두 개가 있는 한 host는 독립된 clock을 가진 두 node가 아닙니다.
먼저 각 node의 논리 이름, monitoring host에서 접근할 주소, 실제 workload 역할을 정합니다.
GPU/rollout node와 storage node 모두 같은 cluster에 등록하고, 모든 application·native source·manifest·Loki 설정에서 같은 node 이름을 사용합니다.

```text
GPU node gpu-a            GPU node gpu-b           Storage node storage-a
  trainer                  rollout / vLLM           3FS / local SSD
  node collector           node collector           host collector + SMART
       |                        |                          |
       +------------------------+--------------------------+
                                |
                                v
                       Monitoring host
                       Prometheus / Loki
                                |
                                v
                         XLayer diagnosis
                         Grafana timeline
```

각 GPU node에서 기존 `node` collector를 실행합니다.
Storage node에는 GPU가 없어도 다음처럼 host collector를 실행할 수 있습니다.
`TOOLS_DIR`은 그 node에서 도구를 설치한 경로이며 `NODE_ADDR`는 monitoring host에서 접근 가능한 local interface 주소로 바꿉니다.

```bash
TOOLS_DIR="$HOME/telemetry/tools" \
NODE_ADDR='10.0.1.10' NODE_NAME='storage-a' \
ENABLE_GPU_METRICS=0 \
OUTPUT_DIR="$HOME/telemetry/state/storage-host" \
  bash scripts/run_telemetry.sh node
```

이 경로는 CPU·memory·network·disk·filesystem·clock metric을 노출하며 NVIDIA driver가 필요하지 않습니다.
SMART도 필요하면 `ENABLE_SSD_HEALTH=1`을 추가하고 관련 권한을 준비합니다.
기존 `storage` role은 SMART 전용이며 host/clock metric을 제공하지 않으므로 host collector를 대신하지 않습니다.
GPU가 있는 collector에서 `ENABLE_GPU_METRICS=0`으로 바꾸면 이전 `gpu.prom`도 제거합니다.

Monitoring host의 `server.conf`에는 모든 host collector를 등록합니다.
`STORAGE_TARGETS`는 선택적인 SMART endpoint이고 `TELEMETRY_TARGETS`는 host/clock endpoint입니다.
둘은 용도가 다릅니다.

```bash
CLUSTER_NAME='training-cluster'
TELEMETRY_TARGETS='gpu-a=10.0.0.10,gpu-b=10.0.0.11,storage-a=10.0.1.10'
# SMART를 활성화한 경우에만 추가합니다.
STORAGE_TARGETS='storage-a=10.0.1.10'
```

VERL launcher에서 지정한 telemetry 환경 변수가 remote Ray worker에 자동 전달된다고 가정하지 않습니다.
Remote worker에도 동일한 `TELEMETRY_RUN_ID`와 각 host의 `TELEMETRY_NODE`, 실제 node-local metric/event directory를 설정하고 SDK를 설치해야 합니다.
Trainer file logger bridge 하나는 trainer boundary를 제공하며 모든 remote worker를 자동 instrument하지 않습니다.
Manifest에서는 같은 role을 여러 node에 반복해서 지정할 수 있습니다.

```bash
python -m xlayer_telemetry.manifest \
  --output "$RUN_ROOT/topology-manifest.json" --run-id 'grpo-001' \
  --role trainer=gpu-a --role rollout=gpu-b --role storage=storage-a
```

### Check Clock Alignment Before Diagnosing

Monitoring host, GPU node, sandbox node와 storage telemetry producer를 모두 같은 신뢰 가능한 NTP source에 동기화합니다.
Ubuntu에서는 운영 환경에 맞는 chrony 또는 systemd-timesyncd를 사용하고 `timedatectl show -p NTPSynchronized`와 해당 service의 tracking 상태를 확인합니다.
두 daemon을 동시에 새로 켜거나 실행 중인 training의 clock을 임의로 변경하지 않습니다.
Service가 enabled인 것만으로 실제 동기화가 완료된 것은 아닙니다.

실제 collector를 등록하고 scrape가 진행된 뒤 monitoring host에서 다음을 실행합니다.
Exit code `0`은 현재 검사 구간의 `aligned`, `1`은 `unsafe` 또는 `unknown`입니다.

```bash
python -m xlayer_telemetry.analysis.clock_quality \
  --prometheus-url 'http://127.0.0.1:19090' \
  --cluster 'training-cluster' \
  --node gpu-a --node gpu-b --node storage-a
```

기본 검사 구간은 최근 60초이며 허용 scrape-relative 차이는 1초, clock sample age는 30초입니다.
`node_time_seconds - timestamp(node_time_seconds)`는 exporter wall clock과 Prometheus scrape clock의 차이를 봅니다.
Network/collection delay도 포함하므로 정밀 NTP offset이나 event timestamp 보정값으로 해석하지 않습니다.
Prometheus는 exporter가 별도 timestamp를 보내지 않는 metric에 scrape timestamp를 사용하지만 event·log·application timestamp는 producer clock에서 만들어집니다.
근거는 [Prometheus configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/), [Node Exporter time collector](https://github.com/prometheus/node_exporter/blob/master/collector/time.go), [timex collector](https://github.com/prometheus/node_exporter/blob/master/collector/timex.go)에 있습니다.

`--allow-unsynchronized`는 kernel sync status 요구만 제외하는 조사 옵션입니다.
이 옵션으로 같은 host의 offset 검사가 통과해도 NTP나 물리 multi-node 동기화가 검증된 것은 아닙니다.
짧은 step은 허용 차이보다 짧을 수 있으므로 threshold를 실제 분석 해상도에 맞추고 sample 간격과 rate window도 함께 확인합니다.
자동 diagnosis 설정은 [Clock and Node Selection](diagnosis.md#clock-and-node-selection)에 있습니다.

### Reproduce the Local Validation

실제 GPU 두 개와 설치된 Prometheus/Node Exporter binary가 있다면 다음 검증을 실행할 수 있습니다.
새 output directory를 지정합니다.
기존 monitoring service는 건드리지 않고 별도 loopback port와 process를 사용한 뒤 정리합니다.

```bash
PYTHONPATH="$PWD" python examples/multinode/validate_local.py \
  --node-exporter "$TOOLS_DIR/node_exporter-1.9.1.linux-amd64/node_exporter" \
  --prometheus "$TOOLS_DIR/prometheus-3.5.0.linux-amd64/prometheus" \
  --output /tmp/xlayer-multinode-check
```

Architecture에 맞춰 binary directory의 `amd64` 또는 `arm64`를 선택합니다.
GPU UUID 두 개, logical node별 application snapshot, storage host metric, shared trace/parent, Step Explorer 조회를 실제 backend에서 검사합니다.
Clock metric에 +12초 offset을 주입하고 storage source도 중단하여 clock screening을 확인합니다.
이 두 fault는 test proxy에서만 만들며 시스템 clock이나 storage를 변경하지 않습니다.

2026-09-30 검증은 RTX PRO 4000 Blackwell 두 개, Node Exporter 1.9.1, Prometheus 3.5.0에서 위 항목을 통과했습니다.
Host와 kernel이 NTP unsynchronized를 보고하여 strict check는 `unsafe`였고, sync status 요구를 제외한 scrape-relative 차이는 수십 ms 이내였습니다.
검증 조건과 항목별 결과는 [Validation record](validation/multinode/validation.json)에 보존합니다.
물리 host는 하나였으며 host counter도 공유합니다.
독립된 GPU/storage 서버, 실제 distributed VERL training, RDMA/NCCL, remote 3FS producer clock과 Loki 전송은 이 검증의 완료 항목이 아닙니다.

## Open the Dashboards

같은 host의 browser에서 `http://127.0.0.1:13000`을 엽니다.
원격 host를 사용하면 자신의 PC에서 다음 SSH tunnel을 열고 `http://localhost:13000`으로 접속합니다.
`user@monitoring-host`는 실제 SSH 접속 대상으로 바꿉니다.

```bash
ssh -NT -L 13000:127.0.0.1:13000 user@monitoring-host
```

먼저 [Start Here](http://127.0.0.1:13000/d/xlayer-start-here)에서 조사할 질문에 맞는 화면을 고릅니다.
Run Overview에서 cluster와 node를 선택하고 target 상태와 최근 GPU sample을 확인합니다.
아직 workload를 연결하지 않았다면 run 목록과 application panel이 비어 있는 것이 정상입니다.
Target이 up인데 run이 보이지 않는다면 [VERL 연결 가이드](verl-quickstart.md#3-check-the-first-completed-step)에서 wrapper·snapshot·collector 경로를 확인합니다.

| Dashboard | 확인할 내용 |
| --- | --- |
| Start Here | 목적별 화면 선택과 조사 순서 |
| Run Overview | Target 상태·sample freshness, step 시간 추이와 선택적 완료 step 목록 |
| Agent RL Stage Correlation | VERL 완료 stage와 등록한 rollout engine 지표 |
| Compute & Communication | GPU·host·NIC/RDMA와 topology |
| Data & Storage | Local device·filesystem, storage topology, 선택적 SMART |
| Bottleneck Summary | 진단 sidecar와 Loki를 연결한 run의 candidate·baseline·evidence |
| Cross-Layer Timeline | 선택한 step 요약·span·step band와 Prometheus resource를 같은 시간축에서 확인 |
| Run Logs | Loki를 활성화했을 때만 제공되는 log 검색 |

각 화면의 필터·패널·측정 범위는 [Dashboard Guide](dashboards.md)에서 설명하고, [실제 실행 GIF](real-verl-demo.md)에서 값이 채워진 예를 볼 수 있습니다.

GPU sample은 기본 30초, 학습 지표는 `Training sample max age (s)` 기본 300초 기준으로 오래된 값을 숨길 수 있습니다.
긴 step에서는 sample age와 filter를 함께 확인하며, N/A를 사용량 0으로 읽지 않습니다.
Prometheus는 `127.0.0.1:19090`에서 실행되고 Grafana 기본 접근 권한은 anonymous Viewer입니다.
관리자 비밀번호는 `GRAFANA_ADMIN_PASSWORD`로 지정합니다.

## Enable Grafana Alerts

Monitoring Guide의 수동 경로에서는 server 설정 파일의 `ENABLE_ALERTS=1`로 Grafana Alerting에 세 가지 운영 규칙을 설치합니다.
단일 host [VERL config 경로](verl-quickstart.md#1-prepare-one-config-file)를 사용한다면 `verl-local.conf`의 `ENABLE_ALERTS=1`을 설정하고 `verl_local.sh down` 후 `up`을 실행합니다.
Node collector 연결 끊김, GPU 표본이 60초 넘게 갱신되지 않거나 사라짐, 지정한 filesystem의 여유 공간 부족을 node별로 평가합니다.
`ENABLE_GPU_METRICS=0`인 collector는 `telemetry_gpu_collection_enabled=0`을 내보내 GPU 누락 경고에서 제외합니다.
이 marker가 없는 구형 collector는 기존 GPU 감시 동작을 유지하므로, GPU 없는 node는 collector와 server를 함께 갱신하고 재시작합니다.
기본 filesystem 대상은 `/`이며, 3FS FUSE 등의 다른 경로를 감시하려면 실제 `mountpoint`를 `ALERT_MOUNTPOINT`에 지정합니다.
기존 server terminal에서 `Ctrl+C`로 종료한 뒤 같은 설정 파일로 다시 시작합니다.

```bash
bash scripts/run_telemetry.sh server --config "$HOME/telemetry/config/server.conf"
```

서버를 다시 시작한 뒤 Grafana의 `Alerting > Alert rules`에서 `XLayer Telemetry` 폴더를 확인합니다.
Node collector와 GPU 규칙은 30초마다 평가하고 조건이 1분 지속되면 발화하며, 용량 규칙은 5분 지속되면 발화합니다.
Prometheus 조회 오류는 `Error` 상태로 표시하고, 해당 지표가 아직 없는 `No Data`는 정상 상태로 처리합니다.
따라서 설정에서 target 자체를 제거하거나 감시할 filesystem이 없으면 이 세 규칙만으로는 누락을 감지하지 못합니다.

규칙 상태는 Grafana에서 보이지만 외부로 받으려면 관리자 계정으로 `Alerting > Contact points`에서 수신처를 설정하고 notification policy에 연결해야 합니다.
프로젝트는 수신처를 자동 설정하지 않으며 `service=xlayer-telemetry` label로 별도 notification policy를 만들 수 있습니다.
관리자 비밀번호와 webhook URL 등은 저장소에 넣지 않습니다.
규칙 파일은 `OUTPUT_DIR/provisioning/alerting/operations.json`에 생성되고 Grafana를 다시 시작할 때 적용됩니다.
파일에서 관리하는 규칙은 Grafana UI에서 직접 편집할 수 없으며 설정 파일을 `ENABLE_ALERTS=0`으로 바꿔 다시 시작하면 세 규칙을 삭제합니다.

이 규칙은 운영 중인 수집 경로만 검사합니다.
완료된 step의 metric은 학습 종료 후에도 마지막 값이 남을 수 있으므로 학습 정지 알림은 활성 run 상태를 따로 계측하기 전까지 포함하지 않습니다.

## Application Metrics

VERL은 [wrapper](verl-quickstart.md), 다른 workload는 [SDK](application-metrics.md)로 JSON snapshot을 생성합니다.
같은 node의 collector를 다시 시작할 때 다음 설정을 추가합니다.

```bash
TOOLS_DIR="$HOME/telemetry/tools" \
NODE_ADDR='127.0.0.1' \
NODE_NAME='gpu-local' \
OUTPUT_DIR="$HOME/telemetry/state/node" \
TELEMETRY_METRICS_DIR='/path/to/run/telemetry-metrics' \
  bash scripts/run_telemetry.sh node
```

Snapshot이 제공하는 loss·step·timer만 변환되며 collector가 학습 값을 만들어 내지는 않습니다.
기본 2초 주기는 `TELEMETRY_METRICS_INTERVAL`로 조정합니다.
Application의 `TELEMETRY_NODE`와 server target 이름을 맞춰 같은 node의 자원 지표를 함께 봅니다.

### Keep the Collector Across Runs

같은 node에서 run을 순차 실행하거나 동시에 실행할 때는 `TELEMETRY_RUNS_ROOT`를 run directory들의 부모로 지정합니다.
Collector는 매 poll마다 바로 아래의 `*/telemetry-metrics`를 발견하고 기존 node·producer identity로 구분합니다.
임의의 하위 directory를 재귀 탐색하지 않습니다.

```bash
TELEMETRY_RUNS_ROOT="$HOME/telemetry-runs" \
  bash scripts/verl_local.sh --config "$HOME/telemetry/config/verl-local.conf" node
```

`verl_local.sh`는 기본적으로 설정한 `RUN_ROOT`의 부모를 사용하므로 보통 이 override도 필요 없습니다.
Run-root 모드의 기본 `TELEMETRY_METRICS_MAX_AGE_SECONDS`는 300초이며, 긴 step은 정상 갱신 간격보다 충분히 큰 값으로 설정합니다.
오래되었거나 미래 시각의 snapshot, 종료 상태가 기록된 run은 현재 application export에서 제외하지만 JSONL·snapshot과 기존 Prometheus 이력은 보존합니다.
다른 node의 시각이 어긋난 snapshot도 제외될 수 있으므로 clock 상태를 먼저 확인합니다.

기존 `--metrics-dir`는 유지하며 CLI에서 반복 지정할 수 있습니다.
직접 지정한 directory만 사용하는 CLI는 `--max-age-seconds`를 생략하면 기존처럼 age 제한 없이 읽습니다.

```bash
python -m xlayer_telemetry.metrics.textfile \
  --metrics-dir /path/to/run-a/telemetry-metrics \
  --metrics-dir /path/to/run-b/telemetry-metrics \
  --node gpu-0 --max-age-seconds 300 --textfile-dir /path/to/collector/textfile
```

## Expand to Multiple Nodes

각 GPU node에는 node 도구와 collector를, monitoring host에는 server 도구를 설치합니다.
Node의 `NODE_ADDR`는 loopback 대신 monitoring host에서 접근할 수 있는 주소로 바꿉니다.
Server 설정 파일의 `TELEMETRY_TARGETS`에 모든 관측 node를 쉼표로 나열하고 기존 server를 종료한 뒤 다시 시작합니다.
예를 들어 `TELEMETRY_TARGETS='trainer-0=10.0.0.10,rollout-0=10.0.0.11'`처럼 적고 주소는 실제 배치에 맞게 바꿉니다.

```bash
bash scripts/run_telemetry.sh server --config "$HOME/telemetry/config/server.conf"
```

Clock를 동기화해야 다른 machine의 같은 시간 구간을 비교할 수 있습니다.
Run directory는 node별로 두고 shared storage의 동일 snapshot을 여러 collector가 읽지 않도록 합니다.

| 연결 방향 | 기본 port | 용도 |
| --- | --- | --- |
| Monitoring host → GPU node | `19100` | Host·GPU·application metric |
| Monitoring host → storage node | `19633` | 선택적 SMART |
| Log collector → monitoring host | `13100` | 선택적 Loki 전송 |
| Browser → monitoring host | SSH tunnel | Loopback Grafana 접근 |

vLLM·Ray 등 추가 endpoint는 해당 port도 접근 가능해야 합니다.
등록 절차는 [Native Metric Endpoints](agent-rl.md#register-native-endpoints)에 있습니다.
서로 다른 node 이름을 임의로 혼용하면 dashboard filter가 데이터를 연결하지 못합니다.

## Add Run Logs with Loki

각 run의 `<log-root>/<run-directory>/logs/**/*.log`, VERL의 `telemetry-events/verl-steps*.jsonl`, EventRecorder JSONL, diagnostics projection을 Alloy가 읽어 Loki에 전송합니다.
Wrapper의 `telemetry-bridge.log`는 이 패턴에 들어가지만 VERL의 metric JSONL이나 terminal의 stdout 전체가 자동으로 Run Logs에 나타나지는 않습니다.
학습 log를 보려면 workload가 같은 `logs/` 아래 `.log` 파일을 쓰도록 설정합니다.
Shared storage를 사용하면 같은 file이 중복 전송되지 않도록 수집 담당 collector를 하나로 정합니다.

Monitoring Guide의 수동 단일 host 예제에서는 monitoring server를 종료하고 `server.conf`의 `ENABLE_LOGS=1`만 바꿔 다시 시작합니다.
기본 `LOKI_LISTEN_ADDR='127.0.0.1'`은 같은 host에서 실행하는 collector가 접근할 수 있습니다.
단일 host [VERL config 경로](verl-quickstart.md#1-prepare-one-config-file)를 사용한다면 `verl-local.conf`의 `ENABLE_LOGS=1`을 설정하고 `verl_local.sh down` 후 `up`을 실행합니다.
이 경로는 log root와 metric snapshot 경로를 같은 `RUN_ID`에서 자동으로 계산하므로 아래의 수동 환경 변수 예제를 입력할 필요가 없습니다.

```bash
bash scripts/run_telemetry.sh server --config "$HOME/telemetry/config/server.conf"
```

다른 node에서 Loki로 전송하려면 `LOKI_LISTEN_ADDR`을 monitoring host의 관리망 주소로 바꾸고, 아래 `LOKI_PUSH_URL`도 그 주소로 바꿉니다.
생성되는 Loki 설정에는 인증이 없으므로 관리망에서만 접근하도록 배치합니다.
기본 보존 기간은 7일이며 `LOKI_RETENTION`으로 바꿀 수 있습니다.

앞의 `grpo-001` 단일 node 예제를 이어간다면 같은 node에서 기존 collector를 종료하고 다음 설정으로 다시 시작합니다.
`TELEMETRY_LOG_ROOTS`는 run directory 자체가 아니라 그 부모 directory이고, `TELEMETRY_METRICS_DIR`은 계속 같은 run의 snapshot directory를 가리킵니다.

```bash
TOOLS_DIR="$HOME/telemetry/tools" \
NODE_ADDR='127.0.0.1' \
NODE_NAME='gpu-local' \
OUTPUT_DIR="$HOME/telemetry/state/node" \
CLUSTER_NAME='training-cluster' \
TELEMETRY_METRICS_DIR="$HOME/telemetry-runs/grpo-001/telemetry-metrics" \
LOKI_PUSH_URL='http://127.0.0.1:13100/loki/api/v1/push' \
TELEMETRY_LOG_ROOTS="verl=$HOME/telemetry-runs" \
  bash scripts/run_telemetry.sh node
```

Log root는 존재하는 절대 경로여야 합니다.
따라서 위 예제에서는 `$HOME/telemetry-runs/grpo-001/logs/`, `telemetry-events/`, `diagnostics/investigation/`을 찾습니다.
여러 root는 `verl=/path/a,custom=/path/b`처럼 구분하고 workload 이름을 중복하지 않습니다.
Alloy의 읽기 offset은 `OUTPUT_DIR/alloy-data`에 저장되며 기본적으로 24시간보다 오래된 file을 제외합니다.
`ALLOY_IGNORE_OLDER_THAN`으로 이 기준을 바꾸고 Alloy state와 Loki data는 각각 host-local 경로에 둡니다.

Run Logs에서 cluster·node·workload·run을 선택합니다.
`run_id`와 file 경로는 log record에 저장되고 `cluster`·`node`·`workload`가 index label로 사용됩니다.
`run_id`는 여기서 경로의 run directory 이름이며 wrapper에 전달한 `--run-id`와 다를 수 있습니다.
Run Overview의 Run Logs 링크는 시간과 run 선택을 전달합니다.
Run Overview에 통합된 Step Explorer 목록은 step event에서 `run_id`와 시간 범위를 읽습니다.
VERL wrapper가 만든 `telemetry/telemetry-events/verl-steps.jsonl`도 수집하며, 기존 기록의 backfill 파일도 같은 패턴으로 읽습니다.
두 경로가 모두 없다면 step 목록은 비어 있습니다.
Grafana Step Explorer는 event를 Loki에서, 자원 그래프를 Prometheus에서 읽으므로 두 datasource의 보존 기간과 선택한 시간 범위를 함께 확인합니다.
`04 · Bottleneck Summary`는 diagnostics projection, `05 · Cross-Layer Timeline`은 EventRecorder span·step event·Prometheus sample을 읽습니다.
진단과 EventRecorder를 쓰지 않은 run에서는 해당 panel이 비어 있으며, 설정 경로는 [Cross-Layer Diagnosis](diagnosis.md)에 있습니다.

## SSD Health

`storage` role은 전용 storage node의 SSD 건강 상태를 수집합니다.
3FS operation latency 수집과는 별도이며 [3FS 연결](agent-rl.md#add-diagnostics)에서 서비스 지표를 추가합니다.
Storage node에 node 도구와 system package `smartmontools`를 설치한 뒤 실행합니다.

```bash
TOOLS_DIR="$HOME/telemetry/tools" \
NODE_ADDR='10.0.0.30' \
OUTPUT_DIR="$HOME/telemetry/state/storage" \
  bash scripts/run_telemetry.sh storage
```

SMART exporter 기본 port는 `19633`, 장치 조회 주기는 60초입니다.
`SMARTCTL_PORT`·`SMARTCTL_INTERVAL`로 조정하고, 실행 파일은 `SMARTCTL_EXPORTER`·`SMARTCTL`로 지정할 수 있습니다.
NVMe SMART 접근에는 추가 권한이 필요할 수 있으며 자동 모드는 필요할 때 `smartctl`만 passwordless sudo로 실행합니다.
`SMARTCTL_SUDO=0` 또는 `1`로 명시할 수 있고 sudo preflight가 실패하면 수집을 시작하지 않습니다.

Monitoring host의 같은 server 설정 파일에 `STORAGE_SYSTEM='3fs'`, `STORAGE_TARGETS='storage-0=10.0.0.30'`을 추가해 재시작합니다.

```bash
bash scripts/run_telemetry.sh server --config "$HOME/telemetry/config/server.conf"
```

`STORAGE_SYSTEM`은 배치 식별 label이며 값을 `3fs`로 설정해도 3FS 서비스를 자동 계측하지 않습니다.
Compute node의 SSD를 수집할 때는 `node` role에 `ENABLE_SSD_HEALTH=1`을 추가하고 해당 SMART endpoint도 server에 등록합니다.

Data & Storage에서 온도·critical warning·media error·endurance를 확인합니다.
`smartctl_device_bytes_written`은 host write 누계이며 SSD 내부 NAND write나 write amplification을 뜻하지 않습니다.
장치 지표에는 다른 workload와 replication 영향도 포함되므로 특정 run의 I/O 양으로 귀속하지 않습니다.

## Topology and Native Sources

`TOPOLOGY_DIR`에 `compute-topology.json`과 `storage-topology.json`을 둔 뒤 node collector에 전달하면 component·edge를 표시합니다.
연결 관계는 사용자가 제공해야 하며 topology 그림만으로 link bandwidth나 서비스 latency를 측정하지 않습니다.
Native service 연결은 [상세 가이드](agent-rl.md#register-native-endpoints)를 따릅니다.

## Check and Stop

`Ctrl+C`로 role을 종료하면 함께 시작한 process를 정리하며, node 종료 시 GPU·application textfile도 제거합니다.
수집기 하나가 종료되어도 해당 role의 나머지 process가 종료되므로 예상치 못한 종료는 해당 terminal과 log를 확인합니다.
구성 변경 시 기존 process를 종료하고 같은 role을 다시 시작하며 다른 workload process를 종료할 필요는 없습니다.
Server는 앞서 복사한 `server.conf`로 다시 시작해 알림·로그·storage 설정을 함께 유지합니다.

| 증상 | 먼저 확인할 항목 |
| --- | --- |
| 도구를 찾지 못함 | 설치 때와 실행 때의 `TOOLS_DIR` |
| GPU 수집 즉시 종료 | `nvidia-smi` 동작, driver, sampler 오류 |
| Target down | Node process, 주소·port, network 접근 |
| GPU는 보이고 run은 없음 | Application metric 연결과 freshness |
| Loki log 없음 | File 경로, 최근 수정 시각, Alloy log와 push URL |
| SMART 값 N/A | `smartctl` 권한, 실제 장치 지원 항목 |

Monitoring host에서 준비된 Python 환경으로 상태를 검사할 수 있습니다.

```bash
PYTHONPATH=. python -m xlayer_telemetry.stack validate-stack \
  --prometheus-url http://127.0.0.1:19090 \
  --grafana-url http://127.0.0.1:13000 \
  --require-targets-up \
  --output /tmp/telemetry-validation.json
```

Loki를 활성화했다면 `--loki-url http://<monitoring-host-address>:13100`을 추가합니다.
`SERVER_CONFIG_ONLY=1`은 server 실행 없이 설정을 생성합니다.
별도 Docker Compose 배치는 [Compose 예제 안내](../examples/dashboards/README.md)를 따릅니다.
기존 node collector에 연결하며 현재 공통 metrics dashboard를 생성해서 사용합니다.

## Demo Details

Interactive demo는 GPU node 4개·node당 GPU 8개, storage node 8개·node당 SSD 4개의 가상 구성을 제공합니다.
정상 학습 → data wait → collective → checkpoint → recovery를 100초 주기로 반복합니다.
Agent RL 예시는 약 6초마다 완료 step 지표를 생성합니다.
Fixture는 `examples/live-demo/`에 있으며 `DEMO_TOPOLOGY_DIR`·`DEMO_ADDR`·`DEMO_PORT`로 설정할 수 있습니다.

아래 synthetic GIF는 현재 dashboard 8개에서 step 선택 → candidate → evidence → Timeline → subsystem detail로 조사하는 예제입니다.
위 interactive exporter와는 별도의 작은 UI fixture로, trainer·GPU·sandbox node 세 문맥과 GPU 두 개를 표현합니다.
Step 127의 duration 18.4 s와 baseline 11.2 s를 비교하고 `storage_queue_saturation` 등의 후보, counter/missing evidence를 확인합니다.
이 값은 실제 hardware나 VERL 성능 측정 결과가 아닙니다.

![현재 Grafana의 synthetic investigation: step 선택, storage candidate, evidence, Timeline, subsystem detail](figures/xlayer-investigation-synthetic.gif)

아래 실제 GIF는 2026-09-30에 완료한 VERL·vLLM·Docker sandbox 실행의 저장 데이터를 최신 UI로 재생합니다.
3 trainer update와 baseline, tool/sandbox span, local disk와 workload log를 보여 줍니다.
이 실행의 verdict는 `no_anomaly_observed`로, synthetic 예제와 달리 storage bottleneck을 만든 실행이 아닙니다.
두 GIF 모두 약 95초이며 화면마다 5–7초 유지합니다.
[Real VERL Agent RL Demo](real-verl-demo.md)에 수집 시각·실행 조건·scope와 이전 3FS POSIX 실험 기록을 정리했습니다.

![현재 Grafana의 실제 VERL Agent RL 기록: 완료 step, baseline, tool/sandbox, GPU, local storage, Loki logs](figures/verl-agent-rl-investigation.gif)

첫 연결이 끝나면 [VERL 연결 가이드](verl-quickstart.md)에서 기존 workload를 연결합니다.
