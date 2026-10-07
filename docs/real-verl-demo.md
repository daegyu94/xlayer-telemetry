# Real VERL Agent RL Demo

실제 VERL Agent RL의 저장 데이터를 당시 Grafana 조사 화면 8개로 재생한 GIF입니다.
학습은 2026-09-30, 캡처는 2026-10-01이며 새로운 학습·측정은 아닙니다.
처음 연결한다면 [VERL Quickstart](verl-quickstart.md)를 따릅니다.

![2026-10-01에 캡처한 실제 VERL Agent RL investigation: Run Overview, Bottleneck Summary, Timeline, vLLM, Sandbox, GPU, Storage, Logs](figures/verl-agent-rl-investigation.gif)

## Watch the Recording

약 95초 GIF에서 화면마다 5–7초 유지합니다.
Start Here → 완료 step → candidate/evidence/baseline → Timeline → vLLM·sandbox·Compute·Storage·Logs로 이동하고 접힌 detail을 펼칩니다.
Step 선택은 Run Overview, 기존 Step Detail은 Timeline에 통합됐습니다.

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

Framework revision·다른 trainer mode의 실행 결과는 [실환경 검증 기록](validation/sandbox/validation-20260930.json)과 [Real validation coverage](integration-reference.md#real-validation-coverage)에 있습니다.
GIF의 캡처 정보와 파일 checksum은 [Recording manifest](validation/dashboards/recordings-20261001.json)에 기록했습니다.
이 작은 integration workload는 SWE-Bench 해결률이나 학습 throughput benchmark가 아닙니다.

## Read the Result

`no_anomaly_observed`는 수집 evidence에서 rule candidate를 찾지 못했다는 뜻으로 모든 병목의 부재를 보증하지 않습니다.
빈 candidate panel은 정상이며 후보·counter/missing evidence 예시는 [synthetic GIF](monitoring-reference.md#demo-details)를 봅니다.
RDMA·policy lag·비활성 KV offload는 N/A/missing으로 남겼습니다.

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

이 smoke recipe는 private [verl-lab](https://github.com/daegyu94/verl-lab) 접근 권한과 준비된 dataset·Docker image가 필요합니다.
공개 VERL 도입에는 lab checkout이 필요하지 않으며 [기존 VERL 명령 연결](verl-quickstart.md)을 사용합니다.
Lab 환경에서는 [sandbox integration](sandbox.md)을 연결합니다.
[Smoke launcher](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/sandbox/run_verl_lab_smoke.sh)의 cgroup 설정을 확인하고 collector·native·Loki를 켭니다.
`show_run`·[validator](https://github.com/daegyu94/xlayer-telemetry/blob/main/examples/sandbox/validate_smoke.py)로 step·trace를 검사하며 같은 명령도 동일 성능을 보장하지는 않습니다.

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

| 재현 준비 | 확인 |
| --- | --- |
| 3FS | POSIX FUSE mount와 KV 하위 directory 쓰기 권한 |
| Dataset / tool | 사용하는 lab checkout README에 따라 준비 |
| Monitoring | [Node/server](monitoring-reference.md#monitor-one-gpu-node)와 `ENABLE_LOGS`·`LOKI_PUSH_URL`·`TELEMETRY_LOG_ROOTS` 설정 |
| Snapshot path | `$RESULTS_DIR/telemetry/telemetry-metrics` |
| Log root | `$LAB_ROOT/results`처럼 RESULTS_DIR의 부모; 수집 담당 한 대 |
| Step file | Wrapper output이 `$RESULTS_DIR/telemetry`이므로 그 아래 `telemetry-events/verl-steps.jsonl` |

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

| 당시 조건과 후속 확인 | 해석 |
| --- | --- |
| NCCL socket 설정 | 이 host의 `ncclNetInit()` 충돌 회피용; 일반 배포 기본값으로 강제하지 않음 |
| vLLM metric | 당시 `disable_log_stats=False`·`prometheus.enable=True`; 현재 launcher 전달 여부는 [Native source](native-sources.md)에서 확인 |
| Trainer mode | 당시 `trainer.v1.trainer_mode=sync`; rollout server `mode=async`와 다른 설정 |
| Dynamic endpoint | 실행 직후 실제 `/metrics`를 native job에 등록해야 queue/offload 기록 가능 |
| Checkpoint / validation | `VERL_SAVE_FREQ=-1`; validation은 두 step마다 실행 |
| 실행 후 | `python -m xlayer_telemetry.show_run "$RESULTS_DIR/telemetry"`로 snapshot·event 확인 |

## Refresh the Recording

[Capture script](https://github.com/daegyu94/xlayer-telemetry/blob/main/scripts/capture_dashboard_demo.py)는 실행 중인 Grafana에서 지정한 run·step·시간 구간을 열고 동일한 investigation 순서를 GIF로 저장합니다.
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

Context 파일을 `--context`로 지정합니다.

```bash
python scripts/capture_dashboard_demo.py \
  --context capture-context.json \
  --output investigation.gif \
  --metadata capture-result.json
```

`source_node`는 step을 기록한 observer, `node`는 조사할 resource, `sandbox_node`는 sandbox worker의 실제 node입니다.
Dedicated 배치에서는 서로 다른 값을 그대로 전달합니다.
결과 metadata에는 화면 순서·유지 시간·선택한 context와 browser 오류 여부가 남습니다.
