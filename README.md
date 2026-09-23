# XLayer Telemetry

VERL 기반 post-training 실행을 GPU·host, rollout engine, network, storage 상태와 함께 해석하는 독립적인 cross-layer telemetry 도구입니다.
Collector, application metric SDK, Grafana dashboard와 실행 분석 도구를 제공하며, 사용자가 운영하는 workload와 cluster에 연결해서 사용합니다.

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
3FS ClickHouse 조회 결과는 실행 진단 파일과 `show_run`에서 확인하며, 3FS 서비스 전용 Grafana dashboard는 제공하지 않습니다.

## Start Here

처음이라면 GPU나 학습 환경 없이 [synthetic demo](docs/monitoring.md#try-the-demo)를 실행해 화면부터 확인합니다.
실제 VERL·vLLM 결과를 보려면 [real-run demo](docs/real-verl-demo.md)를 확인합니다.
실제 VERL 실행은 [VERL Quick Start](docs/verl-quickstart.md)에서 자원 관측과 trainer metric을 함께 연결합니다.
다른 application을 계측하려면 Application Metrics Guide를 사용합니다.

| 원하는 작업 | 안내 |
| --- | --- |
| GPU·host 관측, Prometheus·Grafana 실행 | [Monitoring Guide](docs/monitoring.md) |
| Grafana 화면과 주요 패널 읽는 법 | [Dashboard Guide](docs/dashboards.md) |
| 완료된 VERL step의 자원·log 비교 | [Step Explorer](docs/step-explorer.md) |
| 내 application의 loss·step 기록 | [Application Metrics Guide](docs/application-metrics.md) |
| 기존 VERL 명령에 telemetry 추가 | [VERL Quick Start](docs/verl-quickstart.md) |
| Multi-node, vLLM·Ray·3FS, tool event 연결 | [Cross-Layer Integration Guide](docs/agent-rl.md) |
| 느려진 구간을 조사하고 trace 수집 | [Run Analysis](docs/analysis.md) |
| Metric 이름·단위·label 결정 | [Metrics Contract](docs/metrics.md) |

## Terms Used in This Project

| 용어 | 의미 |
| --- | --- |
| Node / host | 관측 대상 machine |
| Monitoring host | Prometheus·Grafana를 실행하는 machine; 관측 node와 같은 machine이어도 됨 |
| Exporter | metric을 HTTP endpoint로 노출하는 process |
| Collector | JSON이나 장치 상태를 읽어 관측 가능한 metric으로 만드는 process |
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

| 경로 | 내용 |
| --- | --- |
| `xlayer_telemetry/metrics/` | Framework에 독립적인 metric SDK와 textfile 변환 |
| `xlayer_telemetry/adapters/` | Hugging Face Trainer와 VERL file logger 연결 |
| `xlayer_telemetry/` | Resource 수집, manifest·event, 진단과 실행 요약 |
| `scripts/` | 도구 설치, 관측 process 실행, profile·통신 baseline |
| `examples/` | Dashboard, synthetic demo, framework 연결 예시 |
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
