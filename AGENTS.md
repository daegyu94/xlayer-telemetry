# XLayer Telemetry 작업 지침

이 지침은 저장소 전체의 코드·설정·dashboard·문서 작업에 적용합니다.
명시적인 사용자 요청과 더 구체적인 디렉터리 지침을 우선하며, 실행 환경의 권한을 준수합니다.

## 목적과 설계 경계

XLayer는 VERL 기반 Agent RL의 telemetry를 **Collect → Correlate → Diagnose**하는 분석 계층입니다.
Run·step·phase 문맥에 application·vLLM·Ray·Mooncake·GPU·host·network·storage·sandbox 관측을 연결하고, baseline과 비교한 bottleneck candidate 및 evidence를 제공합니다.
설계 개요는 [Architecture](docs/architecture.md), 상세 한계는 [Architecture Reference](docs/architecture-reference.md), 실제 producer·단위·scope의 기준은 [Metrics Contract](docs/metrics-reference.md)입니다.

- Metrics 저장·query는 Prometheus, log 수집·조회는 Alloy/Loki, 3FS 진단 조회는 기존 ClickHouse를 사용합니다.
- Grafana는 investigation UI이며, 저수준 profiling은 Nsight Systems·PyTorch Profiler 등 기존 도구로 이어집니다.
- 새 telemetry backend, sandbox runtime·scheduler, 범용 tracing/profiler 플랫폼을 구현하지 않습니다.
- VERL 연결이 주된 사용 경로지만 공통 context·SDK·진단 모델을 VERL 전용 terminology에 묶지 않습니다.
- Optional source가 없어도 core monitoring과 저장된 artifact 조회가 동작해야 합니다.

## 코드와 수정 원본

| 위치 | 책임과 수정 시 확인할 연결 |
| --- | --- |
| [cli.py](xlayer_telemetry/cli.py), [operations/](xlayer_telemetry/operations/) | 공식 `xltel` CLI, config·health·저장된 run 조회·clock·completion |
| [metrics/](xlayer_telemetry/metrics/), [events.py](xlayer_telemetry/events.py), [sandbox.py](xlayer_telemetry/sandbox.py) | Application snapshot·event/span SDK와 외부 sandbox 계측 |
| [adapters/](xlayer_telemetry/adapters/), [step_history.py](xlayer_telemetry/step_history.py) | Framework scalar/stage 변환, 완료 step 이력과 timestamp provenance |
| [collectors/](xlayer_telemetry/collectors/) | GPU·host resource·sandbox cgroup·topology 관측; diagnosis 판정과 분리 |
| [analysis/](xlayer_telemetry/analysis/) | Backend query·baseline·rule·evidence 품질·optional LLM 분석 |
| [scripts/](scripts/README.md) | Managed lifecycle·VERL wrapper·monitoring 설치·dashboard provisioning·문서 도구 |
| [config/](config/README.md) | Metric 어휘와 JSON Schema; 사용자별 실행 설정과 구분 |
| [examples/](examples/README.md), [demos/](xlayer_telemetry/demos/) | Integration recipe·검증 예제와 명확히 구분된 synthetic data |
| [examples/dashboards/](examples/dashboards/README.md) | Dashboard JSON 원본; `scripts/provision_dashboards.py`와 `scripts/dashboard_views.py`가 활성 datasource·View에 맞춰 생성 |
| [docs/](docs/index.md) | Sphinx·MyST·Furo 사용자 문서; `docs/diagrams/*.d2`와 생성된 SVG 포함 |
| [tests/](tests/), [.github/workflows/](.github/workflows/) | CPU regression, optional 도구·실환경 검증, 문서 build·Pages 배포 |

`xltel`은 Python console entrypoint이며 core lifecycle과 workload 실행은 기존 shell launcher를 재사용합니다.
CLI에서 같은 종료·run 로직을 다시 구현하지 말고, 공통화가 필요하면 양쪽이 같은 구현을 사용하도록 작은 단계로 변경합니다.
SDK import가 collector·analysis·LLM을 적재하지 않도록 유지하고, 제거된 루트 compatibility wrapper나 standalone Step Explorer를 되살리지 않습니다.
Runtime asset을 추가·이동하면 [pyproject.toml](pyproject.toml)의 package data와 checkout 밖에서 실행하는 경로도 확인합니다.

## 유지해야 할 데이터·운영 계약

### Correlation과 evidence

- **Correlation ≠ attribution.** Node/device/shared-service의 동시 변화를 특정 run의 사용량이나 확정 원인으로 표시하지 않습니다.
- Run·node·role·worker·rank·GPU·engine/device identity와 observation scope를 query·집계·candidate·drill-down까지 보존합니다.
  다른 engine·device의 조건을 섞어 같은 entity의 강한 evidence처럼 평가하지 않습니다.
