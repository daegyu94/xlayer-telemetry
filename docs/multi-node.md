# Multi-node 연결

:::{container} xlayer-page-meta
**Task** Target inventory·clock 사전 조건을 확인
:::

**목표:** 같은 cluster의 observer·rollout·storage·sandbox node를 identity와 clock을 보존해 비교합니다.

## 얻는 것

- Node별 host/device metric과 native endpoint.
- 같은 Run의 node별 artifact·clock evidence.
- 검증한 시간 window·scope를 유지한 correlation.

## 준비 조건

| 항목 | 확인 |
| --- | --- |
| Monitoring host | 각 node의 metric port에 접근 가능 |
| Node 이름 | Collector·native source·manifest·SDK에서 같은 논리 이름 |
| Clock | 같은 신뢰할 수 있는 reference와 sync/uncertainty 검증 |
| Local state | Alloy/backend state는 host-local; 중복 수집 피하기 |

![각 node의 collector와 native source를 monitoring host에 등록하고 identity를 보존한다](figures/diagrams/multi-node.svg)

## 1. Configure

| 연결 방향 | 기본 port | 목적 |
| --- | --- | --- |
| Monitoring host → collector | 19100 | Host/GPU/application |
| Monitoring host → DS/MDS | 19100 | Host/I/O·clock collector |
| Collector → Loki | 13100 | Optional log/event |
| Browser → Grafana | SSH tunnel | Loopback UI 접근 |

