# XLayer Grafana App · Scenes PoC

기존 dashboard 위에 **Overview → Analyze → Investigate → Deep Dive** 네 개의 조사 화면을 제공합니다. 왼쪽 Run Context는 화면 이동 중에도 선택한 Run·Step·시간을 유지합니다. Timeline과 기존 subsystem dashboard는 상세 확인에 재사용하며 별도 web service나 telemetry backend는 없습니다.

| 항목 | PoC 범위 |
| --- | --- |
| 기준 코드 | Remote `main` `10130dd` + research/collector 보완 |
| 검증 대상 | Grafana OSS 12.1.0 · Scenes 6.20.0 · React 18 |
| UI | Light 우선, native font + indigo accent, Dark fallback |
| 데이터 | 기존 Prometheus / Loki datasource · 저장된 diagnosis projection |
| 설치 | 선택적 App Plugin. Core `xltel up`에는 자동 설치하지 않음 |
| 실행 안내 | {download}`Plugin README<../grafana/xlayer-app/README.md>` |

## 먼저 확인한 현재 구조

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

## Architecture

![Scenes는 Grafana query runtime을 재사용하고 상세 조사에는 기존 dashboard로 이동한다. ClickHouse는 기존 diagnosis 경로에서만 조회한다](figures/diagrams/grafana-scenes-app.svg)

- `AppPlugin`이 sidebar에 XLayer 진입점 하나를 등록합니다. 화면 간 이동은 context를 유지하는 App navigation에서 제공합니다. Static sidebar 링크에 query state를 기대하지 않습니다.
- `SceneApp` / `SceneAppPage`가 routing과 URL state를 관리합니다.
- 왼쪽 Run Context에 `EmbeddedScene`의 native variable·time picker·refresh picker를 배치합니다. Step 선택은 저장된 완료 record와 실제 window를 선택하는 XLayer component입니다.
- `SceneQueryRunner`는 provisioned panel의 targets·datasource를 읽습니다. 별도 Prometheus/Loki client는 없습니다.
- `VizPanel` / `SceneDataTransformer`는 기존 timeline과 관련 graph를 렌더링합니다.
- XLayer component는 KPI·Matrix·comparison·candidate·evidence·navigation만 담당합니다.

Query contract는 dashboard에 한 번만 정의합니다. App에는 panel ID/ref ID를 선택하는 얇은 catalogue만 있으며, datasource remapping 이후의 JSON을 읽습니다.

## 화면과 다음 행동

### Mockup 채택 기준

