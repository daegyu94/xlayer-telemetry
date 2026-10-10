# Configuration

:::{container} xlayer-page-meta
**Reference** 설정 우선순위·경로·restart 조건
:::

**목적:** 같은 config로 시작·조회·종료하고, 실행 환경과 보관 artifact를 분리합니다.

## 설정 파일 선택

| 우선순위 | 입력 |
| --- | --- |
| 1 | `xltel --config FILE ...` |
| 2 | `XLAYER_CONFIG` |
| 3 | 기본 경로의 config; 기존 `config.conf`가 있으면 계속 사용 |

```bash
xltel config path
xltel config show
xltel config validate
```

**정상 결과:** path는 선택한 파일, show는 resolved setting, validate는 exit code `0`입니다. Show는 workload argv·credential을 공개하지 않습니다.

## TOML 형식

```toml
[telemetry]
TELEMETRY_HOME = "~/telemetry"
RUN_ID = "auto"
ENABLE_LOGS = true
GF_USERS_DEFAULT_THEME = "light"

[workload]
# 이미 동작하는 기존 argv를 배열로 저장합니다.
# command = ["/path/to/verl-env/bin/python", "-m", "verl.trainer.main_ppo"]

[environment]
# child 환경으로 전달할 문자열
# CUDA_VISIBLE_DEVICES = "0,1"
```

| 절 | 의미 |
| --- | --- |
| `[telemetry]` | 알려진 uppercase 설정 key |
| `[workload]` | Command argv 배열 |
| `[environment]` | 추가 child environment; 같은 terminal 값이 우선 |

## 값의 우선순위와 경로

- Command option → 같은 이름의 environment → config → default.
- TOML path는 `~`만 확장합니다. `$HOME`·command substitution은 해석하지 않습니다.
- Boolean은 `true`/`false`; unknown key와 잘못된 type은 시작 전에 거부합니다.
- `TELEMETRY_HOME`은 tools/state/run parent의 기본 경로를 정합니다.
- `RUN_ID = "auto"`는 매 실행의 고유 ID를 생성합니다. 고정 ID는 새 Run마다 변경합니다.

```{admonition} Trusted Bash
:class: important

기존 Bash config는 실행 가능한 신뢰된 파일입니다. 보안 sandbox가 아닙니다. Command는 `VERL_COMMAND=(...)` 배열로 보존하며 TOML로 migration해도 원본 파일은 유지됩니다.
```

## 기능별 설정

