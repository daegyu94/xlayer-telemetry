# KV / Storage 관측

**목표:** KV cache, Mooncake Store, local disk와 shared 3FS의 값을 구분해 연결합니다.

## 얻는 것

| 계층 | 실제 Source | 확인할 값 |
| --- | --- | --- |
| vLLM GPU KV | Native vLLM endpoint | Cache usage·prefix hit·지원되는 preemption/offload |
| Mooncake Store | Connector·master·선택적 client endpoint | RPC·RAM/lookup·DFS batch I/O |
| Local device / filesystem | Node Exporter | Busy·mean I/O latency·IOPS·용량 |
| Shared 3FS | 기존 ClickHouse distributions와 diagnostics config | 같은 시간·metricName의 latency / evidence |
| SSD health | Optional SMART exporter | 온도·warning·media error; operation p99가 아님 |

```{admonition} Scope
:class: important

KV offload bytes, client DFS bytes, block-device bytes는 서로 다른 단계의 관측입니다. 합산하거나 특정 Run의 물리 SSD I/O로 귀속하지 않습니다.
```

## 준비 조건

- [vLLM / Ray](native-sources.md)의 native job 연결.
- 실제 배포에 존재하는 Mooncake endpoint·3FS 데이터.
- Source를 읽는 node와 device identity 확인.

## 1. Configure

[Mooncake source 예제](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/mooncake/native-sources.json)를 기존 native source 파일에 합칩니다. 이미 등록한 vLLM endpoint는 중복 등록하지 않습니다.

| Endpoint | 준비할 설정 |
| --- | --- |
| vLLM connector | `MooncakeStoreConnector`와 기존 vLLM metrics |
| Mooncake master | 실제 `--metrics_port`의 `/metrics` |
| Client DFS | 배포가 client HTTP 초기화 옵션을 실제로 전달해야 함 |

Client HTTP가 지원되지 않는 embedded connector는 DFS panel을 N/A로 유지합니다. 상세 version·초기화 조건은 [Mooncake Reference](integration-reference.md#observe-mooncake-kv-storage)에 있습니다.

## 2. Start / Refresh

```bash
# 이미 native job이 있는 경우
xltel sources refresh
xltel sources
```

**정상 결과:** 등록한 endpoint의 status와 Explore 주소가 표시됩니다. 최초 native job 추가라면 `xltel restart --role server`가 필요합니다.

3FS service 진단은 [diagnostics 연결](diagnosis.md#1-진단-연결)의 기존 config에서 ClickHouse URL·database·filter를 설정합니다.

## 3. Verify

```bash
xltel sources threefs --window-seconds 60
```

**정상 결과:** 설정한 3FS의 조회 결과 또는 명시적인 미설정·error 상태가 JSON으로 표시됩니다. 성공한 HTTP 응답만으로 모든 필요한 metric의 존재를 판단하지 않습니다.

## 볼 화면

- **Stage Correlation → Policy & KV lifecycle / Mooncake:** endpoint·engine별 변화.
- **Data & Storage:** local device와 filesystem; service p99와 구분.
- **Bottleneck Summary:** 선택한 step의 3FS evidence·unit·scope·missing.

## Troubleshooting

| 상태 | 확인할 것 |
| --- | --- |
| Client endpoint 503 | Client metric 비활성 / HTTP 초기화 옵션 |
| Connector만 보임 | Master/client 별도 endpoint와 version 지원 |
| 3FS evidence 없음 | Diagnostics config·database·metricName·filter·settle 시간 |
| SMART만 보임 | SMART는 health이며 3FS service 계측과 별도 |

## 다음

[느린 Step 조사](dashboards.md) · [Storage interpretation](dashboard-reference.md#follow-the-storage-path) · [SMART 운영](monitoring-reference.md#ssd-health)
