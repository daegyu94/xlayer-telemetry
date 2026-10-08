# UI Telemetry Reference

**찾아보는 목적:** MFU·policy version·wrapper 상태·phase 경계의 producer와 N/A 조건을 확인합니다. 기본 연결은 [VERL guide](verl-quickstart.md), 화면 사용은 [App guide](grafana-scenes-poc.md)를 따릅니다.

## Coverage and Missing States

| 화면에 필요한 값 | 실제 producer |
| --- | --- |
| Training MFU | veRL의 stage별 `perf/mfu/*` logger scalar |
| Trainer policy version | 명시 parameter version scalar |
| Rollout policy version | 실제 적용 version을 명시한 application event/gauge |
| Wrapped workload 상태 | Wrapper의 status·exit code와 명시 context |
| Exact / calibrated phase | 실제 application call point의 SDK span |
| Shared GPU / vLLM / storage | Node/device collector·native endpoint |

- Logger key가 없으면 **No data**입니다. GPU util로 MFU를 대신 계산하지 않습니다.
- Step 번호·version lag만 있으면 policy version은 **Unknown**입니다.
- Wrapper context가 없거나 보고가 오래됐으면 상태를 추정하지 않습니다.
- File logger의 duration만 있으면 exact phase 경계는 알 수 없습니다.
- Shared resource의 Run 소유량은 이 값들로 확인할 수 없습니다.

```{admonition} Scope / No data
:class: important

명시적으로 측정한 `0`은 유효한 값입니다. 미설정·query 실패·누락·stale는 별도로 표시합니다. Reported scalar는 완료된 logger 관측이며 phase 경계가 아닙니다. Correlation ≠ attribution.
```

## Framework Reported Scalars

[VERL wrapper](verl-quickstart.md)는 원본 logger key가 있을 때 gauge를 기록합니다. 기존 `run_id`·`node`와 `producer="verl"`·`role="trainer"`·`worker_id="driver"` context를 유지합니다.

| 원본 key | 결과 |
| --- | --- |
| `perf/mfu/actor` | MFU ratio · `phase="actor_update"`, `verl_stage="update_actor"` |
| `perf/mfu/critic` | MFU ratio · `phase="critic_update"`, `verl_stage="update_critic"` |
| `perf/mfu/actor_infer` | MFU ratio · `phase="reference_log_prob"`, `verl_stage="old_log_prob"` |
| `fully_async/count/current_param_version` | `policy_version` · `policy_scope="trainer"` |
| `policy_version` | Custom logger가 명시한 trainer version |

**값과 label 계약**

- MFU metric은 `training_model_flops_utilization_ratio`입니다. 유한한 0–1 ratio만 허용합니다.
- Policy version은 0 이상의 정수이며 최대 `2**53`입니다.
- `reported_key`는 원본 key입니다. Actor·critic·inference를 합산하거나 평균내지 않습니다.
- Percent를 ratio로 임의 변환하거나 범위를 clamp하지 않습니다.

### Verify

```bash
python -m xlayer_telemetry.adapters.verl --describe-metrics
```

**정상 결과:** 출력에 `perf/mfu/actor -> training_model_flops_utilization_ratio`와 `fully_async/count/current_param_version -> policy_version`이 보입니다. GPU·VERL 설치 없이 translation table을 확인하는 명령입니다.

실행한 Run에서는 `logs/verl-metrics.jsonl`의 원본 key와 `telemetry-metrics/verl-trainer-driver*.json`의 sample을 함께 확인합니다.

### 원본 확인

