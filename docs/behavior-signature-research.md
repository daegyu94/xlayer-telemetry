# Behavior Signature / Triggered Profiling 연구 PoC

**목표:** 완료 Step·rollout의 compact signature를 baseline·peer와 비교하고, slow/anomaly에서 선택적 상세 관측을 요청하는 경로를 재현합니다.

```{admonition} 연구 범위
:class: note

이 API는 기존 `xltel` 수집·진단 또는 Grafana App에 자동 연결되지 않습니다. SDK import·기존 backend·기본 CLI config를 유지한 선택적 PoC입니다. GPU/VERL 성능 검증으로 해석하지 않습니다.
```

## 얻는 것

명시 cross-worker / cross-trace link와 trajectory blocking path는 [Execution / Performance Diagnosis](performance-diagnosis.md)의 선택적 API를 사용합니다. 기존 signature의 span sum·local parent relation 의미는 유지하며, path 결과와 서로 대체하지 않습니다.

| 결과 | 확인할 것 |
| --- | --- |
| Behavior signature | Boundary·entity·workload·event/resource summary와 관측 품질 |
| Differential comparison | 비교 가능한 history/peer의 duration·event·resource·relation delta |
| Candidate | Supporting / Against / Missing과 source-quality coverage |
| Profiling request | 다음 cooperative window 요청·budget·deadline·실패 상태 |

```{admonition} Scope / Confidence
:class: important

Correlation ≠ attribution ≠ causality. Node/device/shared-service의 변화는 Run 소유량이나 확정 원인이 아닙니다. `confidence_kind=source_quality`는 관측 품질이며 causality는 `not_established`입니다. 겹친 span duration의 합은 critical path·phase 점유율이 아닙니다.
```

## 준비 조건

- 프로젝트를 설치한 Python 환경. Fixture 재현에는 GPU·VERL·backend가 필요하지 않습니다.
- 결과를 저장할 **새 output directory**. 기존 결과를 덮어쓰지 않습니다.
- 실제 workload 연결은 별도 adapter와 source/clock/identity 검증이 필요합니다.

## 재현 방법

생성한 report·example·CPU profile은 Git에 추가하지 않는 `artifacts/`에 저장합니다.

```bash
python -m pytest -q tests/test_behavior_signature.py
python examples/research/behavior_signature_benchmark.py \
  --output-dir artifacts/signature-micro --real-cpu-capture
python examples/research/behavior_signature_benchmark.py \
  --output-dir artifacts/signature-long --work-iterations 4000000 \
  --iterations 5 --real-cpu-capture
```

**정상 결과:** exit code 0과 `report.json`·`example.json`이 생성됩니다. `--real-cpu-capture`는 별도 synthetic CPU computation에 standard-library `cProfile`을 실제 실행하고 `pstats`로 artifact를 읽어 검증합니다. GPU·VERL·running workload capture와 구분합니다.

## 구현과 입력 계약

구현은 [behavior_signature.py](../xlayer_telemetry/analysis/behavior_signature.py)·[triggered_profiling.py](../xlayer_telemetry/analysis/triggered_profiling.py)·[synthetic fixture](../examples/research/behavior_signature_benchmark.py)에서 확인합니다. 새 telemetry backend를 만들지 않습니다.

| API | 입력 → 결과 |
| --- | --- |
| `Boundary.from_step(record, context)` | 기존 `StepHistoryWriter` artifact → identity·workload·duration·accuracy |
| `summarize(boundary, events, observations)` | SDK event/span와 이미 query-window를 선택한 observation → compact artifact |
| `compare(signature, references, peer=False)` | history 또는 명시 peer → duration/event/resource/relation delta |
| `candidates(signature, comparison)` | CPU/GPU/network/storage 조사 후보와 supporting·against·missing |
| `TriggeredProfiler(argv=None).request(...)` | 기본 disabled. Slow/anomaly → 다음 cooperative window 요청 |

### 분석 예산

