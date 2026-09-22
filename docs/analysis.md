# Run Analysis

이 문서는 VERL 실행이 느려졌을 때 원인 후보를 좁히는 순서를 설명합니다.
먼저 [VERL Quick Start](verl-quickstart.md)로 application과 GPU·host 지표를 연결합니다.
Trace와 통신 baseline은 상시 지표만으로 답하기 어려울 때 추가합니다.

## Start with One Slow Interval

Grafana의 Agent RL Stage Correlation에서 조사할 run과 시간 범위를 선택합니다.
어느 완료 stage의 시간이 증가했는지 찾고 같은 시간·node의 resource 지표를 비교합니다.
예를 들어 rollout 시간이 늘어났다면 vLLM queue, GPU 사용률, tool 대기를 차례로 살펴봅니다.

| 관찰한 변화 | 함께 확인할 신호 | 다음에 조사할 후보 |
| --- | --- | --- |
| Rollout 시간 증가 | vLLM waiting·KV cache·GPU 사용률 | Queue, concurrency, 긴 response |
| Rollout 지연과 낮은 engine queue | Tool span·외부 호출 log | Tool 또는 environment 대기 |
| Actor update 지연과 낮은 GPU 사용률 | CPU·memory·network·input 지표 | Host staging 또는 collective 대기 |
| Weight sync 지연 | NIC/RDMA traffic·error | 전송 경로와 replica 준비 상태 |
| Checkpoint 지연 | Storage latency·device write·queue | Client부터 SSD까지의 I/O 경로 |
| Throughput 감소와 높은 GPU 사용률 | Clock·power·temperature, worker 차이 | Throttling 또는 straggler |

이 표의 신호는 해당 source가 연결되어 있을 때만 보입니다.
`N/A`는 0이 아니며, 먼저 target 상태와 sample age를 확인합니다.
VERL file logger의 stage 값은 step 완료 시 갱신되므로 진행 중인 phase와 혼동하지 않습니다.

## Check the Run Context

같은 시간에 값이 변했다는 사실은 원인 후보를 좁히는 근거입니다.
Host나 shared storage의 metric에는 다른 workload도 포함될 수 있으므로 node·role·device 배치와 run 조건을 함께 확인합니다.
여러 machine의 clock가 맞지 않으면 시간상 비교도 틀어질 수 있습니다.

`show_run`은 monitoring server 없이 run directory의 최신 기록을 보여 줍니다.
Checkout의 Python 환경을 활성화한 뒤 실행합니다.

```bash
PYTHONPATH=. python -m xlayer_telemetry.show_run \
  "$HOME/telemetry-runs/grpo-001"
```

| 읽는 파일 | 확인하는 내용 |
| --- | --- |
| `telemetry-manifest.json` | Run 식별자, 배치와 기록된 실행 조건 |
| `telemetry-metrics/*.json` | Worker의 최신 step과 metric |
| `telemetry-events/*.jsonl` | 최근 event·span |
| `diagnostics/latest.json` | 선택적인 최신 진단 결과 |
| `run-metadata-*.json`, `summary-*.json` | Application이 별도로 제공한 stage 요약 |

마지막 두 종류의 stage 요약 파일은 특정 framework가 자동으로 만든다고 가정하지 않습니다.
파일이 없으면 해당 요약이 생략되며, 이것만으로 실행 실패를 의미하지 않습니다.
Node-local directory라면 파일이 있는 node에서 명령을 실행합니다.

