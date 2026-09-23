# Real VERL + vLLM + 3FS + Loki Demo

이 GIF는 `agentic-rl-lab`의 실제 VERL 학습에서 vLLM KV block을 3FS FUSE 마운트에 오프로드하고, 학습 로그를 Alloy와 Loki로 수집한 Grafana 화면입니다.
완료된 step 1–8을 시간순으로 재생한 뒤 Agent RL, Run Overview, Compute & Communication, Data & Storage, Run Logs 대시보드를 스크롤합니다.
Data & Storage의 `Mount`는 실제 3FS 마운트이며, Run Logs는 같은 실행의 VERL 로그를 보여 줍니다.

![Real VERL, vLLM, 3FS and Loki run](figures/verl-vllm-real-run.gif)

## Read the GIF and Dashboards

GIF의 재생 시간은 45초이며, 그래프의 x축은 2026-09-23 10:40:25–10:43:20(KST)의 실제 수집 시각입니다.
처음 12초는 시간 범위의 끝을 완료 step에 맞춰 이동하므로 화면의 숫자가 1에서 8로 바뀝니다.
이 부분은 학습을 45초에 실시간으로 다시 실행한 장면이 아니라 저장된 시계열을 순서대로 보여 주는 화면입니다.

| GIF 재생 구간 | 화면 | 읽는 순서 |
| --- | --- | --- |
| 0–12초 | Agent RL 상단, step 1–8 | `Latest completed RL step`의 증가를 확인하고, `Worker sample age`로 마지막 업데이트의 신선도를 판단합니다. Stage duration은 완료된 step의 값이므로 현재 진행 중인 phase의 시간으로 읽지 않습니다. |
| 12–19.5초 | Agent RL 아래쪽 | Stage 시간·처리량·GPU 사용률을 같은 시각에 비교합니다. `Live rollout engine signals`의 대기 요청과 `vLLM KV offload store and load`의 전송량은 서로 다른 지표입니다. Offload 카운터가 증가해도 3FS에 쓴 바이트만 따로 집계한 값은 아닙니다. |
| 19.5–25.5초 | Run Overview | Exporter 상태, GPU 표, 학습 sample age를 먼저 봅니다. `N/A`는 해당 metric이 없다는 뜻이지 측정값 0이 아닙니다. |
| 25.5–33초 | Compute & Communication | GPU 사용률·전력·온도와 NIC/RDMA를 비교합니다. 이 지표는 node 전체의 값이며 선택한 run에만 귀속되지 않습니다. |
| 33–40.5초 | Data & Storage | `Mount`에서 3FS FUSE 경로를 확인한 뒤 filesystem 용량과 host NVMe I/O를 구분해 봅니다. 빈 topology·SMART 패널은 해당 source가 연결되지 않았다는 뜻입니다. |
| 40.5–45초 | Run Logs | Loki에 수집된 같은 실행의 VERL 로그를 봅니다. `Run` 필터에는 아래 표의 lab 결과 디렉터리 이름을 사용합니다. |

대시보드를 직접 열 때는 `Cluster`·`Node`·`Run`과 시간 범위를 먼저 맞춘 뒤, Run Overview → Agent RL → Compute & Communication → Data & Storage → Run Logs 순서로 이동하면 됩니다.
같은 시각에 두 그래프가 변해도 원인을 단정할 수 없으며, node 전체나 공유 스토리지 지표는 다른 작업의 영향도 포함할 수 있습니다.
Run Logs에는 일부 tool-call decode error도 보이지만 학습 프로세스는 종료 코드 0으로 완료됐습니다.
오류의 존재와 모델 품질은 별도로 평가해야 합니다.

## Recorded Run

| 항목 | 기록된 실행 |
| --- | --- |
| 실행일 | 2026-09-23 |
| Telemetry run ID | `real-verl-vllm-3fs-loki-20260923-r2` |
| Lab 결과 디렉터리 | `xlayer-verl-vllm-3fs-loki-20260923-r2` |
| Workload | GSM8K tool-agent loop, GRPO + LoRA + FSDP2, async vLLM rollout |
| 모델 | `Qwen/Qwen2.5-1.5B-Instruct` revision `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` |
| Framework | VERL `896a9bba5c5ccd3f000d3d2f7c99d921cfd49236`, vLLM `0.24.0`, PyTorch `2.11.0+cu130` |
| 결과 | 8 training steps, 종료 코드 0, 3FS의 KV `.bin` 파일 682개(약 299 MiB) |
| Metrics | VERL file logger → xlayer bridge → node collector, vLLM `/metrics`, 3FS FUSE filesystem → Prometheus → Grafana |
| Logs | `<lab-results>/logs/training.log` → Alloy → Loki → Run Logs |

