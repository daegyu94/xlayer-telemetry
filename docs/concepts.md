# Concepts · Context, Scope, Precision

**읽는 목적:** 서로 다른 계층의 숫자를 같은 Run의 원인처럼 읽지 않도록 관측 의미를 구분합니다.

## Collect → Correlate → Diagnose

![설정한 source를 수집하고 context·baseline·evidence로 조사하는 네 단계](figures/diagrams/collect-correlate-diagnose.svg)

| 단계 | 하는 일 | 하지 않는 해석 |
| --- | --- | --- |
| Collect | 설정한 metric·event·log source를 수집/query | 설정만으로 source 가용성 보증 |
| Correlate | 같은 시간·identity·scope를 연결 | 시간 overlap을 Run attribution으로 치환 |
| Diagnose | Comparable baseline과 supporting/counter/missing evidence 비교 | Candidate를 확정 원인으로 표현 |
| Deep Dive | 선택한 구간의 log·span·profiler 확인 | 모든 profile/log 자동 분석 |

## Run / Step / Phase / Span

| Context | 의미 | 주의 |
| --- | --- | --- |
| Run | Application 실행 identity | Shared resource의 소유권이 아님 |
| Step / iteration | Framework가 보고한 완료 boundary | Async trainer update가 모든 rollout/tool을 포함하지 않음 |
| Phase | Rollout·actor/critic update·weight sync 등의 작업 이름 | 완료 duration만으로 실행 순서를 만들지 않음 |
| Span / event | 직접 계측한 실행 구간 / 시점 기록 | Trace/parent는 계측한 관계에 한함 |
| Observer | Step·diagnosis를 기록한 node | Resource node와 다를 수 있음 |
| Resource node / device / engine | 실제 관측 대상 | Entity가 다른 조건을 합쳐 strong evidence로 만들지 않음 |

## Exact / Approximate / Sampled

| 관측 | 경계·해상도 | 조사할 때 |
| --- | --- | --- |
| Exact span | Node clock에서 직접 기록한 start/end | 해당 호출 경계와 clock 확인 |
| Calibrated span | 공통 reference로 mapping한 경계 | `± uncertainty`와 reference 확인 |
| Approximate step | Logger observation과 reported duration으로 추정 | Phase의 정확한 실행 구간으로 읽지 않기 |
| Sampled metric | Scrape 시점의 수치 / rate window | Query step을 줄여도 원본 표본이 늘지 않음 |
| Unknown time | 원래 시각을 알 수 없는 replay 등 | 현재 step으로 표시하거나 정밀 join하지 않기 |

```{admonition} Correlation ≠ attribution
:class: important

Node/device/shared service의 동시 변화는 supporting signal입니다. 특정 Run의 사용량이나 인과관계를 증명하려면 실제 path·entity·계측 관계가 필요합니다.
```

## Scope를 먼저 읽기

![Cgroup·device·shared service evidence를 workload window와 비교하되 scope를 유지하는 원칙](figures/diagrams/evidence-scope.svg)

| Scope | 값의 주인 | 예 |
| --- | --- | --- |
| Application / worker | 계측한 Run·worker | 완료 stage duration |
| Cgroup | 지정한 cgroup subtree | Sandbox CPU/I/O PSI |
| Device / interface | 해당 node의 GPU·disk·NIC | GPU utilization·local disk busy |
| Shared service | Endpoint / engine / service | vLLM queue·3FS distributions |

Docker CLI를 실행한 worker의 cgroup이 container resource를 포함한다고 가정하지 않습니다. Local sandbox SSD와 shared 3FS storage를 구분합니다.

## Missing / No data / Zero

| 표시 | 의미 | 다음 행동 |
| --- | --- | --- |
| `0` | 실제 측정된 zero | Source·counter/rate 품질 확인 후 해석 |
| No data / N/A | Source 미설정 또는 현재 범위에 표본 없음 | 필터·endpoint·실제 metric 이름 확인 |
| Stale | Producer 값이 충분히 최근이 아님 | Age·clock·긴 step·종료 여부 확인 |
| Query error | Backend 조회 실패 | 실패 상태·deadline·response 제한 확인 |
| Missing evidence | 판정·비교·attribution에 필요한 근거 없음 | Candidate와 함께 누락 근거 읽기 |

## 다음

[Baseline과 Evidence](diagnosis.md) · [Time Alignment](time-alignment.md) · [Metric catalogue](metrics.md) · [Architecture](architecture.md)
