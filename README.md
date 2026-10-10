# XLayer Telemetry

VERL 기반 Agent RL의 느린 Step을 GPU·vLLM·Ray·network·storage·sandbox 관측과 함께 조사합니다. **Collect → Correlate → Diagnose** 순서로 baseline·bottleneck candidate·supporting/counter/missing evidence를 확인합니다.

**[문서 웹사이트](https://daegyu94.github.io/xlayer-telemetry/)** · **[설치](docs/quickstart.md)** · **[GPU 없이 Demo](docs/demo.md)** · **[기존 VERL 연결](docs/verl-quickstart.md)**

## Development Status

현재 핵심 기능의 PoC 구현과 CPU/Synthetic·제한된 single-node Agent RL validation을 수행한 단계입니다. 다음 핵심 목표는 실제 physical multi-node·concurrent multi-job / rollout replica 환경의 validation입니다. 기능 구현과 분산 실환경 validation 완료는 구분합니다.

| Area | Status | Evidence / Scope |
| --- | --- | --- |
| Core Telemetry & Diagnosis | Implemented (PoC) | [Architecture](docs/architecture.md) · [Correlation limitations](docs/correlation-limitations.md) |
| CPU / Synthetic Validation | Validated (bounded scenarios) | [Validation scope](docs/validation/README.md#current-validation-scope). 실제 모델 동시 학습을 뜻하지 않음 |
| Single-Node Agent RL Validation | Partially Validated | [기존 실측 기록](docs/real-verl-demo.md#recorded-agent-rl-run). 한 host·한 모델·짧은 실행 |
| Physical Multi-Node / Multi-Job Agent RL | Pending | [Planned Validation](docs/validation/README.md#planned-validation) |
| Operational Hardening | In Progress; long-run validation Pending | CPU fault/recovery 회귀 검증은 있음. 분산 운영 안정성 보증은 아님 |

### Next Milestones

- [ ] Physical multi-node에서 clock quality·identity·telemetry 수집·correlation 검증
- [ ] Concurrent multi-job·multi-model·rollout replica의 격리·coverage·조사 흐름 검증
- [ ] 실제 Agent RL workload와 Mooncake / 3FS storage 경로의 end-to-end validation
- [ ] 실제 장애·복구·장시간 실행에서 overhead·query budget·retention·데이터 정확성 검증

완료 기준과 검증 환경은 [Planned Validation](docs/validation/README.md#planned-validation)에서 관리합니다. 기존 backend 개별 실측·VM·Synthetic 결과는 해당 환경의 근거이며 위 Pending 항목을 대신하지 않습니다.

## Get Started

<a id="지금-할-일"></a>

| 상황 | 시작할 곳 | 완료 확인 |
| --- | --- | --- |
| 처음 사용 / GPU 없음 | [Demo 실행](docs/demo.md) | Synthetic Step 127 → What changed? → Evidence |
| 기존 VERL 명령이 동작함 | [VERL 연결](docs/verl-quickstart.md) | 첫 완료 step·snapshot·telemetry health |
| 자원 metric만 필요함 | [GPU & Host](docs/monitoring.md) | 등록한 collector target과 실제 metric |
| 여러 Node를 등록해야 함 | [Cluster Inventory](docs/multi-node.md#1-configure) | 한 TOML/JSON에서 기존 설정 생성 → Offline/scrape/clock 검사 |
| App 설치 / 여러 실험 비교 | [App 설치](docs/app-deployment-reference.md#설치--상태--update) · [Run Explorer](docs/run-comparison.md) | 검증한 package·저장 artifact·metric별 비교 조건 확인 |
| Run이 느림 | [Slow Step Investigation](docs/dashboards.md) | Comparable baseline·candidate·같은 구간의 Timeline |
| No data / 연결 오류 | [문제 해결 Runbook](docs/runbooks.md) | Process → source → age → filter 순서 점검 |

## Installation

<a id="설치"></a>

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

## Telemetry Coverage

<a id="무엇을-연결할-수-있나"></a>

<a id="what-works-today"></a>

| Source | 제공하는 관측 | 연결 / 해석 기준 |
| --- | --- | --- |
| VERL wrapper / SDK | 완료 step·scalar·stage, 직접 기록한 span/event | [VERL](docs/verl-quickstart.md) · [SDK](docs/application-metrics.md) |
| GPU / Host | Utilization·memory·CPU·network/RDMA·diskstats·filesystem | [Monitoring](docs/monitoring.md). Node/device 전체 관측 |
| vLLM / Ray | Native queue·latency·cache·task/resource signals | [Native source](docs/native-sources.md). 배포별 metric 지원 확인 |
| Mooncake / Storage | Connector RPC·DFS·declared DS/MDS·local I/O·선택적 3FS collection evidence | [KV / Storage](docs/kv-storage.md). Client·service·device 통계 구분 |
| Local / 직접 관리하는 Dedicated Sandbox | 명시 lifecycle span·설정한 worker cgroup CPU/memory/I/O pressure | [Sandbox](docs/sandbox.md). GPU-host local SSD와 shared backend 구분 |
| Remote Tool / Reward 호출 | 명시 SDK client span·caller가 기록한 outcome/retry event | [Support boundary](docs/sandbox.md#v1-support-boundary). Remote 내부 모니터링·자동 SandboxFusion 연동은 미지원 |
| Logs / Diagnosis | Loki Step 탐색·Current/Baseline·candidate·evidence | [Logs](docs/logs-events.md) · [Diagnosis](docs/diagnosis.md). Source 설정 필요 |

> **Correlation ≠ attribution ≠ causality.** Shared resource의 동시 변화는 Run 사용량이나 확정 원인이 아닙니다. Exact/calibrated span, approximate step, sampled metric과 missing/zero를 구분합니다. [해석 기준](docs/concepts.md)을 확인하세요.

**Remote Sandbox 내부 모니터링 — Not Supported.** Local/Dedicated resource 관측과 Remote 호출의 client-side telemetry를 구분합니다. SandboxFusion·Managed/External provider의 내부 queue·CPU/memory/I/O·execution lifecycle·cross-service attribution은 지원하지 않습니다. VERL은 외부 서비스를 그대로 사용할 수 있고 기존 SDK로 호출 경계를 기록할 수 있지만, 호출 시간·client error는 remote 서버의 내부 병목이나 작업 성공을 뜻하지 않습니다.

여러 Job의 application metric·log·span은 Run/worker identity로 구분합니다. GPU·host·Ray·Mooncake·storage에는 Run ID가 없거나 공유 scope일 수 있으므로, 같은 시간의 pressure를 특정 Job의 원인으로 단정하지 않습니다. `strong_signal`도 관측된 조건의 강도이며 자원 소유권을 뜻하지 않습니다. GPU 없이 세 Job의 겹침·누락·stale source와 실제 query/diagnosis를 확인하려면 [Multi-job Live Demo](docs/demo.md#multi-job-live-demo)를 사용하세요.

Canonical Metric 정의는 전체 자동 수집 목록이 아닙니다. N/A의 source 조건과 Defined / Collection / Query / Diagnosis / Dashboard 단계를 [Subsystem Metric Coverage](docs/subsystem-metrics.md)에서 확인하세요.

여러 Rollout Replica는 [선언형 inventory](docs/diagnosis-reference.md#declared-rollout-replica-inventory)로 대표 endpoint와 배치 node를 구분합니다. Replica별 일부 metric 누락·freshness·clock·baseline을 보존하며, 배치 선언만으로 request routing이나 trainer의 sample 소비 관계를 추정하지 않습니다.

[선택적 Router / Serving SDK](docs/diagnosis-reference.md#router-membership-and-serving-lifecycle)는 read-only membership과 완료 hook의 sleep/wake·workload·worker-applied policy를 읽습니다. Endpoint UP와 serving 정상은 다르며, 상태가 없으면 Unknown으로 남깁니다. Separate Async의 다음-update sample decision도 보고된 logger key가 있을 때만 표시합니다.

## Investigation Workflow

<a id="어떻게-조사하나"></a>

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

## Documentation

<a id="문서-찾기"></a>

<a id="terms-used-in-this-project"></a>
<a id="scope-and-layout"></a>
<a id="investigation-quality"></a>

| 목적 | 기준 문서 |
| --- | --- |
| 용어·scope·정밀도 | [Concepts](docs/concepts.md) |
| 명령·config·운영 오류 | [CLI](docs/cli.md) · [Configuration](docs/configuration.md) · [Runbooks](docs/runbooks.md) |
| Metric source·unit·label·coverage | [Metrics](docs/metrics.md) |
| XLayer Telemetry Dashboard | [Dashboard guide](docs/grafana-scenes-poc.md). App/Scenes 진입점과 기존 상세 dashboard |
| 전체 데이터 경로·구현 계약 | [Architecture](docs/architecture.md) · [상세 Reference](docs/reference.md) |
| Clock·backend·attribution 한계 | [Correlation limitations](docs/correlation-limitations.md) |
| 실제 검증 범위 | [Validation 기록](docs/validation/README.md). Synthetic와 실장비 구분 |

## SDK & Development

<a id="sdk--개발"></a>

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