- Exact span, approximate bridge interval, sampled metric과 unknown timestamp를 구분합니다.
  원래 시각을 모르는 replay를 현재 step으로 취급하지 않고, clock 보정 시 원본 시각·reference·uncertainty를 남깁니다.
- Async trainer update가 모든 rollout·tool span을 포함한다고 가정하지 않습니다.
  Current/baseline은 비교 가능한 workload·boundary scope에서 선택합니다.
- 미설정·query 실패·no data·stale·unknown과 실제 값 `0`을 구분합니다.
  필수 evidence가 없으면 `missing_evidence` 또는 제한된 supporting signal로 남깁니다.
- Optional LLM은 수집된 metric·baseline을 직접 분석하는 경로입니다.
  Rule 결과를 LLM 판정으로 바꾸지 않고, 모델의 evidence 참조·scope·누락 정보를 실제 입력과 대조합니다.
- Sandbox cgroup 관측과 node/device 관측, local sandbox SSD와 shared 3FS 관측을 구분합니다.
  Docker CLI를 실행한 worker의 cgroup이 container resource를 포함한다고 가정하지 않습니다.

### Labels와 출력 형식

- `sandbox_id`, `container_id`, `trajectory_id`, `request_id`, `prompt_id`, `trace_id`, `span_id`, SWE-Bench instance ID는 event attribute 등으로 보존하고 Prometheus label로 추가하지 않습니다.
- Stable node·role·worker·runtime·device 등의 label도 증가 규모를 확인합니다.
  `run_id`는 application 문맥이며 shared resource metric의 소유권을 뜻하지 않습니다.
- Counter reset, insufficient samples, 단위와 rate window를 입력 계약에 맞게 처리합니다.
- Snapshot 교체의 atomicity, JSONL partial write·재시작·중복 처리, bounded queue/cache/query 비용을 유지합니다.
  Schema version 변경과 additive field 확장은 기존 artifact 읽기에 미치는 영향을 먼저 확인합니다.

### Lifecycle와 failure boundary

- 기존 workload argv·Python 환경과 종료 코드를 보존하며 SIGINT/SIGTERM·child session cleanup을 유지합니다.
- 종료는 XLayer가 만든 process에만 적용합니다.
  PID·start time·boot ID·session 확인, `flock`, output collision 보호를 약화하지 않고 기존 Ray cluster·vLLM·사용자 process를 종료하지 않습니다.
- SDK 기록 실패와 backend 장애는 workload 결과와 분리합니다.
  Query·retry·buffer·shutdown에 상한을 두고 오류·drop·incomplete telemetry를 확인할 수 있게 합니다.
- `status`의 현재 health와 `inspect`의 저장된 artifact 상태를 구분합니다.
  `down`은 config·run 결과를 삭제하는 명령이 아닙니다.
- TOML과 trusted Bash config의 의미, CLI > environment > config > default 우선순위를 유지합니다.
  Bash config는 실행 가능한 신뢰된 입력이며 보안 sandbox로 설명하지 않습니다.
- Subprocess는 argv와 적절한 quoting을 사용하고 `eval`·문자열 shell 조합을 피합니다.
  Credential·token·민감한 workload argv를 log·예제·config 표시·검증 artifact에 노출하지 않습니다.

## 변경 절차

1. 브랜치·작업 트리와 최신 `main`을 확인하고 기존 변경을 보존합니다.
   요청한 작업의 브랜치를 `main`에서 만들며 새 기능보다 실제 call/data path와 기존 구현을 먼저 확인합니다.
2. 문제의 재현 조건과 영향을 확인합니다.
   Bug 수정은 가능한 경우 실패하는 regression test로 재현한 뒤 최소 범위를 수정합니다.
3. 연결된 계약을 함께 확인합니다.
   새 metric/source는 producer·contract·query·scope·optional missing 동작·dashboard/demo·문서, 새 config는 Python validator·shell 전달·CLI/help·package asset·문서가 영향을 받는지 점검합니다.
   관계없는 부분은 변경하지 않고 대규모 abstraction·dependency 추가는 실제 필요가 있을 때만 합니다.
4. 아래 기준에 따라 검증하고 실행하지 못한 범위를 구분합니다.
   CPU fixture·synthetic·VM 검증을 실제 GPU/VERL·Docker cgroup·3FS cluster·물리 multi-node 검증으로 보고하지 않습니다.
