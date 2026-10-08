# Grafana App Reference

**찾아보는 목적:** App의 query 재사용·URL context·observation 조건과 운영 한계를 확인합니다. 설치·조사 절차는 [App guide](grafana-scenes-poc.md)를 따릅니다.

## Architecture

![Scenes는 Grafana query runtime을 재사용하고 상세 조사에는 기존 dashboard로 이동한다. ClickHouse는 기존 diagnosis 경로에서만 조회한다](figures/diagrams/grafana-scenes-app.svg)

- `AppPlugin`이 sidebar에 XLayer 진입점 하나를 등록합니다. 화면 간 이동은 context를 유지하는 App navigation에서 제공합니다. Static sidebar 링크에 query state를 기대하지 않습니다.
- `SceneApp` / `SceneAppPage`가 routing과 URL state를 관리합니다.
- 상단 Run Context에 `EmbeddedScene`의 native variable·time picker·refresh picker를 배치합니다. Step 선택은 저장된 완료 record와 실제 window를 선택하는 XLayer component입니다.
- `SceneQueryRunner`는 provisioned panel의 targets·datasource를 읽습니다. 별도 Prometheus/Loki client는 없습니다.
- `VizPanel` / `SceneDataTransformer`는 기존 timeline과 관련 graph를 렌더링합니다.
- XLayer component는 KPI·Matrix·comparison·candidate·evidence·navigation만 담당합니다.

Query contract는 dashboard에 한 번만 정의합니다. App에는 panel ID/ref ID를 선택하는 얇은 catalogue만 있으며, datasource remapping 이후의 JSON을 읽습니다.

## Query / Context

| 기존 자산 | 재사용 방식 |
| --- | --- |
| `examples/dashboards/*.json` | Query·단위·scope·transform의 수정 원본 |
| `provision_dashboards.py` / `dashboard_views.py` | 활성 datasource와 Loki 여부를 반영한 실제 dashboard를 API로 읽음 |
| Guided / Overview / Focus | 최신 구조는 **Run Overview + Cross-Layer Signals**. Retired Focus를 복구하지 않음 |
| Run / observer / resource | `run_id`·`source_node`·`node`를 구분. Step은 `record_id`와 실제 window로 선택 |
| Bottleneck Summary | Summary / comparison / candidate / evidence JSONL을 그대로 소비 |
| Cross-Layer Timeline | Exact/calibrated span lane와 approximate Step lane의 native panel·transform 재사용 |
| Stage / Compute / Storage / Logs | Deep Dive 링크. 상세 graph를 App 안에 전부 복제하지 않음 |
| 3FS / ClickHouse | 기존 diagnosis가 조회한 shared-service evidence. App의 직접 ClickHouse query는 없음 |

- Run은 `run_id`, 관측한 node는 `source_node`, resource node는 `node`로 구분합니다.
- 완료 Step은 `record_id`와 저장된 실제 window로 선택합니다.
- `candidate_id`는 Deep Dive workspace에 보존합니다. 다른 Step을 선택하면 기존 candidate 선택을 지웁니다.
- Static Grafana sidebar 링크보다 context를 전달하는 App navigation을 사용합니다.

### 명시 workload context

| Run Overview panel | Source |
| --- | --- |
| 40 · MFU | `training_model_flops_utilization_ratio` · framework-reported stage |
| 41 · Policy | 같은 Run의 단일 fresh `policy_version` source 또는 선택 record의 명시 version |
| 42 · Wrapped command | Fresh `telemetry_wrapped_workload_state`와 wrapper 보고 시각 |
| 44 · Worker Step | 집계하지 않은 worker별 completed Step identity |

## Phase × Subsystem

| Cell 상태 | 값의 근거 | 표시하지 않는 것 |
| --- | --- | --- |
| Sampled 숫자 | 같은 Run·Step의 유일한 exact/calibrated span, 같은 resource node, window 안의 query evaluation | Phase 사용량·causal attribution |
| Select entity | 같은 node에서 여러 GPU/engine을 관측 | 임의 average/max로 만든 phase 값 |
| Rolling 숫자 | Phase 안에서 평가한 rate/quantile와 실제 query lookback 표시 | Phase-exclusive 값·phase baseline delta |
| N/A | Phase interval 없음 또는 불명확 | 보고된 stage duration으로 만든 가짜 boundary |
| Step evidence | 저장 diagnosis의 full-Step window | Phase delta / phase baseline |
| Ray 숫자 | 명시 선택한 Session / State의 query 관측 | Session 값을 worker/phase에 귀속·서로 다른 state 합산 |
| MFU 별도 관측 | Framework가 보고한 completed stage scalar | GPU utilization으로 추정하거나 sampled resource cell에 합친 MFU |

