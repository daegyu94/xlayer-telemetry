# GPU & Host 연결

**목표:** 한 host의 GPU·CPU·memory·network·local disk metric을 Prometheus와 Grafana에 연결합니다.

## 얻는 것

| Source | 관측 |
| --- | --- |
| GPU sampler | Device utilization·memory·지원되는 power/temperature/clock |
| Node Exporter | CPU·memory·network·disk·filesystem·clock |
| Monitoring server | Prometheus 저장/query, Light Grafana dashboard |

```{admonition} Scope
:class: important

GPU·host·NIC·disk metric은 shared node/device 관측입니다. Run을 선택해도 그 Run의 사용량으로 분리되지 않습니다.
```

## 준비 조건

- [Quickstart](quickstart.md)의 CLI와 config.
- GPU 사용 시 정상 동작하는 `nvidia-smi`.
- 기본 port `19100`, `19090`, `13000`이 비어 있음.

## 1. Configure

`xltel config path`의 TOML `[telemetry]`에 필요한 값을 지정합니다.

```toml
[telemetry]
CLUSTER_NAME = "training-cluster"
NODE_NAME = "gpu-local"
# GPU 없는 host에서만 지정합니다.
# ENABLE_GPU_METRICS = false
```

```bash
xltel config validate
xltel doctor
xltel install-tools
```

**정상 결과:** config validation 성공, 준비한 도구가 doctor에서 확인됩니다. Driver·VERL은 별도로 준비합니다.

## 2. Start

```bash
xltel up
xltel status
```

**정상 결과:** `Monitoring ready`가 출력되고 collector target이 up입니다. Workload 연결 전 application data가 없는 것은 정상적인 missing 상태입니다.

## 3. Verify

```bash
curl --fail --silent http://127.0.0.1:19100/metrics   | rg 'node_cpu_seconds_total|telemetry_gpu_utilization_percent'
```

**정상 결과:** host counter가 보입니다. GPU를 활성화했다면 supported device metric도 보입니다. CPU-only host에는 GPU 값이 없으며 0%로 대체하지 않습니다.

## 볼 화면

- **Run Overview:** application snapshot·age; Run은 [VERL](verl-quickstart.md)이나 [SDK](application-metrics.md)로 연결합니다.
- **Subsystems → Compute & Communication:** GPU matrix·host·network.
- **Subsystems → Data & Storage:** local device·filesystem.
- 원격 browser: [SSH tunnel](monitoring-reference.md#open-the-dashboards).

## Troubleshooting

| 상태 | 확인할 것 |
| --- | --- |
| Target Down | Collector process·주소·port → `xltel status` |
| Target Up + GPU N/A | Driver·GPU 설정·sampler age |
| GPU 값은 있지만 Run 없음 | Wrapper/SDK snapshot과 collector run parent |
| Data가 stale | Producer 갱신 시각·clock·선택한 시간 |

## 다음

[기존 VERL 연결](verl-quickstart.md) · [vLLM / Ray](native-sources.md) · [Multi-node](multi-node.md) · [운영 Reference](monitoring-reference.md)

:::{container} xlayer-legacy-links

<a id="monitoring-guide" class="xlayer-legacy-anchor"></a>

[Monitoring Guide](monitoring-reference.md#monitoring-guide)

<a id="know-the-roles" class="xlayer-legacy-anchor"></a>

[Know the Roles](monitoring-reference.md#know-the-roles)

<a id="prepare-the-host" class="xlayer-legacy-anchor"></a>

[Prepare the Host](monitoring-reference.md#prepare-the-host)

<a id="try-the-demo" class="xlayer-legacy-anchor"></a>

[Try the Demo](demo.md)

<a id="check-dashboard-coverage" class="xlayer-legacy-anchor"></a>

[Check Dashboard Coverage](monitoring-reference.md#check-dashboard-coverage)

<a id="monitor-one-gpu-node" class="xlayer-legacy-anchor"></a>

[Monitor One GPU Node](monitoring-reference.md#monitor-one-gpu-node)

<a id="1-install-the-tools" class="xlayer-legacy-anchor"></a>

[1. Install the Tools](monitoring-reference.md#1-install-the-tools)

<a id="2-start-the-collector" class="xlayer-legacy-anchor"></a>

[2. Start the Collector](monitoring-reference.md#2-start-the-collector)

<a id="3-start-the-server" class="xlayer-legacy-anchor"></a>

[3. Start the Server](monitoring-reference.md#3-start-the-server)

<a id="retain-data-for-completed-runs" class="xlayer-legacy-anchor"></a>

[Retain Data for Completed Runs](monitoring-reference.md#retain-data-for-completed-runs)

<a id="monitor-gpu-and-storage-nodes-together" class="xlayer-legacy-anchor"></a>

[Monitor GPU and Storage Nodes Together](monitoring-reference.md#monitor-gpu-and-storage-nodes-together)

<a id="check-clock-alignment-before-diagnosing" class="xlayer-legacy-anchor"></a>

[Check Clock Alignment Before Diagnosing](monitoring-reference.md#check-clock-alignment-before-diagnosing)

<a id="reproduce-the-local-validation" class="xlayer-legacy-anchor"></a>

[Reproduce the Local Validation](monitoring-reference.md#reproduce-the-local-validation)

<a id="validate-separate-vm-kernels" class="xlayer-legacy-anchor"></a>

[Validate Separate VM Kernels](monitoring-reference.md#validate-separate-vm-kernels)

<a id="open-the-dashboards" class="xlayer-legacy-anchor"></a>

[Open the Dashboards](monitoring-reference.md#open-the-dashboards)

<a id="enable-grafana-alerts" class="xlayer-legacy-anchor"></a>

[Enable Grafana Alerts](monitoring-reference.md#enable-grafana-alerts)

<a id="application-metrics" class="xlayer-legacy-anchor"></a>

[Application Metrics](monitoring-reference.md#application-metrics)

<a id="keep-the-collector-across-runs" class="xlayer-legacy-anchor"></a>

[Keep the Collector Across Runs](monitoring-reference.md#keep-the-collector-across-runs)

<a id="expand-to-multiple-nodes" class="xlayer-legacy-anchor"></a>

[Expand to Multiple Nodes](monitoring-reference.md#expand-to-multiple-nodes)

<a id="add-run-logs-with-loki" class="xlayer-legacy-anchor"></a>

[Add Run Logs with Loki](logs-events.md)

<a id="ssd-health" class="xlayer-legacy-anchor"></a>

[Storage Cluster Inventory](monitoring-reference.md#storage-cluster-inventory)

<a id="topology-and-native-sources" class="xlayer-legacy-anchor"></a>

[Topology and Native Sources](monitoring-reference.md#topology-and-native-sources)

<a id="check-and-stop" class="xlayer-legacy-anchor"></a>

[Check and Stop](monitoring-reference.md#check-and-stop)

<a id="demo-details" class="xlayer-legacy-anchor"></a>

[Demo Details](monitoring-reference.md#demo-details)

:::
