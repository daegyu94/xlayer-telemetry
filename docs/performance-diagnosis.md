# Execution / Performance Diagnosis

:::{container} xlayer-page-meta
**Task / Research Reference** Robust baseline → explicit execution graph → trajectory blocking path
:::

기존 Rule Diagnosis에 cohort의 변동 폭을 추가하고, 협력 workload가 기록한 SDK 관계에서 trajectory의 blocking path를 조사합니다. 새 backend·collector·framework patch 없이 저장된 artifact와 기존 진단 경로를 사용합니다.

## 지원 범위

| 기능 | 현재 구현 | 조건 / 한계 |
| --- | --- | --- |
| Robust differential diagnosis | Opt-in Median/MAD·conditional median interval·강한 판정 제한 | Application duration만 적용. Resource baseline은 기존 entity-matched 실제 window 유지 |
| Execution Dependency Graph | 같은 Run의 parent·명시 Span Link, 다른 trace/worker/Step 지원 | Framework RPC 자동 계측이나 Run ID 자동 전파는 없음 |
| Trajectory path | 완료를 기다리는 `depends_on` DAG의 observed blocking chain | Complete flat DAG·completion·clock 근거 필수. Nested inclusive span은 보류 |
| Long-tail comparison | 같은 Run·Worker context·명시 workload의 이전 trajectory cohort | 관측한 dimension의 일치이며 모든 workload 조건의 동일성은 아님 |
| Local LLM 입력 | Rule verdict 없이 robust measurement metadata 전달 | 실제 모델 추론 정확도는 이번 검증에서 제외 |

```{admonition} Correlation / Attribution / Causality
:class: important

Parent, sample 소비 관계와 완료 대기는 서로 다릅니다. 실제 blocking chain이 관측되어도 GPU·Mooncake·3FS가 지연의 원인이라는 뜻은 아닙니다. Shared resource의 ownership과 causality는 별도 evidence가 필요합니다.
```

## 1. Robust baseline 활성화