제공한 mockup에서 네 화면의 역할, 공통 context, KPI 계층과 evidence 옆 graph를 채택했습니다. 예시 숫자·event·강한 signal badge는 데이터로 사용하지 않습니다. 최신 화면의 최종 browser 검증 상태는 아래 [Validation](#validation)에서 구분합니다.

| Mockup 패턴 | 채택 / 조정 | 근거와 해석 경계 |
| --- | --- | --- |
| Overview의 KPI 8개 | Reward·Step time·worker throughput·reported rollout·GPU·KV token hit·Storage·Recorded errors | 기존 canonical query / 저장 projection / 반환된 span만 사용. Error count는 query가 반환한 record의 status coverage이며 전체 workload error rate가 아님 |
| 상단 Cluster / Run / Step / Policy / Status | 공통 왼쪽 Run Context, native Cluster·Run·time, 완료 Step 선택, 접힌 observer/resource | 명시 policy와 wrapper 상태만 표시. Source가 없거나 모호하면 N/A / not reported; Step 증가로 Running을 추론하지 않음 |
| KPI mini trend | Native Grafana `Sparkline`, 저장된 Step history 또는 같은 sampled entity의 history | 두 개 이상의 실제 관측이 있을 때만 표시. 빈 history를 보간하거나 baseline trend를 합성하지 않음 |
| Agent RL Timeline | 기존 measured span lane와 approximate Step lane 재사용 | Phase마다 동일한 경계 정확도를 가진 것으로 그리지 않음. Actor/critic span을 합쳐 Training interval을 만들지 않음 |
| Related Metrics + Recent Events | Timeline 옆 실제 EventRecorder event, Related Metrics 옆 source-qualified System Signals | 서로 다른 단위를 하나의 축으로 섞지 않음. System Signals는 저장 candidate이며 collector UP 기반 health가 아님 |
| 별도 Analyze matrix | Phase × Subsystem을 Overview 밖의 독립 조사 화면으로 제공 | Overview 밀도를 줄이고 같은 Run/Step/time을 유지. 조건을 만족하지 못한 cell은 N/A / Shared / Rolling context |
| Investigate comparison + candidate | 기존 Current / Baseline / delta와 candidate projection 재사용 | Baseline p50·STRONG·primary hypothesis를 UI가 새로 판정하지 않음. Scope·entity·comparability와 missing evidence 유지 |
| Analyze 보조 정보 | Top Changes와 Outlier worker snapshots | Top Changes는 Step evidence. Worker 비교는 Run/producer/role/phase/node/reported Step cohort이며 workload comparability는 unverified |
| Evidence 옆 compact graph | Inline evidence와 canonical timeline을 함께 확인 | 동일 선택 window를 사용. 근거를 살피는 비교이며 causal path를 확정하는 그림이 아님 |
| System Pressure 카드 | 제한된 canonical vLLM / Ray / sandbox / network / storage / GPU signal | Session·node·shared-service signal은 현재 context로 표시. 선택 Step/phase에 귀속하거나 snapshot과 저장 evidence를 혼합하지 않음 |
| Deep Dive workspace | `candidate_id`로 Key Findings·Against/Missing·native Detailed Metrics 유지 | Local I/O mean·KV RPC p95·GPU·vLLM·Ray·Sandbox는 독립 단위/scope. Local mean이나 RPC p95를 3FS service p99로 바꾸지 않음 |
| 빨강 / 초록 heatmap | 숫자·상태·scope를 먼저 표시하고 anomaly에만 accent | 변화량의 방향만으로 정상/비정상을 판정하지 않음. Missing과 measured zero를 색상만으로 구분하지 않음 |

### 현재 가능한 것과 추가 계측이 필요한 것

| 분류 | 항목 | PoC 처리 |
| --- | --- | --- |
| **기존 source 재사용** | Canonical KPI·실제 event 목록·네 화면 routing·comparison/candidate·native timeline·bounded System Pressure | 기존 Grafana datasource와 Scenes runtime을 사용. 값을 가져오지 못하면 No data / not reported |
| **명시 producer가 있으면 표시** | veRL stage MFU·trainer policy version·wrapped-command 상태·recorded span errors | MFU는 stage별 logger 값, policy는 명시 version, 상태는 wrapper 보고, errors는 반환 record count. [Coverage 계약](ui-telemetry-coverage.md) 확인 |
| **추가 계측 / contract 필요** | Rollout에 실제 적용된 policy version의 coverage·전체 async Run completion·전체 workload error rate·phase별 comparable baseline | Trainer version / wrapper command / 제한된 span 결과로 대신 표시하지 않음 |
| **추가 coverage 필요** | Calibrated resource clock·raw scrape timestamp·multi-worker phase mapping·per-run 3FS attribution | Missing / unverified를 유지. UI 개선만으로 관측 공백을 해소했다고 설명하지 않음 |
| **의미론상 금지** | Reported duration으로 phase boundary 생성·actor/critic 임의 병합·rolling p95를 짧은 phase 값으로 표시 | Matrix 값을 채우기 위한 근사나 attribution을 하지 않음 |
| **의미론상 금지** | Shared storage를 Run 소유량으로 표시·metric delta로 event 생성·미수집 MFU/Policy/Running 추정 | 관측된 사실·시간적 correlation·실행 관계·causality를 구분 |

카드 수나 mockup의 색을 그대로 맞추는 것보다 **source → scope → observation window → 다음 행동**을 읽을 수 있는지가 기준입니다.

| 화면 | 보는 것 | 다음 행동 |
| --- | --- | --- |
| **Overview** | 출처를 구분한 8 KPI·실제 history sparkline·Timeline + Recent Events·Related Metrics + System Signals | 완료 Step 선택 → Analyze |
| **Analyze** | Phase × Subsystem·System Pressure·Top Changes·worker snapshots | Cell evidence 확인 → Investigate |
| **Investigate** | What changed·baseline·workload comparability·candidate cards | Open Evidence / candidate Deep Dive |
| **Evidence detail** | Supporting / Against / Missing, scope·entity·quality와 compact timeline | Timeline / Storage / Stage / Logs |
| **Timeline** | Measured span과 approximate completed-Step lane를 별도 표시 | Full Timeline에서 event·clock·resource 확인 |
| **Deep Dive** | 선택 candidate의 Key Findings·Against/Missing, native Detailed Metrics tab, System Pressure | Full Storage / Logs / Events·Spans / 기존 dashboard |

네 페이지는 별도 route입니다. Timeline helper route와 기존 상세 dashboard도 유지합니다. `candidate_id`는 URL state로 보존하며 browser Back으로 evidence context에 돌아옵니다.

### MFU / Policy / Status의 실제 지원 범위

| 화면 표시 | Producer / query | 표시하지 않는 것 |
| --- | --- | --- |
| Framework-reported MFU | `perf/mfu/*` → `training_model_flops_utilization_ratio`, Overview canonical panel 40 | GPU util 기반 추정·actor/critic 합산·정확한 phase MFU |
| Policy Version | 선택 record의 명시 version 또는 같은 Run의 단일 fresh `policy_version` source, panel 41 | Step/lag로 추정한 version·trainer version을 rollout applied version으로 귀속 |
| Wrapped command state | Fresh `telemetry_wrapped_workload_state`, panel 42 | Collector UP·전체 async Run completion·미보고 terminal 상태 |
| Recorded errors | 반환된 span status의 error count와 coverage, query limit 5,000 | 전체 workload error rate·완전한 오류 수집을 뜻하는 measured zero |

Logger key가 없거나 freshness/identity 조건을 만족하지 못하면 N/A / not reported를 유지합니다. 여러 source가 있으면 임의로 하나를 선택하지 않습니다. 연결 방법과 세부 한계는 [UI Telemetry Coverage](ui-telemetry-coverage.md)를 따릅니다.

### Phase × Subsystem의 해석

| Cell 상태 | 값의 근거 | 표시하지 않는 것 |
| --- | --- | --- |
| Sampled 숫자 | 같은 Run·Step의 유일한 exact/calibrated span, 같은 resource node, window 안의 query evaluation | Phase 사용량·causal attribution |
| Select entity | 같은 node에서 여러 GPU/engine을 관측 | 임의 average/max로 만든 phase 값 |
| Rolling context | Rate/histogram의 lookback이 짧은 phase 밖까지 포함 | Phase-specific p95/p99·hit ratio·bandwidth |
| N/A | Phase interval 없음 또는 불명확 | 보고된 stage duration으로 만든 가짜 boundary |
| Step evidence | 저장 diagnosis의 full-Step window | Phase delta / phase baseline |
| Ray context | Canonical task aggregation이 node identity를 보존하지 않음 | Session 값을 worker/phase에 귀속 |
| MFU 별도 관측 | Framework가 보고한 completed stage scalar | GPU utilization으로 추정하거나 sampled resource cell에 합친 MFU |

```{admonition} Precision / Scope
:class: important

Exact는 application span 경계의 속성입니다. Resource metric은 sampled이며 query evaluation 시각과 원본 scrape 시각이 같다고 보장하지 않습니다. Exact node-clock span의 cross-clock calibration은 미확인일 수 있습니다. Calibrated uncertainty가 없거나 phase보다 큰 경우 숫자를 연결하지 않습니다. Correlation ≠ attribution ≠ causality.
```

Phase baseline을 새로 계산하지 않습니다. Step Current/Baseline은 저장 projection의 entity·window·scope와 함께 표시하며 workload comparability가 `unverified`이면 그대로 드러냅니다. Baseline `0`에서 relative delta가 없다는 것은 baseline 자체가 없다는 뜻이 아닙니다.

## 작게 구현한 순서

| Phase | 결과와 검증 대상 |
| --- | --- |
| A · Skeleton | Sidebar entry, Scene routing, native Run / observer / resource / time |
| B · Overview | Scope를 표시한 KPI, 완료 Step 목록, 기존 measured timeline·관련 panel |
| C · Matrix | 7 subsystem × measured phase window, entity 선택, rolling/N/A/Step evidence |
| D · Investigation | 기존 projection의 comparison·candidate·evidence, context를 유지한 dashboard 이동 |

Evidence는 **inline detail + compact native timeline**으로 구현했습니다. Deep Dive는 선택 candidate와 native metric tab을 묶습니다. 좁은 화면에서는 세로로 배치하며 별도 drawer engine이나 generic graph framework는 추가하지 않았습니다.

## Validation

검증 workload는 실제 VERL 실행이 아니라 **live synthetic exporter + SDK-generated Step/span/diagnosis**입니다. 기존 `demos.live`와 `demos.diagnosis`를 재사용하고, 반복 fixture의 Step 번호를 증가시켜 다른 window를 같은 record identity로 저장하지 않습니다. Live exporter와 저장 diagnosis는 별도의 synthetic fixture입니다. Diagnosis는 미리 정한 Current/Baseline을 기존 rule engine으로 평가하며 live backend를 다시 분석한 결과가 아닙니다. 이 검증은 query wiring·의미론·UX를 확인하며 diagnosis 정확도 실험은 아닙니다.

재현 방법과 browser 명령은 {download}`Plugin README의 Live demo 절<../grafana/xlayer-app/README.md>`에서 확인합니다. Capture와 machine-readable 결과는 Git에서 제외한 `artifacts/grafana-scenes-poc/`에 저장합니다.

| Loop | 발견한 문제 | 개선 |
| --- | --- | --- |
| 1 | 호스트 external과 Router 호환성, KPI 출처 혼합, 긴 entity label | 실제 loader export에 맞춘 bundle, Grafana history 연결, Step/live 출처 구분 |
| 2 | Epoch constructor 기본 범위 fallback, dictionary의 query provider parent 누락, 반복 synthetic identity | ISO 변환, direct Scene state array, distinct live Step와 native datasource-request 검사 |
| 3 | 좁은 표·detail 이동·context 왕복·missing/zero 표시 | Local table scroll, evidence 진입/복귀, no-data와 optional metadata 404 검사 |

**최종 통합 검증:** CPU regression **1,305개**, frontend regression **31개**, typecheck·production build·Sphinx·D2 검사가 통과했습니다. Grafana 12.1.0 / Scenes 6.20.0의 실제 live demo에서 **1440px·1280px·900px·390px**를 확인했으며 browser error는 **0개**였습니다.

| 검증 | 결과 |
| --- | --- |
| Overview → Step → Analyze → Storage evidence | 실제 기록의 scope·precision·supporting/missing 유지 |
| 기존 Data & Storage → Back | Run·record·observer·resource·GPU·phase·time 유지 |
| Investigate → candidate Deep Dive → native metric tab | `candidate_id`와 선택 Step, supporting/counter/missing 유지 |
| Native datasource 시간 범위 | Step 선택 이후 요청 **25/25**가 실제 18.401s 선택 구간과 일치 |
| 좁은 화면 | Page overflow 없음. Matrix/table은 내부 scroll, evidence/workspace는 세로 배치 |
| Unknown Run / optional dashboard 404 | No data / unavailable 표시. 0 또는 정상 상태로 대체하지 않음 |
| 문서 browser | D2 26개·caption·원본 확대·entry card 검사 **9개 통과** |

Mockup 확장 이후에는 sparkline의 native range 계약, 좁은 Run Context의 control 폭, Related Metrics의 과도한 세로 길이, candidate workspace의 priority와 범례를 추가로 수정했습니다. Related Metrics는 subsystem tab으로 전환하고 telemetry/detail 목록은 접힌 영역으로 옮겼습니다. 마지막 확인 결과는 {download}`browser validation JSON<validation/grafana-scenes-20261008.json>`에 남겼습니다.

### 실제 화면

다음 캡처는 live synthetic exporter와 저장된 SDK fixture로 재현했습니다. Screenshot의 값은 실제 workload 실험 결과가 아닙니다.

```{figure} figures/grafana-app-overview.png
:alt: Live synthetic XLayer Overview의 왼쪽 Run Context, KPI, timeline과 event 목록
:width: 720px

Overview: KPI의 baseline 변화부터 보고 완료 Step을 선택합니다. System Signals는 저장 diagnosis의 범위를 표시합니다.
```

```{figure} figures/grafana-app-analyze.png
:alt: Sampled 숫자, Rolling context와 Step evidence를 구분하는 Phase × Subsystem Matrix
:width: 720px

Analyze: 관측 window와 entity 조건을 만족한 cell만 숫자를 표시합니다. Storage의 Step evidence는 phase attribution이 아닙니다.
```

```{figure} figures/grafana-app-deep-dive.png
:alt: 선택한 Storage candidate의 supporting 및 missing evidence와 native 상세 metric
:width: 720px

Deep Dive: Key Findings와 missing evidence를 확인하고 같은 context의 기존 Storage dashboard로 이어갑니다.
```

Frontend regression은 URL allowlist·multi-node·Logs mapping·zero/missing·canonical query selection·entity·boundary·calibration을 검사합니다. Browser regression은 native request의 실제 interval과 Run/Step/resource 왕복을 확인합니다. Optional dashboard 404 fixture는 UI absence 처리 검사이며 실제 Loki outage 검증과 구분합니다.

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

## 남은 과제

- 실제 GPU/VERL·multi-worker span·3FS ClickHouse 환경의 App journey 검증.
- Production signing과 배포 artifact. Dev unsigned allowlist를 production 배포로 설명하지 않음.
- Panel ID/ref contract 변경을 CI에서 계속 검사. Catalogue cache는 browser reload 시 갱신.
- 실제 측정된 phase interval의 token histogram/baseline과 calibrated sample coverage 확보.
- Exact node-clock sample matching은 correlation preview이며 raw timestamp·clock certainty를 강화해야 함.
- 더 넓은 Grafana 버전 matrix와 automated provisioning/auth failure 검사.

Source 연결·N/A의 의미는 [UI Telemetry Coverage](ui-telemetry-coverage.md), behavior signature·triggered profiling과 논문 비교는 [Research PoC](behavior-signature-research.md)에서 관리합니다. Research API는 이 App의 기본 diagnosis 경로에 자동 연결하지 않습니다.

## References

- [Grafana Scenes app / URL-state caching](https://grafana.com/developers/scenes/scene-app): native routing과 context 보존 패턴.
- [Custom scene objects](https://grafana.com/developers/scenes/advanced-custom-scene-objects): XLayer component를 Scene lifecycle에 연결.
- [Scene transformations](https://grafana.com/developers/scenes/transformations): 기존 panel transform 재사용.
- [Scenes 6 Router migration](https://github.com/grafana/scenes/releases/tag/v6.0.0): Router bundle과 host history 연결 근거.
- [Datadog dashboard UX 검토](grafana-ui-ux-review.md): Light hierarchy·절제한 색·context drill-down을 채택. Branding·가짜 service map·phase attribution은 채택하지 않음.
