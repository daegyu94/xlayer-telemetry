# XLayer Telemetry

Distributed AI/HPC workload의 실행 단위를 GPU·host, rollout engine, network, storage 신호와 연결해 bottleneck candidate와 근거를 조사하는 cross-layer diagnosis 도구입니다.
Collector, application metric SDK, Grafana dashboard와 실행 분석 도구를 제공하며, 사용자가 운영하는 workload와 cluster에 연결해서 사용합니다.
처음 사용한다면 [Start Here](#start-here)에서 synthetic 화면을 확인한 뒤 실제 VERL 실행을 연결합니다.

## Why Cross-Layer Telemetry

학습 step이 느려졌다는 사실만으로는 GPU 연산, rollout queue, network 전송, storage 대기 중 어디를 확인해야 할지 알기 어렵습니다.
이 프로젝트는 application의 실행 단계와 같은 시간·node의 자원 지표를 연결해 조사할 범위를 좁힙니다.
예를 들어 VERL rollout 지연을 vLLM queue 및 GPU 사용률과 비교하고, checkpoint 지연을 3FS latency 및 SSD 상태와 비교할 수 있습니다.

다음은 VERL·vLLM과 3FS를 함께 사용하는 배치의 예입니다.
주된 사용 경로는 기존 VERL 실행에 telemetry를 붙이는 것이며, 3FS와 multi-node 배치는 필요에 따라 추가합니다.
SDK를 사용하면 다른 training framework나 custom loop에도 확장할 수 있습니다.

```text
+------------------------------------+         +--------------------------------+
| GPU CLUSTER                        |         | STORAGE CLUSTER                |
| VERL stages / vLLM rollout         |         | 3FS services                   |
| GPU / host / NIC                   |         | Service latency                |
| 3FS client                         |-- I/O > | SSDs / device health           |
+------------------------------------+         +--------------------------------+
          | telemetry                                   | telemetry
          +----------------------+----------------------+
                                 |
+-------------------------------------------------------------------------------+
| XLAYER TELEMETRY                                                              |
| Metrics > Prometheus / Grafana       Logs > Alloy / Loki                      |
| Events / traces > run artifacts      3FS > ClickHouse queries                 |
| Match time window + node / role / device + run context                        |
+-------------------------------------------------------------------------------+
                                 |
                                 +----> Slow stage > related signals > diagnosis
```

Application에는 `run_id`를 붙이고, system resource와 shared service는 시간 범위와 topology를 기준으로 비교합니다.
공유 GPU·network·storage의 사용량 전체가 특정 run의 사용량이라는 뜻은 아니며, 동시 변화는 원인 후보를 찾는 근거입니다.
3FS ClickHouse 조회 결과는 실행 진단 파일과 `show_run`에서 확인하며, Loki를 연결하면 Bottleneck Summary의 후보 근거로도 볼 수 있습니다.
3FS 서비스 전용 실시간 Grafana panel은 제공하지 않습니다.
Source별 수집 경로는 [Agent RL / VERL 신호 흐름](docs/agent-rl.md#how-the-signals-flow)에 정리했습니다.

## What Works Today

이 저장소는 이미 실행 중인 VERL·vLLM·Ray와 관측 도구를 연결합니다.
아래 표의 “추가 설정”은 기능이 코드에 있어도 사용자의 배포에서 해당 source를 켜야 값이 생긴다는 뜻입니다.

| 영역 | 현재 제공하는 것 | 사용 조건과 해석 범위 |
| --- | --- | --- |
| VERL trainer | File logger wrapper, 완료 step scalar·stage 변환, step event와 run manifest | 실행 가능한 VERL 명령이 필요합니다. 완료 전 현재 phase는 표시하지 않고 step 시간 구간은 근사치입니다. |
| GPU·host | GPU sampler, Node Exporter, Prometheus·Grafana dashboard | NVIDIA GPU와 `nvidia-smi`가 필요합니다. CPU·network·disk 값은 node 전체입니다. |
| vLLM·Ray | Native Prometheus endpoint 등록과 시간·node 비교 | Endpoint와 metric 이름이 배포에 맞아야 합니다. Ray 전용 Grafana panel은 없고 공유 engine metric은 run별로 자동 분리되지 않습니다. |
| Multi-node | Node별 collector, target 등록, topology manifest, node별 step 상세 비교 | Node 이름과 clock을 맞춰야 합니다. Worker 배치와 원인 관계를 자동으로 추론하지 않습니다. |
| Log·step 탐색 | 선택적 Alloy·Loki 수집, Run Logs, Grafana Step Explorer | File 경로와 Loki를 설정해야 합니다. Step Explorer의 경계는 VERL file logger를 바탕으로 추정합니다. |
| Storage·3FS | Filesystem·disk 지표, 선택적 SSD SMART, 3FS ClickHouse 진단 | 3FS service latency는 진단 파일과 선택적 Bottleneck Summary에서 봅니다. 전용 Grafana service panel이나 USRBIO 호출 계측은 제공하지 않습니다. |
| 운영·분석 | 선택적 Grafana alert rule, `show_run`, diagnostics, 짧은 profiler·NCCL 예제 | Alert 수신처는 별도 설정합니다. Profiler trace는 Grafana에 자동으로 들어가지 않습니다. |
| Cross-layer diagnosis | 같은 run의 이전 step 비교, scope가 붙은 rule candidate, Bottleneck Summary와 Timeline | 진단 sidecar를 켜야 JSON 결과가 생기고 Grafana 조사 화면은 Loki도 필요합니다. Shared signal은 run별 사용량이 아닙니다. |

기능별 수집 경로와 설계 이유는 [How XLayer Telemetry Works](docs/architecture.md)에, 실행 순서는 아래 [Start Here](#start-here)에 있습니다.

## Start Here

VERL에 여러 계층을 연결하려면 아래 순서로 읽고, 각 단계의 확인 결과를 얻은 뒤 다음 단계로 이동합니다.
이미 운영 중인 Prometheus·Grafana가 있더라도 처음에는 이 저장소의 단일 node 예제로 경로와 label을 확인하는 편이 이해하기 쉽습니다.

| 순서 | 읽을 문서와 할 일 | 완료 확인 |
| --- | --- | --- |
| 1 | 아래의 [checkout 준비](#prepare-a-checkout) 후 [구현 구조와 설계 원칙](docs/architecture.md)을 읽습니다. | `RUN_ROOT`, collector `OUTPUT_DIR`, monitoring server의 역할을 구분할 수 있습니다. |
| 2 | [Monitoring Guide의 synthetic demo](docs/monitoring.md#try-the-demo)로 수집과 Grafana를 확인합니다. | Start Here에서 Run Overview를 열고 `Exporter targets up`이 0보다 큽니다. |
| 3 | 이미 실행 가능한 VERL 명령을 [VERL 연결 가이드](docs/verl-quickstart.md)에 따라 한 GPU node에 붙입니다. | `show_run`에 완료 step이 나오고 Agent RL에서 step·GPU 값이 보입니다. |
| 4 | [Cross-Layer Integration](docs/agent-rl.md#choose-the-next-source)에서 vLLM·Ray endpoint와 여러 node를 연결합니다. | Prometheus의 `native` target이 up이고 해당 node의 패널이 채워집니다. |
| 5 | 필요하면 [Loki log·step 수집](docs/monitoring.md#add-run-logs-with-loki), [3FS 진단](docs/agent-rl.md#add-diagnostics)을 추가합니다. | Run Logs·Step Explorer 또는 `diagnostics/latest.json`에서 해당 증거를 확인합니다. |
| 6 | [Cross-Layer Diagnosis](docs/diagnosis.md)와 [Dashboard Guide](docs/dashboards.md)로 한 느린 구간을 조사합니다. | Baseline, candidate, evidence, missing evidence의 측정 범위를 구분합니다. |

Synthetic demo는 GPU나 VERL 없이 화면·수집 경로를 익히는 연습입니다.
실제 환경에서 채워진 화면과 재현 조건은 [real-run demo](docs/real-verl-demo.md)에 있으며, 그 recipe가 자신의 VERL 환경을 대신하지는 않습니다.
이미 VERL 명령이 준비돼 있다면 2단계를 건너뛰고 3단계부터 시작할 수 있습니다.
다른 application을 계측하려면 [Application Metrics Guide](docs/application-metrics.md)를 사용합니다.

| 원하는 작업 | 안내 |
| --- | --- |
| GPU·host 관측, Prometheus·Grafana 실행 | [Monitoring Guide](docs/monitoring.md) |
| Grafana 화면과 주요 패널 읽는 법 | [Dashboard Guide](docs/dashboards.md) |
| 완료된 VERL step의 node별 자원·log 비교 | [Step Explorer](docs/dashboards.md#step-explorer) |
| 내 application의 loss·step 기록 | [Application Metrics Guide](docs/application-metrics.md) |
| 기존 VERL 명령에 telemetry 추가 | [VERL 연결 가이드](docs/verl-quickstart.md) |
| Multi-node, vLLM·Ray·3FS, tool event 연결 | [Cross-Layer Integration Guide](docs/agent-rl.md) |
| 느려진 구간을 조사하고 trace 수집 | [Run Analysis](docs/dashboards.md#run-analysis) |
| Rule catalog, baseline, scope와 Bottleneck Summary 사용 | [Cross-Layer Diagnosis](docs/diagnosis.md) |
| Metric 이름·단위·label 결정 | [Metrics Contract](docs/metrics.md) |
| Process·파일·시계열의 연결 원리와 설계 원칙 | [How XLayer Telemetry Works](docs/architecture.md) |

## Terms Used in This Project

| 용어 | 의미 |
| --- | --- |
| Node / host | 관측 대상 machine |
| Monitoring host | Prometheus·Grafana를 실행하는 machine; 관측 node와 같은 machine이어도 됨 |
| Exporter | metric을 HTTP endpoint로 노출하는 process |
| Collector | JSON이나 장치 상태를 읽어 관측 가능한 metric으로 만드는 process |
| Target / scrape | Prometheus가 정해진 주기로 조회하는 exporter 주소 / 그 조회 동작 |
| Snapshot | 한 worker의 최신 metric을 담은 JSON; 전체 step 이력과 다름 |
| Run / `run_id` | 한 번의 workload 실행과 그 식별자 |
| Worker / rank | workload를 수행하는 process와 분산 실행에서의 번호 |
| Manifest | 실행 조건, role 배치, endpoint와 산출물 위치를 기록한 JSON |
| Trace / span | 개별 작업의 시작·종료와 소요 시간을 기록한 상세 증거 |

Prometheus는 수치 시계열을 저장하고 Grafana는 이를 시각화합니다.
Loki는 선택적인 log 저장소이며 Alloy가 log file을 전송합니다.
각 역할은 기존 도구를 조합하고, 이 프로젝트는 계층 사이의 공통 문맥과 연결 절차를 제공합니다.

## Prepare a Checkout

Shell script와 dashboard를 사용하려면 이 저장소를 직접 checkout합니다.
아래 명령은 clone할 상위 directory에서 실행합니다.

```bash
git clone https://github.com/daegyu94/xlayer-telemetry.git
cd xlayer-telemetry
bash scripts/setup.sh
. .venv/bin/activate
```

Python 3.10 이상과 `venv` 지원이 필요합니다.
`setup.sh`는 telemetry용 가상환경과 pytest를 준비하며 GPU driver, CUDA PyTorch, VERL, Prometheus·Grafana는 설치하지 않습니다.
Monitoring binary 설치는 [Monitoring Guide](docs/monitoring.md#prepare-the-host)에서 이어집니다.

문서의 shell 명령은 별도 설명이 없으면 저장소 루트에서 실행합니다.
`<...>`와 `/path/to/...`는 자신의 주소나 경로로 바꾸고, 새 terminal에서도 working directory와 Python 환경을 준비합니다.

## Python Package Usage

Application의 Python 환경에 SDK만 설치하려면 checkout 경로를 사용합니다.

```bash
python -m pip install /path/to/xlayer-telemetry
```

Source를 수정하면서 사용하려면 `python -m pip install -e /path/to/xlayer-telemetry`로 설치합니다.
설치 없이 import하려면 해당 process의 `PYTHONPATH`에 저장소 루트를 추가합니다.
Shell script, dashboard와 demo fixture는 Python package에 포함되지 않으므로 checkout에서 실행합니다.

## Scope and Layout

이 프로젝트는 cluster 배포, workload scheduling, training launcher를 관리하지 않습니다.
VERL wrapper는 전달받은 명령에 file logger와 별도 telemetry process를 연결하며 기존 workload의 종료 코드를 보존합니다.
Event·trace와 native exporter는 해당 source를 활성화했을 때만 이용할 수 있습니다.
각 신호의 생산자와 측정 범위를 유지하는 이유는 [설계 원칙](docs/architecture.md#design-principles)에 설명합니다.

| 경로 | 내용 |
| --- | --- |
| `xlayer_telemetry/metrics/` | Framework에 독립적인 metric SDK와 textfile 변환 |
| `xlayer_telemetry/adapters/` | Hugging Face Trainer와 VERL file logger 연결 |
| `xlayer_telemetry/` | Resource 수집, manifest·event, 진단과 실행 요약 |
| `scripts/` | 도구 설치, config 기반 VERL 시작, 관측 process 실행, profile·통신 baseline |
| `examples/` | Local VERL config, dashboard, synthetic demo, framework 연결 예시 |
| `config/metrics.json` | Metric 이름·단위·측정 범위의 공통 규칙 |

## Local Validation

Checkout의 Python 환경을 활성화한 뒤 실행합니다.

```bash
python -m pytest -q
for script in scripts/*.sh; do bash -n "$script"; done
bash scripts/check_tools.sh
```

CPU 테스트는 외부 training framework 없이 실행할 수 있습니다.
`check_tools.sh`의 미설치 표시는 해당 선택 기능의 도구가 없다는 뜻입니다.
Synthetic demo와 smoke test의 수치는 실제 LLM 학습 성능을 나타내지 않습니다.
