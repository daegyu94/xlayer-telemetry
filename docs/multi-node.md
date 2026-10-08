# Multi-node 연결

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

## 2. Start

Monitoring host:

```bash
xltel up --role server
```

각 collector host:

```bash
xltel up --role node
xltel status
```

**정상 결과:** 각 host가 소유한 role이 시작되고 monitoring host에서 등록 target이 up입니다. 이 명령은 remote host에 자동 배포하지 않습니다.

## 3. Verify

1. Cluster·node 이름이 source·manifest·event에 일치하는지 확인합니다.
2. [Clock preflight](time-alignment.md#correlation-preflight)를 실행한 뒤 current/baseline clock evidence를 확인합니다. Monitoring host도 collector target에 등록합니다.
3. 같은 shared artifact를 여러 collector가 읽지 않는지 확인합니다.
4. Resource node를 바꿔도 observer/step context가 유지되는지 확인합니다.

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

## 다음

[Time Alignment](time-alignment.md) · [Node mapping 상세](integration-reference.md#map-multiple-nodes-to-a-run) · [VM/물리 검증 한계](monitoring-reference.md#validate-separate-vm-kernels)
