# Real VERL Agent RL Demo

이 GIF는 실제 VERL Agent RL 실행에서 저장한 Prometheus·Loki 데이터를 **현재 Grafana dashboard 8개**로 다시 연 화면입니다.
학습은 2026-09-30에 실행했고 화면은 2026-10-01에 캡처했습니다.
새로운 학습이나 성능 측정을 한 것이 아니라, 기록된 SWE-Bench guided patch 실행의 step·resource·tool/sandbox span·log를 최신 investigation workflow로 재생합니다.
처음 연결하는 사용자는 [VERL 연결 가이드](verl-quickstart.md)를 먼저 따라야 합니다.

![현재 Grafana의 실제 VERL Agent RL investigation: Run Overview, Bottleneck Summary, Timeline, vLLM, Sandbox, GPU, Storage, Logs](figures/verl-agent-rl-investigation.gif)

## Watch the Recording

GIF는 약 95초이며 각 화면을 5–7초 유지합니다.
Start Here → Run Overview의 완료 step 목록 → Bottleneck Summary의 diagnosis·evidence·baseline → Cross-Layer Timeline의 경계·span·host resource를 보여 줍니다.
이어서 Agent RL Stage Correlation의 vLLM·sandbox 행, Compute & Communication, Data & Storage, Run Logs로 이동합니다.
Step Explorer의 step 선택은 Run Overview에, 기존 Step Detail의 자원 상세는 Timeline에 통합되어 있습니다.
접혀 있는 상세 행을 펼쳐 보는 동작도 녹화했습니다.

Overview와 resource 화면의 시간 범위는 2026-09-30 12:00:02–12:01:10(KST), 선택한 trainer update 3의 구간은 12:01:03–12:01:10입니다.
화면 전환은 investigation 순서이며 학습 phase의 실행 순서가 아닙니다.
과거 구간의 freshness·target 상태 역시 선택한 수집 시각의 상태를 보여 줍니다.
현재 source가 실행 중이라는 의미는 아닙니다.
패널별 해석은 [Dashboard Guide](dashboards.md)에 정리했습니다.

## Recorded Agent RL Run

| 항목 | GIF에 사용한 실행 |
| --- | --- |
| 실행일 / 화면 캡처일 | 2026-09-30 / 2026-10-01 |
| Telemetry run ID | `agent-e2e-swe-colocate_async` |
| Log directory | `swe-colocate_async` |
| Workload | SWE-Bench guided patch, 실제 VERL GRPO 학습 + vLLM generation + Docker grader |
| 모델 | `Qwen2.5-1.5B-Instruct` |
| Trainer mode | `colocate_async`, trainer update 경계는 approximate |
| 배치 | 물리 host 1대, GPU 2개, colocated Docker sandbox, cgroup v2 parent |
| 결과 | 3 update, 종료 코드 0, tool.call 46개, sandbox.exec 7개 모두 trace/parent 연결 확인 |
| Step duration | 10.585 s / 6.142 s / 6.332 s |
| 관측 source | Application·GPU·host·vLLM·Ray·sandbox cgroup → Prometheus, step·span·diagnosis·trainer log → Loki |
| 선택한 step의 verdict | `no_anomaly_observed` |

