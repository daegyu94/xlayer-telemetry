# XLayer Grafana App · Scenes PoC

기존 Grafana datasource와 canonical panel을 재사용하는 **XLayer Telemetry Dashboard** App Plugin입니다. 8개 Workspace 화면과 6개 Native Dashboard 화면이 공통 sidebar·Light/Dark 배색을 사용합니다. 공통 상단 Run Context가 선택한 Run·Step·resource·시간을 유지하며 theme만 바꾸어도 investigation identity는 변하지 않습니다. 별도 backend나 standalone frontend는 없습니다.

## 화면 선택

| 목적 | 화면 | 다음 행동 |
| --- | --- | --- |
| Run 요약 확인 | Overview: 8 KPI·실제 history sparkline·Timeline + Recent Events·Related Metrics + System Signals | 완료 Step → Analyze |
| Phase와 subsystem 연결 | Analyze: Matrix·System Pressure·Top Changes·worker snapshots | Execution worker / Worker Comparison → Cell evidence → Investigate |
| Baseline과 후보 확인 | Investigate: What changed·candidate·Supporting/Against/Missing | Open Evidence / Deep Dive |
| 선택 후보 검증 | Deep Dive: `candidate_id` workspace·Key Findings·native metric tab·System Pressure | 기존 Timeline / Storage / Logs dashboard |

Timeline·Infrastructure·Logs & Events도 Workspace navigation에서 선택합니다. Start Here·Stage Correlation·Bottleneck Summary·Compute·Storage·Cross-Layer Signals는 같은 shell 안에서 원본 panel을 읽으며, 기존 `/d/*` 상세 URL도 유지합니다. Detailed Metrics는 canonical native panel을 사용하며 자체 graph/query engine을 만들지 않습니다.

## 준비

- Grafana **12.1.0**에서 검증합니다. Manifest의 최소 버전과 다른 버전의 실제 호환성 검증은 구분합니다.
- CI prebuilt ZIP + SHA256 또는 checkout + Node 20 이상·npm. Python core runtime에는 npm dependency가 추가되지 않습니다.
- 기존 XLayer dashboard를 `scripts/provision_dashboards.py`로 provision합니다.
- Prometheus는 core입니다. Loki Step/span/diagnosis는 optional입니다.

## Build / test

