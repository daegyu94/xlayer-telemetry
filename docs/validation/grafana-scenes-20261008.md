# Grafana App 검증 기록 · 2026-10-08

**검증 범위:** Grafana OSS 12.1.0·Scenes 6.20.0에서 live synthetic 데이터와 SDK fixture의 query·context·화면을 확인했습니다. 당시 코드 기준은 `main` `10130dd`와 이후 research·collector·App 보완입니다. 현재 설치·사용 경로는 [App guide](../grafana-scenes-poc.md)를 따릅니다.

## Validation

검증 workload는 실제 VERL 실행이 아니라 **live synthetic exporter + SDK-generated Step/span/diagnosis**입니다. 기존 `demos.live`와 `demos.diagnosis`를 재사용하고, 반복 fixture의 Step 번호를 증가시켜 다른 window를 같은 record identity로 저장하지 않습니다. Live exporter와 저장 diagnosis는 별도의 synthetic fixture입니다. Diagnosis는 미리 정한 Current/Baseline을 기존 rule engine으로 평가하며 live backend를 다시 분석한 결과가 아닙니다. 이 검증은 query wiring·의미론·UX를 확인하며 diagnosis 정확도 실험은 아닙니다.

재현 방법과 browser 명령은 {download}`Plugin README의 Live demo 절<../../grafana/xlayer-app/README.md>`에서 확인합니다. Capture와 machine-readable 결과는 Git에서 제외한 `artifacts/grafana-scenes-poc/`에 저장합니다.

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
| 문서 browser | D2 26개·caption·그림 열기·entry card 검사 **9개 통과** |

Mockup 확장 이후에는 sparkline의 native range 계약, 좁은 Run Context의 control 폭, Related Metrics의 과도한 세로 길이, candidate workspace의 priority와 범례를 추가로 수정했습니다. Related Metrics는 subsystem tab으로 전환하고 telemetry/detail 목록은 접힌 영역으로 옮겼습니다. 마지막 확인 결과는 {download}`browser validation JSON<grafana-scenes-20261008.json>`에 남겼습니다.

### 실제 화면

다음 캡처는 live synthetic exporter와 저장된 SDK fixture로 재현했습니다. Screenshot의 값은 실제 workload 실험 결과가 아닙니다.

```{figure} ../figures/grafana-app-overview.png
:alt: Live synthetic XLayer Overview의 왼쪽 Run Context, KPI, timeline과 event 목록
:width: 720px

Overview: KPI의 baseline 변화부터 보고 완료 Step을 선택합니다. System Signals는 저장 diagnosis의 범위를 표시합니다.
```

```{figure} ../figures/grafana-app-analyze.png
:alt: Sampled 숫자, Rolling context와 Step evidence를 구분하는 Phase × Subsystem Matrix
:width: 720px

Analyze: 관측 window와 entity 조건을 만족한 cell만 숫자를 표시합니다. Storage의 Step evidence는 phase attribution이 아닙니다.
```

```{figure} ../figures/grafana-app-deep-dive.png
:alt: 선택한 Storage candidate의 supporting 및 missing evidence와 native 상세 metric
:width: 720px

Deep Dive: Key Findings와 missing evidence를 확인하고 같은 context의 기존 Storage dashboard로 이어갑니다.
```

Frontend regression은 URL allowlist·multi-node·Logs mapping·zero/missing·canonical query selection·entity·boundary·calibration을 검사합니다. Browser regression은 native request의 실제 interval과 Run/Step/resource 왕복을 확인합니다. Optional dashboard 404 fixture는 UI absence 처리 검사이며 실제 Loki outage 검증과 구분합니다.

## 구현 단계

| Phase | 결과와 검증 대상 |
| --- | --- |
| A · Skeleton | Sidebar entry, Scene routing, native Run / observer / resource / time |
| B · Overview | Scope를 표시한 KPI, 완료 Step 목록, 기존 measured timeline·관련 panel |
| C · Matrix | 7 subsystem × measured phase window, entity 선택, rolling/N/A/Step evidence |
| D · Investigation | 기존 projection의 comparison·candidate·evidence, context를 유지한 dashboard 이동 |

Evidence는 **inline detail + compact native timeline**으로 구현했습니다. Deep Dive는 선택 candidate와 native metric tab을 묶습니다. 좁은 화면에서는 세로로 배치하며 별도 drawer engine이나 generic graph framework는 추가하지 않았습니다.

## 디자인 결정

제공한 mockup에서 네 화면의 역할, 공통 context, KPI 계층과 evidence 옆 graph를 채택했습니다. 예시 숫자·event·강한 signal badge는 데이터로 사용하지 않습니다. 최신 화면의 최종 browser 검증 상태는 [당시 검증](#validation)에서 구분합니다.

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

## 당시 지원 범위

| 분류 | 항목 | PoC 처리 |
| --- | --- | --- |
| **기존 source 재사용** | Canonical KPI·실제 event 목록·네 화면 routing·comparison/candidate·native timeline·bounded System Pressure | 기존 Grafana datasource와 Scenes runtime을 사용. 값을 가져오지 못하면 No data / not reported |
| **명시 producer가 있으면 표시** | veRL stage MFU·trainer policy version·wrapped-command 상태·recorded span errors | MFU는 stage별 logger 값, policy는 명시 version, 상태는 wrapper 보고, errors는 반환 record count. [Coverage 계약](../ui-telemetry-coverage.md) 확인 |
| **추가 계측 / contract 필요** | Rollout에 실제 적용된 policy version의 coverage·전체 async Run completion·전체 workload error rate·phase별 comparable baseline | Trainer version / wrapper command / 제한된 span 결과로 대신 표시하지 않음 |
| **추가 coverage 필요** | Calibrated resource clock·raw scrape timestamp·multi-worker phase mapping·per-run 3FS attribution | Missing / unverified를 유지. UI 개선만으로 관측 공백을 해소했다고 설명하지 않음 |
| **의미론상 금지** | Reported duration으로 phase boundary 생성·actor/critic 임의 병합·rolling p95를 짧은 phase 값으로 표시 | Matrix 값을 채우기 위한 근사나 attribution을 하지 않음 |
| **의미론상 금지** | Shared storage를 Run 소유량으로 표시·metric delta로 event 생성·미수집 MFU/Policy/Running 추정 | 관측된 사실·시간적 correlation·실행 관계·causality를 구분 |

## 관련 기록

[Browser 결과 JSON](grafana-scenes-20261008.json) · [App Reference](../grafana-scenes-reference.md) · [UI 수집 계약](../ui-telemetry-coverage.md)
