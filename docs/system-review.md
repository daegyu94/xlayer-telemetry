# System / Diagnosis Review

**목적:** 대규모 Agent RL의 실행 관계·storage evidence·baseline·반복 분석 비용을 검토하고, 재현 가능한 오류부터 수정합니다. 검토 기준은 2026-10-08의 `main` `c386d2a`입니다.

## 유지한 구조

| 경로 | 현재 지원 | 검토 결과 |
| --- | --- | --- |
| Collect | Wrapper·native exporter·SDK event/span·bounded writer | Producer와 collector를 유지; 새 backend 추가 없음 |
| Correlate | Run/node/worker/rank/GPU·명시 parent·clock provenance | 최근 Event→Step·multiworker UI 수정 유지; Python 비교의 identity 보강 |
| Diagnose | Workload policy·entity-matched query·candidate·counter/missing | Stage와 step의 cohort 통일; 3FS·signature 품질 오류 수정 |
| Operate | Query budget·isolated analysis deadline·JSONL cache·bounded retry·owned cleanup | 기존 제한 유지; backend 내부 scan/cancellation은 별도 과제 |
| Present | Canonical dashboard query·Grafana App/Scenes·Deep Dive | UI·datasource·Loki schema 재구현 없음; evidence labels를 그대로 전달 |

```{admonition} 검증 범위
:class: important

Synthetic/fault fixture·Python 실측·격리 ClickHouse SQL 검사입니다. 실제 veRL sync/async 학습, GPU·RDMA·Mooncake/3FS workload, 물리 multi-node 성능 검증은 실행하지 않았습니다. Correlation ≠ attribution ≠ causality를 유지합니다.
```

## 우선순위

난이도는 수정 범위, 효과는 오류 판정·반복 비용의 영향, 검증 가능성은 현재 환경의 재현 경로를 기준으로 판단합니다.

| 우선순위 / 상태 | 코드 근거와 문제 | 난이도 / 효과 / 검증 |
| --- | --- | --- |
| P0 · 구현 | `diagnosis_analysis.py`·`diagnostics.py`: step과 stage가 다른 history filter 사용; mode·explicit identity·policy version 혼합 | 낮음 / false slowdown·불필요 query 방지 / regression·10만 이력 실측 |
| P0 · 구현 | `ThreeFSClient.query_window()`·`_findings()`: metric-only grouping·matching으로 client/operation identity 소실 | 중간 / 잘못된 storage regression 방지 / actual ClickHouse·multi-entity fixture |
| P0 · 구현 | `behavior_signature.compare()`·`candidates()`: truncated baseline·unrelated exact span으로 quality가 가려짐 | 낮음 / 후보의 잘못된 승격 방지 / unknown·mixed·partial fixture |
| P1 · 남음 | `clock_quality.assess_clocks()`: aggregate callback이 query-result warning/discard count를 받지 않음 | 낮음 / partial clock evidence 보호 / 재현 fixture; production 영향 추가 검증 |
| P1 · 남음 | `prometheus.py`·`ThreeFSClient._query_rows()`: client/outer deadline과 backend 내부 실행·scan 상한은 별개 | 중간 / backend query 비용 제한 / 서버 cancellation·timeout 실험 필요 |
| P1 · 관측 gap | `StepHistoryWriter`·SDK adapter: tool mix·rollout concurrency·policy 적용 경계의 coverage는 producer별 제한 | 중간 / 비교 불가능한 workload 구분 / 실제 producer hook 필요 |
| P2 · 관측 gap | `behavior_signature.py`: worker 내부 parent relation만 요약; cross-worker execution propagation 미구현 | 높음 / RPC·retry·fanout 분석 / 실제 Ray/veRL adapter와 adversarial replay 필요 |
| P2 · 남음 | `JSONLCache`·`StepHistoryWriter._seen`: 반복 scan과 decoded-object/seen-set 메모리는 long run 크기에 영향 | 중간 / 대규모 반복 처리 / bounded index·rotation·RSS 측정 필요 |
| P2 · 남음 | App `module.tsx`: 네 workspace의 rendering 책임이 한 module에 집중 | 중간 / 유지보수성 / 기존 browser journey를 보존하며 분리 가능 |

이미 구현된 entity/freshness selection·clock calibration·counter-reset·query deadline·cache·worker cleanup을 새 기능 후보로 중복 제안하지 않습니다. P1/P2는 현재 한계이며 지원 완료로 표시하지 않습니다.

