# Maintainers · 개발과 문서 검증

**목적:** 사용자 guide와 구현·계약·실측 기록을 함께 유지합니다.

## 개발 환경

```bash
python -m pip install -r requirements.txt -e .
python -m pytest -q
python -m compileall -q xlayer_telemetry examples
git ls-files -z '*.sh' | xargs -0 -r -n 1 bash -n
```

**정상 결과:** 관련 CPU regression과 syntax 검사가 통과합니다. Synthetic·VM 결과를 실제 GPU/VERL·3FS cluster 검증으로 표현하지 않습니다.

## GitHub Actions CI

PR과 `main` push에서 기존 suite를 실행합니다. 실제 backend가 필요한 CPU 테스트는 Python 3.12 job에서 한 번 실행하고, Python 3.10 job은 runtime 호환성을 확인합니다. Backend를 준비한 job에서 테스트가 skip되면 실패 처리해 검증 누락을 숨기지 않습니다.

| Workflow | 자동 검증 | 실행 조건 |
| --- | --- | --- |
| CPU regression tests | SDK·adapter·schema, baseline·clock·sampling·entity, timeout·cleanup, dashboard 계약, CI helper | 모든 PR·`main` push |
| CPU의 Python 3.12 job | 실제 `promtool`의 PromQL 평가, 격리 ClickHouse의 distribution·reset counter·entity·query budget SQL | 위 suite 안에서 실행; 별도 중복 suite 없음 |
| Optional Grafana App PoC | TypeScript test·typecheck·build, single/multi-worker synthetic Grafana·Prometheus·Loki browser journey | App·runtime·config·canonical dashboard·provisioning 변경 |
| Documentation | 문서 link·D2·strict Sphinx·browser geometry | 문서·README·문서 도구 변경 |

**실패 확인:** Actions의 실패 step과 `--durations` 출력을 먼저 확인합니다. CPU artifact의 `pytest.xml`에서 실패·skip·개별 시간을 확인하고, SQL service 장애는 `clickhouse.log`를 봅니다. App artifact는 `metric-contracts/`, `storage-series/`, `clock-quality/`의 JSON·screenshot도 보존합니다. 보존 기간은 7일입니다.

Merge 전 성공을 강제하려면 저장소의 branch protection/ruleset에 required check를 지정합니다. Workflow의 자동 실행과 merge 보호는 별도 설정이며, 이 workflow가 저장소 권한·보호 규칙을 변경하지는 않습니다.

### Real SQL / PromQL을 로컬에서 재현

```bash
python grafana/xlayer-app/scripts/install_ci_tools.py --output artifacts/ci-tools --only prometheus
PROMTOOL="$PWD/artifacts/ci-tools/prometheus-3.5.0.linux-amd64/promtool" \
  python -m pytest -q -ra tests/test_subsystem_signal_queries.py tests/test_mooncake_profile_queries.py

# 사용자가 이 검증을 위해 만든 disposable ClickHouse container만 지정합니다.
XLAYER_TEST_CLICKHOUSE_CONTAINER=owned-clickhouse \
  python -m pytest -q tests/test_storage_distribution_clickhouse.py tests/test_threefs_series_clickhouse.py
```

**정상 결과:** 선택한 테스트가 skip 없이 통과합니다. `PROMTOOL` 또는 container를 지정하지 않은 로컬 실행은 해당 테스트를 skip하며, backend 검증 성공으로 간주하지 않습니다. Installer는 Linux amd64용이며 고정 release의 publisher SHA256을 확인합니다.

CI ClickHouse는 [.github/ci](https://github.com/daegyu94/xlayer-telemetry/tree/main/.github/ci)의 작은 thread·memory 설정과 digest로 고정한 image를 사용합니다. 외부 포트를 열지 않고 `docker exec`로 조회하며, fixture별 고유 Memory database를 생성·제거합니다. CPU 2개·RAM 1 GiB·data tmpfs 256 MiB로 제한하고 job 종료 시 container와 volume을 제거합니다. 이 설정은 production 3FS 배포용이 아닙니다.

```{important}
이 CI가 확인하는 것은 synthetic 입력·fault injection·실제 query evaluator·격리 SQL engine의 동작입니다. 실제 GPU, 물리 multi-node clock, veRL/vLLM/Mooncake 실행, 3FS/pNFS I/O path, replica 선택·run별 attribution은 별도 장비 검증이 필요합니다. pNFS mount나 외부 framework를 모사한 입력만으로 해당 backend 지원을 주장하지 않습니다.
```

## 문서와 UI 검증

```bash
bash scripts/docs.sh build
python -m pytest -q tests/test_documentation_links.py tests/test_documentation_diagrams.py
CHROME_PATH=/path/to/chromium XLAYER_DOCS_SITE=artifacts/docs-site npm test --prefix scripts/docs-browser
```

**정상 결과:** strict Sphinx build에 warning이 없고 link·viewport·diagram 확대·geometry가 통과합니다. Browser dependency 설치는 [Script 안내](https://github.com/daegyu94/xlayer-telemetry/blob/main/scripts/README.md#documentation-diagrams)를 따릅니다.

## D2 원본과 SVG

```bash
python scripts/render_diagrams.py --install
# 원본을 수정한 뒤 재생성합니다.
python scripts/render_diagrams.py
python scripts/render_diagrams.py --check
```

**정상 결과:** pinned D2 0.9.0이 원본·공통 style과 SVG 일치를 확인합니다. Source와 SVG를 같이 commit하고 generated site·binary·run data는 Git에 넣지 않습니다.

| 그림 계층 | 본문 preview | 사용 |
| --- | --- | --- |
| Small | 최대 600px, 원본보다 확대하지 않음 | 짧은 concept flow |
| Medium | 최대 780px | Workflow / 일반 architecture |
| Large | 최대 960px, 본문 폭 이내 | Cross-layer detail / topology |

- Node label은 1–3줄; 자세한 설명은 caption·본문으로 이동.
- Thin neutral border, primary blue/indigo, optional dashed + label.
- 본문은 compact preview; 그림의 mouse·keyboard 접근을 유지.
- Desktop 1440px·laptop 1280px·narrow viewport에서 실제 크기 확인.

## 문서 역할

| 역할 | 본문 패턴 |
| --- | --- |
| Tutorial | 처음부터 성공 기준까지 하나의 경로 |
| Task | 얻는 것 → 준비 → Configure → Start → Verify → 다음 |
| Concept | Mental model·용어·해석 경계 |
| Reference | Source·unit·scope·schema·구현 계약 |
| Validation record | 당시 환경·결과·미실행·한계 |

## 기록과 디자인 근거

[검증 기록](validation/README.md) · [System / Diagnosis review](system-review.md) · [Dashboard UI review](grafana-ui-ux-review.md) · [Documentation review](documentation-ux-review.md) · [실환경 기록](real-verl-demo.md)