| 기본 상한 | 값 |
| --- | --- |
| Event | 4,096개 · one-record lookahead로 truncation 표시 |
| Group / relation | 각각 32개 · parent-chain 검증 O(records) |
| Observation | 64개 |
| Reference | 32개 · 중복 identity 제거 |
| Profiling cooldown / attempts | Controller 기준 60초 / 최대 4회 |
| Capture window / hook deadline | 5초 / 10초 |
| Hook 입력 / 요청 file 예산 | JSON stdin 8 KiB / `max_capture_bytes` 16 MiB |

`Boundary.from_step()`은 기존 `StepHistoryWriter`의 identity·workload·duration·accuracy를 읽습니다. Replay의 unknown 시각을 현재로 바꾸지 않으며 async update는 `trainer_update` scope입니다. History는 같은 entity의 이전 sequence, peer는 명시적으로 선택한 cohort를 사용합니다.

Observation은 `signal`·`scope`·`entity`·`source`·`unit`·`status`·`value`·`accuracy`를 갖습니다.

- Ratio와 percent를 혼합하지 않습니다. 같은 signal도 node/GPU/engine/device/interface/source/unit이 다르면 delta를 만들지 않습니다.
- Counter reset·rate window·insufficient samples는 기존 query/producer에서 처리한 결과를 전달합니다. 이 API는 raw counter에 임의 rate를 적용하지 않습니다.

Workload는 호출자가 명시하는 compact scalar dimension입니다. 비어 있거나 다른 workload는 비교를 거절하며, 동일 token 수만으로 trajectory·tool 종류·policy·parallelism이 같다고 보장할 수 없습니다. Peer는 같은 run/producer/role/step/scope/workload와 명시된 policy version에서 선택하지만 GPU·NIC·storage resource identity를 서로 대신 쓰지 않습니다. History의 context는 optional field의 존재까지 일치해야 합니다.

```{admonition} Parent relation
:class: important

Parent relation은 선택된 worker/boundary 내부에서 실제 `trace_id`와 `parent_span_id`가 연결된 span만 집계합니다. 다른 trace·누락 parent·cycle은 별도 quality count로 남깁니다. Cross-worker RPC propagation과 전역 happened-before graph는 구현하지 않았으며, clock proximity·span containment로 edge를 만들지 않습니다.
```

Relation delta는 `children_per_parent`와 `child_seconds_per_parent`를 구분합니다. Child 수만 늘어난 경우와 같은 child의 비용이 늘어난 경우를 별도로 조사할 수 있지만, duration sum은 overlapping/parallel children을 포함할 수 있습니다. Parent-child identity가 바뀌거나 baseline relation이 없으면 delta는 unknown이며 0으로 채우지 않습니다.

Event의 exact/calibrated/approximate/sampled/unknown 구분을 count로 보존하고, 원본 node 시각 범위와 보정된 correlation 시각 범위·reference·최대 uncertainty를 분리합니다. 이 범위는 관측한 record의 범위이며 연속 coverage나 critical path를 뜻하지 않습니다. 상세 timestamp·span ID·per-event 순서는 원본 artifact에서 확인합니다.

Resource와 연결한 candidate는 실제 supporting span의 current·baseline duration quality를 검사합니다. `duration_accuracy_counts`는 duration에 기여한 span만 세므로 같은 phase의 정상 exact span이나 point event가 unknown/approximate duration을 인증하지 않습니다. 숫자와 delta는 보존하되 이런 evidence는 `supporting_signal`과 quality missing으로 남깁니다.

Event·observation·group budget이 초과된 reference는 baseline에서 제외하고 `rejected_incomplete_references`에 기록합니다. 다른 complete reference가 있으면 이를 사용하며, 없으면 baseline missing을 유지합니다. Duration-specific quality가 없는 이전 signature도 읽을 수 있지만 precise resource correlation 근거로 승격하지 않습니다. 변경 검증은 [시스템 리뷰](system-review.md)에 기록합니다.

## Optional capture와 failure boundary