5. 논리적 변경만 commit하고 `Signed-off-by`를 포함합니다.
   검증 후 로컬 `main`에 `--ff-only`로 병합하고 이번 작업 브랜치만 삭제하며, remote push·remote branch 삭제는 명시적인 요청이 있을 때 수행합니다.

## 검증 방법

저장소 루트에서 프로젝트가 설치된 Python 환경을 사용합니다.
개발 환경은 `bash scripts/setup.sh` 또는 `python -m pip install -r requirements.txt -e .`로 준비하고, 기존 환경을 불필요하게 재생성하지 않습니다.
Runtime은 Python 3.10 이상이며 CPU CI는 Ubuntu 22.04/Python 3.10과 Ubuntu 24.04/Python 3.12를 검사합니다.
문서 build 환경은 `requirements-docs.txt`를 사용하는 Python 3.11 이상이며 runtime dependency와 분리합니다.

관련 test를 먼저 실행하고, 여러 component에 영향을 주는 코드·config 변경에는 전체 CPU suite를 실행합니다.
별도 lint/type checker는 현재 설정되어 있지 않으므로 새 도구나 전수 formatting을 기본 gate로 추가하지 않습니다.

```bash
python -m pytest -q
python -m compileall -q xlayer_telemetry examples
git ls-files -z '*.sh' | xargs -0 -r -n 1 bash -n
```

| 변경 영역 | 우선 확인할 regression |
| --- | --- |
| SDK·event·I/O | `test_app_metrics.py`, `test_events.py`, `test_async_sdk_io.py`, `test_sdk_initialization_failures.py` |
| CLI·config·lifecycle | `test_cli.py`, `test_cli_extensions.py`, `test_verl_local.py`, `test_verl_wrapper.py`, `test_wrapper_job_ownership.py`, `test_sidecar_shutdown.py` |
| Correlation·diagnosis | `test_diagnostics.py`, `test_diagnosis_analysis.py`, `test_metric_query_profiles.py`, `test_evidence_source_scope.py`, `test_multinode_correlation.py`, `test_time_alignment.py` |
| Optional LLM | `test_llm_diagnosis.py`; 실제 모델 추론은 별도 검증 |
| Metric·schema | `test_schema.py`, `test_config_contracts.py`, `test_app_metrics_textfile.py`, `test_collector_outcomes.py` |
| Dashboard·demo | `test_dashboards.py`, `test_dashboard_bottlenecks.py`, `test_investigation_ui_regressions.py`, `test_demo_coverage.py` |
| 문서·diagram | `test_documentation_links.py`, `test_documentation_diagrams.py` |

위 test 파일은 모두 `tests/`에 있으며 변경한 collector/source의 개별 test도 함께 선택합니다.
PromQL 검증은 `PROMTOOL`에 설치된 `promtool` 경로를 지정하며, optional tool 때문에 skip한 test를 통과한 검증에 포함하지 않습니다.
관련 example과 실제 rendering이 필요한 경우의 실행 방법은 [Examples](examples/README.md)와 [Script 안내](scripts/README.md)를 따릅니다.

## Dashboard·문서 작업

- Dashboard는 원본 JSON·provisioning helper를 수정합니다.
  `artifacts/`의 생성 JSON만 고치지 않고 UID·datasource·unit·observer/resource node·run/step/time context와 기존 유효한 링크를 보존합니다.
- 화면은 Run → Slow Step → Candidate → Evidence → Timeline/Subsystem 흐름을 우선합니다.
  상세 panel은 drill-down으로 제공하고 Loki 활성화·비활성화, missing/stale source를 모두 고려합니다.
  UI 변경은 가능하면 실제 Grafana에서 검증하며 JSON test만 실행했다면 rendering을 확인했다고 보고하지 않습니다.
- 사용자 안내는 `xltel`을 기본으로 하고 shell entrypoint는 advanced/internal 사용으로 설명합니다.
  한국어 설명과 자연스러운 English 기술 용어를 유지하며, Markdown 산문은 문단마다 하나의 물리적인 줄로 작성하고 문단 사이에 빈 줄 하나를 둡니다.
  코드·표·목록 구조와 변경하지 않은 부분의 서식을 유지하고 산문의 리터럴 `~`는 `\~`로 씁니다.
- 일반 경로를 먼저 설명하고, 설정·metric 의미·제약의 상세 내용은 해당 기준 문서 한곳에서 관리하며 다른 문서는 링크합니다.
  실행하지 않은 기능을 지원·검증 완료로 표현하지 않습니다.
  과거 검증 기록은 현재 사용 가이드와 구분하고, 작은 변경마다 별도 사용자용 보고서를 추가하지 않습니다.
