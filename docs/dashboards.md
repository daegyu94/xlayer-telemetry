# Dashboard Guide

이 문서는 Grafana의 다섯 대시보드가 어떤 신호를 보여 주는지, 한 실행을 조사할 때 어떤 순서로 읽을지 설명합니다.
대시보드를 띄우는 절차는 [Monitoring Guide](monitoring.md#open-the-dashboards)에, 실제 값이 채워진 화면은 [VERL·vLLM·3FS·Loki 데모](real-verl-demo.md)에 있습니다.

## Select the Context

먼저 Run Overview에서 `Cluster`, `Node`, `Run`과 시간 범위를 선택합니다.
다른 대시보드로 이동하는 링크는 공통 필터와 시간 범위를 전달하지만, 각 화면의 추가 필터는 따로 확인해야 합니다.
`All`을 선택하면 여러 node나 run의 시계열이 함께 표시될 수 있습니다.

| 신호 | 주요 범위 | 읽을 때 주의할 점 |
| --- | --- | --- |
| VERL 학습 지표 | `run_id`, node, role, worker | 선택한 run의 완료 step에서 갱신되며 진행 중인 phase를 실시간으로 뜻하지 않습니다. |
| GPU·host·NIC·device 지표 | node, GPU 또는 device | 해당 node의 전체 사용량이므로 선택한 run만의 값으로 귀속하지 않습니다. |
| Native vLLM 지표 | 등록한 vLLM endpoint와 node | Engine이 여러 run을 처리하면 값이 섞일 수 있으며 `Run` 필터로 분리되지 않습니다. |
| SMART 지표 | storage system label, storage node, SSD | Exporter를 별도로 연결해야 하며 `storage_system=3fs`만 지정해도 3FS 서비스 지표가 생기지는 않습니다. |
| Loki log | cluster, node, workload, log directory | `Run`은 log 경로의 run directory 이름으로 추출되며 telemetry의 `run_id`와 다를 수 있습니다. |

`N/A`는 source가 연결되지 않았거나 선택한 범위에 표본이 없다는 뜻이며 측정값 0과 다릅니다.
Run Overview와 Compute & Communication의 GPU 표는 30초보다 오래된 표본을 숨기고, 학습 패널은 `Training sample max age (s)`로 신선도 기준을 조정합니다.
긴 step에서는 sample age와 원본 log를 함께 확인합니다.

## Run Overview

이 화면은 수집이 살아 있는지와 학습·GPU의 최근 상태를 함께 확인하는 출발점입니다.
`Exporter targets up`은 `telemetry` job의 target 수를 세므로 native vLLM이나 SMART target의 상태까지 나타내지는 않습니다.
`Training sample age by worker`와 `GPU sample age by node`를 먼저 보면 다른 숫자가 최신 표본인지 판단할 수 있습니다.

`GPU utilization matrix`는 node와 GPU index별 최신 사용률을 보여 줍니다.
0%는 측정된 idle 상태이고 빈 칸은 사용 가능하다는 증거가 아닙니다.
아래의 training throughput·step time·loss는 application이 해당 metric을 낼 때만 채워지며, swap 패널은 node 전체의 상태를 보여 줍니다.

## Agent RL Stage Correlation

이 화면은 완료된 VERL step의 단계 시간과 rollout engine 상태를 같은 시간대에 비교합니다.
`Latest completed RL step`이 증가하는지 확인하고, `Worker sample age`로 값의 신선도를 함께 판단합니다.
`Completed RL stage duration`은 step 경계에서 기록된 phase별 소요 시간이며 현재 실행 중인 phase의 경과 시간이 아닙니다.
Reward mean 역시 마지막으로 보고된 값으로, 값 하나만으로 모델 품질 변화를 판단하지 않습니다.

처리량·response length·GPU 사용률을 단계 시간과 비교한 뒤, rollout이 느린 구간에서는 `Live rollout engine signals`의 waiting 요청·KV 사용률·preemption을 봅니다.
이 native vLLM 패널은 [endpoint를 등록](agent-rl.md#register-native-endpoints)했을 때만 채워집니다.
`vLLM KV offload store and load`는 GPU→CPU와 CPU→GPU 전송률을 보여 주며 CPU tier와 filesystem tier를 분리하거나 3FS에 쓴 바이트만 집계하지 않습니다.
Weight sync, policy lag, tool 시간은 해당 source가 기록된 경우에만 나타납니다.

## Compute & Communication

이 화면은 느린 단계가 GPU·process·network 상태와 함께 변했는지 살펴보는 곳입니다.
GPU matrix와 utilization을 본 뒤 power·temperature·SM clock으로 부하와 throttling 가능성을 비교합니다.
Compute-process GPU memory는 PID별 관측값이고 `GPU allocation matrix`는 별도로 기록한 worker 배치에 의존하므로, process를 run에 자동으로 귀속시키지 않습니다.

TCP/Ethernet과 RDMA 패널은 interface 또는 port의 전송량입니다.
이 값만으로 어느 두 endpoint 사이의 traffic인지 알 수 없으며, compute topology matrix에는 별도로 제공한 edge 정보가 있어야 합니다.
`Worker-local application timers`는 application이 보고한 run별 timer가 있을 때만 표시됩니다.

## Data & Storage

`Device`는 host block device 그래프를, `Mount`는 filesystem 그래프를 선택합니다.
3FS FUSE 경로를 `Mount`로 골라도 disk throughput·IOPS·busy time은 선택한 `Device`의 node 전체 지표입니다.
Filesystem used·free space는 선택한 마운트의 용량 상태이고, disk busy time은 지연 시간의 p99가 아닙니다.
같은 시간대의 변화는 조사 단서지만 이번 run의 KV offload I/O 양을 직접 증명하지는 않습니다.

Storage topology 표는 component·edge 정보를 공급했을 때, SSD health 패널은 SMART exporter를 연결했을 때 채워집니다.
3FS 서비스 latency는 이 Grafana 화면에 포함되지 않으며 [ClickHouse 진단](agent-rl.md#add-diagnostics)에서 별도로 확인합니다.

## Run Logs

Loki를 활성화하면 Alloy가 `<log-root>/<run-directory>/logs/**/*.log` 파일을 수집해 이 화면에 표시합니다.
`Cluster`, `Node`, `Workload`, `Run`과 시간 범위를 맞추고, metric이 늦게 갱신된 구간의 메시지를 살펴봅니다.
`Run`에는 telemetry `run_id` 대신 log directory 이름이 들어갈 수 있으므로 결과가 비면 경로와 [Loki 설정](monitoring.md#add-run-logs-with-loki)을 확인합니다.
한 줄의 error나 warning만으로 전체 실행의 성공·실패를 단정하지 말고 종료 코드와 run 산출물을 함께 확인합니다.

## Follow a Slow Interval

Run Overview에서 target 상태와 sample age를 확인하고, Agent RL에서 느려진 완료 stage와 시각을 고릅니다.
같은 node·시간 범위의 Compute & Communication, Data & Storage, Run Logs를 순서대로 비교합니다.
두 신호가 동시에 변해도 인과관계가 확정되지는 않으며, 공유 자원에는 다른 workload의 영향도 포함될 수 있습니다.
완료된 step 하나를 확대하고 다른 step과 비교하려면 [Step Explorer](step-explorer.md)를 엽니다.
증상별 다음 조사 항목과 trace 연결은 [Run Analysis](analysis.md)에서 다룹니다.