일반 설치는 `xltel app install --allow-unsigned`·`xltel app status`·`xltel app update --allow-unsigned`를 사용합니다. Unsigned flag는 격리 PoC 전용이며, 운영용 signed package·external Grafana·rollback은 [배포 Reference](../../docs/app-deployment-reference.md#설치--상태--update)를 따릅니다. 아래 명령은 frontend 개발용입니다.

```bash
cd grafana/xlayer-app
npm ci
npm test
npm run typecheck
npm run build
```

정상 결과: `dist/plugin.json`, `dist/module.js`, `dist/img/logo.svg`가 생성됩니다. `dist/`와 `node_modules/`는 commit하지 않습니다.

## 기존 Grafana에 연결

1. `dist/`를 Grafana plugin directory 아래 **`xlayer-telemetry-app/`**으로 설치합니다.
2. 개발 환경에서만 Grafana 설정 `plugins.allow_loading_unsigned_plugins = xlayer-telemetry-app`를 적용합니다. 다른 unsigned plugin을 허용하거나 signature 검사를 전체 비활성화하지 않습니다.
3. 아래 native plugin provisioning을 추가하고 Grafana를 다시 시작합니다. 기존 auth/datasource/dashboard는 유지합니다.

```yaml
apiVersion: 1
apps:
  - type: xlayer-telemetry-app
    org_id: 1
    disabled: false
```

정상 결과: sidebar의 **More apps → XLayer Telemetry**에서 Overview로 이동합니다. `/a/xlayer-telemetry-app/overview`도 사용할 수 있습니다. Production 배포에는 공식 Grafana plugin signing 절차를 따릅니다. 개발용 ZIP·browser CI·signing 준비 조건은 [배포 Reference](../../docs/app-deployment-reference.md)를 확인합니다.

## Source와 No data

| 표시 | 필요한 실제 source | 해석 경계 |
| --- | --- | --- |
| KPI sparkline | 같은 entity의 sample history 또는 같은 Run/scope/entity의 저장 Step history, 두 관측 이상 | Synthetic trend를 만들지 않음. Instant query에 history가 없으면 생략 |
| Framework-reported MFU | veRL `perf/mfu/*`의 explicit stage scalar | GPU util에서 추정하지 않음. Actor/critic를 합치지 않음 |
| Policy Version | 선택 record의 explicit version 또는 같은 Run의 단일 fresh trainer version | Trainer version은 rollout applied version이 아님 |
| Wrapped command state | Wrapper의 명시 health report → textfile source | 마지막 command 보고이며 전체 async Run 완료가 아님 |
| Recorded errors | 조회한 span의 status field | Returned record count·status coverage·5,000 limit. 전체 workload error rate가 아님 |
| Storage latency | 저장 diagnosis의 shared-service evidence | Local I/O mean·KV RPC p95를 3FS p99로 대신 표시하지 않음 |

MFU·policy·wrapped-command는 producer와 freshness/identity 조건이 맞을 때만 표시합니다. 미수집·stale·모호한 source는 N/A / not reported입니다. 연결 계약은 [UI Telemetry Coverage](../../docs/ui-telemetry-coverage.md)를 따릅니다.

Reported stage의 `verl_stage` 등 metric dimension은 snapshot age의 ownership label과 구분합니다. Async `trainer_update`는 Update time으로 표시하며 legacy의 missing boundary는 Observation으로 남깁니다. Deep Dive의 Connector RPC / DFS / 3FS evidence / Local I/O mean은 기존 canonical panel과 저장 evidence를 재사용합니다. 동일 GPU/Ray Pressure query는 native Scenes proxy로 공유합니다.

## Live demo

기존 stack과 별개인 loopback-only Grafana·Prometheus·Loki를 실행합니다. 이미 설치한 binary를 재사용하며 container나 사용자의 monitoring config를 수정하지 않습니다.

저장소 루트에서 프로젝트가 설치된 Python 환경을 사용합니다. `--tools` 아래에는 `grafana-v12.1.0/`, `prometheus-3.5.0.linux-amd64/`, `loki-linux-amd64`가 있어야 합니다.

```bash
python grafana/xlayer-app/scripts/live_demo.py \
  --tools /path/to/existing-demo-tools \
  --output /tmp/xlayer-scenes-demo \
  --grafana-port 23400
```

정상 결과: `LIVE DEMO http://127.0.0.1:23400/a/xlayer-telemetry-app/overview?...`가 출력됩니다. 새 `--output` directory를 사용해야 합니다. 초기 scrape 후 Run은 `verl-agent-demo`, Cluster는 `scenes-demo`를 선택합니다. **Synthetic demo** badge와 Step history가 보이며 약 90초 동안 실제 scrape를 수집한 뒤 baseline/current completed Step pair가 보입니다. 기본은 storage regression과 normal pair를 번갈아 제공합니다.

1. 상단 Run Context에서 Run·time을 확인합니다. Overview에서 저장 baseline 변화와 live/reported KPI를 구분합니다.
2. Step dropdown 또는 **Analyze Step**으로 완료 Step을 선택합니다. 실제 record identity와 window가 URL·native query에 반영됩니다.
3. Analyze의 **rollout × storage**를 선택합니다. Supporting / Against / Missing evidence와 compact timeline을 읽습니다. Rolling RPC p95는 Shared context이며 phase 값으로 귀속되지 않습니다. Storage selector로 operation/status를 선택하고 Step evidence의 supporting/against/missing을 따로 확인합니다.
4. **Investigate**로 이동해 What changed와 candidate를 확인합니다. **Deep Dive →**는 candidate identity를 유지해 Key Findings와 native metric tab을 엽니다.
5. **Full Storage** 또는 Storage 링크로 기존 dashboard를 엽니다. Browser Back으로 돌아오면 Run/Step/time·candidate context가 유지됩니다.
6. Trace/GPU/engine filter에서 GPU를 `0`으로 제한하면 measured rollout window의 sampled GPU를 확인할 수 있습니다. 여러 device를 선택하면 `Select entity`입니다.

Demo의 MFU·policy·wrapper report도 명시적으로 생성한 synthetic source입니다. 실제 veRL workload의 수집 성공으로 해석하지 않습니다. Native metric·SDK span·저장 diagnosis는 같은 synthetic scenario clock을 사용합니다. Diagnosis scalar는 명시 scenario 통계이며 실제 backend query 결과가 아닙니다. Native RPC p95와 scenario 3FS p99는 다른 source/stat입니다.

`Ctrl-C`는 이 launcher가 만든 process만 종료합니다. 작은 fixture/log와 state는 지정한 directory에 남깁니다. Cleanup은 process 종료를 확인한 뒤 이 directory만 삭제합니다.

세 동시 Job·shared resource·missing/stale source의 실제 query/diagnosis 경로는 같은 명령에 `--multi-job`을 추가합니다. 기존 `--multi-worker`와는 별도 경로이며 [Multi-job Tutorial](../../docs/demo.md#multi-job-live-demo)의 확인 순서를 따릅니다. 이 모드는 모델 이름을 metadata로 기록할 뿐 weights를 실행하지 않고, 기존 단일 Job의 precomputed scenario diagnosis를 사용하지 않습니다.

```bash
python -m pip install playwright
python -m playwright install chromium
python grafana/xlayer-app/scripts/browser_validate.py \
  --url http://127.0.0.1:23400 \
  --output artifacts/grafana-scenes-poc
```

Browser 검사는 충분한 live scrape와 2번째 fixture 생성 후 실행합니다. 설치된 Chromium을 재사용하려면 `--browser /path/to/chromium`을 지정합니다. 결과는 desktop·900px·390px capture와 validation JSON입니다. Test는 실제 Grafana query request의 Step interval도 확인합니다.

`metric_contract_validate.py --url <owned-loopback-demo> --output <new-directory>`는 metrics-only veRL label·async presentation의 browser response fixture와 Storage layer context·GPU/Ray query 재사용을 검사합니다. `ci_demo.py`도 기본 journey 뒤에 이 검사를 실행합니다. Async fixture는 실제 veRL 실행이나 backend 장애 실험이 아닙니다.

최신 네 페이지 UI를 실제 Grafana 12.1.0의 live demo에서 1440px·1280px·900px·390px로 검증했습니다. Browser error는 0개이며 선택 Step의 native datasource 요청 25/25개가 실제 window와 일치했습니다. 최종 완료 범위·test 수·남은 한계는 [검증 기록](../../docs/validation/grafana-scenes-20261008.md#validation)에서 확인합니다.

`--multi-worker`를 추가하면 네 worker의 exact rollout span·명시 `weights.applied` event와 느린 peer 하나를 생성합니다. Analyze의 **Worker Comparison**에서 cohort를 확인하고 **Execution worker**를 선택합니다. 선택한 identity는 URL에 보존하며 observer와 resource node를 구분합니다. 기본 demo는 단일 execution path를 유지합니다.

`--storage-series`는 명시 synthetic 3FS collection point·sparse gap·reset-report amount를 추가합니다. 실제 ClickHouse가 아니라 controlled fixture입니다. Deep Dive → 3FS evidence에서 metric을 선택하면 source timestamp·unit·Current/Baseline·clock/collection coverage를 확인합니다. No interpolation/zero fill·phase ownership·exact throughput/IOPS 추론은 하지 않습니다. Production 설정은 [Storage Correlation](../../docs/storage-correlation.md)을 따릅니다.

## 문제 해결

| 상태 | 확인할 것 |
| --- | --- |
| App not found | Plugin directory 구조·Grafana 로그·enabled provisioning |
| Canonical dashboards not provisioned | Core provisioning을 먼저 실행했는지 |
| Query failed | Native datasource permission/health·원본 dashboard의 query |
| No completed Step | Run/time·Loki step history. No data는 zero가 아님 |
| N/A | Unique measured phase span 부재. Reported duration으로 interval을 만들지 않음 |
| Rolling context | Histogram/rate lookback. 짧은 phase에 귀속하지 않음 |
| MFU가 안 보임 | 원본 `perf/mfu/*` key·bridge snapshot·freshness·같은 Run source |
| Policy N/A / not reported | Explicit version source 유무·source가 하나인지. Step에서 추론하지 않음 |
| Status not reported | Wrapper health의 explicit context·freshness. Collector UP과 별개 |
| Sparkline이 없음 | 같은 entity의 실제 history가 두 관측 이상인지. Instant query는 trend가 없을 수 있음 |
| Candidate workspace가 비어 있음 | Investigate의 candidate Deep Dive로 진입했는지·Run/record/window가 맞는지 |

## 구조 / 한계

- `src/catalog.ts`: 실제 provisioned dashboard metadata와 native Scenes query/variable/viz 연결.
- `src/context.ts`, `semantics.ts`, `selection.ts`, `distributed.ts`, `data.ts`: URL state, observation boundary, projection decoding.
- `src/matrix-contract.ts`: Canonical panel/ref 선택. PromQL/LogQL은 중복 정의하지 않습니다.
- `src/module.tsx`: XLayer Scene 페이지와 custom component.
- `scripts/`: 선택적 live demo와 browser validation.

설치·조사 흐름은 [App guide](../../docs/grafana-scenes-poc.md), query·scope·운영 한계는 [App Reference](../../docs/grafana-scenes-reference.md), 당시 화면·결과는 [검증 기록](../../docs/validation/grafana-scenes-20261008.md)에서 관리합니다. SDK/native collector·diagnosis rule/schema는 이 App이 대체하지 않습니다. [Behavior signature 연구 PoC](../../docs/behavior-signature-research.md)는 별도 선택적 research API이며 기본 App query/diagnosis 경로에 자동 연결되지 않습니다.

## Design comparison

디자인 원본은 `design/dashboard-preview.html`입니다. 이 파일의 가상 수치는 디자인 확인용이며 plugin bundle이나 synthetic exporter의 데이터로 사용하지 않습니다.

기존 loopback demo가 실행된 상태에서 13개 화면을 같은 해상도로 비교합니다.

```bash
python grafana/xlayer-app/scripts/design_validate.py \
  --url http://127.0.0.1:23400 \
  --mockup grafana/xlayer-app/design/dashboard-preview.html \
  --output artifacts/dashboard-design --iteration 1
```

정상 결과: `iteration-1/validation.json`의 `failures`가 비어 있고 `comparison.html`에서 원본과 실제 screenshot을 나란히 확인할 수 있습니다. 1440×1000 / 390×844, Light/Dark를 확인하며 pixel equality나 실제 GPU/Storage 성능 검증을 주장하지 않습니다. 원본 native panel의 query·unit·transform·link는 유지하고 추가 row는 펼칠 때 활성화합니다.

## Readability / 한국어 안내

Infrastructure는 `design/topology-compact.html`의 B2 계층 Map을 사용합니다. 기술 이름·KPI·unit은 영어로 유지하고 Source·Scope·Missing·Clock 안내는 한국어로 표시합니다. Native diagnosis의 warning/missing 값에는 명시 state icon과 theme-adaptive text를 사용합니다.

```bash
python grafana/xlayer-app/scripts/readability_validate.py \
  --url http://127.0.0.1:23400 \
  --mockup grafana/xlayer-app/design/topology-compact.html \
  --output artifacts/readability --iteration 1 --require-contrast
```

정상 결과: 13개 화면 × Desktop/Tablet/Mobile × Light/Dark의 `validation.json`에서 navigation/context 실패와 qualified DOM contrast 미달이 0건입니다. Canvas·복잡한 paint·비활성 control의 예외는 별도로 기록하며 실제 GPU/3FS 성능이나 전체 WCAG 인증을 의미하지 않습니다.
