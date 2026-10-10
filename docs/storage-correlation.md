# Storage Correlation · Common & 3FS

**목표:** 선택한 Step/Trainer update와 같은 시간 범위의 Mooncake 관측을 조사하고, 필요한 경우 기존 3FS Deep Dive로 내려갑니다. Common Storage Correlation은 ClickHouse 없이도 동작합니다. Operation attribution이나 causality는 제공하지 않습니다.

## Common Storage Overview

| 계층 | 현재 재사용하는 관측 | 해석 경계 |
| --- | --- | --- |
| veRL Step / Phase | 완료 duration·exact/calibrated span·approximate step | Async update가 동시 rollout을 소유한다고 가정하지 않음 |
| vLLM Store connector | Operation/status/engine별 RPC p95·실패 key·error RPC | KV communication 관측; 실제 선택 replica나 physical storage latency가 아님 |
| Mooncake DFS client | Batch read/write/staging p95·delivered bytes/keys·failed/skipped keys | Adapter가 3FS인지 pNFS인지 metric 이름만으로 판정하지 않음 |
| Mooncake Master | 명시 Master node의 allocation/capacity·admission failures | Shared memory pool 상태; request별 tier/hit/replica 선택이 아님 |
| Backend-specific | 선택적 3FS ClickHouse distribution·reset reports·collection series | Source의 사용 가능성과 workload의 실제 backend는 별개 |

### Configure / Verify

기존 diagnostics JSON의 `prometheus`에 필요한 profile만 합칩니다. `threefs` 설정은 공통 분석의 전제 조건이 아닙니다.

```json
"prometheus": {
  "url": "http://monitor.internal:19090",
  "metric_profiles": ["mooncake", "mooncake_storage"],
  "mooncake_master_node": "actual-cache-master-node"
}
```

**정상 결과:** Saved report의 `storage_overview.signals`에 source별 current/baseline·unit·statistic·entity·quality가 남습니다. `mooncake`는 기존 6-query profile이며 `mooncake_storage`는 client 5개와 선택적 Master 3개를 재사용합니다. Master 미설정은 `not_configured`이며 rollout node로 대신 조회하지 않습니다.

| 상태 | 의미 / 다음 행동 |
| --- | --- |
| `observed` | Raw 값이 있음. `quality_issues`의 sampling·rolling·freshness 경계를 함께 확인 |
| `not_configured` | 해당 profile 또는 Master node 미설정 |
| `no_data` | Native metric 비활성·idle histogram·미지원 version·source 부재를 아직 구분할 수 없음 |
| `query_failed` | Backend/query 실패. 측정값 0이나 정상 상태로 바꾸지 않음 |
| `stale` / `timestamp_invalid` | 알려진 source 시각이 window 이전 / 미래. Raw만 유지 |
| `clock_unverified` / `insufficient_sampling` | Raw 값은 유지하지만 quantitative delta를 보류 |

`storage_overview.backend.status=not_reported`는 실제 adapter 정보가 없다는 뜻입니다. ClickHouse 접속 설정·DFS metric·3FS source의 `observed` 상태만으로 Mooncake가 그 backend를 사용했다고 표시하지 않습니다. Runtime 지원 capability와 현재 source availability도 구분합니다.

Mooncake 공통 Overview의 `status`는 Current 조회 상태이며 `baseline_status`는 Baseline 관측·누락·entity 비교 제한을 별도로 나타냅니다. Baseline이 비어 있어도 성공한 Current 조회를 query 실패로 표시하지 않습니다.

### Candidate / Deep Dive

1. 느린 completed Step/Update의 comparable baseline을 선택합니다.
2. `mooncake_dfs_read_pressure` / `mooncake_dfs_write_pressure`의 Supporting / Counter / Missing을 확인합니다.
3. Grafana App → **Deep Dive → Common Storage Overview**에서 source coverage를 읽습니다.
4. **Common storage**의 기존 Connector RPC·DFS batch·DFS bytes·Failures panel로 내려갑니다.
5. 3FS 관측이 필요한 경우 **3FS Deep Dive →** 또는 기존 **3FS evidence**를 선택합니다. Run/Step/Worker/time context는 유지합니다.

