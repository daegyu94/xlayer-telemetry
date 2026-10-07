# Grafana App / Scenes (PoC)

**목표:** XLayer App에서 Run을 선택하고 느린 Step의 baseline·candidate·evidence를 기존 dashboard까지 이어서 조사합니다.

## 얻는 것

| 화면 | 확인할 것 |
| --- | --- |
| Overview | Run context·핵심 KPI·Agent RL Timeline·최근 event |
| Analyze | Phase × Subsystem·System Pressure·worker 차이 |
| Investigate | Current / Baseline / Delta·candidate·supporting/counter/missing evidence |
| Deep Dive | 선택한 candidate의 상세 metric·기존 subsystem dashboard |

```{admonition} 선택 사항
:class: note

App은 선택적 PoC입니다. 기존 dashboard를 유지하며 `xltel up`이 plugin을 자동 설치하지 않습니다. 검증한 조합은 Grafana OSS 12.1.0·Scenes 6.20.0입니다.
```

## 준비 조건

- [Monitoring server](monitoring.md)와 기존 XLayer dashboard.
- Node 20 이상·npm. Python core runtime에는 npm이 필요하지 않습니다.
- Step·span·candidate를 보려면 [Loki](logs-events.md)와 [diagnosis](diagnosis.md). Metrics만 연결한 경우 이 영역은 unavailable로 표시됩니다.
- Plugin을 설치할 Grafana의 설정·plugin directory 접근 권한.

## 1. Configure

저장소 루트에서 plugin을 빌드합니다.

```bash
npm ci --prefix grafana/xlayer-app
npm run build --prefix grafana/xlayer-app
```

**정상 결과:** `grafana/xlayer-app/dist/`에 `plugin.json`·`module.js`·`img/logo.svg`가 생성됩니다.

1. `dist/`의 내용을 Grafana plugin directory의 `xlayer-telemetry-app/`에 설치합니다.
2. 격리 개발 환경에서 `plugins.allow_loading_unsigned_plugins = xlayer-telemetry-app`를 설정합니다.
3. Grafana plugin provisioning에 다음 파일을 추가합니다.

```yaml
apiVersion: 1
apps:
  - type: xlayer-telemetry-app
    org_id: 1
    disabled: false
```