최신 snapshot만으로 과거 모든 step을 재구성할 수는 없습니다.
VERL의 원본 이력은 `logs/verl-metrics.jsonl`을 확인하고 다른 application은 자체 log를 사용합니다.
[자동 진단](agent-rl.md#add-diagnostics)을 켰다면 `missing_sources`와 판단 근거도 읽습니다.

## Follow the Storage Path

Checkpoint 지연을 조사한다면 application의 지연 시각을 먼저 정합니다.
그 시간대의 3FS 서비스 latency, storage node의 device 상태, client network를 비교합니다.
3FS ClickHouse의 `max_observed_p99`는 관측된 p99 중 최댓값이며 전체 요청의 global p99가 아닙니다.

SSD SMART 지표는 장치 건강 상태를 설명하고 application의 write latency를 직접 측정하지 않습니다.
Device write bytes 역시 해당 run의 checkpoint bytes와 같다고 가정하지 않습니다.
여러 run이 shared storage를 사용한다면 같은 창에 경쟁한 작업도 확인합니다.

## Capture a Short Trace

원인 후보가 특정 stage나 rank로 좁혀지면 짧은 trace로 CPU 작업, GPU kernel, copy, collective가 겹치는 모습을 확인합니다.
Trace는 수집 비용과 파일 크기가 있으므로 필요한 rank와 구간만 선택합니다.
실제 VERL profiler 연결 참고는 [설정 예제](../examples/verl/torch-profiler.yaml)에 있으며 사용하는 VERL 환경에 맞춰 적용합니다.

직접 작성한 PyTorch loop에는 [selected-rank helper](../examples/pytorch/selected_rank_profiler.py)를 넣을 수 있습니다.
아래 `train_loader`와 `train_step`은 기존 application의 객체와 함수입니다.

```python
from pathlib import Path
from examples.pytorch.selected_rank_profiler import selected_rank_profile

with selected_rank_profile(
    Path("artifacts/traces/run-001"),
    ranks={0},
    skip_first=4,
    wait=1,
    warmup=1,
    active=2,
) as profiler:
    for batch in train_loader:
        train_step(batch)
        profiler.step()
```

모든 iteration에서 `profiler.step()`을 호출해야 schedule이 진행됩니다.
선택하지 않은 rank는 no-op profiler를 사용합니다.
결과 경로와 수집 조건을 run manifest에도 기록하면 나중에 비교하기 쉽습니다.

## Practice with a Synthetic Profile

`run_profile.sh`는 실제 VERL run을 profile하는 명령이 아니라 작은 GPU workload로 수집 절차와 overhead를 확인하는 도구입니다.
CUDA PyTorch 환경과 사용 가능한 GPU가 필요합니다.
다음은 node 한 대에서 연습하는 예이며 Python 경로를 실제 CUDA 환경으로 바꿉니다.

```bash
PROFILE_RUN_ID=profile-001 \
NNODES=1 NODE_RANK=0 MASTER_ADDR=127.0.0.1 \
PYTHON='/path/to/cuda-env/bin/python' \
  bash scripts/run_profile.sh baseline

PROFILE_RUN_ID=profile-001 \
NNODES=1 NODE_RANK=0 MASTER_ADDR=127.0.0.1 \
PYTHON='/path/to/cuda-env/bin/python' \
  bash scripts/run_profile.sh capture
```

| Mode | 실행 내용 |
| --- | --- |
| `baseline` | Profiler 없이 synthetic DDP 실행 |
| `capture` | 같은 workload에서 선택한 rank의 trace 수집 |
| `collective` | Synthetic collective 실행 |

기본 step 수는 `STEPS=24`, 제한 시간은 `RUN_TIMEOUT=300`초입니다.
결과는 `artifacts/telemetry/<run-id>/<mode>/`에 log·manifest·JSON으로 남고 capture에는 trace가 추가됩니다.
이 제한 시간은 상시 GPU sampler의 기본 무제한 실행과 별개입니다.

Script는 기존 GPU process와 최소 가용 memory를 검사합니다.
다른 GPU 작업이 있으면 기다리지 않고 종료하므로 사용 가능한 할당에서 실행합니다.
여러 node에서는 각 node에 같은 `PROFILE_RUN_ID`·`NNODES`·rank 0의 `MASTER_ADDR`를 지정하고 `NODE_RANK`만 다르게 실행합니다.

## Measure a Communication Baseline

통신 병목이 의심될 때 NCCL baseline으로 같은 hardware 경로의 collective 성능을 확인할 수 있습니다.
MPI 지원 `all_reduce_perf`, `mpirun`과 참여 node에 할당된 GPU가 필요합니다.
이 명령은 지정한 node에 실제 GPU·network 부하를 발생시킵니다.

```bash
NCCL_TEST_BINARY='/path/to/all_reduce_perf' \
HOSTS='gpu-0,gpu-1' GPUS_PER_NODE=1 \
  bash scripts/run_nccl_baseline.sh
```

`artifacts/nccl-baseline/manifest.env`에서 측정 조건을, `all-reduce.log`에서 correctness 오류와 bandwidth를 확인합니다.
Baseline은 학습 throughput이 아니므로 같은 node·GPU·network 조건의 비교 기준으로만 사용합니다.

## Compare After a Change

Model, batch, sequence length, concurrency, cache 상태와 topology를 기록하고 비교 run에서 동일하게 유지합니다.
원인 후보를 하나씩 변경한 뒤 profiler를 끈 실제 VERL 실행에서 개선이 유지되는지 확인합니다.
Throughput뿐 아니라 loss·reward와 correctness도 함께 확인하고, synthetic 결과와 실제 workload 결과는 구분해 남깁니다.
