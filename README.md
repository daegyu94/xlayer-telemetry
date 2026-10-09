# XLayer Telemetry

VERL 기반 Agent RL의 느린 Step을 GPU·vLLM·Ray·network·storage·sandbox 관측과 함께 조사합니다. **Collect → Correlate → Diagnose** 순서로 baseline·bottleneck candidate·supporting/counter/missing evidence를 확인합니다.

**[문서 웹사이트](https://daegyu94.github.io/xlayer-telemetry/)** · **[설치](docs/quickstart.md)** · **[GPU 없이 Demo](docs/demo.md)** · **[기존 VERL 연결](docs/verl-quickstart.md)**

## 지금 할 일

| 상황 | 시작할 곳 | 완료 확인 |
| --- | --- | --- |
| 처음 사용 / GPU 없음 | [Demo 실행](docs/demo.md) | Synthetic Step 127 → What changed? → Evidence |
| 기존 VERL 명령이 동작함 | [VERL 연결](docs/verl-quickstart.md) | 첫 완료 step·snapshot·telemetry health |
| 자원 metric만 필요함 | [GPU & Host](docs/monitoring.md) | 등록한 collector target과 실제 metric |
| Run이 느림 | [Slow Step Investigation](docs/dashboards.md) | Comparable baseline·candidate·같은 구간의 Timeline |
| No data / 연결 오류 | [문제 해결 Runbook](docs/runbooks.md) | Process → source → age → filter 순서 점검 |

## 설치

Python 3.10 이상·Git·`venv`가 필요합니다. Managed monitoring은 Linux ARM64/x86_64에서 실행합니다.

<a id="prepare-a-checkout"></a>

```bash
git clone https://github.com/daegyu94/xlayer-telemetry.git
cd xlayer-telemetry
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
xltel --help
xltel init
xltel config validate
```

**정상 결과:** help에 `up`, `run`, `inspect`, `sources`가 표시되고 config validation이 exit code `0`으로 끝납니다. CLI 설치는 VERL·CUDA·monitoring binary를 설치하지 않습니다. 도구 설치와 backend 시작은 [Quickstart 이후 경로](docs/quickstart.md#3-다음-경로-선택)를 따릅니다.

기존 VERL 명령은 기존 Python 환경으로 실행합니다.

```bash
# Monitoring 준비 후, ...을 이미 동작하는 VERL argument로 바꿉니다.
xltel run -- /path/to/verl-env/bin/python -m verl.trainer.main_ppo ...
xltel inspect RUN_ID
```

**정상 결과:** 원래 workload의 종료 코드와 별도로 완료 step·stage·telemetry completeness를 확인할 수 있습니다. [연결 절차와 정상 결과](docs/verl-quickstart.md#3-verify)를 먼저 읽습니다.

## 무엇을 연결할 수 있나

<a id="what-works-today"></a>

| Source | 제공하는 관측 | 연결 / 해석 기준 |
| --- | --- | --- |
| VERL wrapper / SDK | 완료 step·scalar·stage, 직접 기록한 span/event | [VERL](docs/verl-quickstart.md) · [SDK](docs/application-metrics.md) |
| GPU / Host | Utilization·memory·CPU·network/RDMA·diskstats·filesystem | [Monitoring](docs/monitoring.md). Node/device 전체 관측 |
| vLLM / Ray | Native queue·latency·cache·task/resource signals | [Native source](docs/native-sources.md). 배포별 metric 지원 확인 |
| Mooncake / Storage | Connector RPC·DFS·declared DS/MDS·local I/O·선택적 3FS collection evidence | [KV / Storage](docs/kv-storage.md). Client·service·device 통계 구분 |
| Sandbox | Lifecycle span·worker cgroup CPU/memory/I/O pressure | [Sandbox](docs/sandbox.md). GPU-host local SSD와 shared backend 구분 |
| Logs / Diagnosis | Loki Step 탐색·Current/Baseline·candidate·evidence | [Logs](docs/logs-events.md) · [Diagnosis](docs/diagnosis.md). Source 설정 필요 |

> **Correlation ≠ attribution ≠ causality.** Shared resource의 동시 변화는 Run 사용량이나 확정 원인이 아닙니다. Exact/calibrated span, approximate step, sampled metric과 missing/zero를 구분합니다. [해석 기준](docs/concepts.md)을 확인하세요.

여러 Job의 application metric·log·span은 Run/worker identity로 구분합니다. GPU·host·Ray·Mooncake·storage에는 Run ID가 없거나 공유 scope일 수 있으므로, 같은 시간의 pressure를 특정 Job의 원인으로 단정하지 않습니다. `strong_signal`도 관측된 조건의 강도이며 자원 소유권을 뜻하지 않습니다. GPU 없이 세 Job의 겹침·누락·stale source와 실제 query/diagnosis를 확인하려면 [Multi-job Live Demo](docs/demo.md#multi-job-live-demo)를 사용하세요.

Canonical Metric 정의는 전체 자동 수집 목록이 아닙니다. N/A의 source 조건과 Defined / Collection / Query / Diagnosis / Dashboard 단계를 [Subsystem Metric Coverage](docs/subsystem-metrics.md)에서 확인하세요.

여러 Rollout Replica는 [선언형 inventory](docs/diagnosis-reference.md#declared-rollout-replica-inventory)로 대표 endpoint와 배치 node를 구분합니다. Replica별 일부 metric 누락·freshness·clock·baseline을 보존하며, 배치 선언만으로 request routing이나 trainer의 sample 소비 관계를 추정하지 않습니다.

## 어떻게 조사하나

<a id="why-cross-layer-telemetry"></a>
<a id="start-here"></a>

![Run·step·candidate·evidence·같은 interval의 Timeline과 subsystem으로 이어지는 조사 흐름](docs/figures/diagrams/investigation-flow.svg)

| 질문 | 다음 행동 |
| --- | --- |
| 어떤 Step이 느린가? | Run Overview에서 완료 Step 선택 |
| Baseline과 무엇이 달라졌나? | What changed?의 unit·entity·scope 확인 |
| 어떤 근거가 후보를 지지/반박하나? | Supporting / counter / missing evidence 확인 |
| 같은 구간에서 어디를 더 볼까? | Timeline → Compute / vLLM / Storage / Logs |

3FS ClickHouse는 선택적 심층 진단 source입니다. 원본 report p99·counter·collection time-series는 [Storage Correlation](docs/storage-correlation.md)에서 해석하며, service와 특정 SSD/Run의 실제 요청 경로를 추정하지 않습니다.

## 문서 찾기

<a id="terms-used-in-this-project"></a>
<a id="scope-and-layout"></a>
<a id="investigation-quality"></a>

| 목적 | 기준 문서 |
| --- | --- |
| 용어·scope·정밀도 | [Concepts](docs/concepts.md) |
| 명령·config·운영 오류 | [CLI](docs/cli.md) · [Configuration](docs/configuration.md) · [Runbooks](docs/runbooks.md) |
| Metric source·unit·label·coverage | [Metrics](docs/metrics.md) |
| Optional Grafana App / Scenes | [App guide](docs/grafana-scenes-poc.md). 기존 V1과 상세 dashboard 유지 |
| 전체 데이터 경로·구현 계약 | [Architecture](docs/architecture.md) · [상세 Reference](docs/reference.md) |
| Clock·backend·attribution 한계 | [Correlation limitations](docs/correlation-limitations.md) |
| 실제 검증 범위 | [Validation 기록](docs/validation/README.md). Synthetic와 실장비 구분 |

## SDK / 개발

<a id="python-package-usage"></a>
<a id="local-validation"></a>

| 작업 | 명령 / 다음 문서 |
| --- | --- |
| 다른 application Python에 SDK 설치 | `python -m pip install /path/to/xlayer-telemetry` → [SDK 연결](docs/application-metrics.md) |
| 개발 dependency 설치 | `python -m pip install -r requirements.txt -e .` |
| CPU regression | `python -m pytest -q` |
| Syntax / 선택 도구 / docs build | [Maintainers](docs/maintainers.md) |
| Package / module 책임·경로 | [Architecture Reference](docs/architecture-reference.md#package-responsibilities) |

XLayer는 cluster 배포·workload scheduling·sandbox runtime을 관리하지 않습니다. 기존 argv·환경·종료 코드를 보존하며 telemetry 실패를 workload 결과와 분리합니다. [운영 한계](docs/architecture-reference.md#failure-boundaries-and-operating-limits)를 확인하세요.