준비 조건은 기존 [Diagnosis 설정](diagnosis-reference.md#select-a-comparable-workload)과 비교할 workload scalar입니다. Source가 제공하지 않는 policy·token·tool 정보를 만들지 않습니다.

Diagnostics JSON의 `baseline`에 다음 설정을 추가합니다.

```json
{
  "baseline": {
    "match_fields": ["perf/total_num_tokens", "policy_version"],
    "relative_tolerance": 0,
    "robust": {
      "enabled": true,
      "cohort_size": 31,
      "minimum_cohort": 5,
      "z_threshold": 3,
      "relative_floor": 0.05
    }
  }
}
```

기본값은 disabled이며 기존 다섯 관측의 median에 가까운 실제 baseline window를 유지합니다. 활성화하면 같은 execution identity·설정된 workload·시간 reference의 최대 31개 이전 record에서 중복과 겹친 exposure를 제외합니다. Current와 겹친 이전 Step도 독립적인 reference로 세지 않습니다.

| 출력 | 의미 |
| --- | --- |
| `comparison.robust_baseline.signals` | Application Step / rollout / update / checkpoint / communication duration의 count·median·MAD |
| `robust_z` | `(current - median) / max(1.4826 × MAD, relative_floor × median, 1e-9)` |
| `median_interval` | Order statistics로 만든 population median의 조건부 90% 이상 interval |
| `shift_observed` | 절대 robust z가 threshold를 초과. 원인 확률은 아님 |
| `within_variation` | 이 cohort의 dispersion으로 강한 duration 회귀를 뒷받침하지 못함 |
| `insufficient_cohort` / `workload_unverified` | 강한 회귀 판정에 필요한 비교 근거 부족 |

해당 duration evidence를 사용하는 candidate는 통계 근거가 부족하면 최대 `supporting_signal`입니다. Raw 값·delta·resource pressure와 Supporting / Counter / Missing은 유지하고, `robust_baseline:SIGNAL:STATUS`를 missing에 추가합니다. Slow stage finding도 같은 cohort의 dispersion 검사를 통과해야 합니다.

```{admonition} 통계 해석
:class: note

Median interval은 독립적이고 같은 분포에서 나온 cohort라는 가정 아래의 coverage입니다. 시간상 겹치지 않아도 Step 간 autocorrelation·cache drift·policy 변화가 남을 수 있습니다. `robust_z`와 coverage를 원인 confidence나 운영 환경의 false-positive 확률로 사용하지 않습니다. Resource에 다수의 과거 backend query를 추가하거나 rolling p95/p99를 재집계하지 않습니다.
```

## 2. 실제 실행 관계 기록

기존 `EventRecorder.span()`의 `trace_id`·`parent_span_id`를 계속 사용합니다. 다른 trace의 sample을 소비하거나 작업의 완료를 기다리는 경계에는 선택적 `SpanLink`를 기록합니다.

```python
from xlayer_telemetry.events import SpanLink

# generated는 협력 rollout이 반환한 기존 SpanIdentity입니다.
with trainer_recorder.span(
    "trainer.update", phase="training", step=91,
    links=[SpanLink(generated, "consumes")],
):
    update()
```

| Relation / kind | 사용할 때 | Critical path 입력 |
| --- | --- | --- |
| Observed `parent` | 기존 span parent ID가 실제로 연결됨 | Membership만 사용; 자동 wait 추정 금지 |
| Observed `associated`, `follows_from`, `consumes` | 명시적으로 전달받은 context / sample 소비 | 제외 |
| Observed `depends_on` | 이전 operation 완료가 다음 operation 실행의 실제 전제 조건 | 사용 가능 |
| Configured | 호출자가 선언한 배포·관계 | 제외 |
| Temporal | 호출자가 선택한 시간상 관련 관측 | 제외 |
| Unknown | Parent/link 누락·충돌 identity·미확인 endpoint | 보류 |

같은 Step 번호, span containment, 가까운 timestamp만으로 edge를 만들지 않습니다. Duplicate span identity는 임의로 하나를 선택하지 않고 ambiguous로 남깁니다. Graph는 최대 4,096 input record·8,192 edge이며 초과·cycle·unresolved edge를 quality에 표시합니다. ID는 event/artifact에만 기록하고 Prometheus label을 늘리지 않습니다.

## 3. CPU 예제와 저장 artifact 확인

프로젝트 Python 환경과 기존 `xltel init` 설정을 준비한 뒤 GPU·VERL·monitoring stack 없이 generation → queue → 두 개의 async tool → completion을 실행합니다. SDK가 실제 시각과 file I/O를 기록하지만 Agent RL workload·장비 성능을 모사한 CPU 예제입니다. `--output`은 새 directory여야 하며 기존 Run을 덮어쓰지 않습니다.

```bash
python examples/research/trajectory_dependency_demo.py --output /tmp/xltel-trajectory-run
# stdout 또는 execution-analysis.json의 root_argument를 사용합니다.
xltel inspect /tmp/xltel-trajectory-run --execution-graph --root-span TRACE/SPAN
```

정상 결과는 `critical_path.status=observed_path`입니다. Generation·Queue·늦게 완료된 Tool·마지막 Generation의 path를 보이며, 동시에 실행한 다른 Tool의 duration을 합산하지 않습니다. Graph와 기존 Behavior Signature를 함께 반환하되 signature의 duration sum을 path duration으로 바꾸지 않습니다.

| 출력 | 확인할 것 |
| --- | --- |
| `path` | 명시 completion dependency에서 확인한 blocking chain |
| `observed_path_seconds` | 이 chain에 포함된 non-overlapping operation duration |
| `measured_wait_seconds` | `time_kind=wait`로 명시 계측한 span만 합산 |
| `unattributed_seconds` | Trajectory wall duration에서 path observation으로 설명하지 못한 부분 |
| `coverage` | Path duration / trajectory duration. Sampling quality나 원인 confidence가 아님 |
| `delay_candidates` | Path에서 오래 걸린 최대 8개 operation과 supporting / counter / missing |
| `status=unknown` | Complete contract·membership·dependency·clock 근거 부족 또는 nested/cyclic graph |

Caller는 root에 `execution_contract=dependency_dag`, `children_complete=true`를 명시하고, 마지막 operation에 `trajectory_completion=true`를 기록합니다. 이는 실제 instrumented boundary의 계약이며 XLayer가 trace 전체의 completeness를 자동 인증하는 기능은 아닙니다. 모든 direct child가 명시 dependency로 completion에 도달해야 하며, 일반 parent-child 계층이나 background span만으로 path를 산출하지 않습니다.

### Multi-node / history API

`build_graph(records, run_id=..., clock_quality=...)`와 `critical_path(graph, (trace_id, span_id))`를 사용합니다. Cross-node에서는 같은 reference의 calibrated span과 해당 구간을 포함하는 `clock_quality.window`, 실제 관측 노드의 aligned 상태가 필요합니다. 과거 다른 구간의 clock 검사로 새 trajectory를 인증하지 않습니다.

```python
# report는 해당 trajectory 구간·실제 관측 Node를 검사한 기존 diagnosis입니다.
clocks = {**report["clock_quality"], "window": report["analysis_window"]}
graph = build_graph(records, run_id=report["run_id"], clock_quality=clocks)
path = critical_path(graph, (trace_id, span_id))
```

Clock uncertainty 때문에 dependency 순서나 마지막 완료 branch가 구분되지 않으면 raw relation을 유지하고 path는 `unknown`입니다. 같은 Node의 calibration 갱신이 원본 branch 종료 순서를 뒤집거나 다른 종료 시각을 같은 시각으로 바꾸는 경우도 path와 delay candidate를 보류합니다. 단일 Node의 exact span은 remote NTP 검사 없이 분석할 수 있으며, CLI는 저장된 SDK record만 읽으므로 remote clock을 자동 조회하거나 과거 report를 임의로 재사용하지 않습니다.

Long-tail은 `compare_paths(current, references)`에 명시 `sequence`·`workload`와 complete path history를 전달합니다. 같은 Run·producer·role·Node·Worker context와 정확히 같은 workload의 최소 다섯 reference만 사용하며, 중복·미완성·다른 Job·다른 workload는 비교하지 않습니다. 일반 Worker Comparison이나 시간상 겹친 async Trainer Step을 trajectory ownership으로 승격하지 않습니다.

## 연구·Upstream 조사

| 근거 | 적용 / 제외한 아이디어 |
| --- | --- |
| [Pivot Tracing SOSP’15](https://jonathanmace.github.io/papers/mace2015pivot.pdf), [공개 구현](https://github.com/brownsys/tracing-framework/tree/016027d8dbba2795f1903fa80b5ab8073919ed83/pivottracing) | Baggage의 실제 propagation을 전제로 happened-before join 수행. XLayer는 SDK의 명시 ID/link만 사용하며 JVM dynamic instrumentation·분산 query engine을 도입하지 않음 |
| [CRISP ATC’22](https://sites.engineering.ucsb.edu/~sherwood/pubs/ATC-22-CRISP.pdf), [Graph 구현](https://github.com/uber-research/CRISP/blob/main/crisp/graph.py) | Blocking path·inclusive/exclusive 구분 참고. Clock으로 의존성을 추론하거나 child overflow를 보정하는 heuristic은 적용하지 않음 |
| [Robust anomaly statistics](https://arxiv.org/abs/1704.07706), [공개 MAD 구현](https://github.com/twitter/AnomalyDetection/blob/master/R/detect_anoms.R) | Outlier에 둔감한 median/MAD 참고. Agent RL cohort가 seasonal time series라는 근거가 없으므로 STL·seasonality detection은 도입하지 않음 |
| [OpenTelemetry Links](https://opentelemetry.io/docs/specs/otel/trace/api/#link), [Propagators](https://opentelemetry.io/docs/specs/otel/context/api-propagators/) | Parent와 다른 trace의 link 구분. XLayer `SpanLink`는 additive SDK metadata이며 OTel exporter·W3C carrier 자동 integration이 아님 |

2026-10-10 원격 HEAD의 관련 파일을 확인했습니다. 아래는 이번 PoC가 자동 연결한 hook 목록이 아니라, 현재 활용 가능성과 미확인 관계의 소스 근거입니다.

| Source | 관측 가능한 ID / 최소 연결 지점 | 이번 범위의 한계 |
| --- | --- | --- |
| [veRL `rollout_trace_attr`](https://github.com/verl-project/verl/blob/9058412fb308e9f2e31a794ad1d18d97d4286485/verl/utils/rollout_trace.py), [AgentLoop](https://github.com/verl-project/verl/blob/9058412fb308e9f2e31a794ad1d18d97d4286485/verl/experimental/agent_loop/agent_loop.py) | Step·sample_index·rollout_n·trace backend context; AgentLoop의 실제 await 경계 | 이 ID만으로 async Trainer가 소비한 sample이나 storage operation을 연결할 수 없음 |
| [vLLM OTel](https://github.com/vllm-project/vllm/blob/2d69083c4a09dfeb17d761ae96d2e571a628309c/vllm/tracing/otel.py), [Mooncake Connector](https://github.com/vllm-project/vllm/blob/2d69083c4a09dfeb17d761ae96d2e571a628309c/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/store/connector.py) | 기존 trace-header extraction·request/connector boundary를 선택적으로 활용할 여지 | Generation trace와 모든 KV transfer completion의 연결은 자동 지원하지 않음 |
| [Ray RuntimeContext](https://github.com/ray-project/ray/blob/master/python/ray/runtime_context.py) | Job / Task / Actor / Node / Worker ID를 명시 adapter의 event attribute로 기록 가능 | Ray Job ID를 XLayer Run ID로 자동 동일시하지 않음. 이번 작업에서 Ray 실행은 미검증 |
| [Mooncake Client Metrics](https://github.com/kvcache-ai/Mooncake/blob/38ee4194bfd136f544871b79c36f1553c93f6522/mooncake-store/src/client_metric.cpp) | Client·DFS 집계는 기존 Common Storage Correlation에서 재사용 | 집계 metric은 request별 replica 선택·3FS/SSD completion link가 아님 |
| [XLayer Sandbox SDK](../xlayer_telemetry/sandbox.py) / [EventRecorder](../xlayer_telemetry/events.py) | 호출자가 소유한 Tool / Sandbox 경계와 identity | Client-side span은 Remote Sandbox 내부 execution·resource tracing이 아님 |

## 검증과 성능

독립 [fault generator](../examples/research/diagnosis_faults.py)는 raw workload·metric·identity·freshness만 만듭니다. Oracle label은 evaluator에만 전달하며 `DiagnosticEngine`에 candidate나 정답을 주입하지 않습니다. Benchmark는 [실행 스크립트](../examples/research/performance_diagnosis_benchmark.py)를 사용합니다.

```bash
python examples/research/performance_diagnosis_benchmark.py \
  --output /tmp/xltel-diagnosis-report.json --repeats 32
```

이전 checkout과 동일한 입력을 비교하려면 `--baseline-checkout PATH`를 추가합니다. 별도 Python process에서 그 checkout의 실제 engine·SDK를 사용하며 소스나 사용자 monitoring 환경을 변경하지 않습니다.

검증 결과와 측정 조건은 [2026-10-10 CPU report](validation/performance-diagnosis-20261010.json)에 보존했습니다. 기존 `main` `5e6c886`과 같은 seed·320개 case를 비교한 결과입니다.

| 항목 | 기존 main | Robust / 신규 PoC |
| --- | --- | --- |
| Noisy tail / 짧은 cohort의 과도한 strong 판정 | 각각 32/32 | 각각 0/32; raw supporting evidence 보존 |
| GPU waiting / network context / host-memory phenotype | Supporting 기준 160/160 검출 | 160/160 유지. Network·Host는 기존 quality 제한을 보존 |
| Workload mismatch / stale / missing 보류 | 각각 32/32 | 각각 32/32 유지 |
| 전체 backend 요청 수 | 12,160 | 12,160. Query response는 injected fixture |
| Engine median / p95 CPU 시간 | 0.498 / 0.640 ms | Robust 0.799 / 0.872 ms; 기본 모드 0.506 / 0.585 ms |
| SDK synchronous JSONL 기록 | 18.72 µs/span, 약 617 B | 기존 옵션 18.68 µs, 한 link 추가 21.16 µs, 약 696 B |
| Graph + path | 기존 기능 없음 | 64 / 1,024 / 4,096 record: 0.60 / 11.24 / 62.99 ms |
| Graph + path peak traced allocation | 기존 기능 없음 | 4,096 record: 약 12.3 MiB. RSS·collector 총 메모리 측정이 아님 |

Repeated synthetic phenotype의 count이며 production 정확도·physical root-cause recall·confidence calibration 결과가 아닙니다. Report의 `exact`는 oracle의 strong-state 집합 일치, `abstained`는 부족한 근거에서 strong을 보류한 수이며 supporting 관측이나 `bottleneck_suspected` 자체를 모두 제거했다는 뜻은 아닙니다. 입력 fixture 생성은 analyzer timed region에서 제외하며 SDK 시간은 실제 로컬 file append 비용을 포함합니다. Opt-in link는 event volume을 늘리므로 모든 요청에 무조건 계측하지 않습니다.

## Limitations / Future Work

| 우선순위 | 남은 과제 | 최소 수정 지점 / 검증 |
| --- | --- | --- |
| P1 | Autocorrelation·cohort drift·여러 token/tool dimension | 기존 baseline policy / evaluator에서 block-resampling·시간 분리 holdout 비교. 충분한 실제 history 확보 후 적용 |
| P1 | Nested span의 실행·대기 분해 | Instrumented await/join event와 inclusive/exclusive contract. Nested fork/join oracle로 parallel double count·누락 검증 |
| P1 | Cross-node calibration 실제 검증 | 기존 clock preflight·SDK mapping을 물리 Node에서 검사. 이번 CPU fixture의 clock 성공을 실환경 지원으로 일반화하지 않음 |
| P2 | veRL sample → Trainer consumption | AgentLoop / replay-buffer dequeue의 명시 ID·Span Link adapter. Async 재사용·batch 혼합을 먼저 검증 |
| P2 | vLLM KV → Mooncake replica → 3FS operation | Native hook·실제 completion ID가 있는 지점만 선택 계측. 집계 latency·time overlap을 operation attribution으로 사용하지 않음 |
| P2 | LLM 평가·UI projection | 같은 independent oracle와 bounded observation contract로 실제 추론 평가. 그래프를 새 화면으로 확장하기 전에 조사 효용 확인 |

Framework 자동 propagation, 정확한 resource attribution, causal intervention, 실제 GPU·Ray·3FS·physical multi-node 검증은 완료 항목이 아닙니다. 현재 기능은 관측한 근거 안에서 후보와 불확실성을 좁히는 선택적 CPU PoC입니다.
