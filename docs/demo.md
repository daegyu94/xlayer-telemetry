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
