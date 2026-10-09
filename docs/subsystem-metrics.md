# Subsystem KPI & Metric Coverage

:::{container} xlayer-page-meta
**Reference** 정의·수집 조건·query·진단·화면의 지원 단계
:::

**목적:** N/A의 source 조건과 query/rule 활용 범위를 확인합니다. 이름·unit·scope의 기준은 [Metrics Contract](metrics-reference.md), 조사 절차는 [Metrics Guide](metrics.md)입니다.

## 지원 단계와 배포 상태

| 구분 | 의미 | 보장하지 않는 것 |
| --- | --- | --- |
| Defined | Canonical 이름·unit·scope | Producer 존재·자동 수집 |
| Collection 조건 | VERL key·collector·native endpoint·명시 SDK hook의 조건 | 현재 machine에서 연결·성공·fresh함 |
| Queried | Default/opt-in expression의 raw source 참조 | 정상 response·완전한 sample coverage |
| Diagnosed | Rule/Finding input 또는 비교 context | Job의 확정 원인·resource 소유권 |
| Visualized | Canonical dashboard의 raw metric 참조 | App 대표 KPI에 항상 표시됨 |

```{admonition} Capability ≠ live availability
:class: important

Source unavailable, metric absent, stale, collected-but-not-diagnosed와 measured zero를 구분합니다. Shared engine/node/service 값은 Run KPI 소유량이 아닙니다. Correlation ≠ attribution ≠ causality.
```

## 대표 KPI와 N/A 조사

| KPI / subsystem | 실제 source / 연결 조건 | 진단 활용 / 해석 경계 |
| --- | --- | --- |
| Step / rollout / update | VERL `perf/time_per_step`, `timing_s/*` | 완료 observation·approximate boundary. Async update가 rollout을 소유한다는 보장 없음 |
| Worker throughput | `perf/throughput` → `training_tokens_per_second_per_gpu` | Worker/per-GPU 값. `training_tokens_per_second`와 자동 합산/대체하지 않음 |
| Reward / MFU / policy | 명시 logger key + 같은 entity의 sample age | Loss/reward/MFU를 GPU util로 추정하지 않음 |
| vLLM queue / KV | Native endpoint · instance/model/engine | Current/baseline 같은 entity; shared engine |
| Waiting reason | `vllm_waiting` opt-in + exporter gauge 지원 | Capacity/deferred 비교 context. Deferred가 KV 원인은 아님 |
| Ray | Native session/task/resource metrics | JobId·SessionName은 XLayer Run ID와 다름 |
| Mooncake | Connector / Master / DFS Client 각각 연결 | RPC percentile·batch latency·successful key bytes 구분. Backend/replica 미확인 유지 |
| 3FS | Optional ClickHouse·clock·full entity·report population | Maximum reported p99; pooled p99·Run별 I/O 아님 |
| Sandbox resources | 실제 worker cgroup v2 + node/device mapping | PSI·quota·I/O는 node 전체 관측과 다름 |
| Tool / pool lifecycle | EventRecorder / SandboxRecorder / runtime hook | Span만으로 duration·queue·active Prometheus metric을 자동 생성하지 않음 |
| Checkpoint / model load | Reported stage 또는 명시 SDK hook | Duration·logical bytes 별도. Physical bytes/replication overhead를 추정하지 않음 |

## 확인 순서

