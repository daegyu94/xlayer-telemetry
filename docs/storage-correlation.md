# Storage Collection Correlation

**목표:** 선택한 Step/Trainer update와 같은 시간 범위의 3FS collection 보고값을 조회하고, Mooncake·local I/O와 구분해 조사합니다. Operation attribution이나 causality는 제공하지 않습니다.

## 얻는 것

| 관측 | 조회 / 해석 |
| --- | --- |
| 3FS distribution | Collection 초·full producer identity별 positive count·weighted mean·reported p99 max |
| 3FS reset report | 검증된 이름만 반환 report 합계; cumulative counter의 delta가 아님 |
| Gauge / unknown counter | Raw extrema·단일 report 값; 복수 report의 순서와 대표값은 unknown |
| Current / Baseline | 같은 table·metric·full entity. Host clock 미확인이면 delta 유보 |
| Grafana | 기존 Deep Dive의 3FS evidence → 원본 points·records·comparison·coverage |

```{admonition} 시간 해상도
:class: important

3FS `TIMESTAMP`는 producer가 collection을 마친 시각의 정수 초입니다. 1초 DateTime resolution은 정확한 1초 I/O 구간을 뜻하지 않습니다. Collection period는 변경·지연될 수 있고 시작 시각이 저장되지 않습니다. 짧은 phase, async 동시 rollout, clock skew에서는 shared collection context로만 읽습니다.
```

## 준비

- 기존 3FS ClickHouse와 [diagnostics config](diagnosis.md). 새 collector·backend는 필요하지 않습니다.
- 실제 사용한 metric 이름·producer identity·단위를 확인합니다. Deployment가 registry의 검증 revision과 다르면 producer 계약을 다시 확인합니다.
- Grafana 저장 report 조회에는 [Loki / Alloy](logs-events.md)가 필요합니다. Loki가 없어도 artifact·CLI를 읽을 수 있습니다.

## 1. Configure

기존 diagnostics JSON의 `threefs`에 다음 선택 설정을 합칩니다. URL·database·filter는 기존 값을 유지합니다.

```json
"time_series": {
  "enabled": true,
  "distribution_metrics": ["storage_client.overall_latency"],
  "distribution_units": {"storage_client.overall_latency": "ns"},
  "counter_metrics": [
    "storage_client.data_payload_bytes",
    "storage_client.num_completed_ops"
  ],
  "max_points": 2000,
  "host_clock_nodes": {"actual-3fs-host": "registered-node-exporter-instance"}
}
```

**정상 결과:** Config validation이 통과합니다. `host_clock_nodes`는 실제 host와 clock target의 mapping이며 보정값이 아닙니다. 기존 `threefs.clock_nodes`에도 해당 clock target을 등록해야 host screening을 수행할 수 있습니다. Mapping·clock evidence가 없으면 values는 원본으로 남고 delta는 `clock_unverified`입니다.

Mooncake의 기존 6-query `mooncake` profile은 유지합니다. Write·key ops·memory context가 필요하면 다음 companion profile을 선택합니다.

```json
"prometheus": {
  "url": "http://monitor.internal:19090",
  "metric_profiles": ["mooncake", "mooncake_storage"],
  "mooncake_master_node": "actual-cache-master-node"
}
```

**정상 결과:** `mooncake_storage`는 기존 canonical query의 write bytes, successful key ops, write failure/skips를 비교합니다. Master node가 있으면 allocation/capacity bytes와 admission request failures도 조회합니다. Master 미설정은 `mooncake:master:not_configured`이며 rollout node로 대신 조회하지 않습니다.

## 2. Read / Verify

```bash
xltel sources threefs --series --start 1770000100 --end 1770000160
xltel inspect <run-directory>
```

**정상 결과:** Source CLI는 configured series의 original timestamp·entity·kind·unit·status를 JSON으로 출력합니다. 명시 epoch는 조사 구간으로 바꾸며 source clock 기준입니다. `inspect`는 saved report의 current/baseline point 수·source/clock 상태와 full artifact 위치를 보여줍니다.

| 결과 | 다음 행동 |
| --- | --- |
| `observed` | 같은 entity의 collection point와 metric 통계를 확인 |
| `no_data` | Filter·settle·retention·metric 이름 확인. 0으로 채우지 않음 |
| `not_configured` | 조회하려는 distribution/counter metric 목록 설정 |
| `query_failed` | Query budget·schema·source 접속·point 상한 확인 |
| `budget_exhausted` | Current/baseline 전체 point 수·구간·entity filter 축소 |
| `clock_unverified` | Host mapping·clock source·reference 확인; delta로 원인 판정하지 않음 |

## 3. Investigate

1. 기존 Overview에서 completed Step/Update를 선택합니다.
2. Analyze에서 exact/calibrated span과 RPC p95의 rolling/shared 범위를 확인합니다. 3FS collection point를 phase 사용량으로 대체하지 않습니다.
3. Investigate에서 candidate의 Supporting / Counter / Missing을 읽습니다.
4. Deep Dive → **3FS evidence**에서 한 literal metric을 선택합니다. Points-only plot은 current collection 시각을 사용하며 interpolation·forward fill·zero fill을 하지 않습니다.
5. 원본 records에서 baseline의 실제 시각과 full identity를 확인하고 comparable windows·coverage를 함께 읽습니다.
6. Connector RPC → DFS batch/bytes/failures → 3FS → local I/O로 이동합니다. 서로 다른 I/O 계층의 bytes·operation·latency는 합산하지 않습니다.

