# Metric collection coverage audit, 2026-10-03

## Scope and verdict

Baseline: `c43b030dec0d3d9632bf80973513ae5ff61bf304` (`main` at the start of this audit). This report traces implementation, launch/config paths, optional integrations, actual names/units/scope, and remaining bottleneck evidence gaps. Baseline citations are pinned to that commit so concurrent improvements do not move the evidence. The implementation-change section describes the working-tree improvements separately.

The baseline has useful end-to-end collection paths, but its 133-entry canonical contract is substantially broader than its built-in producers. Exactly 37 contract names have an implemented production adapter/collector at baseline, all conditional on connection and available input. The other 96 canonical names have no exact-name production emitter in this checkout. Some are covered by a differently named native family, derived PromQL, or optional event evidence; others require application instrumentation. This is a source inventory, not a claim that 37 live metrics were observed here. No real training workload, GPU, 3FS, Ray, vLLM, DCGM or SMART device endpoint was available in this test environment. Prometheus runtime checks use clearly labeled synthetic fixtures; the read-only Node Exporter smoke below uses only the restricted cloud execution environment.

The contract explicitly says it is a vocabulary rather than a scrape configuration or registry of automatically emitted metrics. Synthetic demo generators, dashboard expressions, documentation examples, manifest fields, and old validation records are not evidence of current automatic production collection.

## End-to-end collection paths