1. `xltel status`·`xltel sources`로 backend/target 상태를 확인합니다.
2. 실제 endpoint의 raw 이름·version·labels를 확인합니다. Target UP도 metric 존재·freshness를 보장하지 않습니다.
3. Collection 조건을 연결하고 producer sample age를 확인합니다.
4. Queried만 있고 rule이 없으면 context로 조사합니다. 새 bottleneck rule이 있다고 해석하지 않습니다.
5. [Multi-job demo](demo.md#multi-job-live-demo)에서 공유 pressure·미확인 관계·누락·stale 구분을 확인합니다.

## vLLM waiting reason

```json
"prometheus": {
  "url": "http://127.0.0.1:9090",
  "metric_profiles": ["vllm", "vllm_waiting"]
}
```

**정상 결과:** 지원하는 exporter에서는 `comparison.signals`에 `vllm_waiting_capacity_requests`·`vllm_waiting_deferred_requests`가 endpoint/model/engine별로 나타납니다. 미지원 metric은 no-data/missing이며 0으로 채우지 않습니다. Reason 수를 이미 계산한 total waiting에 더하지 않습니다.

기준 source는 [vLLM logger](https://github.com/vllm-project/vllm/blob/dd30786fef8b15bd83205833c7b3348b0ba0ebc7/vllm/v1/metrics/loggers.py)와 [scheduler](https://github.com/vllm-project/vllm/blob/dd30786fef8b15bd83205833c7b3348b0ba0ebc7/vllm/v1/core/sched/scheduler.py)입니다. Deferred에는 LoRA budget·KV transfer·blocked status 등의 일시적 제약이 포함됩니다. Query 두 개만 opt-in으로 추가하며 기존 rule/state를 승격하지 않습니다. Baseline이 있으면 최대 4개 metric request가 추가되고 기존 query budget을 공유합니다.

`check_source_freshness=true`이면 최대 4개 timestamp request가 추가됩니다. Multi-job fixture는 126→134 request/Run으로 확인했으며, 기존 기본 production query 수는 유지합니다. Capacity/deferred는 별도 지원 version의 관측이며 미지원 source를 추정해 채우지 않습니다.

## Checkpoint logical I/O 연결 예제

```bash
python -m examples.application.checkpoint_io --output /tmp/xlayer-checkpoint-example
```

**정상 결과:** Payload byte 수·save/load duration·logical bytes/duration snapshot과 성공 outcome을 가진 SDK span 두 개가 생성됩니다. 실패하면 span status는 error로 남고 예외를 숨기지 않습니다. 원래 framework가 아는 logical bytes를 integration hook에 전달하는 예제이며 directory scan·SSD physical writes·fsync durability·3FS attribution을 대신하지 않습니다. Output은 새 directory여야 합니다.

VERL checkpoint에 자동 연결한 것은 아닙니다. 실제 save/load 호출 경계에서 [기존 SDK](application-metrics.md)를 연결해야 합니다. 모델 로딩·sandbox queue/active도 실제 runtime 관측 없이는 만들어내지 않습니다.

## Canonical catalogue

```{include} _includes/metric-coverage.md
:heading-offset: 2
```

## 갱신과 한계

```bash
python scripts/metric_coverage.py
python scripts/metric_coverage.py --check
python scripts/metric_coverage.py --check --json /tmp/xlayer-metric-coverage.json
```

**정상 결과:** catalogue를 생성하고 `--check`는 exit code 0입니다. Contract·VERL translation·source references·query profile·dashboard를 대조합니다. Generic SDK나 demo 문자열을 자동 producer로 계산하지 않습니다. Static reference는 runtime availability/exporter 호환성 검증을 대신하지 않습니다.

Checkpoint bytes·pool queue/active·policy applied coverage·pNFS·operation tracing은 명시 producer 또는 추가 upstream evidence가 필요합니다. [Storage limitations](correlation-limitations.md)·[SDK 연결](application-metrics.md)·[Sandbox lifecycle](sandbox.md)를 따르며 과거 Coverage Audit을 현재 수집 목록으로 사용하지 않습니다.

## 검증 범위

CPU regression 1,555 passed / optional ClickHouse 6 skipped, 실제 promtool과 source/profile·SDK I/O·catalogue drift 검사, App 105개·typecheck 및 strict Sphinx·link/diagram 검사를 통과했습니다. 실제 Grafana/Prometheus/Loki Multi-job fixture에서 Llama의 capacity=10/deferred=4와 endpoint identity, 기존 candidate 상태, missing/stale·Run 이동·Browser Back을 확인했습니다. 이는 synthetic producer 입력이며 실제 veRL/vLLM queue 원인 탐지 정확도 검증은 아닙니다.

문서 catalogue는 desktop 1440/1280px와 narrow 390px에서 browser error·page overflow 없이 확인했습니다. 실제 GPU/RDMA·3FS/pNFS·framework checkpoint·sandbox pool runtime은 미검증입니다. Request 길이 histogram·모델 로딩 byte hook·pool queue/active 자동 aggregation은 이번에 구현하지 않았습니다. Source 지원과 실제 integration 경계가 준비되면 같은 계약과 optional query 경로로 후속 검증합니다.