**정상 결과:** 설치한 plugin directory에서 `plugin.json`과 `module.js`를 읽을 수 있습니다. 운영 배포에는 [Grafana plugin signing](https://grafana.com/developers/plugin-tools/publish-a-plugin/sign-a-plugin)이 필요합니다.

## 2. Start

설정한 Grafana를 기존 배포 절차로 재시작하고 **More apps → XLayer Telemetry → Overview**를 엽니다.

**정상 결과:** `/a/xlayer-telemetry-app/overview`에서 공통 Run Context와 네 화면의 navigation이 보입니다. 기존 datasource·auth·dashboard를 계속 사용합니다.

## 3. Verify

| 확인 | 정상 결과 |
| --- | --- |
| Cluster / Run 선택 | 선택한 source의 KPI 또는 명시적인 No data |
| 완료 Step 선택 | URL의 record·observer·시간 구간 변경 |
| Analyze cell 선택 | 값·scope·observation 상태와 evidence 표시 |
| 기존 Storage dashboard 열기 → Back | Run·Step·resource·시간 구간 유지 |

```{admonition} Scope / Precision
:class: important

Measured span 경계와 sampled resource 관측을 구분합니다. Shared resource의 동시 변화는 correlation이며 Run/phase 사용량이나 원인이 아닙니다. No data·missing evidence를 측정값 `0`으로 읽지 않습니다.
```

## 느린 Step 조사

1. **Overview:** KPI의 baseline 변화와 Timeline을 보고 완료 Step을 선택합니다.
2. **Analyze:** Phase × Subsystem에서 숫자·상태를 확인하고 의심 cell을 엽니다.
3. **Investigate:** What changed와 supporting/counter/missing evidence를 비교합니다.
4. **Deep Dive →:** 선택한 candidate와 같은 context에서 상세 metric을 확인합니다.
5. **Full Storage / Timeline / Logs:** 기존 dashboard로 이동합니다. Browser Back으로 App에 돌아옵니다.

Gauge의 phase delta는 명시된 baseline span·workload fingerprint·동일 entity·query 관측 수가 맞을 때만 표시합니다. `Step evidence`의 delta는 선택한 전체 Step 구간의 비교이며 phase cell로 복사하지 않습니다. [Cell 상태 해석](grafana-scenes-reference.md#phase--subsystem)을 함께 확인합니다.

## GPU 없이 확인

기존 stack과 분리한 live demo를 사용할 수 있습니다. 프로젝트를 설치한 Python 환경과 다음 binary가 필요합니다.

- `--tools` directory: `grafana-v12.1.0/`·`prometheus-3.5.0.linux-amd64/`·`loki-linux-amd64`.
- 위 절차로 빌드한 App. 첫 baseline/current 비교는 실제 scrape를 기다려 약 90초 뒤에 보입니다.
- 비어 있는 output 경로와 사용하지 않는 loopback port.

```bash
python grafana/xlayer-app/scripts/live_demo.py \
  --tools /path/to/existing-demo-tools \
  --output artifacts/scenes-demo \
  --grafana-port 23400
```

**정상 결과:** `LIVE DEMO http://127.0.0.1:23400/a/xlayer-telemetry-app/overview?...`가 출력됩니다. `Cluster=scenes-demo`·`Run=verl-agent-demo`를 선택하면 **Synthetic demo** badge가 보이고 완료된 baseline/current pair가 차례로 추가됩니다. 실행 중인 Step을 완료된 값으로 표시하지 않습니다.

```{admonition} Synthetic data
:class: note

Live exporter와 저장된 Current/Baseline diagnosis는 별도 fixture입니다. 이 demo는 화면·query·context를 확인하는 경로이며 실제 VERL 성능이나 diagnosis 정확도 실험이 아닙니다.
```

`Ctrl-C`는 demo가 만든 process만 종료합니다. Fixture·log·state는 output에 남으며, process 종료를 확인한 뒤 이번 demo directory만 정리합니다.

## Troubleshooting

| 상태 | 확인할 것 |
| --- | --- |
| App not found | Plugin directory·Grafana 로그·enabled provisioning |
| Dashboard not provisioned | 기존 XLayer dashboard 설치 여부 |
| Query failed | 원본 dashboard의 query·datasource health·권한 |
| No completed Step | Run/time filter·Loki step history |
| N/A / Rolling context | [Matrix의 observation 조건](grafana-scenes-reference.md#phase--subsystem) |
| MFU / Policy / Status 누락 | [Producer·freshness·identity](ui-telemetry-coverage.md) |
| Sparkline 없음 | 같은 entity의 실제 history가 두 관측 이상인지 |
| Candidate workspace 비어 있음 | Investigate에서 candidate를 선택했는지 |

## 다음

[Baseline / Evidence](diagnosis.md) · [UI telemetry 연결](ui-telemetry-coverage.md) · [App Reference](grafana-scenes-reference.md) · [실제 화면·검증 기록](validation/grafana-scenes-20261008.md)

:::{container} xlayer-legacy-links

<a id="먼저-확인한-현재-구조" class="xlayer-legacy-anchor"></a>

[먼저 확인한 현재 구조](grafana-scenes-reference.md)

<a id="architecture" class="xlayer-legacy-anchor"></a>

[Architecture](grafana-scenes-reference.md)

<a id="화면과-다음-행동" class="xlayer-legacy-anchor"></a>

[화면과 다음 행동](grafana-scenes-reference.md)

<a id="mockup-채택-기준" class="xlayer-legacy-anchor"></a>

[Mockup 채택 기준](validation/grafana-scenes-20261008.md#validation)

<a id="현재-가능한-것과-추가-계측이-필요한-것" class="xlayer-legacy-anchor"></a>

[현재 가능한 것과 추가 계측이 필요한 것](validation/grafana-scenes-20261008.md#validation)

<a id="mfu--policy--status의-실제-지원-범위" class="xlayer-legacy-anchor"></a>

[MFU / Policy / Status의 실제 지원 범위](ui-telemetry-coverage.md)

<a id="phase--subsystem의-해석" class="xlayer-legacy-anchor"></a>

[Phase × Subsystem의 해석](grafana-scenes-reference.md)

<a id="작게-구현한-순서" class="xlayer-legacy-anchor"></a>

[작게 구현한 순서](validation/grafana-scenes-20261008.md#validation)

<a id="validation" class="xlayer-legacy-anchor"></a>

[Validation](validation/grafana-scenes-20261008.md#validation)

<a id="실제-화면" class="xlayer-legacy-anchor"></a>

[실제 화면](validation/grafana-scenes-20261008.md#실제-화면)

<a id="dashboard와-scenes-비교" class="xlayer-legacy-anchor"></a>

[Dashboard와 Scenes 비교](grafana-scenes-reference.md)

<a id="남은-과제" class="xlayer-legacy-anchor"></a>

[남은 과제](grafana-scenes-reference.md)

<a id="references" class="xlayer-legacy-anchor"></a>

[References](grafana-scenes-reference.md)

<a id="mockup" class="xlayer-legacy-anchor"></a>

[Mockup 디자인 결정](validation/grafana-scenes-20261008.md#디자인-결정)

<a id="mfu-policy-status" class="xlayer-legacy-anchor"></a>

[MFU / Policy / Status](ui-telemetry-coverage.md)

<a id="phase-subsystem" class="xlayer-legacy-anchor"></a>

[Phase × Subsystem](grafana-scenes-reference.md#phase--subsystem)

<a id="dashboard-scenes" class="xlayer-legacy-anchor"></a>

[Dashboard와 Scenes 비교](grafana-scenes-reference.md#dashboard와-scenes-비교)

:::
