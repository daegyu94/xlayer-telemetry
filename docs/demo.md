# Demo 실행 · GPU 없이 조사 흐름 익히기

:::{container} xlayer-page-meta
**Tutorial** GPU 없이 수집 → Step → Evidence → Grafana 확인
:::

**목표:** 별도 synthetic stack에서 Run → Step → Baseline → Evidence → Timeline을 따라갑니다.

## 얻는 것

- VERL·vLLM·Ray 없이 채워지는 metric graph.
- Synthetic Step 127, candidate, tool/sandbox span과 lifecycle event.
- 실제 Prometheus·Grafana·Loki·Alloy를 사용하는 조사 경로.

```{admonition} Synthetic
:class: important

실제 GPU·VERL·3FS 성능 측정이 아닙니다. Live resource curve와 precomputed diagnosis는 독립적인 예제입니다. 3FS evidence는 fixture이며 ClickHouse에 query하지 않습니다.
```

### Synthetic Resource Profiles

- **Storage:** Node·SSD별 read/write 강도와 queue가 다르며, 부하에 따라 mean latency도 변합니다. 기존 diskstats의 처리 시간 counter / ops counter로 조회하는 평균값이며 p99나 3FS service latency를 생성하지 않습니다.
- **GPU:** 같은 Device profile에서 Utilization·Power·Temperature·Clock·Memory를 생성합니다. Process memory는 Device used memory를 넘지 않으며, 별도 DCGM native fixture의 MiB와 sampler의 bytes를 합산하지 않습니다.
- **Host / Network:** Compute·DS·Metadata 역할별 CPU·Memory·RX/TX·RDMA 패턴을 구분합니다. CPU mode rate 합과 link capacity 범위를 유지하며 transmit-wait는 native ticks입니다.

같은 topology·scenario·상대 시각에서는 같은 패턴을 재생합니다. Profile의 계수와 capacity는 demo용 설정이며 검증된 장비 특성·노드 간 traffic path·Run별 resource ownership을 뜻하지 않습니다. Availability·clock 상태와 정상 `0`에는 변동을 추가하지 않습니다.

## 준비 조건

- [Quickstart](quickstart.md)의 CLI 설치 완료.
- 저장소 루트와 활성화한 telemetry Python 환경.
- Demo port `23000`, `29090`, `23100`, `22345`, `29110` 사용 가능.

## 1. 전용 Config 준비

```bash
mkdir -p "$HOME/.config/xlayer"
cp -n examples/synthetic-demo.toml "$HOME/.config/xlayer/demo.toml"
export XLAYER_CONFIG="$HOME/.config/xlayer/demo.toml"
xltel config validate
```

**정상 결과:** validate가 exit code `0`으로 끝납니다. 기존 `demo.toml`이 있다면 파일을 읽고 의도한 `TELEMETRY_HOME`·`TOOLS_DIR`·port인지 확인합니다.

## 2. 도구 설치와 시작

```bash
xltel doctor
# 도구가 없는 경우에만 설치합니다.
xltel install-tools
DEMO_LIVE=1 DEMO_PORT=29110 xltel up
xltel status
```

**정상 결과:** `Monitoring ready`와 Grafana 주소가 출력됩니다. `status`에서 Prometheus·Grafana·Loki가 healthy이고 collector target이 up입니다. Rate는 최소 두 scrape 후 나타납니다.

## 3. 완료 Step과 Evidence 생성

Metric 수집 시작 후 약 30초가 지나면 새 directory에 fixture를 생성합니다.

```bash
python -m xlayer_telemetry.demos.diagnosis   --output "$HOME/telemetry-demo/runs/verl-agent-demo"   --run-id verl-agent-demo --node gpu-node-0
xltel inspect verl-agent-demo
```

**정상 결과:** `Synthetic step 127 with 3 candidates`가 출력됩니다. Inspect에 Step 127과 `storage_queue_saturation`이 나타납니다. Output이 이미 있으면 새 demo state·run directory를 선택합니다.

## 4. Grafana에서 확인