수집 표의 공개 producer는 veRL commit `8718ca30a3f002f93b7c4fd99b9b2506718681bc`에서 확인했습니다. [Engine worker의 MFU 계산](https://github.com/verl-project/verl/blob/8718ca30a3f002f93b7c4fd99b9b2506718681bc/verl/workers/engine_workers.py#L229), [trainer의 stage별 logger key](https://github.com/verl-project/verl/blob/8718ca30a3f002f93b7c4fd99b9b2506718681bc/verl/trainer/ppo/ray_trainer.py#L1373), [fully async trainer의 명시 parameter version](https://github.com/verl-project/verl/blob/8718ca30a3f002f93b7c4fd99b9b2506718681bc/verl/experimental/fully_async_policy/fully_async_trainer.py#L1033)을 기준으로 하며 배포 version과 logger 설정에 따라 key가 없을 수 있습니다.

Fully async logger는 [aggregation rule](https://github.com/verl-project/verl/blob/8718ca30a3f002f93b7c4fd99b9b2506718681bc/verl/experimental/fully_async_policy/detach_utils.py#L213)에 따라 parameter version의 마지막 명시 값을 남깁니다. Bridge는 framework가 적용한 aggregation을 유지하며 sync-cycle scalar를 개별 micro-step·replica의 관측으로 풀어내지 않습니다.

vLLM의 [MFU 관련 native metric](https://docs.vllm.ai/en/stable/usage/metrics/#model-flops-utilization-mfu-performance-metrics)은 `--enable-mfu-metrics`로 노출하는 estimated FLOPs·memory bytes counter입니다. 이 counter를 training MFU ratio로 변환하지 않으며 native endpoint의 engine/device scope를 유지합니다.

## Explicit Policy Version in Events

`CorrelationContext(..., policy_version=...)` 또는 `EventRecorder.policy_applied()`·`span()`에 실제 적용 version을 전달합니다. 아래는 application에 넣는 integration fragment입니다.

```python
from xlayer_telemetry.events import EventRecorder

events = EventRecorder.from_env(producer="my_rollout", role="rollout",
                                 node="rollout-node-0", worker_id="rollout-0")
# applied_version은 해당 worker가 실제 적용을 확인한 native version입니다.
if events is not None:
    events.policy_applied(applied_version, step=training_step)
    with events.span("rollout.generate", phase="rollout",
                     step=training_step, policy_version=applied_version):
        result = generate_sequences(batch)
```

**정상 결과:** `weights.applied` event에 top-level `policy_version`·`policy_version_source="producer_reported"`와 `attributes.policy_scope="worker_applied"`가 기록됩니다. Version 값은 Prometheus label에 추가되지 않습니다.

- `policy_applied()`는 native callback이 적용을 확인한 뒤 호출합니다. 명시적인 node·worker와 0 이상의 integer version이 필요합니다.
- Version이 바뀌면 매 호출에 실제 값을 전달합니다.
- Context의 version은 고정 fallback입니다.
- Step·lag·trainer snapshot으로 rollout applied version을 추론하지 않습니다.
- Overview의 **Policy / KV Lifecycle**은 이 명시 event가 있는 worker만 나열합니다. Event가 없는 worker의 적용 coverage를 보충하지 않습니다.

## Wrapped Workload Status

Wrapper의 `telemetry-health.json`에서 `workload.status`·`exit_code`를 읽고 application collector가 같은 `application.prom`에 게시합니다.

| Metric | 의미 |
| --- | --- |
| `telemetry_wrapped_workload_state{state="..."}` | `running`·`succeeded`·`failed` 세 gauge 중 해당 상태만 1 |
| `telemetry_wrapped_workload_observed_timestamp_seconds` | Wrapper node clock의 마지막 보고 시각 |
| `telemetry_wrapped_workload_exit_code` | Terminal report의 명시 command exit code |

**Identity / artifact 계약**

- `workload_observation`: `run_id`·`node`·`source="wrapper_health"`·`boundary_scope="wrapped_command"`·`clock_scope="node"`·`observed_at`.
- Metric label: `run_id`·`node`·`producer="xlayer"`·`role="launcher"`·`worker_id="wrapper"`·`source="wrapper_health"`·`boundary_scope="wrapped_command"`. Timestamp는 metric 값이며 `clock_scope`는 artifact에 보존합니다.
- `running`은 명시 running report, `succeeded`는 finished + exit 0, `failed`는 finished + nonzero exit입니다. 다른 두 state gauge는 0입니다.
- Pre-launch `starting`은 running gauge를 만들지 않습니다. Terminal timestamp는 cleanup·최종 export 후 보고이며 정확한 command 종료/phase 시각이 아닙니다.
- 오래된 artifact도 `xltel inspect`로 읽을 수 있습니다. Context가 없으면 collector는 directory·manifest에서 identity를 보충하지 않습니다.

```{admonition} Wrapped command scope
:class: important

상태는 wrapper가 실행한 command의 마지막 명시 보고입니다. Collector UP·telemetry completeness·전체 async Ray Run 또는 모든 rollout/tool 작업의 완료 상태와 별개입니다. Terminal report가 없으면 이전 running 값도 freshness 한도 이후 누락됩니다.
```

### Configure / Verify

1. Wrapper의 `--node`/`TELEMETRY_NODE`, collector의 `NODE_NAME`/`--node`, target의 `nodename`, dashboard의 `source_node`를 같은 logical node 이름으로 맞춥니다.
2. 기존 `TELEMETRY_RUNS_ROOT` 또는 `TELEMETRY_METRICS_DIR`로 [application collector](application-metrics.md#4-publish-through-the-node-collector)를 연결합니다.
3. 실제 Run 경로로 textfile projection을 확인합니다.

```bash
python -m xlayer_telemetry.metrics.textfile \
  --runs-root /path/to/runs --node trainer-node \
  --textfile-dir /path/to/check-textfile --once
```

**정상 결과:** 유효한 wrapper report가 있으면 `application.prom`에 state와 observed timestamp가 보입니다. Report가 없거나 identity·freshness가 맞지 않으면 해당 metric을 생략합니다.

### Freshness / 수집 예산

- `--runs-root`: 기본 TTL 300초. 종료 Run의 application snapshot을 제외해도 fresh terminal wrapper report는 게시합니다.
- TTL 이후 current 상태는 unknown/stale입니다. 저장된 마지막 상태는 artifact 조회로 확인합니다.
- Legacy `--metrics-dir`에는 기본 age 제한이 없습니다. Current 상태가 필요하면 `--max-age-seconds`를 지정합니다.
- 직계 Run directory의 최근 artifact 최대 1,024개, artifact당 최대 64 KiB를 읽습니다.
- `telemetry_application_workload_reads_total`·`telemetry_application_workload_rejections_total`·`telemetry_application_workload_limit_drops_total`은 반복 poll을 포함한 누적 작업 횟수입니다.

## Measured Native Phase Hooks

`measured_phase(events, stage, ...)`는 실제 call을 기존 `EventRecorder.span()`으로 감싸는 helper입니다. Wrapper/file logger가 모든 phase를 자동 계측하지 않습니다.

다음은 [veRL generation call point](https://github.com/verl-project/verl/blob/8718ca30a3f002f93b7c4fd99b9b2506718681bc/verl/trainer/ppo/ray_trainer.py#L1510)에 넣는 fragment입니다. Source checkout 변경과 실제 call point 연결은 사용자가 관리합니다.

```python
from xlayer_telemetry.adapters.verl import measured_phase
from xlayer_telemetry.events import EventRecorder

events = EventRecorder.from_env(producer="verl_native", role="trainer",
                                 worker_id="driver")
with measured_phase(events, "gen", step=self.global_steps,
                    attributes={"boundary_scope": "driver_rpc"}):
    combined_gen_output = self.async_rollout_manager.generate_sequences(combined_gen_batch)
```

**정상 결과:** 실제 block entry/exit와 monotonic duration, `verl_stage`·`measurement_source="native_sdk"`가 기록됩니다. Async call은 같은 `with` 안에서 `await`하며 반환값·exception을 유지합니다.

| 관측 상태 | 의미 |
| --- | --- |
| Exact | Calibration 없는 node-clock call 경계 |
| Calibrated | Raw timestamp·reference·uncertainty를 보존한 경계 |
| Unknown | 설정한 calibration 실패 |
| Clock discontinuity | 감지한 clock jump |

- Driver RPC span에는 wait가 포함될 수 있습니다. GPU kernel 시간이나 모든 replica의 phase로 읽지 않습니다.
- Remote worker에는 실제 node·role·worker·rank·GPU context와 `trace_id`·`parent_span_id` 관계를 전달합니다.
- `timing_s/gen` duration으로 exact interval을 만들지 않습니다.
- Helper는 CUDA synchronization·Nsight/PyTorch capture를 실행하지 않습니다. [시간 보정](time-alignment.md)과 [기존 profiler](deep-dive.md)를 따릅니다.

## Validation and Remaining Limits

회귀 검증 범위는 native scalar fixture·invalid/zero·명시 policy·sync/awaited SDK call·calibrated/unknown clock·실제 CPU wrapper → health → textfile projection입니다. 실제 GPU·VERL distributed workload·vLLM engine rendering 검증은 이 수집 지원 검증과 구분합니다.

원본 key가 없거나 hook을 연결하지 않았다면 N/A가 유지됩니다. Shared ownership과 async workload의 전체 completion은 이 지원 범위에서 확정하지 않습니다.

## 다음

[VERL 연결](verl-quickstart.md) · [Native source 등록](native-sources.md) · [Log / Event 조회](logs-events.md) · [App에서 조사](grafana-scenes-poc.md)