```{admonition} Precision / Scope
:class: important

Exact는 application span 경계의 속성입니다. Resource metric은 sampled이며 query evaluation 시각과 원본 scrape 시각이 같다고 보장하지 않습니다. Exact node-clock span의 cross-clock calibration은 미확인일 수 있습니다. Calibrated uncertainty가 없거나 phase보다 큰 경우 숫자를 연결하지 않습니다. Correlation ≠ attribution ≠ causality.
```

Gauge의 phase delta는 같은 producer·worker·node·phase·entity·unit·clock reference와 명시 workload fingerprint를 확인합니다. 기존 `matched_configured_fields`와 양쪽 두 개 이상의 query 관측이 필요하며 rolling/session delta는 만들지 않습니다.

Baseline query는 저장된 명시 구간에만 실행합니다. 별도 URL sync 없는 native query range를 사용하므로 선택 Step의 `from/to`와 Deep Dive context는 유지됩니다. Node/device 값은 같은 phase 시간의 관측이며 사용량 attribution이 아닙니다.

Gauge 숫자는 query 평가값의 window mean입니다. Query 시각과 raw scrape 시각이 같다고 보장하지 않으며 phase 전환 근처에는 이전 sample이 유지될 수 있습니다. Baseline `0`의 relative delta는 숨기고 absolute 값은 보존합니다.

### 멀티worker와 추가 phase

| 선택 | 동작 / 해석 |
| --- | --- |
| Execution path | 같은 phase에 여러 interval이 있으면 ambiguous. Span을 합치거나 첫 worker를 선택하지 않음 |
| Execution worker | Node·producer·role·worker·rank·GPU가 선언된 identity로 필터. Resource node/device 선택은 명시 span context에서만 변경 |
| Worker Comparison | Unique rollout call duration. 같은 operation·scope·producer·role·fingerprint의 3개 이상 cohort만 peer median 비교 |
| Critic / reference / 기타 phase | 현재 Step 안의 실제 measured span이 있을 때 column 추가. Actor update와 별도 표시; 최대 16 phase |
| Scope가 다른 signal | Shared / Session / Rolling / Missing을 유지. Worker 값으로 변환하지 않음 |

Worker selection은 `phase_worker` URL state로 보존하며 observer `source_node`를 resource node로 바꾸지 않습니다. GPU는 span이 device identity를 명시하고 sample이 같은 node/window에 있을 때만 연결합니다. 다른 node의 Step/span clock은 공통 calibrated reference와 uncertainty가 확인되어야 phase query window로 연결합니다. Unaligned worker의 monotonic call duration은 비교할 수 있지만 해당 timestamp로 GPU를 연결하지 않습니다.

## KPI / Evidence

| 표시 | 읽는 방법 |
| --- | --- |
| KPI sparkline | 같은 Run/scope/entity의 실제 history가 두 관측 이상일 때만 표시 |
| Current / Baseline / Delta | 기존 diagnosis projection의 unit·entity·window·comparability를 유지 |
| System Signals | 저장된 candidate 상태. Collector UP 기반 health 판정이 아님 |
| System Pressure | Full entity tuple 유지. 명시 Step evidence 일치 → pending/failed state → 같은 단위의 baseline 변화 → signal 순으로 최대 두 entity 표시 |
| Recorded errors | 조회한 span의 status coverage·count·5,000 record limit. 전체 workload error rate가 아님 |
| Detailed Metrics | Canonical native panel. Local I/O mean·KV RPC p95와 3FS service p99를 구분 |

### KPI / Event 연결 조건

- Application KPI는 canonical query의 observer filter와 full label tuple을 보존합니다. 여러 Run/worker가 반환되면 **Multiple entities**이며 대표값이나 합계를 만들지 않습니다.
- 현재 application KPI는 동일 producer·worker·Run·node·observer·rank의 age를 확인합니다. 누락·충돌이면 **Freshness unknown**, age 초과면 **Stale**로 값을 숨깁니다. 저장 completed-Step observation은 live freshness와 구분합니다.
- Recent Event는 cluster·Run·Step과 명시 `step_record_id`, execution identity 또는 실제 parent chain으로 연결합니다. 같은 번호의 여러 record가 남으면 **Choose Step**, 연결 근거가 없으면 **Step link unavailable**입니다.
- Run/time 전환 중 Loading/Error가 보관한 이전 frame을 현재 custom KPI·Step·evidence로 읽지 않습니다. Native graph의 loading 표시는 Grafana가 관리합니다.
- Pressure supporting match는 선택 Step의 cluster·record·observer·window, resource entity·unit·sample 시각을 함께 확인합니다.
- GPU 낮은 utilization은 자동 fault가 아닙니다. Pressure 카드의 entity 순위와 **Inspect entity** 링크는 조사 우선순위이며 causal verdict가 아닙니다.
- **Diagnosis / Query Coverage**는 현재 provider의 error·empty·optional unavailable·native target 수·Grafana가 보고한 마지막 query elapsed를 보여줍니다. Coverage 표시 때문에 추가 query를 활성화하지 않습니다.