Hook의 표준 입력은 `profiling_request` JSON이며 `target=next_cooperative_window`를 명시합니다. VERL의 기존 profiler 설정이나 [selected-rank PyTorch profiler](../examples/pytorch/selected_rank_profiler.py)를 사용하는 협력 workload가 다음 window를 활성화하는 작은 adapter를 작성할 수 있습니다. 이 PoC는 이미 실행 중인 임의 worker에 profiler를 주입하거나 wrapper의 종료·signal 로직을 재구현하지 않습니다.

`max_capture_bytes`의 기본 16 MiB는 hook에 전달하는 요청 상한입니다. 총 artifact byte 상한은 협력 hook이 직접 지켜야 하며 controller가 외부 profiler의 파일 쓰기를 강제로 제한하지 않습니다. 따라서 일반적인 production profiler를 연결하려면 파일 예산·중단 경로·capture 존재/완전성 검증을 구현하고, hook이 detach한 process를 남기지 않도록 해야 합니다.

Delivery 성공은 `hook_delivered`, `capture_verified=false`입니다. 실행 오류·nonzero exit·timeout은 workload exception으로 전달하지 않으며 실패한 시도도 budget과 cooldown을 소비합니다. `unknown` boundary, truncated signature, 중복 요청은 상세 capture를 시작하지 않고 상태를 반환합니다. 지속 anomaly의 다음 반복은 잡을 수 있지만 한 번만 발생한 tool/rollout 지연은 다음 window에서 재현되지 않을 수 있습니다.

## 측정 방법과 한계

**기록 기준:** 2026-10-07, `main` `10130dd`에서 시작한 독립 연구 worktree의 검증입니다. 아래 수치·test count는 당시 기록이며 현재 checkout의 재실행 결과가 아닙니다.

Seed `20261007`, Python 3.12.3/Linux x86_64에서 160 boundary를 구성했습니다. 각 boundary는 기존 `EventRecorder`로 257 span을 생성하며 CPU·compute·communication·storage phase마다 64개의 event를 갖습니다. Resource evidence와 event duration은 의도적으로 합성한 입력이며 각 단일 증상에서 한 phase 비용을 5배로 늘립니다.

Normal과 네 단일 후보를 각각 32회, missing source·두 후보 혼합·workload mismatch·slow rollout/normal async update를 각각 32회 확인합니다. 별도 peer의 boundary duration과 instrumented relation의 child-cost/fanout 변화도 검사합니다. Threshold는 이 fixture 범위에 맞춘 것이므로 이 수치를 실제 원인 분류 정확도나 논문 재현으로 일반화하지 않습니다.

CPU 비용은 미리 만든 trace를 처리하면서 실제 정수 계산을 실행해 측정합니다. Warmup 1 batch 뒤 7 batch의 median/min/max를 보고하며 mode 순서는 번갈아 바꿉니다. 짧은 CPU workload는 40,000 iteration × 50 boundary/batch, 긴 CPU workload는 4,000,000 iteration × 5 boundary/batch입니다. Fixture 생성·SDK event 수집·disk I/O·backend query·실제 GPU profiler 비용은 timed region에 포함하지 않습니다.

측정 원본은 [micro report](validation/behavior-signature-20261007/micro.json)와 [long report](validation/behavior-signature-20261007/long.json)입니다. Signature byte 감소는 원본 event/log 대비 보관 artifact의 크기 비교이며 현행 SDK collection이나 Alloy/Loki ingestion volume 감소를 뜻하지 않습니다.