## 적용한 변경

### 1. 같은 cohort로 Step / Stage 비교

- Same run/node/worker/boundary/mode와 명시된 cluster/producer/role/rank/local-rank/GPU를 확인합니다.
- Future·unknown·clock-discontinuity 이력을 제외하고 최근 유효 5개를 한 번의 bounded-memory scan으로 유지합니다.
- Step은 그 cohort의 duration median에 가까운 record, stage는 같은 cohort의 stage median을 사용합니다.
- 선택한 policy-version dimension은 exact match; token 수 등의 연속값은 기존 tolerance를 유지합니다.
- Unconfigured workload dimension은 추정하지 않습니다. 기본 `workload_comparability=unverified`와 configured field missing 동작을 유지합니다.

### 2. 3FS entity와 contributor 보존

공식 [3FS monitoring schema](https://github.com/deepseek-ai/3FS/blob/22fca04564c7cc230fd8b9523b8b92864e1dad47/deploy/sql/3fs-monitor.sql)의 identity 열로 grouping합니다. Current·baseline과 latency·request-size evidence를 같은 producer identity에서만 연결합니다.

| 입력 상황 | 수정 후 |
| --- | --- |
| 같은 metric, 다른 client/node/method | 다른 entity; baseline delta·regression으로 결합하지 않음 |
| 같은 entity의 `count>0` p99=10 + `count=0` retained p99=999 | P99·max·weighted mean·freshness는 유효 contributor만 사용 |
| 여러 entity의 p99 | Global/window p99로 재집계하지 않음 |
| Partial/duplicate identity | 임의 first/last 선택 없이 비교 제외 |
| Legacy metric-only artifact | Legacy끼리만 비교; explicit entity와 결합하지 않음 |
| 1,000 entity 초과 | 명시적 query failure; filter를 좁혀 재조회 |

`max_observed_p99`는 **같은 entity의 보고 구간별 p99 최대값**입니다. Global quantile, SSD latency, 특정 Run의 3FS 사용량을 뜻하지 않습니다. Reporter가 empty digest를 게시하지 않는 순정 경로와 별개로, schema상 허용되는 zero-count/custom ingest의 retained extrema도 방어합니다.

### 3. 실제 supporting span의 품질 검사

- Duration에 기여한 span만 `duration_accuracy_counts`에 집계합니다. Point event는 duration quality를 인증하지 않습니다.
- Current와 matching baseline의 quality를 비교 결과에 함께 보존합니다.
- Unknown/approximate/mixed duration은 수치를 보존하지만 precise resource-linked 후보로 승격하지 않습니다.
- Truncated reference는 제외하고 rejection count를 남깁니다. Complete reference가 없으면 missing을 유지합니다.
- 이전 signature의 duration-specific quality가 없으면 unknown으로 해석합니다.

## 재현

저장소 루트의 설치된 Python 환경에서 실행합니다. Output directory는 새 경로여야 하며 기존 artifact를 덮어쓰지 않습니다.

```bash
python -m pytest -q tests/test_baseline_cohort_integrity.py \
  tests/test_storage_distribution_identity.py tests/test_behavior_signature.py
python examples/research/system_review_benchmark.py \
  --baseline-revision c386d2a --history-size 100000 --repeats 7 \
  --output-dir artifacts/system-review-new
```

**정상 결과:** regression 통과, `report.json` 생성. Normal fixture의 baseline과 query 수는 유지되며 incompatible mode/policy의 baseline은 제외됩니다. Benchmark는 명시된 trusted repository revision의 Python 코드를 읽어 비교하므로 full Git checkout이 필요합니다.

Actual ClickHouse 검사는 **작업 전용 disposable container**를 준비한 뒤 실행합니다. Test는 UUID 이름의 in-memory database만 만들고 `finally`에서 제거합니다.

```bash
XLAYER_TEST_CLICKHOUSE_CONTAINER=<owned-test-container> \
  python -m pytest -q tests/test_storage_distribution_clickhouse.py
```

**정상 결과:** 3개 SQL/engine regression 통과. Container 미설정 시 이 3개 검사는 skip이며 CPU test 통과 수에 포함하지 않습니다. Query는 `clickhouse-client`로 실제 실행하며 HTTP transport·3FS workload 성능 검증을 대신하지 않습니다.

## 측정 결과

원본 결과는 [system validation](validation/system-review-20261008.json)에 보존합니다. 10만 same-worker history를 미리 구성하고 before/after 순서를 번갈아 7회 측정했습니다. Input construction·disk I/O·network·backend 실행은 timed region에서 제외합니다.

| Python diagnosis fixture | 이전 | 수정 후 | 해석 |
| --- | ---: | ---: | --- |
| Wall median | 162.1ms | 136.5ms | 최종 통합 fixture에서 15.8% 감소 |
| CPU median | 162.1ms | 136.4ms | Python 처리 비용; workload throughput 아님 |
| 추가 peak Python allocation | 839,885 bytes | 39,117 bytes | 95.3% 감소; input history·전체 RSS 제외 |
| Normal backend request 수 | 24 | 24 | 같은 baseline 유지; 정상 query 생략 없음 |
| 다른 execution mode / policy fixture | 24 | 12 | 비교 불가능한 baseline query 제외 |

Unknown span이 unrelated exact span으로 승격되던 fixture는 `supported_candidate → supporting_signal`, truncated reference는 `reference_count 1 → 0`으로 바뀝니다. Duration과 raw evidence는 제거하지 않습니다.

기존 signature benchmark도 seed `20261007`로 재실행했습니다. CPU/GPU/network/storage 단일 후보와 정상은 각각 8/8, missing·mixed·workload mismatch·slow rollout/normal async update도 각각 8/8에서 fixture의 기대 상태를 유지했습니다. 이는 합성 관측 범위의 판별이며 실제 subsystem fault detection 정확도가 아닙니다.

40 boundary의 raw SDK event 7,777,936 bytes에 대해 summary는 184,405 bytes로 97.63% 작았습니다. 기존 SDK 수집·Loki ingest volume을 줄인 결과는 아닙니다. 짧은 CPU computation에서 summary/compare/candidates의 추가 처리 비용은 약 1.60ms/boundary였고, 별도 synthetic CPU process의 cProfile artifact 502 bytes를 실제 읽어 검증했습니다. 따라서 이 연구 API를 가벼운 기본 수집 경로로 자동 연결하지 않습니다.

| 최종 검증 | 결과 |
| --- | --- |
| 전체 CPU suite | 1,369 passed, skip 0; 실제 promtool·격리 ClickHouse 3개 포함 |
| Grafana App | 81 passed, typecheck·build 통과; frontend source 변경과 Grafana 화면 재검증 없음 |
| Documentation | Link/diagram 10 passed, strict Sphinx build 통과 |
| Docs browser | 9 passed; 새 review page 포함 320/390/430/900/1280/1440px overflow·page error 검사 |
| 독립 리뷰 | Blocking 문제 없음; unknown/mixed/legacy/truncated 메모리 fixture 4개 확인 |
| Cleanup | 작업 전용 database·ClickHouse container·generated benchmark output 정리; 기존 사용자 service 유지 |

## 다음 실험

| 질문 | 필요한 근거 |
| --- | --- |
| Policy 적용 후 KV 변화가 특정 rollout과 연결되는가? | 실제 worker `weights.applied`·cache namespace/version·generation parent context |
| GET/PUT와 3FS I/O amplification을 구분하는가? | Logical KV bytes·client GET/PUT bytes·backend physical I/O·operation identity; 공통 clock·sampling 계약 |
| Workload 변화와 resource regression을 구분하는가? | Token/sequence 분포·tool mix·concurrency를 실제 producer에서 기록하고 comparable cohort 검증 |
| 대규모 diagnosis query 비용을 예측하는가? | Scene/analysis별 latency·scanned rows/bytes·cancelled query·partial availability 측정 |
| Long run 메모리를 제한하는가? | Streaming/index 접근·rotation invalidation·decoded-object RSS·lossless recovery 검증 |

GET/PUT·3FS device/service counter를 시간상 나란히 놓는 것만으로 read/write amplification이나 causality를 계산하지 않습니다. 필요한 observation이 없으면 missing evidence로 남깁니다.

## 반복 분석 비용 리팩토링

2026-10-09의 `main` `2ae3a48`을 기준으로 CLI·SDK·collector·query·diagnosis·Grafana의 책임과 의존성을 조사했습니다. [Ponytail](https://github.com/DietrichGebert/ponytail/blob/main/skills/ponytail/SKILL.md)의 기존 구현 재사용·작은 변경·검증 경계 보존 원칙을 적용했으며, 아래 두 항목만 수정했습니다.

| 판단 | 코드 근거 | 적용 / 유지 |
| --- | --- | --- |
| 수정 | `DiagnosticEngine.analyze()`가 vLLM engine마다 세 metric 목록을 다시 검색 | `diagnosis_analysis.select_vllm_observations()`로 순수 선택 로직 분리; entity index를 한 번 구성하고 공통 engine만 평가 |
| 수정 | `JSONLCache.read()`가 변경 없는 파일에서도 모든 record reference를 복사 | Complete record를 추가할 때만 목록 복사; 진행 중 reader의 기존 snapshot 보존 |
| 유지 | Baseline의 최근 5개 cohort·query budget·isolated deadline·clock/quality gate | 이미 bounded 처리와 regression이 있음; 판정 기준·query 수·failure boundary 유지 |
| 보류 | `diagnostics.py`의 client/persistence 책임, App `module.tsx`의 rendering 집중 | 파일 크기만으로 분리하지 않음; 안정적인 API·HTTP hook·browser 검증을 동반한 별도 변경 필요 |
| 보류 | Health와 artifact의 작은 JSON loader 중복 | 현재 error·non-object 처리 결함이 재현되지 않음; 공통 helper 추가 효과가 작음 |

### 정확성 확인

- 같은 engine의 queue·KV·preemption만 선택합니다. Duplicate identity·unrelated entity는 계속 missing evidence입니다.
- Percent/ratio 변환과 deterministic tie-break를 유지합니다. Missing source를 측정값 `0`으로 대체하지 않습니다.
- Cache의 append·split UTF-8·rotation·truncate·overflow 동작을 유지합니다. Append 중 기존 reader에 새 record가 섞이지 않습니다.
- 변경 전후 101개 synthetic report에서 생성 시각·실행 시간만 제외하고 전체 결과와 query 수가 일치했습니다. Missing preemption·duplicate engine·empty queue·일부 identity mismatch와 shuffled response를 포함합니다.

### 처리 비용 실측

입력을 먼저 구성하고 warm-up 후 before/after 실행 순서를 번갈아 7회 측정했습니다. Wall/CPU 중앙값과 별도 `tracemalloc` peak입니다. Fake backend는 기존 통계 형식의 응답을 반환하며 network·실제 exporter·GPU·ClickHouse 비용을 측정하지 않습니다.

| Synthetic 경로 | 이전 | 수정 후 | 의미 |
| --- | ---: | ---: | --- |
| 500 engine, 전체 rule analysis wall | 140.5ms | 5.1ms | Metric당 반복 목록 검색 제거; deterministic 정렬 유지 |
| 같은 분석 CPU | 140.5ms | 5.1ms | Python 처리 비용 |
| 같은 분석 추가 peak allocation | 552,120 bytes | 475,674 bytes | 입력 fixture 메모리·전체 RSS 제외 |
| 같은 분석 backend request | 24 | 24 | Query budget·수집 coverage 변경 없음 |
| 변경 없는 5만 record cache 조회 wall | 0.995ms | 0.889ms | 전체 record 순회는 여전히 필요 |
| 같은 cache 조회 추가 peak allocation | 408,740 bytes | 8,684 bytes | Cache에 보존된 decoded object 메모리는 제외 |

Regression은 latency 숫자를 gate로 쓰지 않고 identity 검사 횟수와 메모리 상한을 검사합니다. 기존 cache·baseline·quality·scope 테스트와 함께 실행합니다.

```bash
python -m pytest -q tests/test_diagnosis_analysis.py tests/test_incremental_cache.py \
  tests/test_diagnostics.py tests/test_repository_regressions.py tests/test_package_structure.py
```

**검증 결과:** 관련 검사 92개, 전체 CPU suite 및 CI helper 1,523개 통과. 실제 promtool을 사용했으며 격리 ClickHouse 미설정으로 SQL 통합 검사 6개는 skip입니다. Python·shell syntax, 문서 link/diagram 10개와 strict Sphinx build도 통과했습니다.

Frontend·dashboard·upstream 코드는 수정하지 않았으며 실제 GPU/veRL·물리 multi-node·3FS workload와 화면 rendering은 이번 리팩토링의 검증 범위가 아닙니다. Decoded cache와 history 전체의 장기 메모리 비용은 이번 transient-copy 개선으로 해결되지 않습니다.