| 목표 | Key / 적용 | Guide |
| --- | --- | --- |
| CPU-only collector | `ENABLE_GPU_METRICS = false`; collector restart | [GPU & Host](monitoring.md) |
| Log·step·span | `ENABLE_LOGS = true`; node/server restart | [Logs & Events](logs-events.md) |
| Native endpoint | `TELEMETRY_SOURCES_FILE`; 최초 server restart, 이후 refresh | [vLLM / Ray](native-sources.md) |
| Diagnosis | `DIAGNOSTICS_CONFIG`; 새 Run | [Diagnose](diagnosis.md) |
| Prometheus retention | `PROMETHEUS_RETENTION`; server restart | [운영 Reference](monitoring-reference.md#retain-data-for-completed-runs) |
| Native/scrape budget | Bounded sample/target/body/interval 설정 | [Metrics limits](metrics-reference.md#bounded-collection-and-pressure-evidence) |
| Storage inventory | `TOPOLOGY_DIR`; collector restart | [DS/MDS 선언과 identity](monitoring-reference.md#storage-cluster-inventory) |
| 통합 Cluster Inventory | TOML/JSON → 기존 target·topology·native source 설정 생성 | [Multi-node Configure](multi-node.md#1-configure) |

## 검증과 변경

```bash
xltel config validate
xltel restart --role server
xltel status
```

**정상 결과:** config validation과 backend health를 각각 확인합니다. Collector 입력·node·run parent를 바꾸면 collector도 해당 config로 재시작합니다.

### Cluster Configuration Validation

`config validate`와 `doctor`는 명시한 `TOPOLOGY_DIR`까지 검사합니다. JSON 형식·중복 component·충돌한 owner/role·존재하지 않는 edge endpoint·등록되지 않은 resource node·Diagnosis cluster 불일치를 시작 전에 확인할 수 있습니다.

```bash
xltel cluster validate --json
xltel cluster validate --live --json
xltel cluster validate --correlation --json
```

| 검사 | 확인하는 것 | 확인하지 않는 것 |
| --- | --- | --- |
| Offline | 기존 config·topology·native source·Diagnosis identity 정합성 | 실제 endpoint 응답·장비 상태 |
| `--live` | 기존 Prometheus의 target scrape 가용성 | Metric 존재·freshness·device mapping·serving 상태·물리 연결 |
| `--correlation` | `doctor --correlation`과 같은 inventory·관측한 clock quality | 지속적인 clock proof·실제 execution dependency |

`cluster validate`는 정상 `0`, 확인이 필요한 Warning `1`, 설정 Error `2`를 반환합니다. 기존 `config validate`는 Warning을 출력하고 유효한 설정이면 `0`을 유지하며, `doctor`는 Warning도 `incomplete`로 보고합니다. Backend 조회 실패는 `unavailable`이며 Node Down이나 측정값 `0`으로 바꾸지 않습니다.

Topology가 미설정이면 optional 상태를 유지합니다. 명시한 디렉터리가 없거나 인식하는 JSON 파일이 하나도 없으면 Error이며, compute/storage 중 한 파일만 제공하는 기존 구성을 허용합니다. Collector는 읽을 수 있는 raw declaration을 유지하고 잘못된 owner mapping은 보류하며, 파일 내용이 바뀔 때만 정제한 경고를 남깁니다.

### Inventory Contract

Inventory는 별도 Runtime 형식이 아니라 기존 설정의 생성 입력입니다. 프로젝트의 TOML parser를 재사용하고 JSON도 허용하며 YAML dependency는 추가하지 않습니다. [예제](../examples/cluster/inventory.toml)를 수정하고 [Multi-node 절차](multi-node.md#1-configure)로 적용합니다.

| 입력 | 의미 |
| --- | --- |
| `cluster.name`, `monitoring_node` | 논리 cluster와 명시한 monitoring host. Clock 검증은 별도 |
| `nodes[]` | 고유 `name`, IPv4/hostname `address`, `roles`; 선택적 `gpus`, `interfaces`, `devices`, host-local `telemetry_home` |
| `services[]` | 기존 native source의 `name`, `kind`, `node`, `target`, `scheme`, `metrics_path`, stable `labels` |
| `components[]`, `edges[]` | `kind=compute/storage` namespace 안의 명시한 component와 관계 |
| `storage.backend`, `filesystem` | 선언한 배치 설명. 실제 KV tier 선택·I/O path·Run attribution 아님 |

`nodes.collector=false`는 target 등록을 제외합니다. 해당 component의 Runtime owner/device mapping은 보류하고 원래 Node 선언은 manifest에 보존하므로, 미수집 장비가 자동으로 정상 또는 검증된 metric 대상으로 표시되지 않습니다. GPU/Compute node는 기존 UI grouping에 맞는 `gpu-node`/`compute-node`, MDS/DS는 `metadata`/`data`로 생성하며 원래 `roles`는 manifest에 남깁니다.

입력은 1 MiB, Node 256개·native source 1,024개, 각 topology는 component 4,096개·edge 8,192개로 제한합니다. 생성 결과도 기존 consumer의 파일 상한을 검사하며 초과 데이터를 조용히 자르지 않습니다. GPU/NIC/SSD attachment는 선언한 목록에서 생성하고 network 관계는 명시한 edge만 사용합니다.

Run/Replica placement·request routing·policy version·model·Run별 Diagnosis 설정은 생성하지 않습니다. 기존 Run별 설정을 유지하고 cluster/node 불일치만 교차 검사합니다. pNFS는 배치 설명으로 표현할 수 있지만 Backend Deep Dive는 [Future Work](correlation-limitations.md)입니다.

## 다음

[CLI의 설정·migration 상세](cli.md#configuration) · [Monitoring lifecycle](monitoring-reference.md#check-and-stop) · [Architecture](architecture.md)