MFU·policy version·wrapper 상태의 producer와 missing 조건은 [UI Telemetry Reference](ui-telemetry-coverage.md)에서 관리합니다. Trainer version을 rollout applied version으로, wrapper command 상태를 전체 async Run 완료로 바꾸지 않습니다.

## Dashboard와 Scenes 비교

| 기준 | 기존 Dashboard | Scenes PoC |
| --- | --- | --- |
| 상세 metric·Inspect·Explore | 이미 구현됨 | 재사용 |
| Query 유지보수 | Canonical JSON/provisioning | 같은 JSON을 runtime에서 읽음 |
| Run 조사 진입 | Dashboard별 링크 | App navigation 한곳 |
| Scope 조건부 표시 | Text/table 제한 | 숫자/NA/rolling/Step evidence를 조건부 표시 |
| Phase × System | 일반 panel/table 조합 | Entity/window gate가 있는 custom component |
| 운영 비용 | 기존 monitoring stack | 선택적 static plugin bundle + 개발용 npm toolchain |
| 호환성 비용 | Grafana JSON contract | Grafana/Scenes/React Router API·plugin signing 추가 |

**판단:** 네 개의 summary/investigation 화면과 custom Matrix·evidence workspace는 Scenes로 구현하고 검증했습니다. Phase별 comparable baseline·resource identity·raw scrape timestamp·clock coverage는 별도 telemetry 과제이며 standalone frontend 도입 근거는 확인되지 않았습니다.

## 후속 검증

| 후보 | 아직 필요한 근거 |
| --- | --- |
| Global Event Overlay | Native annotation layer로 raw event clock·uncertainty를 유지하는 공통 overlay. 현재는 Timeline / Recent Events / applied boundary table |
| Run Comparison | 다른 Run의 fingerprint·boundary·entity·clock·baseline policy 검증. 현재 comparable Step / Worker cohort만 지원 |
| Applied Policy / KV 전후 분석 | 실제 worker callback 계측과 engine/KV observation 연결. Trainer version이나 counter 변화로 적용을 추정하지 않음 |
| Query budget | 마지막 native query timing은 표시. 누적 request/response 성능 budget·실제 slow backend/권한 장애 실험은 추가 필요 |
| 운영 signature | Organization·token·production root URL로 공식 signing. CI ZIP은 unsigned 개발 artifact |

## 운영 한계

- 실제 GPU/VERL·물리 multi-node·3FS ClickHouse의 App journey는 추가 검증이 필요합니다. 명시 multiworker synthetic span은 별도 fixture로 검증합니다.
- 운영 배포에는 plugin signing과 배포 artifact가 필요합니다. Dev unsigned allowlist는 격리 개발 환경에만 사용합니다.
- Panel ID/ref contract는 CI에서 확인합니다. Catalogue cache는 browser reload 시 갱신됩니다.
- Phase-exclusive histogram과 raw sample timestamp/calibrated coverage는 추가 계측이 필요합니다.
- Exact node-clock sample matching은 correlation preview입니다. Raw timestamp·clock certainty를 추가 확인해야 합니다.
- 검증한 Grafana 버전 외의 호환성과 provisioning/auth 실패 경로는 추가 검증이 필요합니다.

Source 연결·N/A의 의미는 [UI Telemetry Coverage](ui-telemetry-coverage.md), behavior signature·triggered profiling과 논문 비교는 [Research PoC](behavior-signature-research.md)에서 관리합니다. Research API는 이 App의 기본 diagnosis 경로에 자동 연결하지 않습니다.

## Filter 위치와 색상

| 선택 | 근거 |
| --- | --- |
| 상단 Cluster / Run / Step / time | Grafana의 native sidebar와 중복 rail을 줄이고 Matrix 폭을 확보 |
| 접힌 observer/resource/trace filter | 자주 쓰는 context와 세부 identity 선택을 구분 |
| Phase별 색과 subsystem dot | 정상 상태에서도 execution/category를 빠르게 구분 |
| Delta의 amber/teal 방향 | 관측된 증가/감소를 구분. 장애·개선 판정을 뜻하지 않음 |
| Candidate의 attention 색 | 저장된 evidence state를 표시. 원인 확정과 구분 |

AGENTS.md가 dashboard 색 수를 제한하는 것은 아닙니다. 현재 배색은 category·관측 방향·evidence를 구분하기 위한 UI 선택이며 기본 card·missing 상태는 같은 light design system을 사용합니다.

