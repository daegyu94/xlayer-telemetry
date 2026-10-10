# XLayer Telemetry

:::{container} xlayer-hero

**Agent RL을 위한 cross-layer observability**

VERL의 느린 step을 GPU·vLLM·network·storage·sandbox 관측과 함께 조사합니다.

[설치 시작](quickstart.md) · [GPU 없이 Demo 실행](demo.md) · [문제 해결](runbooks.md)

:::

## 지금 할 일

:::{container} xlayer-card-grid

[**처음 사용합니다** GPU 없이 수집과 조사 흐름을 익힙니다. **Demo 실행 →**](demo.md)

[**VERL을 사용 중입니다** 기존 명령과 Python 환경을 유지해 연결합니다. **Connect VERL →**](verl-quickstart.md)

[**Run이 느립니다** 느린 Step을 고르고 baseline과 evidence를 비교합니다. **Slow Step Investigation →**](dashboards.md)

:::

## XLayer가 하는 일

| 단계 | 입력과 결과 | 다음 행동 |
| --- | --- | --- |
| **Collect** | VERL·native endpoint·GPU/host·log/event | [필요한 source 연결](agent-rl.md) |
| **Correlate** | Run·step·node·worker·시간·scope | [Context 이해](concepts.md) |
| **Diagnose** | Baseline·candidate·supporting/counter/missing evidence | [Evidence 읽기](diagnosis.md) |
| **Deep Dive** | 선택한 구간의 log·span·기존 profiler | [후보 검증](deep-dive.md) |

```{admonition} Scope
:class: important

같은 시간의 shared resource 변화는 correlation입니다. 특정 Run의 사용량이나 원인으로 자동 귀속하지 않습니다.
```

## 읽는 순서와 성공 기준

| 지금 단계 | 최소 경로 | 여기까지 확인하면 다음으로 |
| --- | --- | --- |
| 설치 | [Quickstart](quickstart.md) | CLI help·config validation 성공 |
| 연결 | [Demo](demo.md) 또는 [VERL](verl-quickstart.md) → [Source 선택](agent-rl.md) | 실제 metric 또는 완료 step artifact |
| 관측 | [GPU & Host](monitoring.md) → [Logs](logs-events.md) | Source age·Run/observer·시간 범위 확인 |
| 조사 | [Slow Step](dashboards.md) → [Evidence](diagnosis.md) | 비교 가능성·지지/반대/누락 근거 구분 |
| 검증 | [Deep Dive](deep-dive.md) | 같은 구간의 subsystem·log·span으로 후보 검토 |

## 문서를 찾는 방법

| 읽으려는 것 | 경로 |
| --- | --- |
| 처음부터 따라 하기 | Get Started의 [Quickstart](quickstart.md)·[Demo](demo.md)·[VERL 연결](verl-quickstart.md) |
| 특정 subsystem 연결 | Observe의 [Source 선택](agent-rl.md) |
| 연결 / No data 해결 | [문제 해결 Runbook](runbooks.md) |
| 성능 문제 해결 | Investigate의 [증상별 시작점](dashboards.md#증상으로-시작하기) |
| 결과 해석 | Diagnose와 [Concepts](concepts.md) |
| 설정·단위·구현 조회 | Reference의 [Metrics](metrics.md)·[CLI](cli.md)·[Configuration](configuration.md)·[Architecture](architecture.md) |
| App 화면 / N/A 확인 | [선택적 Grafana App PoC](grafana-scenes-poc.md)·[UI source coverage](ui-telemetry-coverage.md) |
| 개발·기록 확인 | Maintainers의 [검증 기록](validation/README.md) |

```{toctree}
:hidden:
:caption: Get Started

설치 / Quickstart <quickstart>
GPU 없이 Demo <demo>
기존 VERL 연결 <verl-quickstart>
```

```{toctree}
:hidden:
:caption: Observe

필요한 Source 고르기 <agent-rl>
GPU & Host 연결 <monitoring>
vLLM / Ray <native-sources>
KV / Storage <kv-storage>
Sandbox <sandbox>
Logs & Events <logs-events>
Application SDK <application-metrics>
Multi-node <multi-node>
```

```{toctree}
:hidden:
:caption: Investigate

문제 해결 / No data <runbooks>
Slow Step Investigation <dashboards>
Subsystem / Deep Dive <deep-dive>
선택적 Grafana App <grafana-scenes-poc>
Run Explorer / Comparison <run-comparison>
```

```{toctree}
:hidden:
:caption: Diagnose

Baseline / Candidate / Evidence <diagnosis>
Optional Local LLM <local-llm>
```

```{toctree}
:hidden:
:caption: Concepts

Context / Scope / Precision <concepts>
Clock / Time Alignment <time-alignment>
```

```{toctree}
:hidden:
:caption: Reference

Metrics <metrics>
CLI <cli>
Configuration <configuration>
Architecture <architecture>
UI Telemetry / MFU·Policy·Status <ui-telemetry-coverage>
Storage correlation 계약 <storage-correlation>
상세 Reference <reference>
```

```{toctree}
:hidden:
:caption: Maintainers

Development / Docs <maintainers>
실환경 기록 <real-verl-demo>
Dashboard design <grafana-ui-ux-review>
Behavior signature / Triggered profiling 연구 <behavior-signature-research>
System / Diagnosis review <system-review>
Documentation design <documentation-ux-review>
검증 기록 <validation/README>
```

<a id="start-here" class="xlayer-legacy-anchor"></a>

[Start Here: 현재 작업에 맞는 guide](quickstart.md)

<a id="prepare-a-checkout" class="xlayer-legacy-anchor"></a>

[Prepare a Checkout: 설치 절차](quickstart.md#1-checkout과-python-환경-준비)
