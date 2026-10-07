# Architecture · 먼저 전체 흐름 이해하기

XLayer는 기존 관측 backend 위에서 workload context·baseline·evidence를 연결하는 분석 계층입니다. 실행 방법은 [Quickstart](quickstart.md)에서 시작합니다.

## Mental model

![Application과 shared resource를 수집하고 context와 baseline에서 조사 후보를 만드는 전체 흐름](figures/diagrams/system-overview.svg)

- **Collect:** Wrapper·SDK·collector·native endpoint와 기존 backend query.
- **Correlate:** Run·step·phase·node·worker·device·시간·scope.
- **Diagnose:** Comparable baseline과 supporting/counter/missing evidence.
- **Deep Dive:** Grafana·log·span·기존 profiler로 추가 확인.

```{admonition} 설계 경계
:class: important

XLayer는 새 telemetry backend, sandbox runtime/scheduler, 범용 tracing/profiler 플랫폼을 만들지 않습니다. Shared resource correlation을 Run attribution이나 causality로 표현하지 않습니다.
```

## Runtime architecture

![Monitoring lifecycle, workload 실행, 저장된 Run 분석은 서로 다른 책임이다](figures/diagrams/runtime-architecture.svg)

| 경로 | 책임 | 실패 경계 |
| --- | --- | --- |
| `xltel up/down` | 이 config가 만든 monitoring process 관리 | 기존 Ray·vLLM·사용자 process는 종료하지 않음 |
| `xltel run` | 기존 argv·Python 환경에 wrapper/bridge·선택적 diagnosis 연결 | Workload exit code 유지 |
| Collector / native | 관측값 생산·scrape | Diagnosis 판정과 분리 |
| Prometheus / Loki | Metric / log·event 저장·query | Backend 실패를 0으로 치환하지 않음 |
| 기존 ClickHouse | 선택한 3FS service window 조회 | Shared-service evidence |
| `inspect` / analysis | 저장 artifact와 backend window 비교 | Current health와 historical state 구분 |

## Artifact와 운영 state

| 경로 | 보관하는 것 | 해석 |
| --- | --- | --- |
| Config | 사용자 설정·workload argv | Trusted Bash와 declarative TOML 구분 |
| `state/` | 소유 PID·생성 config·backend data·service log | Monitoring 운영 상태 |
| `runs/` | Manifest·snapshot·step/span·diagnosis·profile artifact | Workload 이력 |

`down`은 소유 process를 종료하고 config·run을 삭제하지 않습니다. Backend retention이 지난 metric은 복구할 수 없어도 저장 artifact는 별도로 읽습니다.

## 지켜야 하는 계약

| 계약 | 이유 |
| --- | --- |
| 기존 argv·환경·종료 코드 보존 | Telemetry 실패와 workload 실패를 분리 |
| PID·start time·boot ID·session·lock 검증 | 소유하지 않은 process 종료 방지 |
| Node/device/engine identity와 scope 유지 | 다른 entity를 하나의 strong evidence로 합치지 않기 |
| Exact / calibrated / approximate / sampled 구분 | 정밀도와 clock uncertainty 보존 |
| Missing / query failure / stale / zero 구분 | 없는 값으로 정상 판정하지 않기 |
| Query·retry·buffer 비용 bounded | Workload와 backend에 독립적인 failure boundary |

## 더 깊이 보기

| 필요한 내용 | Reference |
| --- | --- |
| 한 완료 step의 파일·metric 변환 | [Completed-step path](architecture-reference.md#follow-one-completed-step) |
| Python package / module path | [Package responsibilities](architecture-reference.md#package-responsibilities) |
| Failure / retention / clock / diagnosis 한계 | [Operating limits](architecture-reference.md#failure-boundaries-and-operating-limits) |
| Source 확장과 eBPF 판단 | [Add one source](architecture-reference.md#add-one-source-at-a-time) |

## 다음

[Context / Scope](concepts.md) · [Metric source](metrics.md) · [전체 구현 Reference](architecture-reference.md)

:::{container} xlayer-legacy-links

<a id="how-xlayer-telemetry-works" class="xlayer-legacy-anchor"></a>

[How XLayer Telemetry Works](architecture-reference.md#how-xlayer-telemetry-works)

<a id="why-xlayer-exists" class="xlayer-legacy-anchor"></a>

[Why XLayer Exists](concepts.md)

<a id="requirements-and-invariants" class="xlayer-legacy-anchor"></a>

[Requirements and Invariants](architecture-reference.md#requirements-and-invariants)

<a id="operational-and-historical-state" class="xlayer-legacy-anchor"></a>

[Operational and Historical State](architecture-reference.md#operational-and-historical-state)

<a id="package-responsibilities" class="xlayer-legacy-anchor"></a>

[Package Responsibilities](architecture-reference.md#package-responsibilities)

<a id="python-module-paths" class="xlayer-legacy-anchor"></a>

[Python Module Paths](architecture-reference.md#python-module-paths)

<a id="when-to-revisit-ebpf" class="xlayer-legacy-anchor"></a>

[When to Revisit eBPF](architecture-reference.md#when-to-revisit-ebpf)

<a id="the-basic-path" class="xlayer-legacy-anchor"></a>

[The Basic Path](concepts.md#collect--correlate--diagnose)

<a id="follow-one-completed-step" class="xlayer-legacy-anchor"></a>

[Follow One Completed Step](architecture-reference.md#follow-one-completed-step)

<a id="what-each-file-is-for" class="xlayer-legacy-anchor"></a>

[What Each File Is For](architecture-reference.md#what-each-file-is-for)

<a id="failure-boundaries-and-operating-limits" class="xlayer-legacy-anchor"></a>

[Failure Boundaries and Operating Limits](architecture-reference.md#failure-boundaries-and-operating-limits)

<a id="failure-isolation" class="xlayer-legacy-anchor"></a>

[Failure Isolation](architecture-reference.md#failure-isolation)

<a id="add-one-source-at-a-time" class="xlayer-legacy-anchor"></a>

[Add One Source at a Time](architecture-reference.md#add-one-source-at-a-time)

<a id="match-identity-and-time" class="xlayer-legacy-anchor"></a>

[Match Identity and Time](architecture-reference.md#match-identity-and-time)

<a id="multi-node-correlation-boundary" class="xlayer-legacy-anchor"></a>

[Multi-node Correlation Boundary](architecture-reference.md#multi-node-correlation-boundary)

<a id="design-principles" class="xlayer-legacy-anchor"></a>

[Design Principles](architecture-reference.md#design-principles)

:::
