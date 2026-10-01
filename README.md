# XLayer Telemetry

VERL trainer·vLLM·Ray·tool/sandbox와 GPU·host·network·storage의 telemetry를 **Collect → Correlate → Diagnose**하는 cross-layer performance diagnosis 프레임워크입니다.
Run·step·phase 문맥에서 느린 구간의 bottleneck candidate와 evidence를 조사합니다.
Wrapper·collector·SDK로 기존 VERL 환경을 연결하고 Grafana에서 탐색합니다.
CPU/GPU kernel 상세 분석은 Nsight Systems·PyTorch Profiler로 이어집니다([설계 배경](docs/architecture.md#why-xlayer-exists)).
[문서 웹사이트](https://daegyu94.github.io/xlayer-telemetry/)에서 검색과 단계별 안내를 이용하거나, 아래 [Start Here](#start-here)부터 시작합니다.

## Why Cross-Layer Telemetry

느린 step의 원인은 GPU 연산, rollout queue, network 전송, storage 대기 등 여러 계층에 있을 수 있습니다.
XLayer는 실행 단계와 같은 시간·node의 자원 지표를 연결해 조사 범위를 좁힙니다.
예를 들어 rollout 지연은 vLLM queue·GPU 사용률과, checkpoint 지연은 3FS latency·SSD 상태와 비교합니다.

| 단계 | XLayer가 하는 일 | 사용자에게 남는 결과 |
| --- | --- | --- |
| **1. Collect** | VERL file logger, native vLLM·Ray endpoint, GPU·host exporter, 선택적 Loki log·event와 3FS ClickHouse source를 연결 | Metric 시계열, log·event, run artifact; source별 설정 필요 |
| **2. Correlate** | Run·step·phase와 node·role·worker·device·topology를 기준으로 신호를 연결하고, 계측된 span의 trace 관계를 보존 | 같은 실행 구간에서 무엇이 함께 변했는지 조사할 문맥 |
| **3. Diagnose** | Current/baseline 관측값에서 rule 기반 후보와 supporting·counter·missing evidence를 생성; 선택적으로 local LLM이 메트릭을 직접 분석 | 검토 가능한 bottleneck candidate와 다음 조사 지점 |

Logs와 profiler artifact는 관련 증거를 확인하는 경로이며 모든 log·profile을 자동으로 진단 입력에 넣는 것은 아닙니다.
서브시스템 간 호출 관계도 계측된 span에 한해 연결하며, endpoint 등록만으로 전체 함수 호출 체인이 생성되지는 않습니다.

아래는 VERL·vLLM과 3FS를 사용하는 배치 예입니다.
기존 VERL 실행부터 연결하고 필요할 때 3FS·multi-node를 추가합니다.
SDK·adapter는 다른 framework에도 이식할 수 있으며, 주된 integration은 VERL입니다.

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
| 1. Collect   > metrics / logs / events / run artifacts                        |
| Prometheus / Loki / existing exporters / ClickHouse queries                   |
| 2. Correlate > run / step / phase + node / role / worker / device / topology  |
| 3. Diagnose  > baseline > candidate > evidence / missing evidence > deep dive |
+-------------------------------------------------------------------------------+
                                 |
                                 +----> Grafana investigation / show_run / profiler
```

Application은 `run_id`로, system·shared service는 시간·node·topology로 비교합니다.
동시 변화는 원인 후보이며 공유 자원의 run별 사용량을 뜻하지 않습니다.
단독 자원 또는 경쟁 부하를 통제한 실험에서 관계를 가장 명확히 해석할 수 있고, 공유 환경의 attribution에는 process·cgroup·client 등 추가 근거가 필요합니다.
3FS ClickHouse 결과는 진단 파일·`show_run`·선택적 Bottleneck Summary에서 보며 전용 실시간 service panel은 없습니다.
[신호 흐름](docs/agent-rl.md#how-the-signals-flow)에서 source별 경로를 확인합니다.

## What Works Today

이 저장소는 이미 실행 중인 VERL·vLLM·Ray와 관측 도구를 연결합니다.
아래 표의 “추가 설정”은 기능이 코드에 있어도 사용자의 배포에서 해당 source를 켜야 값이 생긴다는 뜻입니다.

| 영역 | 현재 제공하는 것 | 사용 조건과 해석 범위 |
| --- | --- | --- |
| VERL trainer | File logger wrapper, 완료 step scalar·stage 변환, step event와 run manifest | 실행 가능한 VERL 명령이 필요합니다. 완료 전 현재 phase는 표시하지 않고 step 시간 구간은 근사치입니다. |
| GPU·host | GPU sampler, Node Exporter, Prometheus·Grafana dashboard | GPU 수집은 NVIDIA와 `nvidia-smi`가 필요합니다. GPU 없는 node는 `ENABLE_GPU_METRICS=0`으로 host만 수집합니다. CPU·network·disk 값은 node 전체입니다. |
| vLLM·Ray | Native Prometheus endpoint 등록과 시간·node 비교 | Endpoint와 metric 이름이 배포에 맞아야 합니다. Ray 전용 Grafana panel은 없고 공유 engine metric은 run별로 자동 분리되지 않습니다. |
| Multi-node | Node별 collector, target 등록, topology manifest, node별 step 상세 비교 | Node 이름과 clock을 맞춰야 합니다. Worker 배치와 원인 관계를 자동으로 추론하지 않습니다. |
| Log·step 탐색 | 선택적 Alloy·Loki 수집, Run Logs, Run Overview의 완료 step 목록과 Timeline | File 경로와 Loki를 설정해야 합니다. Step 경계는 VERL file logger를 바탕으로 추정합니다. |
| Storage·3FS | Filesystem·disk 지표, 선택적 SSD SMART, 3FS ClickHouse 진단 | 3FS service latency는 진단 파일과 선택적 Bottleneck Summary에서 봅니다. 전용 Grafana service panel이나 USRBIO 호출 계측은 제공하지 않습니다. |
| Agent sandbox | 선택적 lifecycle span, sandbox worker cgroup v2 I/O·CPU·memory, local SSD와의 진단 후보 | 외부 runtime 계측과 안정적인 worker cgroup이 필요합니다. 개별 sandbox의 SSD 사용량으로 자동 귀속하지 않습니다. |
| 운영·분석 | 선택적 Grafana alert rule, `show_run`, worker deadline·incremental cache를 사용하는 diagnostics, 짧은 profiler·NCCL 예제 | Alert 수신처는 별도 설정합니다. Cache 한도 초과 시 전체 scan을 사용합니다. Profiler trace는 Grafana에 자동으로 들어가지 않습니다. |
| Cross-layer diagnosis | 같은 run의 이전 step 비교, scope가 붙은 rule candidate, Bottleneck Summary와 Timeline | 진단 sidecar를 켜야 JSON 결과가 생기고 Grafana 조사 화면은 Loki도 필요합니다. Shared signal은 run별 사용량이 아닙니다. |
| 선택적 local LLM diagnosis (experimental) | 수집 메트릭·baseline을 모델이 직접 분석해 자유 형식의 후보와 evidence 참조 생성 | [Ollama와 모델을 별도 설치](docs/local-llm.md)하고 CLI로 호출합니다. Rule catalog 없이 명시적으로 호출하며, 선택한 step의 검토된 결과는 Loki를 통해 Grafana에 표시할 수 있습니다. 자동 호출은 하지 않습니다. |

동작 원리는 [Architecture](docs/architecture.md), 실제 producer·metric·scope는 [수집 범위](docs/metrics.md#what-is-actually-collected)에 정리했습니다.

## Start Here

아래 순서로 연결하고 각 단계의 결과를 확인합니다.
기존 Prometheus·Grafana가 있어도 단일 node 예제로 경로·label을 먼저 확인하는 편이 쉽습니다.

| 순서 | 읽을 문서와 할 일 | 완료 확인 |
| --- | --- | --- |
| 1 | 아래의 [checkout 준비](#prepare-a-checkout) 후 [구현 구조와 설계 원칙](docs/architecture.md)을 읽습니다. | `RUN_ROOT`, collector `OUTPUT_DIR`, monitoring server의 역할을 구분할 수 있습니다. |
| 2 | [Monitoring Guide의 synthetic demo](docs/monitoring.md#try-the-demo)로 수집과 Grafana를 확인합니다. | Start Here에서 Run Overview를 열고 `Exporter targets up`이 0보다 큽니다. |
| 3 | 이미 실행 가능한 VERL 명령을 [VERL 연결 가이드](docs/verl-quickstart.md)에 따라 한 GPU node에 붙입니다. | `show_run`에 완료 step이 나오고 Agent RL에서 step·GPU 값이 보입니다. |
| 4 | [Cross-Layer Integration](docs/agent-rl.md#choose-the-next-source)에서 vLLM·Ray endpoint와 여러 node를 연결합니다. | Prometheus의 `native` target이 up이고 vLLM panel 또는 Ray query에서 실제 값이 나옵니다. |
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
| 완료된 VERL step의 node별 자원·log 비교 | [Run Overview → Timeline](docs/dashboards.md#step-explorer) |
| 내 application의 loss·step 기록 | [Application Metrics Guide](docs/application-metrics.md) |
| 기존 VERL 명령에 telemetry 추가 | [VERL 연결 가이드](docs/verl-quickstart.md) |
| Multi-node, vLLM·Ray·3FS, tool event 연결 | [Cross-Layer Integration Guide](docs/agent-rl.md) |
| SWE-Bench·Terminal-Bench sandbox 관측 | [Agent Sandbox Guide](docs/agent-rl.md#observe-an-agent-sandbox) |
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
| Span | 계측한 작업 하나의 시작·종료, 소요 시간과 상태 |
| Trace | 같은 `trace_id`를 가진 span과 parent 관계로 연결한 호출 흐름 |

Prometheus는 수치 시계열을 저장하고 Grafana는 이를 시각화합니다.
Loki는 선택적인 log 저장소이며 Alloy가 log file을 전송합니다.
각 역할은 기존 도구를 조합하고, 이 프로젝트는 계층 사이의 공통 문맥과 연결 절차를 제공합니다.

## Prepare a Checkout

Shell script와 dashboard를 사용하려면 이 저장소를 직접 checkout합니다.

```bash
git clone https://github.com/daegyu94/xlayer-telemetry.git
cd xlayer-telemetry
bash scripts/setup.sh
. .venv/bin/activate
```

Python 3.10 이상과 `venv`가 필요합니다.
`setup.sh`는 telemetry SDK를 editable 설치하고 CPU test 환경을 준비합니다.
GPU driver·VERL·CUDA와 monitoring 도구는 별도이며, [Monitoring Guide](docs/monitoring.md#prepare-the-host)에서 이어갑니다.

문서의 shell 명령은 별도 설명이 없으면 저장소 루트에서 실행합니다.

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
| `xlayer_telemetry/collectors/` | GPU·host·sandbox cgroup·topology 수집 구현 |
| `xlayer_telemetry/analysis/` | Rule·선택적 LLM 진단, baseline·clock·evidence 품질과 investigation projection |
| `xlayer_telemetry/demos/` | Synthetic metric·diagnosis 생성기 |
| `xlayer_telemetry/` | 공개 event·sandbox SDK, manifest·step 이력, 공통 helper와 조회·실행 도구 |
| [scripts/](scripts/README.md) | 기본 실행 경로와 선택 스크립트 안내; 도구 설치·관측 process·profile·통신 baseline |
| [examples/](examples/README.md) | 실행 예제·선택 안내, local VERL config, 공통 dashboard와 framework 연결 설정 |
| [docs/validation/](docs/validation/README.md) | 검증 당시의 환경·결과·실행하지 않은 범위 |
| [config/](config/README.md) | Metric 공통 어휘·JSON Schema와 rule diagnosis 출력 계약 |

Python import와 `python -m` 명령은 책임별 package 경로를 사용합니다.
이전 루트의 collector·analysis 호환 wrapper는 제거했으며, 외부 코드에서 직접 사용했다면 [module 경로 변경표](docs/architecture.md#python-module-paths)에 따라 바꿉니다.
Metric·event SDK와 저장된 run 데이터의 형식은 그대로입니다.

## Local Validation

```bash
python -m pytest -q
for script in scripts/*.sh; do bash -n "$script"; done
bash scripts/check_tools.sh
```

CPU 테스트는 외부 training framework 없이 실행할 수 있습니다.
CPU regression workflow는 Ubuntu 22.04/Python 3.10과 Ubuntu 24.04/Python 3.12에서 테스트·syntax 검사를 실행하도록 구성합니다.
실제 GPU·backend 통합 검증 범위는 [Validation Records](docs/validation/README.md)에 별도로 기록합니다.
`check_tools.sh`의 미설치 표시는 해당 선택 기능의 도구가 없다는 뜻입니다.
Synthetic demo와 smoke test의 수치는 실제 LLM 학습 성능을 나타내지 않습니다.

## Investigation Quality

Collector는 여러 run을 발견할 수 있으며 wrapper의 telemetry completeness를 workload exit code와 구분합니다.
[Baseline 비교 조건과 sampling quality](docs/diagnosis.md#select-a-comparable-workload), [sandbox device event](docs/agent-rl.md#preserve-device-evidence-in-events), [선택한 step의 optional LLM 진단](docs/local-llm.md#diagnose-a-selected-grafana-step)을 통해 evidence의 비교 가능성과 측정 한계를 확인합니다.