| Path | What is implemented at baseline | Activation and important limits | Evidence |
| --- | --- | --- | --- |
| veRL file logger | Completed logger record → adapter → atomic per-worker latest JSON → application textfile → Node Exporter → Prometheus. Deduplicated step JSONL is separate. | Wrapper starts the bridge at 0.2 s polling. Only supported finite scalar keys are exported; unknown keys stay in the original logger. Stage summaries are not exact live boundaries. | [scripts/run_verl_with_telemetry.sh:346-356](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/scripts/run_verl_with_telemetry.sh#L346-L356); [xlayer_telemetry/adapters/verl.py:18-120](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/adapters/verl.py#L18-L120); [xlayer_telemetry/metrics/textfile.py:140-230](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/metrics/textfile.py#L140-L230); [xlayer_telemetry/step_history.py:74-127](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/step_history.py#L74-L127) |
| SDK | Arbitrary validated gauge/counter values, run/producer/role/worker/node identity, optional rank/local-rank/GPU. Latest JSON replaces the previous snapshot. | Caller must invoke `emit`; no counter accumulation, histogram API, automatic framework probes, or complete step retention between scrapes. Counter semantics belong to caller. | [xlayer_telemetry/metrics/emitter.py:138-225](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/metrics/emitter.py#L138-L225) |
| Hugging Face / TRL callback | Loss, loss-log interval duration, input-token throughput, global step. | Must install callback; world process zero and loss logs only. `training_step_time_seconds` is the elapsed loss-log interval, potentially multiple steps, not guaranteed single-step latency. | [xlayer_telemetry/adapters/hf_trainer.py:27-58](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/adapters/hf_trainer.py#L27-L58) |
| Events and sandbox lifecycle | Exact monotonic duration, wall-clock boundaries, trace/span/parent IDs, exception status; sandbox queue/acquire/prepare/exec/reset/release plus execution result and optional cgroup deltas. | Explicit instrumentation required. Events remain JSONL/Loki evidence; no automatic conversion to Prometheus histograms, errors, retries, active/queued gauges. Writes occur on span completion, so a hung span has no completion record. | [xlayer_telemetry/events.py:159-267](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/events.py#L159-L267); [xlayer_telemetry/sandbox.py:29-128](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/sandbox.py#L29-L128) |
| GPU sampler | nvidia-smi → JSONL and `gpu.prom`; utilization, watts, Celsius, SM MHz, used/total bytes, process bytes, freshness. | Node launcher defaults GPU sampling on. Unsupported nvidia-smi fields are omitted. Device-wide/process observation, not attribution to one training run. | [xlayer_telemetry/collectors/gpu_sampler.py:23-112](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/gpu_sampler.py#L23-L112); [scripts/run_telemetry.sh:294-336](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/scripts/run_telemetry.sh#L294-L336) |
| Resource sampler | `/proc/meminfo` and target mount's `/sys/dev/block/major:minor/stat` → JSONL. | Standalone opt-in module, default 0.2 s; not launched by the production node/wrapper scripts and no Prometheus textfile output. Fails if target cannot be resolved to a local block stat, e.g. some overlay/network mounts. | [xlayer_telemetry/collectors/resource_sampler.py:23-96](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/resource_sampler.py#L23-L96) |
| Host / network / disk | Node Exporter default collectors plus XLayer textfiles. No allowlist of dashboard-used families. | Node launcher starts v1.9.1 at port 19100. Available families depend on OS/kernel/device/exporter collectors. CPU, meminfo, vmstat, pressure, diskstats, filesystem, netdev/netstat, time/timex, Infiniband are host/device observations. | [scripts/run_telemetry.sh:294-336](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/scripts/run_telemetry.sh#L294-L336); [scripts/run_telemetry.sh:405-463](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/scripts/run_telemetry.sh#L405-L463) |
| DCGM / cAdvisor | Generic native endpoint registration can scrape installed external exporters. | Neither exporter is launched by the production node script; no canonical or `telemetry_gpu_*` conversion. GPU panels expecting XLayer sampler names will not automatically use raw DCGM families. | [xlayer_telemetry/source_discovery.py:26-87](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/source_discovery.py#L26-L87); `examples/dashboards/README.md:38-40` |
| vLLM / Ray | Generic raw endpoint passthrough with component/source and supplied stable labels. | Operator enables native metrics and registers reachable endpoints. Registration is not readiness. No upstream SDK instrumentation or canonical remapping is installed by XLayer. Shared service identity must not be mislabeled as run ownership. | [xlayer_telemetry/source_discovery.py:26-87](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/source_discovery.py#L26-L87); [scripts/run_telemetry.sh:405-463](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/scripts/run_telemetry.sh#L405-L463) |
| 3FS native exporter | Any explicitly supplied Prometheus endpoint can be registered. | Example host:port is a placeholder, not installation of a 3FS exporter. Native fields are deployment-specific. | [xlayer_telemetry/source_discovery.py:26-87](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/source_discovery.py#L26-L87); `examples/verl/native-sources.json:24-33` |
| 3FS ClickHouse | Bounded HTTP query of `distributions`; subsystem CLI also reads `counters`. | Existing 3FS ingestion and credentials required. Read-only aggregate evidence, not export of canonical storage metrics to Prometheus. Distribution units are producer-defined. `max_observed_p99` is maximum reported subgroup/time p99, not pooled global p99. Counters table mixes reset-on-collection recorders and gauges; raw min/max/last/sample count/freshness is retained instead of fabricated rate. | [xlayer_telemetry/analysis/diagnostics.py:74-194](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/analysis/diagnostics.py#L74-L194); [xlayer_telemetry/subsystems.py:82-123](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/subsystems.py#L82-L123) |
| Sandbox cgroup | Stable cgroup v2 subtree → 19 families plus freshness, two-second default. | Separate explicit sampler invocation; stable worker labels only. CPU quota stats apply to selected cgroup's own bandwidth quota; hierarchical memory/IO stats include children. Missing controllers omitted; first/reset PSI interval omitted. | [xlayer_telemetry/collectors/sandbox_sampler.py:81-195](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/sandbox_sampler.py#L81-L195) |
| SMART | smartctl_exporter → optional 60 s Prometheus job. | `ENABLE_SSD_HEALTH=1` on node or storage role, smartctl/device access required. Optional passwordless sudo preflight; health/wear/temperature, not per-operation storage latency. | [scripts/run_telemetry.sh:44-96](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/scripts/run_telemetry.sh#L44-L96); [scripts/run_telemetry.sh:405-463](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/scripts/run_telemetry.sh#L405-L463) |
| Topology | Supplied compute/storage JSON → component/edge info gauges. | `TOPOLOGY_DIR`, ten-second default. Describes supplied topology, not automatic network/storage discovery or bandwidth measurement. | [xlayer_telemetry/collectors/topology_textfile.py:14-58](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/topology_textfile.py#L14-L58); [scripts/run_telemetry.sh:294-336](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/scripts/run_telemetry.sh#L294-L336) |

## Complete emitted families and raw evidence at baseline

### veRL bridge

Eight direct logger translations:

| Logger key | Exported family | Semantics |
| --- | --- | --- |
| `perf/throughput` | `training_tokens_per_second_per_gpu` | Logger-reported tokens/s/GPU |
| `perf/total_num_tokens` | `training_tokens_processed` | Current completed-step token count, gauge; not a lifetime counter |
| `perf/time_per_step` | `training_step_time_seconds{phase="rl_step"}` | Completed step seconds; `timing_s/step` fallback |
| `critic/score/mean` | `reward_score_mean` | Step mean |
| `critic/rewards/mean` | `reward_mean` | Step mean, may legitimately be negative |
| `response_length/mean` | `rollout_output_tokens_mean` | Mean output length, not output throughput |
| `num_turns/mean` | `agent_turns_mean` | Mean per logger population, not episode histogram |
| `tool_call_counts/mean` | `agent_tool_calls_mean` | Mean calls, not failure/timeout counts |

`timing_s/<stage>` exports `rl_stage_duration_seconds`; `timing_per_token_ms/<stage>` exports `rl_stage_time_per_token_seconds` after division by 1000. Stage allowlist: step, gen, reward, old_log_prob, ref, values, adv, update_critic, update_actor, update_weights, save_checkpoint, testing, dump_rollout_generations, start_profile, stop_profile. Phase plus original `verl_stage` labels keep aliases distinguishable. These are completed gauges, not latency histogram distributions or start/end tracing. All supported stage definitions: [xlayer_telemetry/adapters/verl.py:18-120](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/adapters/verl.py#L18-L120).

The textfile layer also emits `training_step`, `training_sample_timestamp_seconds`, and when a GPU assignment exists `training_gpu_allocation`. Collector internals emit `telemetry_application_snapshot_reads_total`, `telemetry_application_snapshot_cache_hits_total`, `telemetry_application_snapshot_rejections_total`, `telemetry_application_sample_rejections_total`. Rejections count repeated scan operations, not unique dropped records. Source: [xlayer_telemetry/metrics/textfile.py:140-230](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/metrics/textfile.py#L140-L230); `xlayer_telemetry/metrics/textfile.py:264-269`.

### GPU and standalone resource sampler

GPU textfile families: `telemetry_gpu_sample_timestamp_seconds`, `telemetry_gpu_utilization_percent`, `telemetry_gpu_power_watts`, `telemetry_gpu_temperature_celsius`, `telemetry_gpu_sm_clock_mhz`, `telemetry_gpu_memory_used_bytes`, `telemetry_gpu_memory_total_bytes`, `telemetry_gpu_process_memory_bytes`. The node launcher separately emits `telemetry_gpu_collection_enabled`. JSONL additionally records compute PID/name/UUID, unsupported-field names, process-query errors and MemTotal/MemAvailable/MemFree/Buffers/Cached/SwapTotal/SwapFree bytes. Process rows are not total unified memory. No baseline ECC/Xid, power/thermal throttling reasons, PCIe/NVLink congestion, memory-bandwidth utilization, allocator reserved/allocated/fragmentation, or GPU OOM event collector. Evidence: [xlayer_telemetry/collectors/gpu_sampler.py:23-112](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/gpu_sampler.py#L23-L112); [scripts/run_telemetry.sh:294-336](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/scripts/run_telemetry.sh#L294-L336).

Resource JSONL fields: monotonic seconds, wall time ns, backing major:minor; memtotal/memavailable/swaptotal/swapfree bytes; read/write operations, read/write bytes, read/write time ms, IO in-flight, busy ms, weighted busy ms. Rates/latency/utilization must be derived from repeated cumulative observations with correct units/reset behavior; current counters are not rates. Evidence: [xlayer_telemetry/collectors/resource_sampler.py:23-96](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/resource_sampler.py#L23-L96).

### Native vLLM / Ray and infrastructure

Configured endpoints are scraped as their native exposition, not only the panel subset. Baseline panels explicitly consume vLLM running/waiting requests, prompt/generation token counters, KV cache usage, preemption counter, e2e/queue/TTFT histogram buckets, and `vllm:kv_offload_total_bytes_total`; exact availability, labels, units and histogram shape must be checked against installed version. Ray panels consume `ray_tasks`, `ray_actors`, `ray_resources`, `ray_object_store_memory`, and `ray_memory_manager_worker_eviction_total`. A matching panel expression is not a producer. Source: `examples/dashboards/agent-rl-stages.json`; [xlayer_telemetry/source_discovery.py:26-87](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/source_discovery.py#L26-L87).

Host families include native node CPU seconds, memory bytes/swap counters, CPU/memory/IO pressure stall counters where enabled, per-device IO completed/bytes/timing/in-flight, filesystem size/availability, interface bytes/errors/drops, TCP retransmissions, time/timex, and optional Infiniband counters. Baseline dashboard focus is narrower than scrape collection; network retry/congestion and PSI already available from the exporter should be queried before adding a duplicative polling collector. SMART panels consume health/status/critical warning/available spare/media errors/percentage-used/bytes-written/temperature families under `smartctl_device_*`. Evidence: [scripts/run_telemetry.sh:294-336](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/scripts/run_telemetry.sh#L294-L336); `examples/dashboards/data-storage.json`; `examples/dashboards/compute-communication.json`.

### Sandbox and topology

Sandbox families are enumerated in the canonical ledger below. Pool `sandbox_active`, `sandbox_queued`, create/reset duration, failures are contracts only; the cgroup sampler cannot infer runtime pool state. Lifecycle events can measure real queue/acquire/prepare/exec/reset/release if callers wrap those operations; runtime outcome is separate from reward/tool correctness. Per-sandbox/trajectory/trace/request IDs belong in events and are not cgroup Prometheus labels. Resource deltas from overlapping spans must not be summed as distinct ownership. Evidence: [xlayer_telemetry/collectors/sandbox_sampler.py:81-195](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/sandbox_sampler.py#L81-L195); [xlayer_telemetry/sandbox.py:29-128](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/sandbox.py#L29-L128).

Topology emits only `telemetry_topology_component_info{kind,component,role}` and `telemetry_topology_edge_info{kind,source,destination,relation}`, value 1. Source: [xlayer_telemetry/collectors/topology_textfile.py:14-58](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/topology_textfile.py#L14-L58).

## Bottleneck coverage and remaining source gaps

| Workload area | Existing usable evidence | Important missing or conditional evidence | Priority and action |
| --- | --- | --- | --- |
| Rollout/generation latency | Completed `gen` time; optional native e2e, TTFT, queue histograms; explicit exact spans | Time per output token/prefill/decode distributions, tail lengths, per-phase stall reason, queue age, retries/timeouts/errors require native source/version or instrumentation; bridge means cannot reconstruct percentiles | P1: expose upstream-native families with version-grounded names; add bounded logger keys only when units and semantics verified |
| Rollout throughput/saturation | Per-step training tokens/s/GPU, native generation/prompt counters and running/waiting, KV use/preemptions | Request completed/error counts and prompt/output histograms vary by engine; training throughput is not serving throughput. Concurrency, oldest waiter, admission limits are not inferred | P1: use reset-aware native rate, stable engine identity and explicit freshness; preserve missing vs zero |
| Training step and distributed stalls | Completed stage durations, GPU util/memory, host CPU/PSI, node/RDMA throughput, optional HF loss logs | Data wait vs compute vs communication, collective retries, rank-local stragglers/overlap/bubbles, MFU, allocator peak/fragmentation not automatic | P1: rank-local SDK/timer or selected profiler spans; export semantic bounded summaries, not arbitrary logger-to-label passthrough |
| Checkpoint save/restore | `save_checkpoint` completed stage; node device throughput/busy; explicit event SDK possible | Actual bytes, read/write/metadata/fsync latency, restore duration, retry/error count, bandwidth and durability stage not automatic; device throughput is shared IO | P1: wrap framework checkpoint operation with bytes/outcome/retry semantics and exact span; do not label host bytes as checkpoint ownership |
| KV offload | Native transferred bytes counter visible if engine/connector emits it; 3FS distributions/raw counters; device/network evidence | Connector-level load/store/lookup/prefetch latency, hit/miss, cache occupancy, pending transfers, evictions, failures/retries not synthesized. GPU↔CPU transferred bytes do not prove filesystem/3FS transfer | P1: preserve tier/connector/direction identity; add connector-specific probes only with upstream evidence; require storage-specific evidence for attribution |
| Sandbox/tool queue and execution | Explicit lifecycle spans/outcomes, cgroup CPU quota/PSI, memory high/max/OOM, block IO | Pool capacity/active/queued age, creation/reset histograms, runtime retry policy, live stuck-span age, IO full PSI and memory quota/swap headroom not at baseline | P1: runtime instrumentation + low-cardinality pool metrics; P2: additional kernel-source cgroup pressure/headroom fields |
| Host/GPU memory pressure | Device used/total bytes, host available/swap/PSI, cgroup current/peak/events | Device allocation headroom is not allocator fragmentation; GPU device counters not per-worker ownership; cgroup memory.max/high/swap and denominator missing | P1: inspect pressure/error evidence before assigning causes; conditional framework allocator instrumentation |
| Network/storage congestion | Node bytes/errors/drops/retransmissions, RDMA when present, disk IO busy/queue timing, optional 3FS latency | Link-capacity normalization, ECN/PFC/IB wait/retry details, per-run flow attribution, filesystem latency/error/retry and tier-specific bottleneck attribution depend on collector/upstream | P1: use existing raw node fields; optional specialized exporter only when needed. Busy alone does not prove saturation on parallel devices |
| Collection health | Freshness, application rejection counters, SDK async IO status, target up, wrapper health | Baseline global/native scrape limits absent; collector bounds do not cap every input; GPU PID labels on by default; raw SDK event records can grow | P0: budgets, cardinality opt-in, finite validation and loss/freshness reporting before higher-volume instrumentation |

## Operational bounds and overhead audit

- Baseline generated Prometheus config: global scrape every 2 s (Compose every 5 s); native shares global cadence; file discovery refresh 30 s; SMART scrape 60 s. There are no explicit scrape timeout/sample/label/value-length/body limits or per-source budgets in baseline generated config. Endpoint discovery file is generated once at launch; Prometheus refreshing generated output does not automatically reread/edit the original source config. Evidence: [scripts/run_telemetry.sh:405-463](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/scripts/run_telemetry.sh#L405-L463); `examples/dashboards/prometheus.yml:1-27`.
- GPU sampling invokes up to two nvidia-smi subprocesses per loop (10 s timeout each), flushes JSONL each sample, and sleeps 1 s after collection; effective period is collection latency + sleep, not a strict 1 Hz deadline. GPU errors can terminate sampler; last successful timestamp is required to identify stale textfiles. Baseline process PID labels can churn across short-lived jobs. Evidence: [xlayer_telemetry/collectors/gpu_sampler.py:23-112](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/gpu_sampler.py#L23-L112).
- SDK is synchronous by default. Async mode bounds queued records to 256/4 MiB, coalesces latest snapshots, drops when full, and has one-second default flush. Serialization occurs before submission and is not bounded by queued-byte limit. Queue status is available via `io_status`, not automatically exported into Prometheus. No promise of zero overhead or lossless telemetry. Evidence: [xlayer_telemetry/metrics/emitter.py:138-225](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/metrics/emitter.py#L138-L225); [xlayer_telemetry/io_writer.py:14-105](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/io_writer.py#L14-L105).
- Application collector defaults to 2 s. Cache has 1,024-file/16 MiB retained-source limits, but baseline `json.load`, number of directory entries, samples per snapshot, current exposition size and arbitrary label values have no matching hard ingest cap. Run-root discovery is one directory level, filters terminal runs and uses default 300 s max age; explicit metrics directory has no default age limit. Evidence: [xlayer_telemetry/metrics/textfile.py:23-137](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/metrics/textfile.py#L23-L137); `xlayer_telemetry/metrics/textfile.py:234-271`.
- Stable SDK identity fields are 64-character bounded; arbitrary metric label syntax is checked and identity overrides prohibited, but high-cardinality request IDs and unlimited label values are not runtime-blocked. Contract recommendations are guidance only. Native labels likewise have no baseline value-length/count limits. Sandbox sampler enforces five stable label keys and no transient IDs. Topology has deduplication but no entry-count limit. Evidence: [xlayer_telemetry/metrics/emitter.py:138-225](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/metrics/emitter.py#L138-L225); [xlayer_telemetry/source_discovery.py:26-87](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/source_discovery.py#L26-L87); [xlayer_telemetry/collectors/sandbox_sampler.py:81-195](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/sandbox_sampler.py#L81-L195); [xlayer_telemetry/collectors/topology_textfile.py:14-58](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/topology_textfile.py#L14-L58).
- 3FS HTTP response is capped at 8 MiB. Raw counters enforce 1,000 entity groups via SQL LIMIT 1001; distributions have byte cap but no explicit group cap. Filters are allowlisted and SQL-escaped. CLI query window max is one day with settle delay; counters cannot reuse a method filter unavailable in that table. Analysis backend budget defaults 30 s plus isolated worker deadline. Baseline Prometheus range JSON read has request timeout but no response-size limit in the shared client. Evidence: [xlayer_telemetry/analysis/diagnostics.py:74-194](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/analysis/diagnostics.py#L74-L194); [xlayer_telemetry/subsystems.py:82-123](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/subsystems.py#L82-L123); [xlayer_telemetry/analysis/query_budget.py:19-63](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/analysis/query_budget.py#L19-L63); [xlayer_telemetry/prometheus.py:83-110](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/prometheus.py#L83-L110).
- Per-worker step history keeps an in-memory set of all seen record IDs and rereads the history on restart; JSONL and GPU log files have no module-internal rotation/retention. Prometheus retention defaults one day; filesystem/log retention remains an operator responsibility. Evidence: `xlayer_telemetry/step_history.py:60-72,129-136`; [xlayer_telemetry/collectors/gpu_sampler.py:23-112](https://github.com/daegyu94/xlayer-telemetry/blob/c43b030dec0d3d9632bf80973513ae5ff61bf304/xlayer_telemetry/collectors/gpu_sampler.py#L23-L112); `scripts/run_telemetry.sh:353-363`.

## Canonical ledger: all 133 baseline contract entries

Legend: **E** = exact canonical family implemented by a production collector/adapter, only when source/input is present; **N** = native equivalent/related evidence can be collected under different names, no automatic canonical emitter; **I** = requires explicit integration/derivation/profiler/runtime instrumentation and has no exact-name automatic producer. N is conditional, not a guarantee of a particular external exporter version. These classifications exclude synthetic/demo code. Every contract entry is listed once; exact producer evidence keys are given below.

### training

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `training_tokens_per_second` | E | HF callback |
| `training_samples_per_second` | I | No exact-name producer; framework adapter |
| `training_step_time_seconds` | E | veRL bridge |
| `training_loss` | E | HF callback |
| `training_step` | E | application textfile |
| `training_timer_seconds` | I | No exact-name producer; Megatron rank-local timer |
| `training_sample_timestamp_seconds` | E | application textfile |
| `training_model_flops_utilization_ratio` | I | No exact-name producer; framework adapter and model manifest |
| `training_data_wait_seconds` | I | No exact-name producer; framework timer |
| `training_compute_seconds` | I | No exact-name producer; framework timer |
| `training_communication_seconds` | I | No exact-name producer; framework timer or selected trace |
| `training_communication_overlap_ratio` | I | No exact-name producer; selected trace |
| `training_pipeline_bubble_ratio` | I | No exact-name producer; framework adapter or selected trace |
| `training_rank_straggler_ratio` | I | No exact-name producer; Megatron StragglerDetector or summary |
| `training_dataset_load_time_seconds` | I | No exact-name producer; framework phase marker |
| `training_model_load_time_seconds` | I | No exact-name producer; framework phase marker |
| `training_evaluation_time_seconds` | I | No exact-name producer; framework phase marker |
| `training_tokens_per_second_per_gpu` | E | veRL bridge |
| `training_tokens_processed` | E | veRL bridge |

### rollout

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `rollout_requests_queued` | N | vLLM waiting requests |
| `rollout_queue_age_seconds` | I | No exact-name producer; verl adapter |
| `rollout_requests_per_second` | N | native completion counter rate when exposed |
| `rollout_output_tokens_per_second` | N | vLLM generation counter rate |
| `rollout_time_to_first_token_seconds` | N | vLLM TTFT histogram |
| `rollout_time_per_output_token_seconds` | N | vLLM token/decode histogram when exposed |
| `rollout_kv_cache_utilization_ratio` | N | vLLM KV usage gauge |
| `rollout_preemptions_total` | N | vLLM preemption counter |
| `rollout_output_tokens_mean` | E | veRL bridge |
| `rollout_replica_ready_lag_seconds` | I | No exact-name producer; weight apply completion event |

### agent

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `agent_turns` | I | No exact-name producer; verl agent adapter |
| `agent_tool_call_duration_seconds` | I | explicit tool span, no automatic metric histogram |
| `agent_tool_call_errors_total` | I | explicit span error/outcome, no counter aggregation |
| `agent_tool_call_timeouts_total` | I | explicit outcome instrumentation, no counter aggregation |
| `agent_environment_wait_ratio` | I | No exact-name producer; OpenTelemetry summary |
| `agent_turns_mean` | E | veRL bridge |
| `agent_tool_calls_mean` | E | veRL bridge |

### sandbox

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `sandbox_active` | I | No exact-name producer; optional sandbox runtime adapter |
| `sandbox_queued` | I | No exact-name producer; optional sandbox runtime adapter |
| `sandbox_create_duration_seconds` | I | explicit prepare/acquire lifecycle spans, no aggregate metric |
| `sandbox_reset_duration_seconds` | I | explicit reset span, no aggregate metric |
| `sandbox_failures_total` | I | explicit exec result events, no aggregate counter |
| `sandbox_io_read_bytes_total` | E | optional cgroup sampler |
| `sandbox_sample_timestamp_seconds` | E | optional cgroup sampler |
| `sandbox_io_write_bytes_total` | E | optional cgroup sampler |
| `sandbox_io_read_ops_total` | E | optional cgroup sampler |
| `sandbox_io_write_ops_total` | E | optional cgroup sampler |
| `sandbox_io_pressure_ratio` | E | optional cgroup sampler |
| `sandbox_cpu_usage_seconds_total` | E | optional cgroup sampler |
| `sandbox_cpu_throttled_seconds_total` | E | optional cgroup sampler |
| `sandbox_cpu_throttled_periods_total` | E | optional cgroup sampler |
| `sandbox_cpu_periods_total` | E | optional cgroup sampler |
| `sandbox_cpu_pressure_ratio` | E | optional cgroup sampler |
| `sandbox_memory_bytes` | E | optional cgroup sampler |
| `sandbox_memory_peak_bytes` | E | optional cgroup sampler |
| `sandbox_memory_pressure_ratio` | E | optional cgroup sampler |
| `sandbox_memory_full_pressure_ratio` | E | optional cgroup sampler |
| `sandbox_memory_high_events_total` | E | optional cgroup sampler |
| `sandbox_memory_max_events_total` | E | optional cgroup sampler |
| `sandbox_oom_total` | E | optional cgroup sampler |
| `sandbox_oom_kill_total` | E | optional cgroup sampler |

### orchestration

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `orchestration_tasks_pending` | N | Ray task-state gauges |
| `orchestration_scheduling_delay_seconds` | N | Ray scheduling histograms when exposed |
| `orchestration_actor_restarts_total` | N | Ray restart state/counters when exposed |
| `orchestration_object_store_used_bytes` | N | Ray object store memory |
| `orchestration_object_spill_bytes_total` | N | Ray spill counters when exposed |
| `rl_stage_duration_seconds` | E | veRL bridge |
| `rl_stage_time_per_token_seconds` | E | veRL bridge |
| `policy_version` | I | No exact-name producer; VERL checkpoint manager instrumentation |
| `policy_version_lag` | I | queried by diagnosis; caller must emit actual version lag |

### gpu

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `gpu_utilization_percent` | N | telemetry_gpu_utilization_percent / optional DCGM |
| `gpu_memory_used_bytes` | N | telemetry_gpu_memory_used_bytes |
| `gpu_memory_total_bytes` | N | telemetry_gpu_memory_total_bytes |
| `gpu_power_watts` | N | telemetry_gpu_power_watts / optional DCGM |
| `gpu_temperature_celsius` | N | telemetry_gpu_temperature_celsius / optional DCGM |
| `gpu_clock_hertz` | N | telemetry_gpu_sm_clock_mhz (unit conversion required) |
| `gpu_errors_total` | N | optional DCGM error families; no synthesized counter |
| `gpu_memory_peak_bytes` | I | No exact-name producer; DCGM Exporter or framework allocator |
| `gpu_utilization_imbalance_ratio` | I | No exact-name producer; summary derived from DCGM Exporter |
| `gpu_memory_imbalance_ratio` | I | No exact-name producer; summary derived from DCGM Exporter |

### host

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `host_cpu_utilization_percent` | N | node_cpu_seconds_total rate |
| `host_memory_used_bytes` | N | node_memory_* bytes difference |
| `host_memory_pressure_ratio` | N | node_pressure_memory_* stall counter rate |
| `host_memory_peak_bytes` | I | No exact-name producer; Node Exporter |
| `host_pinned_memory_used_bytes` | I | No exact-name producer; framework adapter or selected trace |
| `telemetry_application_snapshot_reads_total` | E | application collector self-metric |
| `telemetry_application_snapshot_cache_hits_total` | E | application collector self-metric |
| `telemetry_application_snapshot_rejections_total` | E | application collector self-metric |
| `telemetry_application_sample_rejections_total` | E | application collector self-metric |

### network

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `network_receive_bytes_per_second` | N | node_network_receive_bytes_total rate |
| `network_transmit_bytes_per_second` | N | node_network_transmit_bytes_total rate |
| `network_errors_total` | N | node_network_*_errs_total |
| `rdma_receive_bytes_per_second` | N | node_infiniband_*_received_bytes_total rate |
| `rdma_transmit_bytes_per_second` | N | node_infiniband_*_transmitted_bytes_total rate |
| `rdma_errors_total` | N | optional node_infiniband error families |
| `collective_bandwidth_bytes_per_second` | I | No exact-name producer; NCCL Tests |
| `collective_communication_time_seconds` | I | No exact-name producer; framework timer or selected trace |
| `network_communication_bytes_total` | I | No exact-name producer; framework timer or selected trace |
| `network_synchronization_wait_seconds` | I | No exact-name producer; framework timer or selected trace |
| `collective_baseline_utilization_ratio` | I | No exact-name producer; workload summary and NCCL Tests baseline |

### storage

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `storage_read_bytes_per_second` | N | node_disk_read_bytes_total rate |
| `storage_write_bytes_per_second` | N | node_disk_written_bytes_total rate |
| `storage_iops` | N | node_disk_*_completed_total rates |
| `storage_latency_seconds` | I | No exact-name producer; selected I/O trace |
| `storage_read_bytes_total` | N | node_disk_read_bytes_total |
| `storage_write_bytes_total` | N | node_disk_written_bytes_total |
| `storage_metadata_operations_per_second` | I | No exact-name producer; filesystem exporter or selected I/O trace |
| `storage_queue_depth` | N | node_disk_io_now or weighted IO-time rate |
| `storage_device_utilization_percent` | N | node_disk_io_time_seconds_total rate × 100 |
| `storage_filesystem_capacity_used_ratio` | N | node_filesystem_* size/availability |
| `storage_remote_read_bytes_per_second` | N | optional mountstats/client family rate |
| `storage_remote_write_bytes_per_second` | N | optional mountstats/client family rate |
| `storage_cache_hit_ratio` | I | No exact-name producer; storage client metrics |

### checkpoint

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `checkpoint_size_bytes` | I | No exact-name producer; framework adapter |
| `checkpoint_save_time_seconds` | I | veRL checkpoint_save stage is related evidence under rl_stage_duration_seconds |
| `checkpoint_load_time_seconds` | I | No exact-name producer; framework timer |
| `checkpoint_throughput_bytes_per_second` | I | No exact-name producer; framework adapter |
| `checkpoint_read_throughput_bytes_per_second` | I | No exact-name producer; framework adapter |
| `checkpoint_write_throughput_bytes_per_second` | I | No exact-name producer; framework adapter |
| `checkpoint_frequency_steps` | I | manifest only, no metric export |

### container

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `container_cpu_utilization_percent` | N | optional external cAdvisor; not launched by XLayer |
| `container_memory_used_bytes` | N | optional external cAdvisor; not launched by XLayer |
| `container_network_receive_bytes_per_second` | N | optional external cAdvisor; not launched by XLayer |
| `container_network_transmit_bytes_per_second` | N | optional external cAdvisor; not launched by XLayer |
| `container_filesystem_read_bytes_per_second` | N | optional external cAdvisor; not launched by XLayer |
| `container_filesystem_write_bytes_per_second` | N | optional external cAdvisor; not launched by XLayer |

### data_movement

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `data_movement_bytes_total` | I | No exact-name producer; framework phase marker or selected trace |
| `data_movement_duration_seconds` | I | No exact-name producer; framework phase marker or selected trace |
| `data_movement_effective_bandwidth_bytes_per_second` | I | No exact-name producer; derived from bytes and duration |
| `host_to_gpu_copy_time_seconds` | I | No exact-name producer; framework timer or selected trace |
| `host_to_gpu_copy_bytes_total` | I | No exact-name producer; framework adapter or selected trace |
| `weight_sync_bytes_total` | I | No exact-name producer; weight transfer instrumentation |

### reward

| Canonical name | Status | Producer or missing implementation |
| --- | --- | --- |
| `reward_score_mean` | E | veRL bridge |
| `reward_mean` | E | veRL bridge |

Ledger totals: {'E': 37, 'I': 52, 'N': 44}; total 133. E counts are production source support, not default-on or live-observed coverage.

## Implementation changes and validation

The following changes are implemented in this audit working tree. Baseline findings above remain pinned and unchanged; extra queries do not install exporters or manufacture absent source data.

### Fixed: invalid negative veRL timers

`VerlMetricsAdapter.translate` previously accepted negative finite seconds/milliseconds and a negative primary `perf/time_per_step`, while `StepHistoryWriter` rejected the same time values. That could publish a negative latency and prevent a valid fallback while history reported a different nonnegative duration. The change drops only invalid negative time observations, uses the valid `timing_s/step` fallback when primary time is negative, preserves zero and signed rewards, and updates `--describe-metrics`. New tests reproduced five failures before the fix; 16 focused adapter/ingestion tests passed after the fix.

### Implemented: existing native evidence becomes diagnosable

`xlayer_telemetry/analysis/metric_queries.py` adds **nine opt-in profiles / 50 bounded signal queries**: host (6), disk (7), filesystem (4), network (7), RDMA (3), vLLM (6), KV offload (5), Ray (5), DCGM (7). Default diagnostic query count is unchanged. Profiles cover PSI, page faults and runnable queue; read/write/flush mean IO latency and queue depth; capacity/inodes/readonly/errors; NIC errors/drops and TCP retransmits; supported RDMA wait/error/discard; TTFT/TPOT/queue/e2e p95 and token throughput; offload bytes/allocation failures/lookup delay; Ray object spill/mmap/pending transfers/evictions; DCGM compute/memory/PCIe/error evidence. See [profile semantics and configuration](../diagnosis.md#optional-exporter-metric-profiles).

Queries retain units, statistic, source expression, scope and entity labels. Current/baseline comparisons require exact non-metric-name label equality rather than comparing maxima from different devices. Idle denominators and unsupported finite DCGM sentinels are excluded. XID stays a last-error-code gauge; DCGM profiling PCIe stays bytes/s; Ray spilled bytes stays occupancy; RDMA transmit-wait remains ticks/s. KV-offload current and legacy names are alternatives rather than a double-counted sum. New slowdown/PSI/queue candidates are bounded to supporting evidence, not causal proof; checkpoint/actor/critic stage symptoms are now retained. Implementation: [metric_queries.py](../../xlayer_telemetry/analysis/metric_queries.py), [diagnostics.py](../../xlayer_telemetry/analysis/diagnostics.py), [diagnosis_analysis.py](../../xlayer_telemetry/analysis/diagnosis_analysis.py); tests: [test_metric_query_profiles.py](../../tests/test_metric_query_profiles.py).

Dashboard changes add **35 panels in collapsed detail rows**, reusing raw exporter families: host pressure and collector health, network/RDMA backpressure, DCGM health/profiling, device IO/filesystem pressure, vLLM phase/cache/offload, Ray retry/placement state, and native scrape health/cost. Existing overview query count is preserved apart from the improved current/legacy KV-offload fallback. [Dashboard signal guide](../dashboards.md#bottleneck-signals-beyond-utilization) details scope and limits. [Runtime dashboard evidence](metric-dashboard-coverage-20261003.json) is synthetic Prometheus 3.5.0 evidence, not a real workload measurement; no Grafana/Loki UI rendering is claimed by that record.

### Implemented: budgets and safer GPU diagnostics

- Generated/Compose Prometheus config now enforces 100,000 samples per scrape, 1,024 targets, 16 MB body, 40 labels, 128-byte label names and 1,024-byte label values by default. Configured scrape timings are host 2 s/2 s timeout, native 5 s/4 s timeout, SMART 60 s/10 s timeout. Validated overrides bound sample/target/body/native cadence, and timeout must not exceed interval. Exceeding a Prometheus scrape limit can reject the scrape, so monitor target health and narrow/shard sources instead of assuming partial success. Source: [run_telemetry.sh](../../scripts/run_telemetry.sh), [config validation](../../xlayer_telemetry/operations/config.py), [example config](../../examples/dashboards/prometheus.yml); [operator guide](../metrics.md#bounded-collection-and-pressure-evidence).
- Native source input is bounded before JSON decoding at 1 MiB; at most 1,024 endpoints, 16 supplied labels each, 128-character names/256-byte values. Duplicate normalized endpoint registrations, reserved/internal label overrides, nested/nonfinite label values and query/fragment-bearing metrics paths are rejected. Source: [source_discovery.py](../../xlayer_telemetry/source_discovery.py); tests: [test_source_discovery.py](../../tests/test_source_discovery.py).
- Per-PID GPU collection is off by default. `GPU_PROCESS_METRICS=1` opts in, `GPU_MAX_PROCESSES` defaults 256 (range 1–4,096), and device sampling continues independently of process-query errors. `telemetry_gpu_process_collection_enabled` and `telemetry_gpu_process_samples_truncated` expose configuration and cap impact. Malformed GPU identities and duplicate/invalid process identities are omitted; CLI interval/duration require finite positive values. Two new genuine health metrics raise the contract from 133 to **135** entries, with **39** exact implemented families; the baseline 96 non-automatic canonical names are not relabeled as newly collected. Source: [gpu_sampler.py](../../xlayer_telemetry/collectors/gpu_sampler.py); tests: [test_gpu_sampler.py](../../tests/test_gpu_sampler.py), [collection budgets](../../tests/test_collection_budgets.py).
- Prometheus query ingestion now rejects responses larger than 8 MiB, matrices over 1,000 series or 200,000 points, before an apparently complete result can be returned. 3FS distribution query gains `LIMIT 1001` and rejects more than 1,000 metric groups. These are query failure/missing evidence, never silent top-N success. Operators can narrow selectors/windows or increase query step. Source: [prometheus.py](../../xlayer_telemetry/prometheus.py), [ThreeFSClient](../../xlayer_telemetry/analysis/diagnostics.py); tests: [test_prometheus.py](../../tests/test_prometheus.py), [query profiles](../../tests/test_metric_query_profiles.py).

These changes do not fully bound all possible resource use: SDK serialization/input label volume, current application snapshot/exposition parsing, nvidia-smi subprocess response buffering, JSONL/history retention and topology size still require operator/application limits. GPU process caps are post-query retained-row bounds, not a bound on all subprocess bytes. Raw event/trace IDs remain outside Prometheus by design.

### Latest source contracts checked for the extensions

The extension implementation and source review use the following primary references, recorded on 2026-10-03. "Latest" means the checked upstream snapshot, not a guarantee that every installed deployment matches it:

- Bundled Node Exporter v1.9.1 [PSI collector](https://github.com/prometheus/node_exporter/blob/v1.9.1/collector/pressure_linux.go), [diskstats](https://github.com/prometheus/node_exporter/blob/v1.9.1/collector/diskstats_linux.go), [InfiniBand](https://github.com/prometheus/node_exporter/blob/v1.9.1/collector/infiniband_linux.go). Newer mlx5 ACK/ECN/retry families absent from this bundled version are not fabricated.
- vLLM `5f30fc7031cae49bf51073fc953d419b08f8887c`: [V1 logger metrics](https://github.com/vllm-project/vllm/blob/5f30fc7031cae49bf51073fc953d419b08f8887c/vllm/v1/metrics/loggers.py), [offload connector metrics](https://github.com/vllm-project/vllm/blob/5f30fc7031cae49bf51073fc953d419b08f8887c/vllm/distributed/kv_transfer/kv_connector/v1/offloading/metrics.py).
- DCGM Exporter `fafd151148052628061a80450b4ee037a5fa0c3c`: [default-counters.csv](https://github.com/NVIDIA/dcgm-exporter/blob/fafd151148052628061a80450b4ee037a5fa0c3c/etc/default-counters.csv). Profiling/violation field support remains hardware/driver/config-dependent.
- [Ray system metrics reference](https://docs.ray.io/en/latest/ray-observability/reference/system-metrics.html) and Ray `43b706d733c590cf497cc322c57b9fd610bf214f` [raylet definitions](https://github.com/ray-project/ray/blob/43b706d733c590cf497cc322c57b9fd610bf214f/src/ray/raylet/metrics.h) / [emission semantics](https://github.com/ray-project/ray/blob/43b706d733c590cf497cc322c57b9fd610bf214f/src/ray/raylet/local_object_manager.cc). Internal pending-spill/restore gauges are explicitly version-dependent.

### Observed restricted Node Exporter smoke

The official Node Exporter **1.9.1**, revision `f2ec547b49af53815038a50265aa2adcd1275959`, was downloaded into temporary storage and queried on loopback, then terminated. Default startup could not succeed because this execution sandbox has no `/sys`. A proc-compatible, explicitly selected collector subset yielded **359 samples / 168 distinct sample names / 49,705 bytes** in one HTTP response. Memory, vmstat, netstat and loopback netdev families were present. Filesystem fields described the virtual/container-visible mounts, and its gather log reported 17 duplicate-mount metric errors despite collector success=1. PSI/time collector success=0 reflected missing PSI/sysfs sources; `node_time_seconds` alone did not establish clock health. CPU/diskstats/GPU/RDMA and physical NIC/SSD health were not validated. This is a partial source-availability smoke, not normal host deployment acceptance or an overhead benchmark. See [machine-readable smoke record](metric-node-exporter-smoke-20261003.json).

### Test environment and reproducibility

- Initial default Python had no pytest. Test requirements from the repository (`pytest>=8,<10`, `jsonschema>=4.18,<5`) were installed in a temporary venv from PyPI, alongside an editable package. Python 3.12.14; pytest 9.1.1; jsonschema 4.26.0.
- A first full test attempt in the shared checkout was interrupted when concurrent edits started and is not counted as a strict baseline.
- Strict baseline uses a separate `git archive c43b030dec0d3d9632bf80973513ae5ff61bf304` extraction with its own pytest `pythonpath=.` and the same venv. No worker edits can change the archived files.
- Some Linux process lifecycle tests inspect `/proc/<pid>/task/<pid>/children`. A contained probe confirmed valid PIDs, readable `/proc/<pid>/stat`/PPID and FIFO `wchan`, but no optional `task/<pid>/children` file. Tests originally depended on that optional file. Readonly default HOME also causes default-output-path tests to fail; writable temporary HOME is used for fair reruns.
- Strict archived baseline full suite: **700 passed, 8 failed, 4 skipped in 248.99 s**. Eight failing tests rerun with writable HOME: **4 passed, 4 failed in 26.51 s**; the four HOME-path failures are therefore environment-dependent.
- The four reproduced baseline failures are `test_analysis_deadline.py::test_cli_sigterm_stops_its_analysis_worker` and `test_sidecar_shutdown.py::test_forced_diagnostics_shutdown_stops_actual_blocked_analyzer` (missing `/proc/PID/task/PID/children`), `test_sidecar_shutdown.py::test_unresponsive_owned_sidecar_preserves_workload_exit_and_cleanup[diagnostics]` (eight-second timeout), and `test_telemetry_regressions.py::test_wrapper_forwards_termination_to_workload` (five-second timeout). The two timeout failures were subsequently traced to a real wrapper ownership bug and fixed as described below. The two process-discovery failures were fixed without bypassing actual child-process integration.
- Final frozen-checkout full suite: **775 passed, 0 failed, 0 skipped in 71.27 seconds**. All optional PromQL checks and offline Docker Compose configuration validation ran. No HOME override was needed because tests now isolate HOME themselves. Command: `PATH=<docker-cli>:<prometheus-tools>:$PATH DOCKER_CONFIG=<compose-plugin-dir> PROMTOOL=<promtool> python -m pytest -q -rs --durations=15`. Intermediate mixed-revision runs are not a final validation result.
- No actual GPU/RDMA/SMART/3FS/native-training integration, training throughput benchmark or production deployment is claimed by this source audit. Repository publication status is tracked separately from metric validation.


### Fixed: lifecycle failures, with real process guarantees preserved

The user separately requested fixing the baseline failures. `sidecar_is_owned` had treated all
`jobs -p` entries as live children. Bash also lists completed-but-unreported jobs there,
causing each already-exited health/bridge sidecar to pay unnecessary TERM/KILL grace periods.
The wrapper now checks running and stopped jobs (`jobs -pr; jobs -ps`), preserving stopped
controller cleanup and refusing stale completed-job ownership. The new real-shell regression
fails on the untouched baseline and passes with the fix. Original eight-second diagnostic
shutdown and five-second workload signal-forwarding assertions are unchanged; measured focused
runs improved from approximately 10.8/8.4 seconds to 2.82/0.45 seconds respectively.

The tests now enumerate actual direct children using `/proc/<pid>/stat` PPID and retain process
starttime identities instead of requiring optional `/proc/<pid>/task/<pid>/children` files.
They still wait for an actual analyzer blocked in the injected FIFO (`wait_for_partner`), send
SIGTERM or SIGSTOP to the real controller, require owned worker termination and correct workload
exit status, and verify an unrelated process survives. Deterministic helper tests cover PPID
selection and PID reuse. The CLI SIGTERM integration is stronger than baseline because it now
checks both actual FIFO blocking and unrelated-process survival. No new skip, relaxed timeout,
mocked replacement for the OS integration, or production analysis-deadline relaxation was added.
Both blocked-FIFO integration tests passed five additional consecutive reruns; 16 related
lifecycle/helper tests passed together.

An autouse test fixture gives each test a separate writable HOME, outside that test's own
`tmp_path`, so default-output-path behavior remains exercised without touching a developer's
real home or requiring an outer HOME override. Existing empty-directory and config-default
assertions are preserved. This resolves the four read-only-HOME baseline failures through test
isolation, not a change to production defaults. Local Docker CLI 29.7.1 and Compose 5.6.0 were
used solely for offline Compose configuration validation; no Docker daemon or containers were
started. That previously unavailable optional configuration test also passes.

Changed lifecycle files: `scripts/run_verl_with_telemetry.sh`, `tests/conftest.py`,
`tests/_process_helpers.py`, `tests/test_process_helpers.py`, `tests/test_wrapper_job_ownership.py`,
`tests/test_analysis_deadline.py`, and `tests/test_sidecar_shutdown.py`.


### Final verification status

| Check | Status | Result and scope |
| --- | --- | --- |
| Full frozen CPU regression suite | PASS | 775 passed; zero failures and skips; 71.27 s |
| Real Prometheus / dashboard fixture smoke | PASS | All 177 dashboard expressions returned finite synthetic values |
| Real promtool semantic fixtures | PASS | Included in full suite; missing vs zero, entity scope, sentinel, denominator, compatibility semantics |
| Generated and example Prometheus config | PASS | promtool 3.5.0 parses both; example absolute deployment file paths naturally absent in this checkout |
| Python compileall / all shell scripts / git diff whitespace | PASS | No syntax or whitespace errors |
| Official Node Exporter source smoke | PARTIAL | Actual restricted-container fields observed; missing `/sys`/PSI and duplicate virtual-mount errors disclosed above |
| GPU / RDMA / physical NVMe / SMART / real veRL-vLLM-Ray / 3FS / multi-node | NOT RUN | Hardware and production services unavailable; synthetic results do not validate performance or attribution |
| Grafana browser rendering / remote CI / deployment | NOT RUN | No rendering/deployment/remote publication claim |

Validation was completed locally before the subsequently requested work-branch push. Publishing the work branch does not merge or deploy it; no pull request, merge or deployment is included in this task.