현재 Matrix는 checkpoint·critic·reference 등 실제 measured span이 있을 때 해당 column을 추가합니다. Sandbox는 같은 trace의 실제 parent chain으로 연결된 execution span과 worker/cgroup identity를 확인하며, 관계가 없으면 값을 넣지 않습니다. Storage operation과 Ray state는 명시 selector로 선택합니다.

## 실제 화면

다음 화면은 같은 clock의 live synthetic metric·SDK span·저장 report로 재현했습니다. 실제 GPU/VERL 성능 검증과 구분합니다.

```{figure} figures/grafana-app-context-overview.png
:alt: Native Grafana sidebar를 유지하고 상단에 Run Context를 묶은 XLayer Overview
:width: 720px

상단 filter는 Run·Step·시간을 유지하면서 card와 chart의 폭을 확보합니다. Phase별 색은 execution category이며 health 판정이 아닙니다.
```

```{figure} figures/grafana-app-phase-matrix.png
:alt: Gauge baseline delta와 Shared Rolling context, Ray state와 Storage operation 선택을 구분하는 Phase Matrix
:width: 720px

GPU·vLLM·연결된 worker 관측은 조건을 만족할 때 baseline과 비교합니다. RPC p95·KV·network·Ray는 명시된 scope의 관측이며 phase 소유량이 아닙니다.
```

검증은 1440/1280/900/390px, 정상/지연 scenario, entity 선택, evidence·기존 dashboard 왕복을 포함합니다. 선택 Step 요청 22/22가 같은 구간을 유지하고, 5개 native baseline query만 명시 baseline 범위를 사용했습니다. Browser error는 0개였으며 CPU 1,313개·frontend 43개를 통과했습니다. {download}`검증 JSON<validation/grafana-scenes-phase-20261008.json>`에서 당시 context를 확인합니다.

### 분산 context 보강 검증

| 검증 | 결과 |
| --- | --- |
| CPU / frontend / CI helper | 1,329 / 81 / 17개 통과 |
| 문서 link·diagram / browser | 10 / 9개 통과. Sphinx warning 없음 |
| 단일 worker의 전체 조사 경로 | Overview → Matrix → Evidence → Storage → Back → Investigate → Deep Dive |
| 선택 Step의 native query | 22/22 요청이 같은 window. Baseline 5개는 별도 명시 구간 |
| 4-worker live fixture | 선택 전 ambiguous → comparable cohort → 느린 worker → sampled GPU → Compute → Back |
| Applied policy / 좁은 화면 | 명시 event가 있는 4 worker만 표시. 1440/1280/900/390px와 부분 가용성 확인 |
| Error / delay / Run 변경 | Browser datasource boundary fixture로 부분 실패·회복·이전 Run 값 차단 확인 |
| Evidence remount | Native timeline을 열어도 같은 조사 문맥의 Evidence 유지. 문맥이 달라지면 해제 |

```{figure} figures/grafana-app-worker-comparison.png
:alt: 명시 workload fingerprint가 같은 네 rollout worker 중 18초 call을 peer median 5.15초와 비교하는 measured worker table
:width: 720px

같은 operation·scope·fingerprint의 네 worker만 peer cohort를 구성합니다. 18초 call은 느린 후보이며 다른 node의 GPU는 명시 worker 선택과 calibrated window가 확인될 때 연결합니다.
```

이번 검증은 controlled injected clock을 사용하는 synthetic workload입니다. Physical multi-node clock·실제 GPU/veRL·3FS 성능 검증과 구분합니다. {download}`검증 JSON<validation/grafana-scenes-distributed-20261008.json>`은 단일·멀티worker 경로와 browser error 0개를 기록합니다.

## 관련 문서

[App guide](grafana-scenes-poc.md) · [배포 / Browser CI](app-deployment-reference.md) · [Metrics contract](metrics-reference.md) · [2026-10-08 검증 기록](validation/grafana-scenes-20261008.md)

## References

- [Grafana Scenes app / URL-state caching](https://grafana.com/developers/scenes/scene-app): native routing과 context 보존 패턴.
- [Custom scene objects](https://grafana.com/developers/scenes/advanced-custom-scene-objects): XLayer component를 Scene lifecycle에 연결.
- [Scene transformations](https://grafana.com/developers/scenes/transformations): 기존 panel transform 재사용.
- [Scenes 6 Router migration](https://github.com/grafana/scenes/releases/tag/v6.0.0): Router bundle과 host history 연결 근거.
- [Datadog dashboard UX 검토](grafana-ui-ux-review.md): Light hierarchy·절제한 색·context drill-down을 채택. Branding·가짜 service map·phase attribution은 채택하지 않음.
