---
orphan: true
---

# UI Telemetry Coverage

UI의 MFU·policy version·workload 상태·phase boundary에는 각각 명시적 producer가 필요합니다. 이 문서는 기존 file logger bridge, event SDK와 application textfile collector에서 지원하는 연결 및 남는 N/A의 의미를 설명합니다.

## Coverage and Missing States

| 항목 | 지원 경로 | Source가 없을 때 |
| --- | --- | --- |
| Training MFU | veRL가 보고한 stage별 `perf/mfu/*` scalar | 해당 logger key가 없으면 `no data`; 다른 source로 추정하지 않음 |
| Trainer policy version | Fully async veRL의 명시 parameter version scalar | Step 번호·version lag만 있으면 `unknown` |
| Rollout policy version | Application이 실제 적용된 version을 event 또는 gauge로 명시 | Trainer version을 rollout version으로 대신 표시하지 않음 |
| Wrapped workload 상태 | Wrapper의 status·exit code artifact → application textfile | Wrapper가 없거나 오래된 artifact에 명시 context가 없으면 `unsupported` 또는 `unknown` |
| Exact / calibrated phase | Application call point에 연결한 event SDK span | File logger duration만 있으면 exact boundary는 `unknown` |
| Shared GPU / vLLM / storage | 기존 node/device/native endpoint 관측 | Source 미설정·query 실패·no data·stale를 구분하며 run 소유량은 `unknown` |

명시적으로 관측된 `0`은 유효한 값입니다. 미설정·누락·비정상 범위·stale는 시계열 생략으로 표현하며 `0` 또는 정상 상태로 채우지 않습니다.

```{important}
Reported scalar는 완료된 logger observation이며 현재 phase의 시간 경계가 아닙니다. Shared resource와 같은 시간에 변한 사실은 correlation이며 특정 run의 사용량이나 원인을 증명하지 않습니다.
```

## Framework Reported Scalars

[VERL Quickstart](verl-quickstart.md)의 `xltel run` wrapper는 원본 logger key가 있을 때 다음 gauge를 기록합니다. `producer="verl"`·`role="trainer"`·`worker_id="driver"`·`run_id`·`node`는 기존 snapshot context를 유지합니다.

| 원본 logger key | Metric | 추가 label | 값 검증 |
| --- | --- | --- | --- |
| `perf/mfu/actor` | `training_model_flops_utilization_ratio` | `phase="actor_update"`, `verl_stage="update_actor"`, `reported_key="perf/mfu/actor"` | Finite, 0–1 |
| `perf/mfu/critic` | 같은 metric | `phase="critic_update"`, `verl_stage="update_critic"`, 원본 `reported_key` | Finite, 0–1 |
| `perf/mfu/actor_infer` | 같은 metric | `phase="reference_log_prob"`, `verl_stage="old_log_prob"`, 원본 `reported_key` | Finite, 0–1 |
| `fully_async/count/current_param_version` | `policy_version` | `policy_scope="trainer"`, 원본 `reported_key` | Nonnegative integer, 최대 `2**53` |
| `policy_version` | `policy_version` | `policy_scope="trainer"`, `reported_key="policy_version"` | Custom logger가 명시한 같은 범위의 integer |

MFU는 GPU util에서 계산하지 않으며 percent를 ratio로 임의 변환하거나 범위를 clamp하지 않습니다. Actor·critic·actor inference는 별도 관측이므로 전체 run MFU로 합산하거나 평균내지 않습니다.

실제 translation table은 GPU·VERL 설치 없이 확인할 수 있습니다.

```bash
python -m xlayer_telemetry.adapters.verl --describe-metrics
```

출력에 `perf/mfu/actor -> training_model_flops_utilization_ratio`와 `fully_async/count/current_param_version -> policy_version`이 나타납니다. Run에서는 `logs/verl-metrics.jsonl`의 원본 key와 `telemetry-metrics/verl-trainer-driver*.json`의 sample을 함께 확인합니다.