- 도식은 D2를 원본으로 하고 SVG로 게시합니다.
  `docs/diagrams/*.d2` 또는 `_style.d2`를 바꾸면 pinned version을 사용하는 `python scripts/render_diagrams.py --install`로 도구를 준비하고 `python scripts/render_diagrams.py`로 재생성합니다.
  원본·SVG를 함께 commit하며 alt text·title·description과 모바일 가독성을 유지합니다.
  ASCII·Mermaid·Excalidraw 그림이나 figure 스타일 전환 UI를 추가하지 않습니다.

### Documentation experience

- 사용자 작업 기준의 Get Started → Observe → Investigate → Diagnose 흐름과 Concepts·Reference·Maintainers를 구분합니다.
  Tutorial은 처음부터 성공 확인까지, Task는 실제 작업, Concept는 해석 모델, Reference는 source·unit·scope·설정·구현 계약을 담당합니다.
- Task는 목적·얻는 것 → 준비 조건 → Configure → Start → Verify → Troubleshooting → Next로 작성합니다.
  명령 뒤에는 정상 output·exit code·artifact·UI 확인 조건을 가능한 범위에서 제시하고 실제 코드와 대조합니다.
- 긴 산문보다 짧은 bullet·표·단계·command·expected result·symptom → action mapping을 우선합니다.
  문단은 보통 2–3문장 이내이며 한 문장에서 여러 개념을 설명하지 않습니다. 필요한 배경과 상세 계약은 기준 문서에 링크합니다.
- Scope·attribution·exact/calibrated/approximate/sampled·clock uncertainty·missing/no data/zero처럼 판단을 바꾸는 제약은 본문 가까이에 semantic callout으로 둡니다.
  Callout을 모든 문단에 반복하지 않고, 계측하지 않은 기능·event·수치를 설명 편의상 만들어내지 않습니다.
- 페이지를 분리하면 기존 URL/anchor·원본 command·중요한 limitation·검증 기록을 보존하고 canonical 다음 경로로 연결합니다.
  Sidebar·landing card·본문의 Next와 README 링크를 함께 확인하며, 같은 상세 정보를 여러 문서에서 중복 관리하지 않습니다.
- Sphinx·MyST·Furo를 유지하며 가벼운 CSS/card·native table/figure로 빠르게 훑는 technical wiki 밀도를 지향합니다.
  Typography·spacing·배색의 원본은 [xlayer.css](docs/_static/xlayer.css)이며, white/light-gray·thin border·blue/indigo를 기본으로 하고 Dark·접근성도 확인합니다.
- D2는 [공통 style](docs/diagrams/_style.d2)과 [pinned renderer](scripts/render_diagrams.py)를 사용합니다.
  Node label은 1–3줄, 설명은 caption·본문에 둡니다. 불필요한 padding·connector 길이를 줄이고 색상 외에도 label·line style로 scope·optional을 구분합니다.
- Diagram은 Small/Medium/Large 역할에 맞는 compact preview를 사용하고 원본보다 과도하게 확대하지 않습니다.
  README와 docs가 같은 SVG를 사용하며 원본 확대·keyboard 접근·alt/title/description·label geometry를 유지합니다.
- Navigation·typography·diagram을 크게 바꾸면 변경 전후 사이트를 실제 build/browser에서 비교합니다.
  Desktop·laptop·narrow viewport와 Demo·VERL 연결·Slow Step investigation 경로를 확인하고, command smoke·synthetic·실제 GPU/VERL 검증을 구분해 보고합니다.

```bash
python -m pytest -q tests/test_documentation_links.py tests/test_documentation_diagrams.py
bash scripts/docs.sh build
# D2 원본·style을 변경한 경우:
python scripts/render_diagrams.py --check
```

문서 browser 검사는 [scripts/README.md](scripts/README.md#documentation-diagrams)의 Playwright 경로를 따릅니다.
Generated site는 `artifacts/docs-site/`에 두며 binary·모델·run data·대용량 capture와 함께 Git에 추가하지 않습니다.

## 실환경 검증과 정리

새 사용자·multi-process·backend failure 검증은 작업 전용 config·state·port·run directory로 격리합니다.
기존 monitoring stack, VERL 환경, Docker/VM 자원과 사용자 config·run artifact를 테스트 목적으로 reset·종료·삭제하지 않습니다.
생성한 자원의 소유 범위를 먼저 기록하고 필요한 작은 결과를 보존한 뒤 이번 작업의 process·container·temporary directory만 정리합니다.
완료 시 실제 검증 결과, skip·미실행 항목, 남은 제약과 cleanup 상태를 간단히 보고합니다.