1. [Run Overview](http://127.0.0.1:23000/d/telemetry-overview)를 엽니다.
2. Cluster `demo-b300`, Run `verl-agent-demo`, Resource `All`을 선택합니다.
3. 완료 step 목록의 **127 · Duration 18.4 s**를 누릅니다.
4. **What changed? → Candidate → Evidence**를 읽습니다.
5. **Cross-Layer Timeline → Subsystems → Data & Storage**로 이동합니다.

**정상 결과:** selected record·observer·시간이 유지됩니다. Shared metric은 Run 사용량으로 표시되지 않으며, `synthetic` origin을 확인할 수 있습니다.

## 5. 종료

```bash
xltel down
unset XLAYER_CONFIG
```

**정상 결과:** `Monitoring stopped`가 출력됩니다. Config·Run·log artifact는 남습니다.

## 문제가 생겼다면

| 상태 | 행동 |
| --- | --- |
| Port 사용 중 | Demo config의 port와 `DEMO_PORT`를 함께 변경 |
| Rate만 비어 있음 | 두 번 이상 scrape 후 다시 확인 |
| 완료 step·candidate가 없음 | Fixture path·Alloy·Loki·시간 범위 확인 |
| Logs가 없음 | Log directory `verl-agent-demo`, Run context `verl-agent-demo` 확인 |
| Source가 N/A | Fixture coverage와 필터 확인; N/A를 0으로 해석하지 않기 |

## Multi-job Live Demo

기존 단일 Job·`--multi-worker`를 유지하면서 세 Job이 같은 GPU node와 infrastructure를 공유하는 선택적 App demo입니다. Model 이름은 Run metadata이며 실제 weights를 내려받거나 실행하지 않습니다.

### 준비 → 시작

- [App build 및 binary 준비](grafana-scenes-poc.md)를 완료합니다.
- 새 `--output` directory와 사용 가능한 loopback Grafana port를 선택합니다.

```bash
python grafana/xlayer-app/scripts/live_demo.py \
  --tools /path/to/existing-demo-tools \
  --output /tmp/xlayer-multi-job \
  --grafana-port 23400 --multi-job
```

**정상 결과:** `LIVE DEMO http://127.0.0.1:23400/...`와 세 Job의 schedule이 출력됩니다. 약 110초 동안 실제 scrape를 수집한 뒤 `COMPLETED MULTI-JOB ... runs=3`가 표시됩니다. 세 Run의 SDK Step·span·log와 실제 Prometheus 조회로 만든 diagnosis가 Loki에 저장됩니다.

Job별 완료 시각마다 `PUBLISHED MULTI-JOB`가 출력되고 해당 Run의 Step·진단이 먼저 보입니다. 다른 Job의 완료를 기다리지 않으며 분석 동시성은 3개로 제한합니다. 기존 isolated analyzer의 60초 deadline을 사용합니다. Model은 Run Context에 읽기 전용 metadata로 표시하며 실행한 weights를 검증한 값이 아닙니다.

| Run / metadata model | Producer 입력 / 조사할 것 |
| --- | --- |
| `demo-qwen` · Qwen2.5 | Step duration 유지. 다른 Job의 shared I/O pressure는 함께 관측될 수 있음 |
| `demo-llama` · Llama 3.1 | 두 선언 Rollout Replica. `llama-0` queue 정상, 두 node에 배치한 `llama-1` queue·KV pressure 증가·preemption metric 누락; reward source 누락 |
| `demo-deepseek` · DeepSeek distill | Actor update·checkpoint 증가와 host pressure 중첩; application snapshot stale, native preemption source 누락, engine 관계 미확인 |

### Grafana에서 확인

1. Run을 **All**로 선택하면 같은 Step·worker ID를 가진 Job들을 Run 열로 구분합니다. Job·완료 Step을 하나 선택합니다.
2. Overview의 application KPI와 shared resource card를 구분합니다. Reward missing·stale snapshot을 측정값 `0`으로 읽지 않습니다.
3. Analyze → Investigate에서 Current/Baseline의 Run과 candidate의 실제 endpoint/device identity를 확인합니다.
   Llama의 **Rollout Replica Coverage**에서는 정상 engine과 일부 metric이 누락된 혼잡 engine을 별도로 확인합니다. **Inspect endpoint** → Stage dashboard → Browser Back으로 context가 유지되는지 확인합니다. 이는 같은 process의 synthetic node labels이며 물리 multinode/TP/DP 실행은 아닙니다.
   CPU Mock Router snapshot에서는 `llama-0` endpoint가 UP여도 Router에서 제거·sleeping 상태입니다. `llama-1`은 긴 prompt·tool workload와 높은 in-flight count를 보고하며, 명시 worker-applied policy만 표시합니다. 이 불균형의 원인이 Sticky routing이라고 확정하지 않습니다. 실제 upstream Router와 weight update는 실행하지 않습니다.
4. Candidate 카드의 **Run relation unverified / configured endpoint**를 먼저 확인하고, Evidence의 `run_resource_attribution_unverified`를 읽습니다. Strong observed pressure가 있어도 특정 Job의 원인 확정은 아닙니다.
5. Deep Dive → 기존 Storage/Compute/Logs → Browser Back으로 Run·Step·node·time이 유지되는지 확인합니다. 다른 종료 시각의 Job을 조사하려면 먼저 Grafana time picker로 최근 구간을 넓힙니다.
6. 모델 정보는 Run directory의 `telemetry-manifest.json`과 `run.metadata` event에서 확인합니다. Application Prometheus label에 model을 추가하지 않습니다. vLLM의 기존 `model_name`·endpoint label은 native source identity입니다.
7. DeepSeek Overview의 **Reported Async Trainer Decision**에서 다음-update sample gap·전환 결정을 읽습니다. Synthetic logger의 관측이며 실제 trainer가 현재 sample을 기다렸다는 증거가 아닙니다.

```{admonition} 관측한 것과 검증하지 않은 것
:class: important

Schedule은 지연·pressure·누락을 만드는 producer 입력이며 diagnosis 정답을 주입하지 않습니다. 새 경로는 실제 `DiagnosticEngine`·Prometheus·Loki·Grafana를 사용합니다. Qwen의 Step이 정상이어도 공유 resource Finding은 남을 수 있으며 legacy `bottleneck_suspected`는 Job 원인 확정이 아닙니다. ClickHouse/3FS를 설정한 것처럼 표시하지 않고, operation attribution·replica selection·실제 model training·물리 multi-node·async 실행 소유 관계는 검증하지 않습니다.
```

`Ctrl-C`로 이번 launcher의 process를 종료합니다. State에는 synthetic artifact가 남으므로 종료를 확인한 뒤 지정한 demo directory만 삭제합니다. `--multi-job`과 `--multi-worker`는 각각 다른 검증 경로이며 동시에 사용하지 않습니다.

## 다음

[느린 Step 조사](dashboards.md) · [기존 VERL 연결](verl-quickstart.md) · [Fixture coverage / 상세 검증](monitoring-reference.md#check-dashboard-coverage)

## Scenario-driven Live Demo

**Scenario Explorer**는 제품의 재현·회귀 검증 진입점입니다. 상황을 선택하면 질문·기대 결과·조사 순서와 실제로 게시된 replay를 보여 줍니다. 각 replay의 **Open Run / Investigate**는 원본 Run·record·observer·시간 구간을 전달하며, 이전 시나리오의 worker/engine/candidate filter를 가져오지 않습니다.

### Scenario Coverage Matrix

| 우선순위·시나리오 | 재현하는 상황 | 실제 검증 경로·완료 기준 |
| --- | --- | --- |
| P0 `normal` | 같은 workload의 baseline/current | SDK → Loki, live scrape → Grafana; 기록된 진행·duration·후보 없는 완료 진단 확인 |
| P0 `slow-rollout` | Rollout duration·vLLM queue 증가, Storage는 baseline 유지 | 실제 SDK span과 synthetic scenario comparison; 후보 → Evidence → vLLM |
| P0 `storage-regression` | Storage latency·device pressure 동시 변화 | Shared-service 관측·누락된 경로 근거를 유지; Run attribution 금지 |
| P0 `recorded-error` | 정상 baseline 뒤 tool error 한 개 | 실제 SDK error span → Loki → Recorded errors / Timeline; 전체 실패율 추정 금지 |
| P0 `multi-job` | 세 Job·여러 node label·두 configured replica | 실제 Prometheus query·DiagnosticEngine → Loki → App; engine 조건 혼합 방지 |
| P0 `partial-telemetry` | 첫 replay의 missing/stale, 다음 replay의 source 복구 | 같은 Run identity로 source가 복구되며 과거 diagnosis는 당시 evidence를 유지 |
| P1 `async-replicas` | 명시 async trainer_update와 replica lifecycle/policy | StepHistoryWriter의 async 경계, 독립 instrumented rollout call; 전체 rollout ownership 추정 금지 |
| P0 Query failure / retry | 선택/필수 dashboard 503·404와 복구 | `context_recovery_validate.py` browser response fixture; 실제 backend 장애와 구분 |
| P0 Clock unsafe / unknown | Clock 검증 부재와 복구 | `clock_quality_validate.py` browser response fixture; phase/delta 보류·raw signal 유지 |
| 공통 UI | Light/Dark·1440/1024/390px·legend/filter·Back/Forward/Reload/share | `redesign_validate.py`, `lab_validate.py`와 기존 native panel journey |
| 공통 경계 | Zero·unknown·partial coverage·선택이 목록 밖인 상태·baseline mismatch | CPU regression과 실제 browser fixture를 조합하며 coverage를 전체 workload health로 해석하지 않음 |

Live exporter는 VERL-shaped application·vLLM·Ray·GPU·host CPU/memory·network·Mooncake·storage·sandbox source를 기존 metric contract로 노출합니다. Single-job comparison의 scenario scalar와 synthetic 3FS service 수치는 명시 producer 입력이며 실제 ClickHouse 측정이 아닙니다. Multi-job은 실제 query/diagnosis 경로를 통과하지만 부하와 node label은 synthetic입니다.

### 하나의 Grafana에서 선택·실행·재생

[App build와 binary 준비](grafana-scenes-poc.md#gpu-없이-확인) 후 Scenario Lab을 한 번 실행합니다. 사용자는 아래 Grafana URL 하나만 사용하며 시나리오별 port를 관리하지 않습니다.

```bash
python grafana/xlayer-app/scripts/live_demo.py --scenario-lab \
  --tools /path/to/existing-demo-tools \
  --output /tmp/xlayer-scenario-lab \
  --grafana-port 23400
```

**정상 결과:** `SCENARIO LAB http://127.0.0.1:23400/a/xlayer-telemetry-app/demo`가 출력됩니다. Explorer에서 시나리오 목적·기대 결과·Workflow를 읽고 **Run scenario**를 누릅니다. 실제 scrape와 SDK pair가 완료되면 **Open Run / Investigate**로 같은 Run·observer·record·window를 이어서 조사합니다.

`--scenario-lab`은 Grafana와 시나리오 제어를 같은 launcher에서 함께 실행하며 systemd 서비스로 등록하지 않습니다. 터미널을 유지하고 `Ctrl+C`로 종료하면 이 Lab이 만든 Grafana와 replay backend도 함께 정리됩니다. `--output`은 매번 새 directory를 사용하며, 기존 `scenario_lab.py` 직접 실행도 사용할 수 있습니다. 이 옵션에서는 시나리오를 Explorer에서 선택하므로 `--scenario`, `--multi-job`, `--multi-worker`, `--storage-series`, `--cycles`, `--app-package`를 함께 지정하지 않습니다.

| 동작 | 상태 전환·보존 범위 |
| --- | --- |
| Run scenario | Idle → Starting → Collecting → Ready. 새 generation·Run ID·backend store 생성 |
| Replay scenario | 이전 backend를 종료하고 같은 입력 조건을 새 generation으로 반복 |
| Stop scenario | Stopping → Stopped. Owned backend 종료, 검증 요약 보존, live query 링크 비활성화 |
| Reset demo | Resetting → Idle. 이전 replay의 관측·UI 선택 대상 제거 |
| 실패 | Error와 Data generation / Collection / Query / Diagnosis 단계의 실패 이유 표시 |

Grafana와 datasource UID는 유지하고 Prometheus/Loki/exporter만 replay별로 격리합니다. 제어 API는 격리 Lab의 `jsonData.demoControlUrl`로 설정한 [Grafana App proxy](https://grafana.com/developers/plugin-tools/how-to-guides/app-plugins/add-authentication-for-app-plugins)를 사용하며 production App에서는 기본 비활성입니다. 전환 즉시 이전 backend routing을 폐기하고 이전 generation의 늦은 query 응답은 실패로 처리합니다. 이전 replay URL은 **Replay expired**를 표시하며 새 데이터로 대체하지 않습니다.

같은 시나리오는 workload·phase duration·source fault의 입력 서명을 재현합니다. Timestamp·Run/record/span ID는 새로 생성되며 원본 시각을 현재로 바꾸거나 backdated Prometheus sample을 만들지 않습니다. **Expected results & composition**에는 기대 duration·candidate·허용된 복합 조건을, **Validation stages**에는 생성·수집·실제 query·diagnosis·UI 검증 상태를 표시합니다. UI workflow는 browser regression이 실행한 경우에만 passed로 보고하며 미실행은 pending입니다.

`partial-telemetry`는 같은 generation 안에서 missing/stale pair와 recovery pair를 연속 생성합니다. 다른 조합은 registry의 검증된 구성만 허용하며 임의의 source 조건을 합쳐 잘못된 인과관계를 만들지 않습니다. Explorer는 활성 generation의 최근 28개 관측 링크를 보관합니다. Explorer가 열려 있는 동안에는 한 번에 하나의 상태 요청으로 자동 갱신합니다. 일시적인 연결 실패에는 마지막 기록을 보존하되 `Unverified`로 표시하고 실행 명령·replay 진입을 보류하며, 연결 복구 후 같은 문맥으로 재개합니다. 선택만 한 다른 시나리오에는 기존 실행의 검증 결과를 표시하지 않습니다. 좁은 화면에서는 **Scenario** 선택 목록으로 바꾸며 선택·Back/Forward·Reload는 같은 URL 상태를 사용합니다.

기존 `live_demo.py --scenario ...`는 개별 producer 개발을 위한 advanced launcher입니다. 사용자 기본 경로와 통합 E2E는 `scenario_lab.py`의 단일 Explorer를 사용합니다.

### 자동 UI/E2E 회귀

```bash
python grafana/xlayer-app/scripts/ci_scenarios.py \
  --tools /path/to/existing-demo-tools \
  --state /tmp/xlayer-scenario-ci \
  --output artifacts/scenario-ci \
  --scenarios normal slow-rollout recorded-error partial-telemetry async-replicas
```

Playwright와 Chromium이 필요하며 기존 설치를 재사용하려면 `--browser /path/to/chromium`을 지정합니다. 정상 결과는 최상위 `validation.json`이 `passed`, `owned_processes_stopped=true`이며 `browser/validation.json`의 각 scenario가 `passed`입니다. 하나의 Grafana에서 Explorer의 실제 버튼으로 모든 시나리오를 선택·실행합니다. 실패 시 scenario·사용자 workflow·화면과 bounded service log를 남깁니다. 기존 `ci_demo.py`에는 네 KPI·disclosure·URL 회귀와 `release_review_validate.py`의 검증 소속·통신 복구·장시간 polling·Loading·Synthetic 출처·모바일 선택 검사가 연결되어 있고, Grafana CI의 scenario matrix는 정상·지연·오류·복구·async 경로를 반복 검사합니다.

Runner는 자신이 만든 process만 종료합니다. 결과를 확인한 뒤 지정한 state directory만 제거하며 browser 보고서는 Git에 추가하지 않습니다. 실제 GPU/VERL model weights·물리 multi-node clock/RDMA·3FS cluster population·장시간 overhead는 **Pending**이며 synthetic/UI 통과와 별도로 검증해야 합니다.