| Synthetic 판별 항목 | 결과 | 해석 |
| --- | --- | --- |
| CPU/GPU/network/storage 단일 후보 | 각각 32/32에서 해당 후보만 supported, slow 32/32 | Fixture에 정의한 범위에서만 구분; GPU 고장·원인 입증이 아님 |
| 정상 | 32/32에서 supported 후보 없음, slow 0/32 | 이 fixture의 false positive 0 |
| Missing source | 32/32 supporting signal과 missing 유지 | 값을 0으로 채우거나 완전한 후보로 승격하지 않음 |
| CPU+storage mixed | 32/32에서 두 후보 유지 | 한 원인으로 강제 선택하지 않음 |
| 다른 workload | 32/32 비교 거절 | Token/workload 차이를 regression으로 단정하지 않음 |
| Slow rollout + 정상 async update | 32/32 독립 판별 | Trainer update가 rollout을 포함한다고 가정하지 않음 |
| Peer | 동일 workload peer 1개, duration ratio 2.003, slow=true | 다른 peer의 device metric을 현재 entity에 대신 쓰지 않음 |
| Relation delta | 같은 child 수의 비용 증가와 child 수 64개 증가를 분리 | Count 변화와 child duration per parent를 별도 보존 |
| Optional CPU capture | 두 실행 모두 cProfile artifact 502 bytes, pstats 읽기 검증 | 별도 synthetic CPU process의 실제 capture. GPU/VERL capture는 미검증 |

| 보관 artifact 크기, 160 boundary | UTF-8 bytes | Signature 대비 |
| --- | ---: | ---: |
| Raw SDK JSONL events | 31,111,737 | 44.11배 |
| 별도 synthetic readable logs | 3,448,377 | Event와 별도로 계산한 log stream |
| Raw events + logs | 34,560,114 | 49.00배 |
| Signatures | 705,292 | Raw event 대비 97.73% 감소 |

| 실제 CPU 처리, median | 짧은 workload | 긴 workload |
| --- | ---: | ---: |
| Workload only wall time/boundary | 1.376ms | 128.649ms |
| Workload + signature wall time/boundary | 2.883ms | 131.817ms |
| Workload + signature/compare/candidates wall time/boundary | 2.975ms | 134.214ms |
| Summary/compare/candidates 자체 처리 시간 | 1.557ms | 1.690ms |
| 같은 반복의 CPU work에 대한 추가 처리 비용 비율 | 111.07% | 1.24% |
| Workload + raw JSON serialization wall time/boundary | 2.265ms | 134.112ms |

추가 비용 비율은 각 반복에서 따로 계측한 instrumentation 시간 / 같은 반복의 CPU work 시간의 median입니다. Whole-batch median끼리 비교한 wall-time 차이는 각각 116.15%, 4.33%지만, 긴 실행의 min/max 범위가 겹쳐 작은 throughput 효과를 정밀하게 추정할 수 없습니다. 긴 workload에서 확인한 1.24%는 이 Python summary 처리 구간의 paired cost이며 실제 VERL runtime overhead가 아닙니다.

크기 감소와 CPU 절감은 다른 결과입니다. Signature 처리는 raw JSON serialization보다 비쌌고 짧은 CPU boundary의 비용을 두 배 이상으로 만들었습니다. 따라서 작은 boundary에 매번 자동 연결하기보다 완료 boundary의 offline/선택적 조사에 쓰고, producer 쪽 incremental summary를 검토하기 전에 실제 workload의 cadence·cost budget을 측정해야 합니다.

검증 결과 새 regression 24개와 전체 CPU suite `1241 passed, 14 skipped`가 완료됐습니다(187.69초). 첫 실행의 skip 14건은 모두 `PROMTOOL` 미설정 때문이었습니다. 이후 기존 `promtool`을 읽기 전용으로 지정하여 해당 8개 test 파일을 다시 실행했고 `115 passed`, skip 0으로 실제 PromQL 평가도 완료했습니다(17.15초).

Documentation link/diagram 검사·Sphinx strict build·Python compile·전체 tracked shell의 `bash -n`·`git diff --check`도 통과했습니다. 전체 suite와 PromQL 재실행의 통과 수는 겹치는 case를 포함하므로 서로 합산한 수를 별도 test 개수로 표시하지 않습니다.

D2 원본·SVG·dashboard/UI는 수정하지 않았습니다. 실제 GPU·VERL·multi-node·Grafana/browser rendering은 검증하지 않았고 monitoring stack·사용자 config·기존 workload는 시작·종료·변경하지 않았습니다. Test와 optional capture의 새 child process는 종료됐으며 작은 측정 JSON만 저장소에 보존하고 generated site·profile artifact는 `artifacts/`에 남겼습니다.