새 candidate는 느린 Step과 DFS p95 regression 또는 failed-key 관측이 겹칠 때 생성됩니다. Latency/error의 client identity가 다르면 supporting/counter로 결합하지 않습니다. Backend·operation 관계가 없으므로 최대 `supporting_signal`이며, client identity가 없으면 `weak_signal`입니다. 기존 clock·baseline·sampling gate와 query budget을 재사용하며 새 query는 추가하지 않습니다.

Baseline workload 조건이 선언되지 않으면 `workload_comparability_unverified`도 missing에 남습니다. Coverage에서 **Source metrics ↗**를 선택하면 실제 source node/endpoint로 이동하며 observer·Run·Step·time은 유지합니다. RPC endpoint와 숫자 engine index를 혼동하지 않습니다.

```{important}
Mooncake DFS histogram은 delivered I/O가 있는 batch에서만 관측됩니다. 전부 실패한 batch는 오류가 있어도 latency sample이 없을 수 있습니다. Checksum 실패는 delivered bytes/keys에도 기여할 수 있으므로 success와 error가 배타적이지 않습니다. 두 값을 더해 요청 수를 만들거나 `errors / (success + errors)`를 실패 확률로 계산하지 않습니다. Skipped write는 DFS 이전 replica transfer의 실패일 수 있습니다.
```