```{admonition} Index time / Source time
:class: note

Loki의 저장 위치는 owner observation의 `observed_at`입니다. 실제 point는 별도의 `sample_timestamp_ms`에 보존합니다. Baseline point를 current 시간축으로 이동하지 않습니다. 동일 report가 동시 Run/phase 조사에 보일 수 있지만 각 Run의 기여량으로 배분하지 않습니다.
```

## Counter와 p99 계약

| 함정 | 유지하는 경계 |
| --- | --- |
| Reset reports 5, 7 | 반환 report 합계 12; `increase=2`로 계산하지 않음 |
| Gauge reports 5, 7 | 합계 12를 I/O로 사용하지 않음. 같은 초의 last/order는 unknown |
| Missing row | Zero suppression·idle·loss를 구분할 수 없음; no zero fill |
| 1초 cadence 가정 | 실제 collection interval이 없어 exact bandwidth/IOPS를 만들지 않음 |
| 성공 bytes / completed ops | 성공 응답 bytes와 실패 포함 ops는 다른 모집단; 임의 request size로 나누지 않음 |
| Base와 per-user counter | 같은 report의 다른 population일 수 있으므로 합산하지 않음 |
| RDMA write bytes | Read 결과 전송일 수 있음; SSD write와 구분 |
| 여러 p99 | 같은 entity의 reported p99 최대값만 보존; pooled/global p99 아님 |

Counter registry의 `source_revision`은 계약 검증 기준이며 실제 서버 version discovery가 아닙니다. [3FS recorder](https://github.com/deepseek-ai/3FS/blob/22fca04564c7cc230fd8b9523b8b92864e1dad47/src/common/monitor/Recorder.cc)와 [schema](https://github.com/deepseek-ai/3FS/blob/22fca04564c7cc230fd8b9523b8b92864e1dad47/deploy/sql/3fs-monitor.sql)를 기준으로 합니다.

## 예산과 한계

- 기본은 disabled. 기존 query·수집 cadence와 backend는 유지합니다.
- Series window 최대 1시간, metric 이름 각 종류 최대 16개, response 8 MiB, current/baseline·distribution/counter 전체 retained points 최대 2,000개입니다.
- Series를 켜면 configured table당 current/baseline 최대 4개 조회가 기존 30초 query budget을 공유합니다. Source CLI도 같은 budget을 적용합니다.
- Companion Mooncake profile은 client 5개, 명시 Master 3개입니다. Baseline이 있으면 각각 두 번 조회합니다. Clock/freshness 검사는 추가 요청을 사용할 수 있습니다.
- 실제 collection interval·complete operation coverage가 없으므로 storage candidate에 missing을 유지하고 strong 승격을 제한합니다. Quantitative confidence나 causality는 계산하지 않습니다.

## 검증 결과

격리 ClickHouse 25.1.5.31의 실제 SQL, Prometheus/promtool, controlled live demo와 fault fixture를 사용했습니다. 전체 CPU 1,451개, frontend 96개, 문서 link/diagram 10개·browser 9개가 통과했습니다. 실제 GPU/Agent RL·물리 multi-node workload는 검증하지 않았습니다.

```{figure} figures/grafana-storage-collection-points.png
:alt: Raw collection timestamp별 reported latency spike와 sparse gap을 phase 소유량으로 귀속하지 않는 Storage Deep Dive
:width: 720px

Point plot은 현재 source timestamp를 사용합니다. Baseline 원본 시각·full identity와 clock 미확인 상태는 아래 records/comparison/coverage에서 함께 확인합니다.
```

2,000개 합성 report의 Python 처리·projection 7회 median은 6.62ms, 추가 peak allocation은 약 3.89MB, serialized projection은 약 2.08MB였습니다. Backend·network·수집 비용은 이 timed region에 포함하지 않았습니다. 기본 추가 query는 0이며 opt-in series·보관량 비용은 point 상한으로 제한합니다. {download}`검증 원본<validation/storage-correlation-20261008.json>`에서 측정 범위를 확인합니다.

## P1 / P2 · TBD

다음은 이번 P0에 구현하지 않습니다. 기존 memory/DFS/native I/O metric과 중복된 collector는 제안하지 않습니다.

| 과제 | 필요성 / 효과 | 예상 계측 지점 | PoC 검증 |
| --- | --- | --- | --- |
| 실제 Memory/DFS replica 선택 | 요청한 tier와 실제 fallback·retry 구분 | Mooncake 선택 함수·Store worker의 requested/selected replica attribute | Forced memory/DFS/fallback에서 실제 선택과 대조 |
| GET/PUT operation trace | Queue→lookup/RPC→D2H→DFS→GPU-ready latency 분리 | Store client/connector enqueue/dequeue와 explicit nested SDK spans | 지연·retry·async overlap 주입, native wall time과 span 대조 |
| End-to-end context | Rollout→engine→KV→3FS 실제 parent 관계 보존 | veRL/Ray/vLLM/Mooncake adapter와 RPC metadata | Concurrent 두 Run·missing parent·restart·skew에서 cross-run 연결 0 |
| 3FS queue/network/SSD 세부 지연 | 기존 overall/server/network latency와 분리되지 않는 내부 대기 구간 | Storage queue admission/dequeue·RDMA completion·AIO boundaries | Queue/network/device 독립 지연 주입, 기존 latency와 일관성 확인 |
| Run/Worker I/O attribution | Shared report를 실제 사용량으로 귀속할 근거 확보 | Operation context propagation와 client/server request linkage | Concurrent workload·partial capture에서 unknown/coverage 보존 |

Request/key/trace ID는 event attribute로 보존하며 Prometheus label에 추가하지 않습니다. Histogram p95들을 더해 end-to-end latency나 critical path로 만들지 않습니다. 외부 프레임워크 수정·source overhead 평가는 후속 과제입니다.