## 현재 지원과 연구 gap

### Agent RL context

- **기존 구현:** `events.py`의 run/node/role/worker/rank/GPU와 trace/span, `adapters/verl.py` scalar/stage 변환
- **관측 공백:** 자동 RPC propagation·rollout별 모든 span의 연결은 없음
- **PoC 선택:** 명시 parent edge만 조인; signature가 entity와 boundary를 보존

### 완료 step

- **기존 구현:** `step_history.py`의 workload·trainer_update/rl_step·approximate/replay unknown provenance
- **관측 공백:** async trainer update에 rollout을 자동 포함할 수 없음
- **PoC 선택:** step/rollout을 별도 boundary로 요약하고 비교

### diagnosis

- **기존 구현:** `analysis/diagnosis_analysis.py`의 workload-matched history·rule·scope, `evidence_quality.py`·`clock_quality.py`
- **관측 공백:** event 반복 패턴과 peer/history 차이를 한 artifact로 보기 어려움
- **PoC 선택:** bounded count/duration/resource summary와 supporting/against/missing evidence

### 상세 profiling

- **기존 구현:** `examples/pytorch/selected_rank_profiler.py`, `examples/verl/torch-profiler.yaml`, `scripts/run_profile.sh`
- **관측 공백:** slow boundary에서 capture를 자동 요청하는 선택 정책 없음
- **PoC 선택:** cooldown·attempt budget·deadline을 둔 opt-in hook

### 전체 data path

- **기존 구현:** SDK snapshot/event → existing files/Alloy/Loki/Prometheus → run artifacts/diagnostics → Grafana; GPU/host/network/storage collector·native optional source
- **관측 공백:** sampling보다 짧은 현상·shared resource ownership·실제 원인 확정은 불가
- **PoC 선택:** observation quality를 confidence와 분리하고 조사 후보만 제시

## 검증한 논문과 공개 구현

### EROICA · NSDI 2026

