# 문제 해결 · 다음 확인부터 고르기

:::{container} xlayer-page-meta
**Runbook** 새 설정을 추가하기 전에 process → source → freshness → context를 확인합니다.
:::

## 빠른 점검

같은 config로 시작·조회·종료합니다. 다른 config를 사용했다면 명령마다 `xltel --config FILE ...`을 지정합니다.

```bash
xltel config path
xltel config validate
xltel status
```

| 확인 | 정상 결과 | 실패하면 |
| --- | --- | --- |
| Config | 의도한 경로, validate exit code `0` | 오류 key/path 수정 → [Configuration](configuration.md) |
| Process / backend | 필요한 role/backend가 실행 중 | [도구·process 점검](monitoring.md#troubleshooting) |
| Collector target | 등록한 target을 조회할 수 있음 | 주소·port·target 설정 확인 |

```{admonition} Health와 관측값은 다릅니다
:class: important

Target `up=1`은 exporter 조회 성공입니다. GPU 값의 freshness·Run completeness·cluster 정상 상태를 보장하지 않습니다. No data·stale·query failure는 measured zero와 다릅니다.
```

## 증상 → 확인 → 다음 단계

| 증상 | 먼저 확인 | 다음 guide |
| --- | --- | --- |
| `xltel`을 찾을 수 없음 | 설치한 Python 환경·`xltel --help` | [설치](quickstart.md#문제가-생겼다면) |
| Backend / target Down | `xltel status`, process·주소·port | [GPU & Host](monitoring.md#troubleshooting) |
| Target Up + metric 없음 | Source/version·metric 이름·filter | [Native sources](native-sources.md#troubleshooting) |
| GPU / application 값이 오래됨 | Source timestamp·sample age·완료 step | [Freshness 계약](metrics-reference.md#collector-health) |
| Run은 끝났는데 Step 없음 | File logger·bridge health·step JSONL | [VERL 연결](verl-quickstart.md#troubleshooting) |
| Artifact에는 Step, Grafana에는 없음 | Loki/Alloy·Run/observer·시간 범위 | [Logs & Events](logs-events.md) |
| Baseline 없음 / incomparable | Boundary·worker·workload·clock/sample 조건 | [Baseline](diagnosis.md#2-comparable-baseline-확인) |
| Candidate 없음 | 미연결 diagnosis, evidence 부족, 조회 범위 | [진단 결과](diagnosis.md#3-candidate와-verdict-읽기) |
| Cross-node 비교 보류 | Inventory·관측된 clock offset/uncertainty/freshness | [Multi-node 사전 검사](multi-node.md) |
| Storage p99 없음 | Connector·device·3FS service를 구분, source 설정 | [KV / Storage](kv-storage.md#troubleshooting) |

## 느린 Run이라면

1. [Run Overview](dashboards.md#1-run과-시간-선택)에서 완료 Step과 source age를 확인합니다.
2. [Current / Baseline](diagnosis.md#2-comparable-baseline-확인)의 비교 가능성을 확인합니다.
3. [Candidate / Evidence](diagnosis.md#4-supporting--counter--missing-evidence)를 읽습니다.
4. 같은 구간의 [Timeline / subsystem](deep-dive.md)으로 이동합니다.

## 운영 변경이 필요하다면

| 변경 | 기준 문서 |
| --- | --- |
| Role별 시작·종료 / 소유 process | [CLI](cli.md#host-roles-for-multi-node-deployment) |
| Retention / historical data | [Monitoring operations](monitoring-reference.md#retain-data-for-completed-runs) |
| 원격 Grafana 확인 | [SSH tunnel](monitoring-reference.md#open-the-dashboards) |
| Native endpoint 추가 | [Source 등록](native-sources.md) |
| Sampling / query 비용 | [Diagnosis budget](diagnosis-reference.md#backend-query-budget) |

`down`은 XLayer 소유 process를 종료하며 config·Run artifact를 삭제하지 않습니다. 기존 workload나 다른 monitoring stack을 문제 해결 목적으로 종료하지 않습니다.