각 host의 config에서 `CLUSTER_NAME`·`NODE_NAME`·`NODE_ADDR`를 실제 배치에 맞춥니다. Server의 target 등록·remote Loki 주소는 [운영 설정](monitoring-reference.md#expand-to-multiple-nodes)을 따릅니다.

### 하나의 Inventory에서 설정 생성

기존 TOML/JSON을 직접 유지하는 방식도 지원합니다. 같은 Node 정보를 반복 입력하기 어렵다면 [Inventory 예제](../examples/cluster/inventory.toml)의 주소·역할·장치를 실제 구성으로 바꾼 뒤 기존 설정 bundle을 생성합니다.

```bash
mkdir -p "$HOME/.config/xlayer"
cp examples/cluster/inventory.toml "$HOME/.config/xlayer/inventory.toml"
# inventory.toml의 placeholder 주소·장치·endpoint를 실제 배치로 수정
xltel cluster validate --inventory "$HOME/.config/xlayer/inventory.toml" --json
xltel cluster render --inventory "$HOME/.config/xlayer/inventory.toml" \
  --output "$HOME/.config/xlayer/cluster-bundle" \
  --prometheus-url http://monitoring.internal:19090
xltel --config "$HOME/.config/xlayer/cluster-bundle/server.toml" config validate
```

**정상 결과:** Offline 검증은 `status=valid`, exit code `0`입니다. 새 bundle에는 `server.toml`, `nodes/*.toml`, `topology/*.json`, 선택적 `native-sources.json`, hash·count가 있는 `manifest.json`이 생성됩니다. 디렉터리는 `0700`, 파일은 `0600`이며 기존 output은 덮어쓰지 않습니다. 변경 시 새 output을 생성하고 확인한 뒤 설정을 전환합니다.

`--prometheus-url`은 server와 node 설정에 같은 조회 주소를 전달합니다. 실제 monitoring API의 기존 내부 접속 경로나 터널 주소를 지정하며, 이 option이 server의 loopback bind·공개 범위·인증을 변경하지는 않습니다. Remote 조회가 불가능하면 target discovery는 unavailable이며 collector 자체의 장애를 뜻하지 않습니다.

| 배치 대상 | 적용 |
| --- | --- |
| Monitoring host | 해당 host에서 render한 `server.toml` 사용. Source/topology 경로는 생성한 host의 absolute path |
| 각 collector host | 해당 `nodes/NAME.toml`만 복사. 기본 home과 선택적 `telemetry_home`은 받는 host 기준 |
| Topology publisher 한 곳 | 생성한 topology JSON을 host-local 디렉터리에 복사하고 그 node config에 `TOPOLOGY_DIR` 지정 |
| Run / Rollout Replica | 기존 Run별 Diagnosis/SDK 설정을 유지. Inventory가 동적 실행 배치를 대신하지 않음 |

`status --role node/server`는 exporter의 `up`과 설정 주소의 `configuration_status`를 구분합니다. 명백한 IP 불일치는 `mismatch`, 중복 target은 `ambiguous`, 확인하지 못한 주소는 `unknown`이며 DNS 이름과 IP의 동등성을 추정하지 않습니다. Topology publisher를 비활성화하거나 종료하면 XLayer의 `topology.prom`만 정리하고 사용자 textfile은 보존합니다.

예제에서는 등록한 `monitoring-0` collector를 publisher로 사용할 수 있습니다. 같은 host의 생성된 node 설정에서 다음 경로를 실제 bundle 위치로 지정합니다.

```toml
# nodes/monitoring-0.toml의 [telemetry]에 추가
TOPOLOGY_DIR = "/absolute/path/to/cluster-bundle/topology"
```

```{admonition} 설정 생성과 관측 시작은 별도
:class: important

`cluster render`는 설치·SSH 배포·서비스 시작을 하지 않습니다. Server role만 시작해도 topology gauge가 게시되지는 않으므로, 등록한 collector 한 곳에 JSON과 `TOPOLOGY_DIR`를 배치해야 합니다. GPU node의 local Sandbox SSD를 선언해도 shared 3FS의 I/O로 귀속하지 않습니다.
```

## 2. Start

Monitoring host:

```bash
xltel --config /path/to/cluster-bundle/server.toml up --role server
```

각 collector host:

```bash
xltel --config /path/on/this/host/node.toml up --role node
xltel --config /path/on/this/host/node.toml status --role node
```

**정상 결과:** 각 host가 소유한 role이 시작되고 monitoring host에서 등록 target이 up입니다. 이 명령은 remote host에 자동 배포하지 않습니다.

## 3. Verify

1. Cluster·node 이름이 source·manifest·event에 일치하는지 확인합니다.
2. [Clock preflight](time-alignment.md#correlation-preflight)를 실행한 뒤 current/baseline clock evidence를 확인합니다. Monitoring host도 collector target에 등록합니다.
3. 같은 shared artifact를 여러 collector가 읽지 않는지 확인합니다.
4. Resource node를 바꿔도 observer/step context가 유지되는지 확인합니다.

```bash
xltel --config /path/to/cluster-bundle/server.toml cluster validate --live --json
# DIAGNOSTICS_CONFIG를 실제 Run/Replica/clock inventory에 맞게 설정한 경우
xltel --config /path/to/cluster-bundle/server.toml cluster validate --correlation --json
```

**정상 결과:** target observation의 `health=up`을 확인합니다. 이는 scrape 가용성만 의미하며 `metric_coverage`와 `device_identity`는 `not_checked`로 남습니다. Clock 검사는 기존 Diagnosis inventory와 query budget을 재사용하며, 선언한 전체 Node의 clock을 자동으로 검증하지 않습니다.

```{admonition} Remote worker
:class: important

Trainer wrapper의 환경 변수가 remote Ray worker에 자동 전달되지 않습니다. Run ID·실제 node·local metric/event path와 SDK를 worker 환경에 설정합니다. 한 host의 여러 GPU는 독립 clock을 가진 여러 node가 아닙니다.
```

## 문제가 생겼다면

| 상태 | 행동 |
| --- | --- |
| Remote target Down | Bind 주소·port·network 확인 |
| Node filter로 data가 사라짐 | 논리 node 이름·label 정합 확인 |
| Clock unsafe/unknown | 정밀 비교를 보류하고 reference·uncertainty 확인 |
| Duplicate log/sample | 수집 담당 node와 local state 분리 |
| `edge_endpoint_unknown` | 같은 compute/storage namespace에 edge의 두 component를 선언 |
| `resource_node_unregistered` | Topology owner와 `TELEMETRY_TARGETS`의 논리 Node 이름 확인 |
| `diagnosis_cluster_mismatch` | Run별 Diagnosis와 monitoring의 cluster를 일치시킴 |
| `live_discovery_unavailable` | Prometheus 접근을 복구. Node Down으로 해석하지 않음 |

CLI wizard·SSH discovery·Grafana 설정 편집·drift 자동 판정과 node target File SD는 향후 과제입니다. 현재 Infrastructure는 읽기 전용 탐색 화면이며, Inventory의 정적 배치로 실제 Replica serving/routing·물리 연결·Run 소유권을 추론하지 않습니다.

## 다음

[Time Alignment](time-alignment.md) · [Node mapping 상세](integration-reference.md#map-multiple-nodes-to-a-run) · [VM/물리 검증 한계](monitoring-reference.md#validate-separate-vm-kernels)