Source는 [DFS metric 정의](https://github.com/kvcache-ai/Mooncake/blob/dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2/mooncake-store/include/client_metric.h)와 [실제 DFS read/write call site](https://github.com/kvcache-ai/Mooncake/blob/dcddb56c0cddc96f41eeb8dd82e5bd9f497183a2/mooncake-store/src/client_service.cpp)입니다. Common Overview는 기존 summary query의 additive metadata를 읽으며 별도 datasource·backend·Prometheus client를 만들지 않습니다.

## 3FS Deep Dive · Implemented

아래는 기존 ClickHouse 전용 조회입니다. Common Storage와 구분하며, 저장된 distribution·time-series·counter의 기능과 해석 경계를 유지합니다.

## 얻는 것

| 관측 | 조회 / 해석 |
| --- | --- |
| 3FS distribution | Collection 초·full producer identity별 positive count·weighted mean·reported p99 max |
| 3FS reset report | 검증된 이름만 반환 report 합계; cumulative counter의 delta가 아님 |
| Gauge / unknown counter | Raw extrema·단일 report 값; 복수 report의 순서와 대표값은 unknown |
| Current / Baseline | 같은 table·metric·full entity. Host clock 미확인·서로 다른 report window/population은 delta 유보 |
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

**정상 결과:** Config validation이 통과합니다. `host_clock_nodes`는 실제 host와 clock target의 mapping이며 보정값이 아닙니다. 기존 `threefs.clock_nodes`에도 해당 clock target을 등록해야 host screening을 수행할 수 있습니다. Mapping·raw source clock evidence가 없으면 values는 원본으로 남고 delta는 `clock_unverified`입니다. Application timestamp calibration만으로 3FS producer clock을 검증하지 않습니다.

Mooncake profile은 [Common Storage Overview](#common-storage-overview)의 설정을 재사용합니다. 3FS collection 값과 client batch/key 값은 서로 대체하거나 합산하지 않습니다.

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
| `different_report_window_exposure` | Reset report 구간 길이·정수 초 bucket 수를 맞춤. 합계를 throughput 변화로 해석하지 않음 |

## 3. Investigate

1. 기존 Overview에서 completed Step/Update를 선택합니다.
2. Analyze에서 exact/calibrated span과 RPC p95의 rolling/shared 범위를 확인합니다. 3FS collection point를 phase 사용량으로 대체하지 않습니다.
3. Investigate에서 candidate의 Supporting / Counter / Missing을 읽습니다.
4. Deep Dive → **3FS evidence**에서 한 literal metric을 선택합니다. Points-only plot은 current collection 시각을 사용하며 interpolation·forward fill·zero fill을 하지 않습니다.
5. 원본 records에서 baseline의 실제 시각과 full identity를 확인하고 comparable windows·coverage를 함께 읽습니다.
6. Connector RPC → DFS batch/bytes/failures → 선택적 3FS evidence로 이동합니다. 기존 local I/O panel은 별도 related context입니다. 실제 backend/resource path가 확인되지 않으면 같은 I/O로 합산하거나 귀속하지 않습니다.

```{admonition} Index time / Source time
:class: note

Loki의 저장 위치는 owner observation의 `observed_at`입니다. 실제 point는 별도의 `sample_timestamp_ms`에 보존합니다. Baseline point를 current 시간축으로 이동하지 않습니다. 동일 report가 동시 Run/phase 조사에 보일 수 있지만 각 Run의 기여량으로 배분하지 않습니다.
```

## Counter와 p99 계약

| 함정 | 유지하는 경계 |
| --- | --- |
| Reset reports 5, 7 | 반환 report 합계 12; `increase=2`로 계산하지 않음 |
| 다른 길이의 reset report 구간 | 원본 합계는 유지하되 delta 유보. 같은 길이도 complete I/O total이나 exact rate가 아님 |
| Gauge reports 5, 7 | 합계 12를 I/O로 사용하지 않음. 같은 초의 last/order는 unknown |
| Missing row | Zero suppression·idle·loss를 구분할 수 없음; no zero fill |
| 1초 cadence 가정 | 실제 collection interval이 없어 exact bandwidth/IOPS를 만들지 않음 |
| 성공 bytes / completed ops | 성공 응답 bytes와 실패 포함 ops는 다른 모집단; 임의 request size로 나누지 않음 |
| Base와 per-user counter | 같은 report의 다른 population일 수 있으므로 합산하지 않음 |
| RDMA write bytes | Read 결과 전송일 수 있음; SSD write와 구분 |
| 여러 p99 | 같은 entity의 reported p99 최대값만 보존; pooled/global p99 아님 |
| 다른 길이·report 수의 p99 구간 | Raw extrema는 유지하되 delta·latency regression 보류. Aggregate와 series가 같은 비교 helper 사용 |

### Distribution 비교 상태

`report_count`는 positive-count distribution report 수이며 operation sample 수인 `count`와 다릅니다. `observed_second_count`는 aggregate에 실제 report가 존재하는 정수 초 수입니다. 기존 aggregate query에 두 값을 추가하며 별도 backend 요청은 하지 않습니다.

| `comparison_status` | 의미 / 다음 확인 |
| --- | --- |
| `shared_report_window` | 같은 길이·정수 timestamp slots·report 수·관측된 초 수; 반환 report의 극댓값 비교만 허용 |
| `different_report_window_exposure` | 구간 길이나 timestamp slots가 다름; 동일 exposure로 다시 조회 |
| `different_report_population` | Report 수 또는 관측된 초 수가 다름; raw records·source coverage 확인 |
| `report_population_unknown` | 이전 artifact/custom source에 report 모집단 정보 없음; 원본 값만 확인 |
| `insufficient_timestamp_resolution` | 1초보다 짧은 구간; 더 넓은 window로 조회 |
| `clock_unverified` / `producer_clock_unverified` | Source clock 미검증; [clock 검사](time-alignment.md#correlation-preflight) 확인 |

Report 수가 적은 원인이 idle·zero suppression·누락인지 추정하지 않습니다. 같은 report 수라도 complete collection coverage는 계속 `unknown`입니다. `comparison_quality`에는 양쪽 구간·report 수·관측된 초 수가 남으며, 비교 보류 상태는 저장된 projection과 optional LLM observation input에도 보존합니다.

Counter registry의 `source_revision`은 계약 검증 기준이며 실제 서버 version discovery가 아닙니다. [3FS recorder](https://github.com/deepseek-ai/3FS/blob/22fca04564c7cc230fd8b9523b8b92864e1dad47/src/common/monitor/Recorder.cc)와 [schema](https://github.com/deepseek-ai/3FS/blob/22fca04564c7cc230fd8b9523b8b92864e1dad47/deploy/sql/3fs-monitor.sql)를 기준으로 합니다.

## 예산과 한계

- 기본은 disabled. 기존 query·수집 cadence와 backend는 유지합니다.
- Common coverage는 이미 수집한 결과만 요약합니다. Profile 미선택 시 signal row를 만들지 않아 metadata는 512 bytes 미만입니다. CPU fixture의 6-signal builder 7회 × 1,000회 median은 약 11μs, 추가 peak allocation은 4.1KiB, JSON은 2.4KB였습니다. Backend query·network·UI rendering 비용은 이 측정에 포함하지 않았습니다.
- Series window 최대 1시간, metric 이름 각 종류 최대 16개, response 8 MiB, current/baseline·distribution/counter 전체 retained points 최대 2,000개입니다.
- Series를 켜면 configured table당 current/baseline 최대 4개 조회가 기존 30초 query budget을 공유합니다. Source CLI도 같은 budget을 적용합니다.
- Companion Mooncake profile은 client 5개, 명시 Master 3개입니다. Baseline이 있으면 각각 두 번 조회합니다. Clock/freshness 검사는 추가 요청을 사용할 수 있습니다.
- 실제 collection interval·complete operation coverage가 없으므로 storage candidate에 missing을 유지하고 strong 승격을 제한합니다. Quantitative confidence나 causality는 계산하지 않습니다.

## 검증 결과

격리 ClickHouse 25.1.5.31의 실제 SQL, Prometheus/promtool, controlled live demo와 fault fixture를 사용했습니다. 전체 CPU 1,457개, frontend 96개, 문서 link/diagram 10개·browser 9개가 통과했습니다. 실제 GPU/Agent RL·물리 multi-node workload는 검증하지 않았습니다.

```{figure} figures/grafana-storage-collection-points.png
:alt: Raw collection timestamp별 reported latency spike와 sparse gap을 phase 소유량으로 귀속하지 않는 Storage Deep Dive
:width: 720px

Point plot은 현재 source timestamp를 사용합니다. Baseline 원본 시각·full identity와 clock 미확인 상태는 아래 records/comparison/coverage에서 함께 확인합니다.
```

2,000개 합성 report의 Python 처리·projection 7회 median은 12.52ms, 추가 peak allocation은 약 2.96MB, serialized projection은 약 2.69MB였습니다. Backend·network·수집 비용은 이 timed region에 포함하지 않았습니다. 격리 ClickHouse의 1,000-point 조회 5회 median은 75.34ms였으며 Docker exec/client 시작·decode를 포함합니다. Production HTTP·실제 storage workload 성능은 아닙니다. 기본 추가 query는 0이며 opt-in series·보관량 비용은 point 상한으로 제한합니다. {download}`검증 원본<validation/storage-correlation-20261008.json>`에서 측정 범위를 확인합니다.

## P1 / P2 · TBD

### pNFS Deep Dive · Future Work

이번 변경에는 pNFS 기능·query profile·UI를 구현하지 않습니다. POSIX adapter의 root가 실제 NFS/pNFS mount라는 운영 근거가 있을 때만 후속 PoC를 설계합니다. Local SSD Deep Dive 확장은 이번 범위에서 제외합니다.

| 후속 단계 | 최소 수정 / 기대효과 | 난이도·예상 비용 / PoC |
| --- | --- | --- |
| Backend/adapter 선언 | Manifest/config에 실제 adapter·client node·mount namespace·export·MDS/DS inventory를 선언하고 공통 coverage metadata에 연결 | 낮음–중간; 작은 metadata 비용. 동일한 `posix` adapter의 local/NFS path를 임의 분류하지 않는 fixture 검증 |
| NFS client source | 기존 Node Exporter의 opt-in `mountstats`와 nfs counter 활용. RPC queue/response/request mean·bytes·timeouts·retransmissions 구분 | 중간; mount/operation 수에 비례한 scrape 비용. 실제 mount namespace, idle·reset·remount·missing sample에서 positive denominator와 identity 확인 |
| MDS / DS source | 각 kernel nfsd/DS host의 별도 counter·network·device evidence와 clock inventory 활용 | 중간; 선택 query만 추가. MDS metadata/RPC 지연·DS I/O 지연을 독립 주입; mountaddr를 DS로 오인하지 않음 |
| Backend-specific UI | Common Overview와 기존 context-preserving Deep Dive 선택을 재사용하되 NFS RPC 통계를 3FS p99로 변환하지 않음 | 낮음–중간; native Grafana panel 재사용. 두 source가 동시 있을 때 statistic·entity·scope 혼합 0 |
| 실제 DS / operation 관계 | Native layout/DS 정보나 bounded hook이 있을 때만 추가. 미제공이면 unknown 유지 | 높음; kernel/layout/server별 계측 검토 TBD. MDS fallback·DS retry·shared KV key·concurrent Run으로 관계와 누락 대조 |

Node Exporter `mountstats`는 현재 managed launcher에서 켜지지 않습니다. NFSv4.1 mount나 pNFS event count만으로 실제 DS·layout·SSD latency·operation ownership을 확정할 수 없습니다. Source·metric 의미와 native hook의 기준은 [Limitations & Future Work](correlation-limitations.md#future-work-tbd)에서 관리합니다.

다음은 이번 P0에 구현하지 않습니다. 기존 memory/DFS/native I/O metric과 중복된 collector는 제안하지 않습니다.

이 표는 Storage PoC의 후속 계측 경계를 보존합니다. 최신 upstream·3FS/Local SSD/pNFS 비교와 난이도·예상 overhead·PoC 방법의 기준은 [공통 Limitations & Future Work](correlation-limitations.md#future-work-tbd)에서 관리합니다. 이미 있는 native metric과 local relation/signature 기능은 재구현 대상으로 제안하지 않습니다.

| 과제 | 필요성 / 효과 | 예상 계측 지점 | PoC 검증 |
| --- | --- | --- | --- |
| 실제 Memory/DFS replica 선택 | 요청한 tier와 실제 fallback·retry 구분 | Mooncake 선택 함수·Store worker의 requested/selected replica attribute | Forced memory/DFS/fallback에서 실제 선택과 대조 |
| GET/PUT operation trace | Queue→lookup/RPC→D2H→DFS→GPU-ready latency 분리 | Store client/connector enqueue/dequeue와 explicit nested SDK spans | 지연·retry·async overlap 주입, native wall time과 span 대조 |
| End-to-end context | Rollout→engine→KV→3FS 실제 parent 관계 보존 | veRL/Ray/vLLM/Mooncake adapter와 RPC metadata | Concurrent 두 Run·missing parent·restart·skew에서 cross-run 연결 0 |
| 3FS queue/network/SSD 세부 지연 | 기존 overall/server/network latency와 분리되지 않는 내부 대기 구간 | Storage queue admission/dequeue·RDMA completion·AIO boundaries | Queue/network/device 독립 지연 주입, 기존 latency와 일관성 확인 |
| Run/Worker I/O attribution | Shared report를 실제 사용량으로 귀속할 근거 확보 | Operation context propagation와 client/server request linkage | Concurrent workload·partial capture에서 unknown/coverage 보존 |

Request/key/trace ID는 event attribute로 보존하며 Prometheus label에 추가하지 않습니다. Histogram p95들을 더해 end-to-end latency나 critical path로 만들지 않습니다. 외부 프레임워크 수정·source overhead 평가는 후속 과제입니다.
