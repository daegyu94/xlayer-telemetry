# Metrics · Source와 Scope부터 읽기

**찾는 순서:** Scope → producer → 실제 이름 → unit → 관측 window → 비교 조건.

```{admonition} Contract와 관측
:class: important

`config/metrics.json`에 이름이 있다는 사실은 built-in producer나 해당 배포의 수집 성공을 보증하지 않습니다. 실제 exporter 이름·label을 endpoint와 Prometheus에서 확인합니다.
```

## 빠른 catalogue

| 용도 / Scope | 실제 이름 | Source / 관측 | 이 값으로 판단하지 않는 것 |
| --- | --- | --- | --- |
| 완료 step / Run·observer·worker | `training_step_time_seconds` | VERL logger bridge 또는 명시적 SDK snapshot; seconds | 정확한 phase 실행 순서 |
| 완료 stage / worker·phase | `rl_stage_duration_seconds` | Logger의 `timing_s/*`; seconds | 현재 진행 중 phase의 경과 시간 |
| 완료 throughput / GPU 단위 | `training_tokens_per_second_per_gpu` | VERL `perf/throughput`; tokens/s/GPU | 전체 cluster tokens/s |
| GPU / device | `telemetry_gpu_utilization_percent` | GPU sampler; sampled percent | 선택한 Run의 utilization |
| Local disk / node·device | `node_disk_io_time_seconds_total` | Node Exporter counter; rate는 busy fraction | Operation p99·NVMe 포화·NAND writes |
| vLLM / endpoint·model·engine | `vllm:num_requests_waiting` | Native queue gauge | 개별 Run 대기량 |
| vLLM / engine | `vllm:kv_cache_usage_perc` | Native GPU KV usage ratio | Prefix hit ratio·Mooncake master RAM |
| Sandbox / 지정 cgroup | `sandbox_io_pressure_ratio` | Worker cgroup PSI | Container별 physical SSD bytes |
| Shared 3FS / metricName | ClickHouse distributions | 기존 service latency window | Global request p99·특정 step의 I/O 소유량 |

## 대표 metric 읽기

### training_step_time_seconds

| Field | 확인할 내용 |
| --- | --- |
| Meaning | Application이 보고한 완료 step duration |
| Source | VERL file logger bridge; generic SDK는 직접 기록 |
| Unit / scope | Seconds / Run·observer·producer·role·worker |
| Observation | 완료 snapshot이며 live phase timer가 아님 |
| Use | Comparable step regression과 symptom 선택 |
| Limit | File logger interval은 approximate; replay time은 unknown일 수 있음 |
| Related | `rl_stage_duration_seconds`, [Timeline](dashboards.md#4-agent-rl-timeline과-related-metrics) |

### node_disk_io_time_seconds_total

| Field | 확인할 내용 |
| --- | --- |
| Source / scope | Node Exporter / node·block device |
| Type / unit | 누적 counter / seconds |
| Use | Reset-aware rate로 I/O busy fraction 비교 |
| Limit | Rate lookback은 짧은 step 앞의 활동도 포함; latency percentile이 아님 |
| Related | Queue depth·mean I/O latency·IOPS·filesystem·[Storage](kv-storage.md) |

### vLLM queue와 KV

| 읽을 것 | 확인할 조건 |
| --- | --- |
| Queue / usage / preemption | 같은 endpoint·model·engine identity |
| Histogram p95 | Bucket·rate window·충분한 표본; exporter version 지원 |
| Native scope | Shared engine이며 run별 자동 attribution 없음 |
| Prefix hit / offload | GPU cache usage와 다른 metric; source를 각각 확인 |

## 값과 품질 확인

| 상태 | 처리 |
| --- | --- |
| Counter reset / 첫 표본 | Rate·delta의 충분한 표본 확인 |
| Zero denominator | Capacity/latency ratio를 0으로 만들지 않음 |
| Missing unit | Unknown으로 표시하고 다른 단위와 비교하지 않음 |
| Stale / 미래 시각 | Age·clock 확인; 현재값으로 읽지 않음 |
| Entity 교체 | 동일 identity의 baseline이 없으면 missing |

## 연결과 해석 Reference

| 찾는 것 | 기준 |
| --- | --- |
| 실제 수집 Source 전체 | [수집 범위](metrics-reference.md#what-is-actually-collected) |
| VERL scalar mapping | [Bridge mapping](metrics-reference.md#inspect-the-verl-mapping) |
| Metric schema / unit / policy | [Contract file](metrics-reference.md#read-the-contract-file) |
| Labels / cardinality | [Label rules](metrics-reference.md#choose-labels-carefully) |
| Collection / query 한도 | [Bounded collection](metrics-reference.md#bounded-collection-and-pressure-evidence) |
| Rule·sample quality | [Diagnosis Reference](diagnosis-reference.md) |

## 다음

[Source 연결](agent-rl.md) · [Context / Scope](concepts.md) · [전체 Metrics Contract](metrics-reference.md)

:::{container} xlayer-legacy-links

<a id="metrics-contract" class="xlayer-legacy-anchor"></a>

[Metrics Contract](metrics-reference.md#metrics-contract)

<a id="what-is-actually-collected" class="xlayer-legacy-anchor"></a>

[What Is Actually Collected](metrics-reference.md#what-is-actually-collected)

<a id="inspect-the-verl-mapping" class="xlayer-legacy-anchor"></a>

[Inspect the VERL Mapping](metrics-reference.md#inspect-the-verl-mapping)

<a id="understand-a-metric" class="xlayer-legacy-anchor"></a>

[Understand a Metric](metrics-reference.md#understand-a-metric)

<a id="read-the-contract-file" class="xlayer-legacy-anchor"></a>

[Read the Contract File](metrics-reference.md#read-the-contract-file)

<a id="connect-layers-with-context" class="xlayer-legacy-anchor"></a>

[Connect Layers with Context](metrics-reference.md#connect-layers-with-context)

<a id="choose-labels-carefully" class="xlayer-legacy-anchor"></a>

[Choose Labels Carefully](metrics-reference.md#choose-labels-carefully)

<a id="name-phases-by-the-work" class="xlayer-legacy-anchor"></a>

[Name Phases by the Work](metrics-reference.md#name-phases-by-the-work)

<a id="add-and-validate-a-metric" class="xlayer-legacy-anchor"></a>

[Add and Validate a Metric](metrics-reference.md#add-and-validate-a-metric)

<a id="collector-health" class="xlayer-legacy-anchor"></a>

[Collector Health](metrics-reference.md#collector-health)

<a id="bounded-collection-and-pressure-evidence" class="xlayer-legacy-anchor"></a>

[Bounded Collection and Pressure Evidence](metrics-reference.md#bounded-collection-and-pressure-evidence)

:::