vLLM은 [filesystem KV tier](https://docs.vllm.ai/en/latest/features/kv_offloading_usage/#filesystem-fs)의 `root_dir`로 3FS FUSE 마운트를 사용했습니다.
3FS 마운트에서 KV 파일을 확인했고, 학습 중 수집한 vLLM `kv_offload_total_bytes_total`의 GPU→CPU 및 CPU→GPU 카운터가 증가했습니다.
3FS ClickHouse의 같은 시간대 `distributions`에는 `fuse.write.latency` 632회와 `storage.req_write.succ_latency` 682회가 기록됐습니다.
이 값은 공유 마운트의 관측치이며 이번 실행만의 I/O로 분리한 수치가 아닙니다.

이번 경로는 **POSIX/FUSE I/O**입니다.
USRBIO API를 직접 호출한 실행이나 USRBIO 성능 측정으로 해석하면 안 됩니다.
Data & Storage의 disk throughput·IOPS·busy time은 node 전체의 NVMe 지표이며, 3FS `Mount`의 filesystem 용량 지표와 별개입니다.
3FS 서비스의 ClickHouse latency와 SMART exporter는 이 GIF의 Grafana 패널에 연결하지 않았으므로 관련 패널은 비어 있습니다.
Reward mean은 0이었으므로 이 데모는 모델 품질 개선의 증거가 아닙니다.

GIF는 완료된 실제 수집 기록의 시간 범위를 step마다 변경한 뒤 브라우저에서 다섯 대시보드를 스크롤해 캡처했습니다.
Run Logs의 `Run` 변수에는 telemetry run ID 대신 lab 결과 디렉터리 이름이 들어갑니다.
Alloy가 `<log-root>/<run-directory>/logs/**/*.log` 경로에서 이 값을 추출하기 때문입니다.

## Reproduce the Run

3FS가 POSIX FUSE 경로에 마운트돼 있고 KV 파일을 저장할 하위 디렉터리에 쓰기 권한이 있어야 합니다.
`agentic-rl-lab`의 dataset과 Docker sandbox image는 해당 저장소의 `README.md`에 따라 준비합니다.
[Monitoring Guide](monitoring.md#monitor-one-gpu-node)에 따라 node collector와 server를 시작하고, server에는 `ENABLE_LOGS=1`, node에는 `LOKI_PUSH_URL`과 `TELEMETRY_LOG_ROOTS`를 지정합니다.
Node collector의 `TELEMETRY_METRICS_DIR`는 아래 결과 디렉터리의 `telemetry/telemetry-metrics`로 설정합니다.

```bash
export LAB_ROOT=/path/to/agentic-rl-lab
export THREEFS_MOUNT=/path/to/3fs/poc-mount
export RESULTS_DIR="$LAB_ROOT/results/xlayer-verl-vllm-3fs-$(date -u +%Y%m%dT%H%M%SZ)"
export MODEL_ID=Qwen/Qwen2.5-1.5B-Instruct
export MODEL_REVISION=989aa7980e4cf806f80c7fef2b1adb7bc71aa306
export VERL_LOGGER='["console","file"]'
export VERL_TRAINING_STEPS=8
export VERL_SAVE_FREQ=-1 VERL_TEST_FREQ=2
export VLLM_KV_OFFLOAD_DIR="$THREEFS_MOUNT/vllm-poc/$(basename "$RESULTS_DIR")"
export VLLM_KV_OFFLOAD_CPU_BYTES=268435456
export NCCL_NET_PLUGIN=none NCCL_NET=Socket NCCL_IB_DISABLE=1

TELEMETRY_PYTHON="$PWD/.venv/bin/python" \
  bash scripts/run_verl_with_telemetry.sh \
    --output "$RESULTS_DIR/telemetry" \
    --run-id "$(basename "$RESULTS_DIR")" \
    --node gpu-local --execution-mode async \
    -- bash "$LAB_ROOT/scripts/run-qwen3-4b-agentic.sh"
```

명령은 xlayer-telemetry 저장소 루트에서 실행합니다.
이 host에서는 처음 실행 때의 `ncclNetInit()` 충돌을 피하려고 위 NCCL socket 설정을 사용했습니다.
동적으로 정해지는 vLLM `/metrics` 주소를 학습이 시작된 직후 [Native Endpoint 등록](agent-rl.md#register-native-endpoints) 절차로 Prometheus의 `native` job에 추가해야 offload·queue 패널을 기록할 수 있습니다.
`VERL_SAVE_FREQ=-1`로 checkpoint 저장을 끄고 validation은 두 step마다 실행했습니다.
실행 후 `python -m xlayer_telemetry.show_run "$RESULTS_DIR/telemetry"`로 step snapshot과 event를 확인할 수 있습니다.