[EROICA, NSDI 2026](https://www.usenix.org/conference/nsdi26/presentation/guan-yu), [paper](https://www.usenix.org/system/files/nsdi26-guan-yu.pdf)

- **핵심 아이디어:** 성능 저하 뒤 profiling하고 function behavior pattern을 비교
- **XLayer 적용:** 반복 event의 count·duration summary, history/peer delta와 bounded trigger
- **구현 확인:** 논문·공식 페이지 확인. 저자 공개 EROICA repo는 당시 검색에서 확인하지 못함; 구현 재현으로 주장하지 않음

### FLARE · NSDI 2026

[FLARE, NSDI 2026](https://www.usenix.org/conference/nsdi26/presentation/cui), [paper](https://www.usenix.org/system/files/nsdi26-cui.pdf)

- **핵심 아이디어:** lightweight full-stack tracing와 regression diagnosis
- **XLayer 적용:** 기존 optional source에 대해 supporting/against/missing를 함께 보존
- **구현 확인:** 논문·공식 페이지 확인. 해당 논문의 저자 공개 repo는 확인하지 못함; 같은 이름의 RL/RPC/Spark 프로젝트와 구분

### Pivot Tracing · SOSP 2015

[Pivot Tracing, SOSP 2015](https://www.microsoft.com/en-us/research/publication/pivot-tracing-dynamic-causal-monitoring-for-distributed-systems/), [implementation](https://github.com/brownsys/tracing-framework/tree/master/pivottracing)

- **핵심 아이디어:** propagated Baggage와 happened-before join
- **XLayer 적용:** SDK의 실제 trace/parent identity로만 제한된 parent-child relation 집계
- **구현 확인:** 공식 공개 repository의 `pivottracing`·`tracingplane`와 Baggage 설명 확인. JVM dynamic instrumentation은 이식하지 않음

### Relational Debugging · OSDI 2023

[Relational Debugging, OSDI 2023](https://www.usenix.org/conference/osdi23/presentation/ren), [paper](https://www.usenix.org/system/files/osdi23-ren.pdf)

- **핵심 아이디어:** good/bad 실행에서 causally related event의 상대 관계 비교
- **XLayer 적용:** instrumented parent-child count와 child duration per parent의 delta
- **구현 확인:** 논문이 [Perspect](https://gitlab.dsrg.utoronto.ca/dsrg/perspect)를 공개 artifact로 연결하나 접근 실패. x86 binary 분석 구현은 확인·실행하지 못함

논문·공개 구현 확인일은 2026-10-07입니다. 이 PoC는 네 시스템의 재구현이나 논문 성능 결과의 재현이 아닙니다. Agent RL에서는 heterogeneous trajectory length·policy version·tool count가 정상 패턴도 바꾸므로 동일 workload 및 boundary 비교를 먼저 요구합니다.

Pivot Tracing은 공개 Java source도 직접 읽었습니다. [PivotTracing.java](https://github.com/brownsys/tracing-framework/blob/master/pivottracing/agent/src/main/java/edu/brown/cs/systems/pivottracing/agent/PivotTracing.java)는 `use_baggage` 설정에 따라 실제/disabled Baggage API를 선택하고, [Baggage.java](https://github.com/brownsys/tracing-framework/blob/master/tracingplane/client/src/main/java/edu/brown/cs/systems/baggage/Baggage.java)는 thread-local state와 명시 `start`·`fork`·`join`을 제공합니다. 시간의 근접성 대신 실제 전달된 metadata가 연결 근거라는 경계만 가져왔고 해당 JVM 구현은 실행하지 않았습니다.

## 발전 가능성과 다음 검증

| 다음 작은 단계 | 필요한 근거 | 지금 주장할 수 없는 것 |
| --- | --- | --- |
| Real Agent RL workload에 opt-in boundary callback | Policy/version, trajectory/tool 종류, exact rollout boundary와 원래 workload exit code 보존 | 현재 fixture의 성능을 실제 VERL overhead로 일반화 |
| Producer 쪽 incremental signature | bounded count/mean/variance, raw artifact와 결과 parity, drop/restart 기록 | 현재 offline 요약만으로 수집·network 비용 감소 |
| Native profiler cooperative hook | selected rank·next-window activation, 파일 예산·artifact 완전성·실패 주입 | Dispatcher 성공이 GPU capture 성공이라는 주장 |
| 명시 RPC baggage propagation | 실제 producer/consumer에서 전달된 token, missing edge·fanout·비동기 boundary 검증 | 동시 시각만으로 causal edge 또는 attribution 생성 |
| Heterogeneous peer/history 선택 | trajectory/token/tool·policy·parallelism match와 충분한 반복, anomaly contamination 분석 | 모든 actor/engine의 duration을 동일 workload로 취급 |
| Real GPU/NIC/storage ambiguity 실험 | 자원별 정상/느림/mixed·stale/no-data case와 scope-preserving drill-down | GPU utilization 자체가 GPU 고장의 확정 증거 |

이 PoC는 생산 profiling overhead, 실제 GPU/NCCL/NIC/3FS fault localization, multi-node clock 품질, cross-process propagation을 검증하지 않았습니다. Missing/source-quality와 bounded 분석 비용을 작은 fixture로 확인한 단계이며 production 진단에는 workload별 source coverage와 profiler 완전성 평가가 추가로 필요합니다. 현재 사용자 경로와 제약은 [Diagnosis Reference](diagnosis-reference.md), [Metrics Contract](metrics-reference.md), [Time alignment](time-alignment.md)에서 관리합니다.

## 다음

[Diagnosis Reference](diagnosis-reference.md) · [Metrics Contract](metrics-reference.md) · [Time Alignment](time-alignment.md) · [기존 profiler 연결](deep-dive.md)

:::{container} xlayer-legacy-links

<a id="구현과-실험" class="xlayer-legacy-anchor"></a>

[구현과 실험](#구현과-입력-계약)

:::