Framework revision·다른 trainer mode의 실행 결과는 [실환경 검증 기록](../examples/sandbox/validation-20260930.json)과 [Real validation coverage](agent-rl.md#real-validation-coverage)에 있습니다.
GIF의 캡처 정보와 파일 checksum은 [Recording manifest](../examples/dashboards/validation/recordings-20261001.json)에 기록했습니다.
이 작은 integration workload는 SWE-Bench 해결률이나 학습 throughput benchmark가 아닙니다.

## Read the Result

Bottleneck Summary의 `no_anomaly_observed`는 수집된 evidence에서 rule candidate가 만들어지지 않았다는 뜻입니다.
이 경우 candidate/evidence 패널이 비어 있는 것은 정상이며, 모든 자원에 병목이 없다는 보장은 아닙니다.
병목 후보와 supporting/counter/missing evidence가 채워진 예제는 [synthetic investigation GIF](monitoring.md#demo-details)에서 볼 수 있습니다.
실제 GIF에서는 RDMA·policy version lag와 활성화하지 않은 KV offload 등의 source를 N/A 또는 missing evidence로 유지했습니다.

Timeline은 exact EventRecorder span, approximate trainer update 경계, sampled resource metric을 구분합니다.
일부 tool/sandbox span의 `step`은 명시적으로 전달되지 않아 비어 있습니다.
같은 trace의 parent 연결은 확인할 수 있지만, 시간 구간에 나타난 모든 tool이 해당 trainer update에 속한다고 단정하면 안 됩니다.
Async rollout의 span은 필요하면 해당 trace의 실제 시간 구간으로 확장해 확인합니다.

Sandbox 행은 안정적인 worker cgroup의 I/O·PSI와 같은 node의 local NVMe busy를 보여 줍니다.
Pool occupancy는 runtime producer가 제공하지 않아 N/A입니다.
Cgroup과 device는 observation scope가 다르므로 device I/O 전체를 특정 trajectory의 사용량으로 읽지 않습니다.
이번 GIF는 3FS KV offload나 USRBIO 측정 화면이 아닙니다.
이전 3FS POSIX 실험의 결과와 조건은 [아래 기록](#3fs-posix-experiment-record-2026-09-23)에 별도로 남겼습니다.

Run Logs의 `Log directory`는 `swe-colocate_async`, `Run context`는 `agent-e2e-swe-colocate_async`입니다.
두 값이 다른 것은 log 경로와 application identity가 서로 다른 문맥이기 때문입니다.

## Reproduce an Agent RL Validation

이미 실행 가능한 [verl-lab](https://github.com/daegyu94/verl-lab) 환경에서 [Sandbox integration example](agent-rl.md#observe-an-agent-sandbox)을 연결합니다.
Smoke launcher는 [run_verl_lab_smoke.sh](../examples/sandbox/run_verl_lab_smoke.sh)이며 dataset·Docker grader image·VERL 환경이 필요합니다.
현재 checkout의 준비 절차와 cgroup 설정을 먼저 확인합니다.
Collector·native endpoint·Loki를 연결한 뒤 `show_run`과 [smoke validator](../examples/sandbox/validate_smoke.py)로 step과 sandbox trace를 확인합니다.
동일한 모델이나 명령만 실행한다고 GIF와 같은 성능이 보장되는 것은 아닙니다.

## 3FS POSIX Experiment Record (2026-09-23)

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
당시 Grafana에는 3FS 서비스의 ClickHouse latency와 SMART exporter를 연결하지 않았습니다.
Reward mean은 0이었으므로 이 데모는 모델 품질 개선의 증거가 아닙니다.
당시 학습 로그에는 일부 tool-call decode error가 있었지만 학습 프로세스는 종료 코드 0으로 완료됐습니다.

현재 Run Logs에서는 `Log directory`에 lab 결과 디렉터리 이름을, `Run context`에 telemetry run ID를 지정합니다.
Alloy는 `<log-root>/<run-directory>/logs/**/*.log` 경로에서 log directory를 추출하므로 두 값은 서로 다를 수 있습니다.

### Reproduce the 3FS Recipe

이 기록은 당시 `agentic-rl-lab` recipe를 기준으로 하며, 현재 lab 저장소는 [verl-lab](https://github.com/daegyu94/verl-lab)입니다.
아래 명령은 해당 launcher와 환경 변수 계약을 유지한 checkout에서 사용하는 참고 예시이며, 현재 VERL version 전체의 호환성을 보장하지 않습니다.
새 배포에서는 `verl-lab`의 설치·dataset 준비 절차를 확인하고 [일반 VERL wrapper](verl-quickstart.md)를 먼저 연결합니다.

3FS가 POSIX FUSE 경로에 마운트돼 있고 KV 파일을 저장할 하위 디렉터리에 쓰기 권한이 있어야 합니다.
Dataset과 필요한 tool environment는 사용하는 lab checkout의 `README.md`에 따라 준비합니다.
[Monitoring Guide](monitoring.md#monitor-one-gpu-node)에 따라 node collector와 server를 시작하고, 같은 server 설정 파일에 `ENABLE_LOGS=1`, node에는 `LOKI_PUSH_URL`과 `TELEMETRY_LOG_ROOTS`를 지정합니다.
Node collector의 `TELEMETRY_METRICS_DIR`는 아래 결과 디렉터리의 `telemetry/telemetry-metrics`로 설정합니다.
`TELEMETRY_LOG_ROOTS`는 `$LAB_ROOT/results`처럼 `$RESULTS_DIR`의 부모를 가리키고, 학습 log를 수집할 collector 한 대에만 설정합니다.
Wrapper의 `--output`이 `$RESULTS_DIR/telemetry`이므로 Run Overview의 완료 step 목록과 Timeline에 연결할 event 파일은 `$RESULTS_DIR/telemetry/telemetry-events/verl-steps.jsonl`에 생깁니다.

```bash
export LAB_ROOT=/path/to/verl-lab
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
    --node gpu-local --execution-mode sync \
    -- bash "$LAB_ROOT/scripts/run-qwen3-4b-agentic.sh"
```

명령은 xlayer-telemetry 저장소 루트에서 실행합니다.
이 host에서는 처음 실행 때의 `ncclNetInit()` 충돌을 피하려고 위 NCCL socket 설정을 사용했습니다.
기록된 실행에서는 `actor_rollout_ref.rollout.disable_log_stats=False`와 `actor_rollout_ref.rollout.prometheus.enable=True`로 native metrics를 활성화했습니다.
현재 lab launcher가 이 두 값을 모두 전달한다고 가정하지 말고 사용하는 VERL의 지원 설정을 [Native Endpoint 등록](agent-rl.md#register-native-endpoints) 절차로 확인합니다.
이 recipe의 `trainer.v1.trainer_mode`는 `sync`입니다.
`actor_rollout_ref.rollout.mode=async`는 vLLM server 방식이며 XLayer의 trainer 경계 `--execution-mode`와 다른 설정입니다.
동적으로 정해지는 vLLM `/metrics` 주소를 학습이 시작된 직후 [Native Endpoint 등록](agent-rl.md#register-native-endpoints) 절차로 Prometheus의 `native` job에 추가해야 offload·queue 패널을 기록할 수 있습니다.
`VERL_SAVE_FREQ=-1`로 checkpoint 저장을 끄고 validation은 두 step마다 실행했습니다.
실행 후 `python -m xlayer_telemetry.show_run "$RESULTS_DIR/telemetry"`로 step snapshot과 event를 확인할 수 있습니다.

## Refresh the Recording

[Capture script](../scripts/capture_dashboard_demo.py)는 실행 중인 Grafana에서 지정한 run·step·시간 구간을 열고 동일한 investigation 순서를 GIF로 저장합니다.
별도의 학습이나 telemetry 생성은 하지 않으므로 Prometheus와 Loki에 해당 시간의 데이터가 보존되어 있어야 합니다.
Playwright와 Chromium, `ffmpeg`·`ffprobe`는 녹화할 때만 필요한 optional dependency입니다.
인증이 필요한 Grafana에서는 먼저 해당 환경에 맞는 browser 접근 방식을 구성해야 합니다.

Context JSON에는 자신의 Grafana 주소와 실제 label·step record·시간 범위를 지정합니다.
아래의 시간은 위 기록의 예시이며 자신의 실행에서는 보존된 구간의 Unix epoch millisecond로 바꿉니다.

```json
{
  "grafana_url": "http://127.0.0.1:13000",
  "variables": {
    "cluster": "agent-rl-e2e",
    "node": "gpu-local",
    "source_node": "gpu-local",
    "run_id": "agent-e2e-swe-colocate_async",
    "record_id": "ea0b16c92669273759c247bd",
    "sandbox_node": "gpu-local",
    "log_run_id": "swe-colocate_async",
    "diagnosis_method": "rule"
  },
  "from_ms": 1790737202223,
  "to_ms": 1790737270217,
  "step_from_ms": 1790737263884,
  "step_to_ms": 1790737270217,
  "data_origin": "observed"
}
```

위 내용을 `capture-context.json`으로 저장하고 녹화 dependency가 있는 Python 환경에서 실행합니다.

```bash
python scripts/capture_dashboard_demo.py \
  --context capture-context.json \
  --output investigation.gif \
  --metadata capture-result.json
```

`source_node`는 step을 기록한 observer, `node`는 조사할 resource, `sandbox_node`는 sandbox worker의 실제 node입니다.
Dedicated 배치에서는 서로 다른 값을 그대로 전달합니다.
결과 metadata에는 화면 순서·유지 시간·선택한 context와 browser 오류 여부가 남습니다.
