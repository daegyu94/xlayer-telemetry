# Grafana App 배포 Reference

**찾아보는 목적:** App build 산출물·개발용 package·실제 Grafana browser CI와 운영 서명의 경계를 확인합니다. 일반 설치와 조사 흐름은 [App guide](grafana-scenes-poc.md)를 따릅니다.

Managed Grafana의 기본 버전은 **13.1.7**이며 기존 App + Scenes UI와 Scenes 6.20.0을 유지합니다. 실제 Grafana 13.1도 기본 React 19 runtime을 사용하므로, XLayer는 공식 `[feature_toggles] react19 = false` 설정을 별도 owned `grafana-compat.ini`로 적용합니다. 선언·installer 범위 `>=12.1.0 <13.2.0`은 모든 runtime의 호환성을 뜻하지 않으며, `app status`는 Grafana 13의 관측된 frontend flag도 검사합니다. React 19 JSX runtime externalization·최소 Grafana 버전 조정과 13.2 이상 검증은 Future Work입니다. [공식 migration 안내](https://grafana.com/developers/plugin-tools/migration-guides/update-from-grafana-versions/migrate-12_x-to-13_x/).

## 배포 단계

| 단계 | 산출물 / 확인 | 현재 지원 |
| --- | --- | --- |
| Build | `dist/plugin.json`·`module.js`·logo | CI에서 unit·typecheck·build 실행 |
| 개발용 package | Plugin ID directory를 포함한 ZIP + SHA256 | 재현 가능한 unsigned archive 생성 |
| Browser regression | Live Prometheus/Loki·Grafana 13.1.7 (React 18 mode)·Scenes 6.20.0 | 격리 runner에서 실제 조사 경로 실행 |
| 운영용 signature | Grafana가 발급한 `MANIFEST.txt` | 공식 절차 필요. 자동 발급·검증은 미구현 |
| 운영 배포 | 기존 Grafana plugin directory·provisioning | 운영자 배포 절차 사용. Marketplace 자동 게시 없음 |

## Managed Grafana Upgrade

1. **Prepare:** 현재 config의 `SERVER_OUTPUT_DIR`을 확인하고 `xltel down --role server`로 owned server를 종료합니다. SQLite `grafana-data`와 plugin·provisioning·config를 별도 backup에 보존하세요. External Grafana의 DB와 서비스는 운영자의 배포 절차를 따릅니다.
2. **Configure:** 기존 TOML `[telemetry]`에서 `GRAFANA_VERSION = "13.1.7"`을 지정합니다. `GF_FEATURE_TOGGLES_ENABLE = "vizLegendFacetedFilter"`가 기본값이며, 명시한 빈 문자열은 opt-out으로 유지합니다.
3. **Install / Start:** `xltel install-tools --role server`로 publisher SHA256을 확인한 archive를 설치하고 `xltel up --role server`를 실행합니다. 새 binary는 `grafana-13.1.7/`에 설치되며 기존 `grafana-v12.1.0/`을 삭제하지 않습니다. Fresh SQLite migration에는 bounded startup 대기 시간을 사용합니다.
4. **Verify:** `xltel app status`와 `/a/xlayer-telemetry-app/overview`에서 기존 Scenes UI·선택 context·query·drill-down을 확인합니다. Version 범위와 React runtime·실제 served bundle 검사는 별개입니다.
5. **Rollback:** Server를 종료한 뒤 **업그레이드 전 DB·config backup을 복구**하고 `GRAFANA_VERSION = "12.1.0"`으로 시작합니다. 13이 migration한 DB에 12 binary만 연결해서 downgrade하지 않습니다. `app rollback`은 plugin backup을 복구하며 Grafana DB를 되돌리는 명령이 아닙니다.

External Grafana 13.1은 기존 config의 `[feature_toggles]`에 `react19 = false`와 `vizLegendFacetedFilter = true`를 적용해야 현재 App bundle을 사용할 수 있습니다. XLayer는 external config·DB·NTP 서비스를 직접 수정하지 않습니다.

### Series Readability

Matrix는 canonical panel에 device identity 차원이 선언된 경우에만 표시합니다. GPU Utilization과 Disk I/O Busy Ratio는 고정 0–100% 색상 척도를 사용하며, GPU·SSD·NIC/RDMA 값을 Node × GPU / Device / Interface로 비교합니다. Sandbox Pressure와 Node 집계 Network처럼 device label이 없는 관측은 Matrix에서 추정하지 않고 기존 Time series로 보여줍니다. 같은 좌표에 서로 다른 instance/process identity가 있으면 값을 합치지 않고 Unknown으로 표시합니다.

**Latest evaluation / Range mean / Range max**를 선택하고, 셀을 누르면 같은 source series의 상세 시계열을 엽니다. Range mean은 수집 간격이 불규칙한 경우 시간 가중 평균이 아닙니다. Sandbox Pressure와 Node 집계 Network처럼 device identity가 없거나 ambiguous한 panel은 Matrix에서 제외하거나 identity를 Unknown으로 남깁니다.

**Time series**는 기존 전체 관측과 native Series visibility를 제공합니다. Disk throughput·IOPS·latency의 색은 현재 선택된 같은 Metric 안에서만 상대 비교하며, 색상은 Health나 Run ownership을 뜻하지 않습니다. Missing·Stale·Unknown·Query Failure와 실제 0을 구분합니다. 선택한 Metric이 응답에서 빠지면 다른 Metric으로 바꾸지 않고 No data를 표시합니다. KPI의 작은 그래프는 숫자·설명과 별도 행에 배치합니다.

## 설치 / 상태 / Update

일반 설치는 기존 `xltel` config를 선택한 뒤 실행합니다. 현재 archive는 unsigned PoC이며 production signing을 대신하지 않습니다.

```bash
# 격리 Managed Grafana PoC
xltel app install --allow-unsigned
xltel app status
xltel app update --allow-unsigned
xltel app rollback
```

**정상 결과:** 검증한 파일과 App provisioning을 준비합니다. 활성화가 필요하면 `restart_required`, 같은 bundle이면 `unchanged`, Grafana API·version·enabled state·실제 served module SHA256·signature policy·canonical datasource UID가 확인되면 `ready`입니다. Ready는 이 설치 검사 범위이며 datasource query health나 모든 Grafana 버전의 browser 호환성을 보증하지 않습니다.

| Package 입력 | 동작 |
| --- | --- |
| 기본 | 현재 `main`과 같은 commit의 성공한 App CI artifact를 `gh`로 조회하고 ZIP/SHA256 검증 |
| CI 접근 불가 | Checkout과 npm이 있으면 private temporary copy에서 ci·test·typecheck·build·package |
| `--build` | CI 대신 격리 build 선택. 기존 `dist`·`node_modules`를 변경하지 않음 |
| `--package FILE.zip` | `.zip.sha256` 또는 `--sha256 HEX`와 대조. ZIP traversal·duplicate·symlink·크기·ID 검증 |

GitHub artifact 접근에는 `gh` 인증과 Actions read 권한이 필요할 수 있습니다. 현재 tag release나 Marketplace package를 제공한다고 주장하지 않습니다. CI artifact는 7일 retention이며 접근 불가·만료 시 local build 또는 명시한 package를 사용합니다. SHA256은 archive integrity이며 Grafana signature가 아닙니다.

```bash
xltel app install --package /path/to/app.zip --sha256 <sha256> --allow-unsigned
# 명시한 경우만 기존 owned server lifecycle 재시작
xltel app update --package /path/to/new-app.zip --sha256 <sha256> --allow-unsigned --restart
```

기본으로 process를 재시작하지 않습니다. 이미 enabled된 App이 새 module을 제공하면 status 검사로 확인할 수 있습니다. Version/manifest/provisioning 변화로 재시작이 필요하면 기존 배포 절차를 사용합니다. `--restart`는 managed server role을 재시작하므로 Grafana뿐 아니라 그 role의 Prometheus·Loki도 대상입니다. 변경이 없으면 재시작하지 않습니다.

### External Grafana

```bash
xltel app install --external --plugins-dir /var/lib/grafana/plugins \
  --provisioning-dir /etc/grafana/provisioning \
  --package /path/to/signed-app.zip --sha256 <sha256>
xltel app status --external --plugins-dir /var/lib/grafana/plugins \
  --provisioning-dir /etc/grafana/provisioning
```

External mode는 명시한 directory만 사용하며 service·auth·datasource·user DB를 수정하거나 재시작하지 않습니다. 기존 symlink 설치와 사용자 편집 provisioning은 자동 교체하지 않습니다. Unsigned external PoC의 allowlist와 재시작은 운영자가 직접 적용합니다.

API 권한이 필요하면 `GRAFANA_SERVICE_ACCOUNT_TOKEN`을 environment 또는 TOML `[environment]`에서 제공합니다. Token은 로그·receipt·argv에 기록하지 않습니다. API를 확인하지 못하면 Unknown이며 설치 파일만 보고 Ready로 표시하지 않습니다.

### 실패 / Rollback

Update 전에 현재 파일과 설치 receipt의 hash를 대조합니다. 수동 설치로 receipt가 없거나 파일이 변경됐으면 rollback 가능성을 확인할 수 없으므로 기존 서비스·파일을 보존하고 update를 거부합니다. 설치 상태를 정리하거나 명시적인 별도 plugin directory를 사용하세요.

Update는 previous plugin 한 개를 Grafana가 scan하지 않는 별도 directory에 보존합니다. 파일 교체·receipt 기록 실패는 기존 plugin과 provisioning을 복구합니다. Managed `--restart` 활성화 실패는 rollback을 시도하며, API 권한 부족처럼 이유가 불명확하면 장애 원인을 단정하지 않습니다.

Package 준비 명령은 180초 timeout을 사용합니다. Timeout에서는 XLayer가 만든 build process group의 npm·compiler·test child만 정리하고, 설치된 plugin을 보존한 채 실행 시간 초과를 안내합니다.

기존 rollback 백업은 새 package의 staging·교체·provisioning·receipt 기록이 모두 성공한 뒤에 교체합니다. 실패한 다음 업데이트가 현재 plugin이나 이전 backup·receipt를 없애지 않으며, 복구 후 기존 `app rollback`도 사용할 수 있습니다. Process 강제 종료나 복구 자체의 I/O 실패로 `retained-previous`가 남으면 다음 업데이트를 보류하므로 App state를 보존하고 복구해야 합니다. 전원 장애의 multi-file crash atomicity까지 보장하는 journal은 아닙니다.

`xltel app rollback`은 이전 파일을 복원하며 필요한 재시작은 별도입니다. 기존 auth·datasource·user DB와 다른 plugin은 유지합니다. 운영 signing은 아래 공식 절차를 따릅니다.

<a id="개발용-package"></a>

## 개발용 package 생성

저장소 루트에서 build와 package를 실행합니다. Output은 새 directory를 선택합니다.

```bash
npm ci --prefix grafana/xlayer-app
npm run build --prefix grafana/xlayer-app
python grafana/xlayer-app/scripts/package_plugin.py \
  --output artifacts/grafana-app-package
```

**정상 결과:** `xlayer-telemetry-app-0.1.0-unsigned.zip`과 같은 이름의 `.sha256` 파일이 생성됩니다. ZIP의 최상위 directory는 `xlayer-telemetry-app/`입니다. 이미 같은 archive가 있으면 덮어쓰지 않습니다.

```bash
cd artifacts/grafana-app-package
sha256sum -c xlayer-telemetry-app-0.1.0-unsigned.zip.sha256
unzip -l xlayer-telemetry-app-0.1.0-unsigned.zip
```

**정상 결과:** `OK`와 plugin directory 아래의 `plugin.json`·`module.js`·`img/logo.svg`가 보입니다. SHA256은 전달 중 파일 변조 확인용이며 Grafana signature가 아닙니다. `MANIFEST.txt`가 있는 signed directory는 이 도구로 다시 package하지 않습니다.

```{admonition} 개발 / 운영 경계
:class: important

Unsigned allowlist는 격리 개발 Grafana에서 `xlayer-telemetry-app` 하나만 허용합니다. CI package를 운영용 signed release로 취급하지 않습니다. 기존 auth·datasource·dashboard 설정을 App package로 덮어쓰지 않습니다.
```

## 실제 browser CI

[App workflow](https://github.com/daegyu94/xlayer-telemetry/blob/main/.github/workflows/grafana-app.yml)는 다음 순서로 검증합니다.

1. Node 22·Python 3.12에서 App test·typecheck·build·package.
2. 공식 release의 고정 SHA256으로 Grafana 13.1.7·Prometheus 3.5.0·Loki 3.7.3 검증 후 설치.
3. 실제 exporter scrape와 SDK span/diagnosis fixture를 생성하는 `live_demo.py` 실행.
4. 단일 / Multi-worker / Multi-job CI job에서 완료된 baseline/current pair를 기다린 뒤 기존 browser journey 실행. 단일 경로는 Scenes 내부의 GPU·Storage·Network Legend 조작도 확인.
5. 자신이 시작한 process만 종료하고 JSON report·화면 capture·제한된 synthetic log·ZIP을 7일 artifact로 보존.

Browser context는 `locale=en-US`·`timezone=UTC`를 명시합니다. POSIX host locale을 Chromium이 잘못 전달하면 Grafana의 `Intl.NumberFormat`이 App 진입 전에 실패할 수 있습니다. Entry 실패 artifact는 제한된 DOM·browser/console 오류·query status metadata를 기록하며 query expression·header·credential을 보존하지 않습니다.

기본 readiness deadline은 300초, browser deadline은 180초, CI job 상한은 15분입니다. 서비스가 먼저 종료되면 실패하며 다른 monitoring process를 readiness 성공으로 취급하지 않습니다. Launcher state·database·원본 telemetry는 artifact에 올리지 않습니다.

| Browser 경로 | 확인 |
| --- | --- |
| Overview → Analyze | 완료 Step·Run/resource identity·native query 시간 구간 |
| Matrix → Evidence | Shared·Sampled·Missing 해석 유지 |
| Storage dashboard → Back | Step·node·time context 유지 |
| Investigate → Deep Dive | Candidate identity·evidence·native metric tab |
| Desktop / narrow | Page overflow·evidence 사용성 |
| Unknown Run / optional dashboard 부재 | No data·부분 가용성. 실제 backend 장애와 구분 |
| Native Run 변경 중 응답 지연 | 이전 Run KPI·Step을 현재 값으로 표시하지 않음 |
| Structured error / 1.2초 delay fixture | Browser datasource boundary에서 오류·회복 확인. 실제 backend outage·성능 실험 아님 |
| Multi-worker Matrix → Compute → Back | Ambiguous → 명시 worker 선택·peer cohort·resource/observer context |

### 로컬에서 같은 경로 실행

프로젝트가 설치된 Python 환경에서 실행합니다. Linux amd64 전용 installer이며 Playwright는 검증 환경에만 설치합니다.

```bash
python -m pip install playwright==1.56.0
python -m playwright install chromium
python grafana/xlayer-app/scripts/install_ci_tools.py \
  --output artifacts/grafana-ci-tools
python grafana/xlayer-app/scripts/ci_demo.py \
  --tools artifacts/grafana-ci-tools \
  --state artifacts/grafana-ci-state \
  --output artifacts/grafana-ci-browser
```

**정상 결과:** `CI journey passed; owned Grafana/Prometheus/Loki/exporter stopped.`가 출력됩니다. `ci-validation.json`과 각 화면의 PNG가 남습니다. 실패하면 nonzero exit code와 제한된 service log를 확인합니다. State는 새 directory여야 합니다.

CI 다운로드 cache도 매번 고정 publisher checksum과 대조합니다. [Monitoring installer](https://github.com/daegyu94/xlayer-telemetry/blob/main/scripts/install_telemetry_tools.sh)도 Grafana archive의 publisher checksum을 확인하며, 다른 도구의 local digest 기록과 구분합니다. 이 CI 도구가 운영 monitoring installer를 대체하지 않습니다.

## Validation Scope

2026-10-11의 격리된 CPU/Synthetic 환경에서 Grafana 13.1.7의 기존 Scenes 조사 경로·Multi-worker·Multi-job, GPU/Storage/Network Legend의 Light/Dark·1440px/390px 조작을 검증했습니다. Legacy 12.1.0에서도 기본 Scenes browser journey를 확인했습니다. Ubuntu 22.04 container에서는 binary 실행과 `/api/health`만 확인했으므로, 기존 Ubuntu 22.04 CPU CI는 유지합니다.

후속 Matrix UI 변경은 단일 Job의 기존 live stack에서 GPU/Storage/Network의 계산 선택·단일 series 상세·Legend·KPI 그래프/텍스트 분리를 Light/Dark·1440px/390px에서 검증했습니다. Matrix 숫자의 대비도 확인했으며, 이 변경 이후 Multi-worker / Multi-job browser journey를 별도로 다시 실행한 것은 아닙니다.

실제 GPU·RDMA·물리 Multi-node·3FS cluster 성능과 운영 DB upgrade/restore는 이번 검증 범위가 아닙니다. App CI는 Grafana 13.1.7을 기본으로 검사하며 React 19 runtime·13.2 이상은 검증하지 않았습니다. Browser report·Screenshot은 CI artifact에 보존하고 과거 검증 기록을 현재 버전의 보증으로 해석하지 않습니다.

## 운영용 signing

| 준비 | 확인 |
| --- | --- |
| Grafana organization | Plugin ID의 organization prefix와 계정 소유권 일치 여부 |
| Access Policy token | `plugins:write` 권한. Secret/environment로만 전달 |
| Private plugin | 실제 Grafana `root_url`과 `rootUrls` 일치 |
| Public plugin | Grafana review·승인된 signature level |
| Signed build | 파일을 모두 확정한 뒤 공식 도구로 signature 생성. 서명 후 변경하지 않음 |

[Grafana 공식 signing 절차](https://grafana.com/developers/plugin-tools/publish-a-plugin/sign-a-plugin)와 [공식 packaging 절차](https://grafana.com/developers/plugin-tools/publish-a-plugin/package-a-plugin)를 따릅니다. 현재 repository에는 token·organization 소유권·production root URL이 없으므로 운영용 signature 발급을 수행하지 않습니다. Plugin ID를 바꿀 때는 route·public path·provisioning·dashboard backlink를 함께 이관해야 합니다.

## 남은 운영 검증

- 실제 GPU/veRL·multi-node·3FS는 synthetic browser CI의 검증 범위 밖입니다.
- Grafana 최소 버전 조건과 모든 상위 버전 호환성을 동일하게 취급하지 않습니다. Upgrade 때 같은 browser journey를 다시 실행합니다.
- Native query 요청 수·응답시간 budget, 실제 느린 backend·permission 오류, signing 및 배포 rollback은 추가 검증이 필요합니다.

## 다음

- [App Reference](grafana-scenes-reference.md): query·scope·context 계약.
- [검증 기록](validation/grafana-scenes-20261008.md): 당시 화면과 실행 결과.
