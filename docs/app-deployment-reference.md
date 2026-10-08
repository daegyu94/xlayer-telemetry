# Grafana App 배포 Reference

**찾아보는 목적:** App build 산출물·개발용 package·실제 Grafana browser CI와 운영 서명의 경계를 확인합니다. 일반 설치와 조사 흐름은 [App guide](grafana-scenes-poc.md)를 따릅니다.

## 배포 단계

| 단계 | 산출물 / 확인 | 현재 지원 |
| --- | --- | --- |
| Build | `dist/plugin.json`·`module.js`·logo | CI에서 unit·typecheck·build 실행 |
| 개발용 package | Plugin ID directory를 포함한 ZIP + SHA256 | 재현 가능한 unsigned archive 생성 |
| Browser regression | Live Prometheus/Loki·Grafana 12.1.0·Scenes 6.20.0 | 격리 runner에서 실제 조사 경로 실행 |
| 운영용 signature | Grafana가 발급한 `MANIFEST.txt` | 공식 절차 필요. 자동 발급·검증은 미구현 |
| 운영 배포 | 기존 Grafana plugin directory·provisioning | 운영자 배포 절차 사용. Marketplace 자동 게시 없음 |

## 개발용 package

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
2. 공식 release의 고정 SHA256으로 Grafana 12.1.0·Prometheus 3.5.0·Loki 3.7.3 검증 후 설치.
3. 실제 exporter scrape와 SDK span/diagnosis fixture를 생성하는 `live_demo.py` 실행.
4. 단일 / 멀티worker CI job에서 baseline/current pair 두 개를 기다린 뒤 기존 journey·metric/storage/clock contract와 `versions_validate.py`를 실행. V1/V2 여섯 화면의 값·query·context·empty/error/stale 경계를 비교.
5. 자신이 시작한 process만 종료하고 JSON report·화면 capture·제한된 synthetic log·ZIP을 7일 artifact로 보존.

Browser context는 `locale=en-US`·`timezone=UTC`를 명시합니다. POSIX host locale을 Chromium이 잘못 전달하면 Grafana의 `Intl.NumberFormat`이 App 진입 전에 실패할 수 있습니다. Entry 실패 artifact는 제한된 DOM·browser/console 오류·query status metadata를 기록하며 query expression·header·credential을 보존하지 않습니다.

기본 readiness deadline은 240초, browser deadline은 180초, CI job 상한은 15분입니다. 서비스가 먼저 종료되면 실패하며 다른 monitoring process를 readiness 성공으로 취급하지 않습니다. Launcher state·database·원본 telemetry는 artifact에 올리지 않습니다.

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

CI 다운로드 cache도 매번 고정 publisher checksum과 대조합니다. 기존 [monitoring installer](https://github.com/daegyu94/xlayer-telemetry/blob/main/scripts/install_telemetry_tools.sh)의 local digest 기록과 구분합니다. 이 CI 도구가 운영 monitoring installer를 대체하지 않습니다.

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