수집 표의 공개 producer는 veRL commit `8718ca30a3f002f93b7c4fd99b9b2506718681bc`에서 확인했습니다. [Engine worker의 MFU 계산](https://github.com/verl-project/verl/blob/8718ca30a3f002f93b7c4fd99b9b2506718681bc/verl/workers/engine_workers.py#L229), [trainer의 stage별 logger key](https://github.com/verl-project/verl/blob/8718ca30a3f002f93b7c4fd99b9b2506718681bc/verl/trainer/ppo/ray_trainer.py#L1373), [fully async trainer의 명시 parameter version](https://github.com/verl-project/verl/blob/8718ca30a3f002f93b7c4fd99b9b2506718681bc/verl/experimental/fully_async_policy/fully_async_trainer.py#L1033)을 기준으로 하며 배포 version과 logger 설정에 따라 key가 없을 수 있습니다.

Fully async logger는 [aggregation rule](https://github.com/verl-project/verl/blob/8718ca30a3f002f93b7c4fd99b9b2506718681bc/verl/experimental/fully_async_policy/detach_utils.py#L213)에 따라 parameter version의 마지막 명시 값을 남깁니다. Bridge는 framework가 적용한 aggregation을 유지하며 sync-cycle scalar를 개별 micro-step·replica의 관측으로 풀어내지 않습니다.

vLLM의 [MFU 관련 native metric](https://docs.vllm.ai/en/stable/usage/metrics/#model-flops-utilization-mfu-performance-metrics)은 `--enable-mfu-metrics`로 노출하는 estimated FLOPs·memory bytes counter입니다. 이 counter를 training MFU ratio로 변환하지 않으며 native endpoint의 engine/device scope를 유지합니다.

## Explicit Policy Version in Events

`CorrelationContext(..., policy_version=...)`와 `EventRecorder.event()`·`span()`의 `policy_version=`은 application이 명시한 nonnegative integer를 event의 top-level field로 저장합니다. Record에는 `policy_version_source="producer_reported"`가 붙으며 version 값은 Prometheus label에 추가되지 않습니다.

```python
from xlayer_telemetry.events import EventRecorder

events = EventRecorder.from_env(producer="my_rollout", role="rollout")
# applied_version은 해당 worker가 실제 적용을 확인한 native version입니다.
if events is not None:
    events.event("weights.applied", phase="weight_sync",
                 policy_version=applied_version)
    with events.span("rollout.generate", phase="rollout",
                     step=training_step, policy_version=applied_version):
        result = generate_sequences(batch)
```

한 process 안에서 version이 바뀌면 매 호출에 실제 값을 전달합니다. Context의 version은 고정 fallback이며 `step`·`policy_version_lag`·trainer snapshot에서 rollout version을 추론하지 않습니다.

## Wrapped Workload Status

Wrapper는 기존 `telemetry-health.json`의 `workload.status`·`exit_code`를 유지하고 `workload_observation`에 명시 `run_id`·`node`·`source="wrapper_health"`·`boundary_scope="wrapped_command"`·`clock_scope="node"`·`observed_at`을 추가합니다. 이 field가 없는 기존 artifact도 `xltel inspect`로 읽을 수 있지만 collector는 directory 이름이나 manifest의 role에서 상태 identity를 보충하지 않습니다.

Application collector가 읽는 `telemetry-metrics`의 부모에 health artifact가 있을 때 다음 gauge를 같은 `application.prom`에 게시합니다.

| Metric | 값과 의미 |
| --- | --- |
| `telemetry_wrapped_workload_state{state="running"}` | 명시 `running`이면 1 |
| `telemetry_wrapped_workload_state{state="succeeded"}` | 명시 `finished`와 exit code 0이면 1 |
| `telemetry_wrapped_workload_state{state="failed"}` | 명시 `finished`와 nonzero exit code이면 1 |
| `telemetry_wrapped_workload_observed_timestamp_seconds` | `workload_observation.observed_at`; wrapper node clock의 보고 시각 |
| `telemetry_wrapped_workload_exit_code` | 명시 terminal report에만 게시하는 command exit code |

유효한 한 report마다 세 state gauge를 모두 게시하며 해당 상태만 1, 나머지는 0입니다. 공통 label은 `run_id`·`node`·`producer="xlayer"`·`role="launcher"`·`worker_id="wrapper"`·`source="wrapper_health"`·`boundary_scope="wrapped_command"`입니다.

최초 pre-launch artifact는 `workload.status="starting"`이므로 running gauge를 생성하지 않습니다. Terminal report는 command 종료 후 cleanup·최종 export를 거친 뒤 기록되며 timestamp는 정확한 command 종료 시각이나 phase boundary를 뜻하지 않습니다.

```{important}
상태는 wrapper가 실행한 command의 마지막 명시 보고입니다. Collector `up`, telemetry completeness, 전체 async Ray run·모든 rollout/tool 작업의 완료 상태와 별개입니다. Wrapper 자체가 중단되어 terminal report가 없으면 이전 running 값도 freshness 한도 이후 누락됩니다.
```

Node collector는 application과 같은 logical node에서 실행합니다. Wrapper의 `--node`/`TELEMETRY_NODE`, collector의 `NODE_NAME`/`--node`, monitoring target의 `nodename`과 dashboard의 `source_node`가 같은 이름이어야 합니다.

기존 `TELEMETRY_RUNS_ROOT` 또는 `TELEMETRY_METRICS_DIR` 경로를 사용하며 새로운 backend 설정은 필요하지 않습니다. [Application collector 설정](application-metrics.md#4-publish-through-the-node-collector)을 따른 뒤 다음 명령으로 CPU에서도 projection을 확인할 수 있습니다.

```bash
python -m xlayer_telemetry.metrics.textfile \
  --runs-root /path/to/runs --node trainer-node \
  --textfile-dir /path/to/check-textfile --once
```

생성된 `application.prom`에서 state와 observed timestamp를 확인합니다. `--runs-root`는 기본 300초 TTL을 적용하며 종료된 run의 application snapshot은 제외해도 그 기간 안의 terminal wrapper report는 게시합니다.

TTL 이후는 current status를 `unknown`/`stale`로 표시하며 저장된 마지막 완료 상태는 artifact 조회로 구분합니다. 명시 `--metrics-dir`만 사용하는 legacy 모드는 기존처럼 age 제한이 없으므로 현재 상태가 필요하면 `--max-age-seconds`를 지정합니다.

Collector는 직계 run directory만 발견하고 최근 artifact 최대 1,024개, artifact당 최대 64 KiB를 읽습니다. `telemetry_application_workload_reads_total`, `telemetry_application_workload_rejections_total`, `telemetry_application_workload_limit_drops_total`은 반복 poll을 포함한 누적 작업 횟수이며 누락·limit를 확인하는 signal입니다.

## Measured Native Phase Hooks

`measured_phase(events, stage, ...)`는 기존 veRL stage vocabulary를 `EventRecorder.span()`에 연결하는 explicit helper입니다. 호출자가 실제 call point에 연결해야 하며 wrapper나 file logger bridge가 모든 phase를 자동 계측하지 않습니다.

공개 veRL trainer의 [실제 generation call point](https://github.com/verl-project/verl/blob/8718ca30a3f002f93b7c4fd99b9b2506718681bc/verl/trainer/ppo/ray_trainer.py#L1510)에 다음과 같이 연결할 수 있습니다. 아래 fragment는 해당 trainer 메서드 안에 삽입하는 integration 예이며 source checkout 변경은 사용자가 관리합니다.

```python
from xlayer_telemetry.adapters.verl import measured_phase
from xlayer_telemetry.events import EventRecorder

events = EventRecorder.from_env(producer="verl_native", role="trainer",
                                 worker_id="driver")
with measured_phase(events, "gen", step=self.global_steps,
                    attributes={"boundary_scope": "driver_rpc"}):
    combined_gen_output = self.async_rollout_manager.generate_sequences(combined_gen_batch)
```

Helper는 실제 block entry/exit에서 node clock과 monotonic duration을 기록하며 `verl_stage`·`measurement_source="native_sdk"`를 event attribute에 보존합니다. Async 함수에서는 같은 `with` 안에서 `await`하여 실제 awaited call을 감싸며 workload 반환값과 exception을 유지합니다.

이 driver span에는 해당 호출의 wait가 포함될 수 있으므로 GPU kernel 시간이나 모든 rollout replica의 phase로 읽지 않습니다. 개별 remote worker를 계측하면 그 worker의 node·role·worker·rank·GPU context를 사용하고 `trace_id`·`parent_span_id`를 실제 관계에 따라 전달합니다.

기존 [clock calibration](time-alignment.md)을 사용하면 raw timestamp·reference·uncertainty를 유지하며 `boundary_accuracy="calibrated"`를 기록합니다. Calibration이 없으면 `exact`은 node-clock 경계이고, 설정된 calibration이 실패하면 `unknown`, clock jump가 감지되면 `clock_discontinuity`입니다.

`timing_s/gen` 같은 reported duration으로 fake exact interval을 만들지 않습니다. Low-level profiling·CUDA synchronization·Nsight/PyTorch capture는 이 helper가 실행하지 않으며 기존 profiling 도구로 이어집니다.

## Validation and Remaining Limits

회귀 검증은 native logger scalar fixture, invalid 값·0 구분, explicit policy field, sync/awaited SDK call, calibrated/unknown clock, 실제 CPU wrapper command → health artifact → textfile projection을 포함합니다. 전체 CPU suite, config schema, 문서 link/build 및 shell syntax를 함께 확인하며 실제 GPU·VERL distributed workload·vLLM engine rendering 검증은 별도로 수행해야 합니다.

실제 배포에서 MFU·policy key가 나오지 않거나 phase hook을 연결하지 않았다면 해당 기능은 계속 N/A입니다. Native service의 shared ownership과 async workload의 전체 completion을 이 지원 범위에서 확정하지 않습니다.

다음 작업은 [VERL 연결](verl-quickstart.md), [source 등록](native-sources.md), [log/event 조회](logs-events.md)에서 필요한 producer를 활성화하고 원본 artifact와 UI query를 대조하는 것입니다.
