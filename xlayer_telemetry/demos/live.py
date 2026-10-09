"""Serve a bounded synthetic B300 cluster for the existing Grafana dashboards."""

from __future__ import annotations

import argparse
import json
import math
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from xlayer_telemetry.metrics.prometheus import GaugeSample, format_gauges
from xlayer_telemetry.adapters.verl import VerlMetricsAdapter
from xlayer_telemetry.measurements import finite_number
from xlayer_telemetry.operations.config import assets_root
from .scenario import frame_at, load_scenario, phase_values
from .multi_job import load_schedule, native_values, resource_values
from dataclasses import replace


_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_GIB = 1024 ** 3
_AGENT_RL_STEP_SECONDS = 6


def load_topology(directory: Path) -> tuple[dict, dict]:
    """Load the JSON-compatible YAML fixtures without a YAML dependency."""
    try:
        gpu = json.loads((directory / "gpu_topology.yaml").read_text(encoding="utf-8"))
        storage = json.loads((directory / "storage_topology.yaml").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid live-demo topology: {error}") from error
    for key, payload, nodes, count in (
        ("GPU", gpu, "gpu_nodes", "gpus_per_node"),
        ("storage", storage, "storage_nodes", "ssds_per_node"),
    ):
        if (not isinstance(payload, dict) or not isinstance(payload.get(nodes), list) or not payload[nodes]
                or not all(isinstance(node, str) and _NAME.fullmatch(node) for node in payload[nodes])
                or type(payload.get(count)) is not int or payload[count] <= 0):
            raise ValueError(f"invalid {key} topology")
    if not isinstance(gpu.get("gpu_model"), str) or not isinstance(gpu.get("intra_node_interconnect"), str):
        raise ValueError("invalid GPU topology")
    for payload in (gpu, storage):
        network = payload.get("network")
        if (not isinstance(network, dict) or not isinstance(network.get("transport"), str)
                or finite_number(network.get("bandwidth_gbps")) is None
                or network["bandwidth_gbps"] <= 0):
            raise ValueError("invalid network topology")
    nodes = gpu["gpu_nodes"] + storage["storage_nodes"]
    metadata = storage.get('metadata_nodes', [])
    if not isinstance(metadata, list) or any(not isinstance(node,str) or not _NAME.fullmatch(node) for node in metadata):
        raise ValueError('invalid metadata node topology')
    nodes += metadata
    if len(set(nodes)) != len(nodes) or set(nodes) & {"topology", "vllm", "ray", "dcgm", "mooncake-master", "mooncake-client"}:
        raise ValueError("demo node names must be unique and not reserved native endpoints")
    return gpu, storage


def _phase(elapsed: float) -> tuple[str, dict[str, float]]:
    cycle = elapsed % 100
    if cycle < 35:
        return "training", {"gpu": 94, "tokens": 7600, "step": 1.1, "read": .5, "write": .2, "rx": 180, "tx": 180, "busy": .10, "waiting": 1, "waiting_capacity": 1, "waiting_deferred": 0}
    if cycle < 55:
        return "data_wait", {"gpu": 61, "tokens": 4700, "step": 1.7, "read": 6, "write": .2, "rx": 420, "tx": 150, "busy": .82, "waiting": 8, "waiting_capacity": 6, "waiting_deferred": 2}
    if cycle < 75:
        return "collective", {"gpu": 75, "tokens": 5800, "step": 1.4, "read": .5, "write": .2, "rx": 330, "tx": 330, "busy": .15, "waiting": 1, "waiting_capacity": 1, "waiting_deferred": 0}
    if cycle < 90:
        return "checkpoint", {"gpu": 68, "tokens": 5100, "step": 1.6, "read": .3, "write": 5, "rx": 160, "tx": 320, "busy": .68, "waiting": 8, "waiting_capacity": 3, "waiting_deferred": 5}
    return "recovery", {"gpu": 87, "tokens": 6800, "step": 1.2, "read": 1, "write": .5, "rx": 220, "tx": 220, "busy": .24, "waiting": 1, "waiting_capacity": 1, "waiting_deferred": 0}


class Demo:
    def __init__(self, topology_dir: Path, *, scenario_state: Path | None = None, multi_job_state: Path | None = None) -> None:
        if scenario_state is not None and multi_job_state is not None:
            raise ValueError('single-job and multi-job states are mutually exclusive')
        self.multi_job_state = multi_job_state
        self.multi_job = None
        self.multi_completed = {}
        self.gpu, self.storage = load_topology(topology_dir)
        self.network = f"{self.gpu['network']['transport']}-{self.gpu['network']['bandwidth_gbps']:g}Gbps"
        if self.network != f"{self.storage['network']['transport']}-{self.storage['network']['bandwidth_gbps']:g}Gbps":
            raise ValueError("GPU and storage network topology must match")
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.counters: dict[tuple[str, str], tuple[float, float]] = {}
        self.scenario_state = scenario_state
        self.scenario: dict | None = None
        self.completed_frame: dict | None = None
        self.active_frame: dict | None = None
        self.active_phase: dict | None = None

    def _counter(self, node: str, name: str, rate: float, now: float) -> float:
        key = (node, name)
        value, previous = self.counters.get(key, (0.0, now))
        value += max(0, now - previous) * rate
        self.counters[key] = value, now
        return value

    def metrics(self, endpoint: str) -> list[GaugeSample]:
        now = time.monotonic()
        phase, values = _phase(now - self.started)
        with self.lock:
            if self.multi_job_state is not None:
                try: self.multi_job = load_schedule(self.multi_job_state)
                except FileNotFoundError: pass
                if self.multi_job is not None:
                    valid_runs={job['scenario']['run_id'] for job in self.multi_job['jobs']}
                    self.multi_completed={run:entry for run,entry in self.multi_completed.items() if run in valid_runs}
                    for job in self.multi_job['jobs']:
                        completed=[frame for frame in job['scenario']['frames'] if frame['end']<=time.time()]
                        if completed:
                            frame=completed[0] if job['stale_application'] else completed[-1]
                            self.multi_completed[job['scenario']['run_id']]=(job,frame)
                    values = resource_values(self.multi_job, time.time())
                    for job in self.multi_job['jobs']:
                        replicas = job.get('rollout_replicas', [])
                        if endpoint == job['instance'] or any(row['instance'] == endpoint for row in replicas):
                            rows = self._vllm(now, native_values(job, time.time(), replica_instance=endpoint), identity=endpoint,
                                              labels={'model_name': job['model'], 'engine': '0'})
                            missing_preemption = job['missing_preemptions'] or (bool(replicas) and endpoint != job['instance'])
                            return [r for r in rows if not missing_preemption or r.name != 'vllm:num_preemptions_total']
            if self.scenario_state is not None:
                wall = time.time()
                try:
                    schedule = load_scenario(self.scenario_state)
                except FileNotFoundError:
                    schedule = self.scenario
                if self.scenario is not None:
                    for frame in self.scenario["frames"]:
                        if frame["end"] <= wall:
                            self.completed_frame = frame
                self.scenario = schedule
                active = frame_at(schedule, wall) if schedule is not None else None
                self.active_frame, self.active_phase = active if active is not None else (None, None)
                if schedule is not None:
                    for frame in schedule["frames"]:
                        if frame["end"] <= wall:
                            self.completed_frame = frame
                    if active is not None:
                        phase, values = self.active_phase["phase"], phase_values(*active)
                    else:
                        # Between pairs, retain the completed application snapshot.
                        # Resource samples reflect an idle synthetic node, not a past phase.
                        phase = "idle"
                        values = {**values, "gpu": 8, "waiting": 0, "busy": .05, "tokens": 0,
                                  "sandbox_pressure": .01, "kv_hit": .78, "kv_slow": 0}
            if endpoint in self.gpu["gpu_nodes"]:
                return self._gpu_node(endpoint, now, phase, values)
            if endpoint in self.storage["storage_nodes"]:
                return self._storage_node(endpoint, now, phase, values)
            if endpoint in self.storage.get('metadata_nodes', []):
                return self._storage_node(endpoint, now, phase, values, devices=False)
            if endpoint == "topology":
                return self._topology()
            if endpoint == "vllm":
                if self.multi_job_state is not None: return []
                return self._vllm(now, values)
            if endpoint == "ray":
                return self._ray(now, values)
            if endpoint == "mooncake-master":
                return self._mooncake_master(now)
            if endpoint == "mooncake-client":
                return self._mooncake_client(now)
            if endpoint == "dcgm":
                return self._dcgm(now, values)
        raise ValueError(f"unknown demo endpoint: {endpoint}")

    def _gpu_node(self, node: str, now: float, phase: str, value: dict[str, float]) -> list[GaugeSample]:
        samples = [
            GaugeSample("telemetry_gpu_sample_timestamp_seconds", "Synthetic GPU sample timestamp.", time.time()),
            GaugeSample("live_demo_phase_info", "Current synthetic demo phase.", 1, {"phase": phase}),
        ]
        if self.gpu["gpus_per_node"] > 0:
            samples.append(GaugeSample("telemetry_gpu_process_sample_timestamp_seconds",
                                       "Synthetic GPU process memory sample timestamp.", time.time()))
        samples.extend(self._host(node, now, value))
        samples.extend(self._disk_pressure(node, "nvme0n1", now, value))
        samples.extend(self._filesystem_health())
        read_bytes = value["read"] * _GIB
        write_bytes = value["write"] * _GIB
        for name, rate in (
            ("node_disk_read_bytes_total", read_bytes),
            ("node_disk_written_bytes_total", write_bytes),
            ("node_disk_reads_completed_total", read_bytes / (256 * 1024)),
            ("node_disk_writes_completed_total", write_bytes / (256 * 1024)),
            ("node_disk_io_time_seconds_total", value["busy"]),
        ):
            samples.append(GaugeSample(name, "Synthetic disk counter.", self._counter(node, name, rate, now), {"device": "nvme0n1"}, kind="counter"))
        for name, rate in (
            ("node_network_receive_bytes_total", value["rx"] * 1e9 / 8),
            ("node_network_transmit_bytes_total", value["tx"] * 1e9 / 8),
        ):
            samples.append(GaugeSample(name, "Synthetic RoCE counter.", self._counter(node, name, rate, now), {"device": "roce0"}, kind="counter"))
        samples += [
            GaugeSample("node_filesystem_size_bytes", "Synthetic data filesystem size.", 64 * 1024 * _GIB, {"mountpoint": "/mnt/data", "fstype": "xfs"}),
            GaugeSample("node_filesystem_avail_bytes", "Synthetic data filesystem free space.", 39 * 1024 * _GIB, {"mountpoint": "/mnt/data", "fstype": "xfs"}),
        ]
        for rank in range(self.gpu["gpus_per_node"]):
            wobble = math.sin(now / 4 + rank) * 2
            labels = {
                "run_id": "live-demo", "producer": "synthetic", "role": "trainer",
                "node": node, "worker_id": str(rank), "local_rank": str(rank),
            }
            samples += [
                GaugeSample("telemetry_gpu_utilization_percent", "Synthetic GPU utilization.", max(0, min(100, value["gpu"] + wobble)), {"gpu": str(rank)}),
                GaugeSample("telemetry_gpu_power_watts", "Synthetic GPU power.", 820 + value["gpu"] * 3, {"gpu": str(rank)}),
                GaugeSample("telemetry_gpu_temperature_celsius", "Synthetic GPU temperature.", 54 + value["gpu"] * .2 + wobble, {"gpu": str(rank)}),
                GaugeSample("telemetry_gpu_sm_clock_mhz", "Synthetic GPU SM clock.", 1800 + value["gpu"] * 5, {"gpu": str(rank)}),
                GaugeSample("telemetry_gpu_memory_used_bytes", "Synthetic device GPU memory used.", 160 * _GIB, {"gpu": str(rank)}),
                GaugeSample("telemetry_gpu_memory_total_bytes", "Synthetic device GPU memory capacity.", 288 * _GIB, {"gpu": str(rank)}),
                GaugeSample("telemetry_gpu_process_memory_bytes", "Synthetic training process GPU memory.", 144 * _GIB, {"gpu": str(rank), "pid": str(9000 + rank), "gpu_uuid": f"DEMO-{node}-{rank}"}),
                GaugeSample("training_sample_timestamp_seconds", "Synthetic training sample timestamp.", time.time(), labels),
                GaugeSample("training_step", "Synthetic training step.", int(now - self.started), labels),
                GaugeSample("training_tokens_per_second", "Synthetic worker throughput.", value["tokens"], labels),
                GaugeSample("training_step_time_seconds", "Synthetic training step duration.", value["step"], labels),
                GaugeSample("training_loss", "Synthetic training loss.", 1.4 + rank * .01, labels),
                GaugeSample("training_gpu_allocation", "Synthetic worker GPU allocation.", 1, {**labels, "gpu": str(rank)}),
            ]
            timers = {"forward": .28, "backward": .47, "communication": .12 if phase != "collective" else .48,
                      "data_loader": .08 if phase != "data_wait" else .62, "checkpoint": .01 if phase != "checkpoint" else .40}
            samples.extend(GaugeSample("training_timer_seconds", "Synthetic worker timer.", timer, {**labels, "timer": name}) for name, timer in timers.items())
        if node == self.gpu["gpu_nodes"][0]:
            if self.multi_job_state is not None:
                for job in (self.multi_job or {}).get('jobs', []):
                    entry=self.multi_completed.get(job['scenario']['run_id'])
                    if entry is None: continue
                    job,frame=entry
                    rows = self._scenario_agent(now, schedule=job['scenario'], frame=frame, include_sandbox=False)
                    rows = [replace(sample, value=job['reward']) if sample.name == 'reward_mean' else
                            replace(sample, value=frame['end'] - 600) if sample.name == 'training_sample_timestamp_seconds' and job['stale_application'] else sample
                            for sample in rows]
                    samples.extend(r for r in rows if not job['missing_reward'] or r.name != 'reward_mean')
            else:
                samples.extend(self._scenario_agent(now) if self.scenario_state is not None else self._agent_rl(now))
        return [s for s in samples if s.labels.get('run_id') != 'live-demo'] if self.multi_job_state is not None else samples

    def _scenario_agent(self, now: float, *, schedule=None, frame=None, include_sandbox=True) -> list[GaugeSample]:
        """Only completed application observations; resource samples remain live."""
        schedule = schedule or self.scenario
        if schedule is None:
            return []
        labels = {"run_id": schedule["run_id"], "producer": "verl-file-demo", "role": "trainer",
                  "node": schedule["node"], "worker_id": "driver"}
        frame = frame or self.completed_frame
        samples = []
        if frame is not None:
            stages = {p["phase"]: p["end"] - p["start"] for p in frame["phases"]}
            samples.extend([
                GaugeSample("training_sample_timestamp_seconds", "Synthetic training sample timestamp.", frame["end"], labels),
                GaugeSample("training_step", "Synthetic training step.", frame["step"], labels),
                GaugeSample("training_step_time_seconds", "Synthetic training step duration.", frame["end"] - frame["start"], labels),
                GaugeSample("reward_mean", "Synthetic completed-step reward mean.", .732, labels),
                GaugeSample("training_tokens_per_second", "Synthetic worker throughput.", 64 * 512 / stages["rollout"], labels),
            ])
            samples.extend(GaugeSample("rl_stage_duration_seconds", "Synthetic completed VERL stage duration.", duration,
                                       {**labels, "phase": phase}) for phase, duration in stages.items())
            samples.extend(GaugeSample("training_timer_seconds", "Synthetic worker timer.", duration,
                                       {**labels, "timer": phase}) for phase, duration in stages.items())
            reported = VerlMetricsAdapter.translate({"perf/mfu/actor": .58, "fully_async/count/current_param_version": frame["policy_version"]})
            samples.extend(GaugeSample(metric.name, "Synthetic explicitly reported framework scalar.", metric.value,
                                       {**labels, **dict(metric.labels)}) for metric in reported)
            for worker, seconds in enumerate((stages["rollout"] * .8, stages["rollout"] * .9,
                                               stages["rollout"], stages["rollout"] * .85)):
                peer = {**labels, "producer": "synthetic-rollout-peers", "role": "rollout", "worker_id": f"rollout-{worker}"}
                samples.extend([
                    GaugeSample("training_sample_timestamp_seconds", "Synthetic training sample timestamp.", frame["end"], peer),
                    GaugeSample("training_step", "Synthetic training step.", frame["step"], peer),
                    GaugeSample("rl_stage_duration_seconds", "Synthetic completed VERL stage duration.", seconds, {**peer, "phase": "rollout"}),
                ])
        wrapper = {**labels, "producer": "xlayer", "role": "launcher", "worker_id": "wrapper",
                   "source": "wrapper_health", "boundary_scope": "wrapped_command"}
        samples.extend(GaugeSample("telemetry_wrapped_workload_state", "Synthetic explicit wrapped-command state.",
                                   1 if state == "running" else 0, {**wrapper, "state": state})
                       for state in ("running", "succeeded", "failed"))
        samples.append(GaugeSample("telemetry_wrapped_workload_observed_timestamp_seconds",
                                   "Synthetic node-clock wrapper report timestamp.", time.time(), wrapper))
        if include_sandbox: samples.extend(self._sandbox(now))
        return samples

    def _agent_rl(self, now: float) -> list[GaugeSample]:
        """Return a step-boundary VERL Agent run plus scrape-time live signals."""
        elapsed = now - self.started
        completed_step = int(elapsed // _AGENT_RL_STEP_SECONDS) + 1
        step_age = elapsed % _AGENT_RL_STEP_SECONDS
        wave = math.sin(completed_step * .8)
        rollout = 2.8 + .35 * wave + (1.4 if completed_step % 5 == 0 else 0)
        reward = .48 + .08 * math.cos(completed_step * .6)
        actor_update = 1.55 + .18 * math.sin(completed_step * .45)
        critic_update = .86 + .09 * math.cos(completed_step * .5)
        weight_sync = .32 + .06 * math.sin(completed_step * .9)
        stages = {
            "rollout": rollout,
            "reward": reward,
            "actor_update": actor_update,
            "critic_update": critic_update,
            "weight_sync": weight_sync,
        }
        labels = {
            "run_id": "verl-agent-demo",
            "producer": "verl-file-demo",
            "role": "trainer",
            "node": self.gpu["gpu_nodes"][0],
            "worker_id": "driver",
        }
        samples = [
            GaugeSample("training_sample_timestamp_seconds", "Synthetic training sample timestamp.", time.time() - step_age, labels),
            GaugeSample("training_step", "Synthetic training step.", completed_step, labels),
            GaugeSample("reward_mean", "Synthetic completed-step reward mean.", .61 + completed_step * .004 + .025 * wave, labels),
            GaugeSample("training_tokens_per_second_per_gpu", "Synthetic completed-step throughput per GPU.", 2380 - rollout * 115 + 35 * wave, labels),
            GaugeSample("rollout_output_tokens_mean", "Synthetic completed-step response length.", 310 + 18 * math.cos(completed_step * .7), labels),
            GaugeSample("agent_tool_call_duration_seconds", "Synthetic browser tool latency.", .72 + .2 * math.sin(elapsed / 4), {**labels, "tool": "browser"}),
            GaugeSample("policy_version_lag", "Synthetic live rollout policy lag.", 1 + int((elapsed % 18) > 12), {**labels, "replica": "rollout-0"}),
            GaugeSample("training_step_time_seconds", "Synthetic training step duration.", sum(stages.values()), labels),
            GaugeSample("training_tokens_per_second", "Synthetic worker throughput.", 32 * (2380 - rollout * 115), labels),
            GaugeSample("training_loss", "Synthetic training loss.", 1.4 - .05 * wave, labels),
        ]
        samples.extend(GaugeSample("training_timer_seconds", "Synthetic worker timer.", duration,
                                  {**labels, "timer": name}) for name, duration in stages.items())
        samples.extend(GaugeSample("training_gpu_allocation", "Synthetic worker GPU allocation.", 1,
                                  {**labels, "gpu": str(gpu)}) for gpu in range(self.gpu["gpus_per_node"]))
        # Stable rollout workers provide an explicitly synthetic same-step peer
        # cohort. These are completed observations, not GPU attribution.
        for worker, base_duration in enumerate((2.8, 3.0, 4.9, 3.2)):
            peer = {"run_id": "verl-agent-demo", "producer": "synthetic-rollout-peers",
                    "role": "rollout", "node": self.gpu["gpu_nodes"][0], "worker_id": f"rollout-{worker}"}
            samples.extend([
                GaugeSample("training_sample_timestamp_seconds", "Synthetic training sample timestamp.", time.time()-step_age, peer),
                GaugeSample("training_step", "Synthetic training step.", completed_step, peer),
                GaugeSample("rl_stage_duration_seconds", "Synthetic completed VERL stage duration.", base_duration + .03*wave, {**peer, "phase": "rollout"}),
            ])
        # Exercise the real adapter with explicitly synthetic native logger keys.
        reported = VerlMetricsAdapter.translate({"perf/mfu/actor": .47 + .06*wave,
            "perf/mfu/critic": .31, "fully_async/count/current_param_version": 128 + completed_step//4})
        samples.extend(GaugeSample(metric.name, "Synthetic explicitly reported framework scalar.", metric.value,
                                   {**labels, **dict(metric.labels)}) for metric in reported)
        wrapper_labels={"run_id":"verl-agent-demo", "node":self.gpu["gpu_nodes"][0],
                        "producer":"xlayer", "role":"launcher", "worker_id":"wrapper",
                        "source":"wrapper_health", "boundary_scope":"wrapped_command"}
        # Simulated wrapper reports exercise the same published state contract;
        # they do not describe an actual distributed veRL command or Ray completion.
        samples.extend(GaugeSample("telemetry_wrapped_workload_state", "Synthetic explicit wrapped-command state.",
                                   1 if state=="running" else 0,{**wrapper_labels,"state":state})
                       for state in ("running","succeeded","failed"))
        samples.append(GaugeSample("telemetry_wrapped_workload_observed_timestamp_seconds",
                                  "Synthetic node-clock wrapper report timestamp.",time.time(),wrapper_labels))
        samples.extend(self._sandbox(now))
        samples.extend(
            GaugeSample("rl_stage_duration_seconds", "Synthetic completed VERL stage duration.", duration, {**labels, "phase": phase})
            for phase, duration in {**stages, "rl_step": sum(stages.values())}.items()
        )
        return samples

    def _host(self, node: str, now: float, value: dict[str, float]) -> list[GaugeSample]:
        samples = [
            GaugeSample("node_memory_MemTotal_bytes", "Synthetic host total memory.", 1024 * _GIB),
            GaugeSample("node_memory_MemAvailable_bytes", "Synthetic host memory available.", 760 * _GIB),
            GaugeSample("node_memory_SwapTotal_bytes", "Synthetic host swap total.", 32 * _GIB),
            GaugeSample("node_memory_SwapFree_bytes", "Synthetic host swap free.", 30 * _GIB),
            GaugeSample("node_memory_Dirty_bytes", "Synthetic dirty memory.", value["write"] * _GIB),
            GaugeSample("node_memory_Writeback_bytes", "Synthetic writeback memory.", value["write"] * _GIB / 4),
            GaugeSample("node_procs_running", "Synthetic runnable process count.", 3),
            GaugeSample("node_procs_blocked", "Synthetic blocked process count.", 2 if value["busy"] > .5 else 0),
            GaugeSample("node_time_seconds", "Synthetic synchronized node clock.", time.time()),
            GaugeSample("node_timex_sync_status", "Synthetic clock synchronization status.", 1),
            GaugeSample("node_timex_offset_seconds", "Synthetic NTP offset fixture, not host synchronization.", .001),
            GaugeSample("node_timex_maxerror_seconds", "Synthetic kernel uncertainty fixture, not measured UTC accuracy.", .001),
        ]
        for cpu in range(4):
            for mode, rate in (("idle", .60), ("user", .25), ("system", .10), ("iowait", .04), ("steal", .01)):
                samples.append(GaugeSample("node_cpu_seconds_total", "Synthetic host CPU counter.",
                    self._counter(node, f"cpu-{cpu}-{mode}", rate, now), {"cpu": str(cpu), "mode": mode}, kind="counter"))
        for name in ("node_vmstat_pswpin", "node_vmstat_pswpout"):
            samples.append(GaugeSample(name, "Synthetic swap page counter.",
                                       self._counter(node, name, 2, now), kind="counter"))
        for name, rate in {
            "node_pressure_cpu_waiting_seconds_total": value.get("host_pressure", .04),
            "node_pressure_memory_waiting_seconds_total": .02,
            "node_pressure_memory_stalled_seconds_total": .01,
            "node_pressure_io_waiting_seconds_total": value["busy"] / 2,
            "node_pressure_io_stalled_seconds_total": value["busy"] / 4,
            "node_vmstat_pgmajfault": 5,
            "node_vmstat_oom_kill": 0,
            "node_netstat_Tcp_RetransSegs": .5,
        }.items():
            samples.append(GaugeSample(name, "Synthetic host pressure/error counter.",
                                       self._counter(node, name, rate, now), kind="counter"))
        for direction in ("receive", "transmit"):
            for kind in ("errs", "drop"):
                name = f"node_network_{direction}_{kind}_total"
                samples.append(GaugeSample(name, "Synthetic NIC error/drop counter.",
                    self._counter(node, name, .05, now), {"device": "roce0"}, kind="counter"))
        for collector in ("cpu", "meminfo", "vmstat", "pressure", "diskstats", "filesystem", "netdev", "netstat", "infiniband"):
            samples.append(GaugeSample("node_scrape_collector_success", "Synthetic node collector status.", 1,
                                       {"collector": collector}))
        for name, rate in (("node_infiniband_port_data_received_bytes_total", value["rx"] * 1e9 / 8),
                           ("node_infiniband_port_data_transmitted_bytes_total", value["tx"] * 1e9 / 8),
                           ("node_infiniband_port_errors_received_total", .02),
                           ("node_infiniband_port_discards_received_total", .03),
                           ("node_infiniband_port_discards_transmitted_total", .04),
                           ("node_infiniband_port_transmit_wait_total", 200),
                           ("node_infiniband_link_downed_total", 0),
                           ("node_infiniband_link_error_recovery_total", 0),
                           ("node_infiniband_symbol_error_total", .01),
                           ("node_infiniband_local_link_integrity_errors_total", .02)):
            samples.append(GaugeSample(name, "Synthetic RDMA port counter.",
                self._counter(node, name, rate, now), {"device": "mlx5_0", "port": "1"}, kind="counter"))
        return samples

    def _disk_pressure(self, node: str, device: str, now: float, value: dict[str, float]) -> list[GaugeSample]:
        """Bounded per-device counters with mean latency consistent with IOPS."""
        labels = {"device": device}
        samples = [GaugeSample("node_disk_io_now", "Synthetic outstanding I/O.", 2, labels)]
        for name, rate in {
            "node_disk_read_time_seconds_total": value["read"] * _GIB / (256 * 1024) * .0004,
            "node_disk_write_time_seconds_total": value["write"] * _GIB / (256 * 1024) * .0008,
            "node_disk_io_time_weighted_seconds_total": 2 * value["busy"],
        }.items():
            samples.append(GaugeSample(name, "Synthetic device latency/queue counter.",
                self._counter(node, name + "/" + device, rate, now), labels, kind="counter"))
        return samples

    def _filesystem_health(self) -> list[GaugeSample]:
        return [GaugeSample(name, "Synthetic filesystem inode/status gauge.", value,
                            {"mountpoint": "/mnt/data", "fstype": "xfs"}) for name, value in {
            "node_filesystem_files": 1000000,
            "node_filesystem_files_free": 750000,
            "node_filesystem_readonly": 0,
            "node_filesystem_device_error": 0,
        }.items()]

    def _sandbox(self, now: float) -> list[GaugeSample]:
        labels = {"run_id": self.scenario["run_id"] if self.scenario is not None else "verl-agent-demo", "producer": "synthetic", "role": "sandbox",
                  "worker_id": "pool-0", "runtime": "containerd", "filesystem": "overlayfs", "deployment": "colocated"}
        pressure = (phase_values(self.active_frame, self.active_phase)["sandbox_pressure"] if self.active_frame is not None
                    else .01 if self.scenario_state is not None
                    else .02 if _phase(now - self.started)[0] == "training" else .43)
        values = {"sandbox_active": 4, "sandbox_queued": 2 if pressure > .1 else 0, "sandbox_io_pressure_ratio": pressure,
                  "sandbox_cpu_pressure_ratio": .08, "sandbox_memory_pressure_ratio": .03,
                  "sandbox_memory_full_pressure_ratio": .01, "sandbox_memory_bytes": 6 * _GIB,
                  "sandbox_memory_peak_bytes": 8 * _GIB, "sandbox_sample_timestamp_seconds": time.time()}
        samples = [GaugeSample(name, "Synthetic sandbox gauge.", value, labels) for name, value in values.items()]
        for name, rate in {"sandbox_io_read_bytes_total": .1 * _GIB, "sandbox_io_write_bytes_total": .2 * _GIB,
                           "sandbox_io_read_ops_total": 400, "sandbox_io_write_ops_total": 800,
                           "sandbox_cpu_usage_seconds_total": 1.5, "sandbox_oom_total": 0,
                           "sandbox_oom_kill_total": 0, "sandbox_memory_high_events_total": 2,
                           "sandbox_memory_max_events_total": 0,
                           "sandbox_cpu_periods_total": 10, "sandbox_cpu_throttled_periods_total": 1,
                           "sandbox_cpu_throttled_seconds_total": .05}.items():
            samples.append(GaugeSample(name, "Synthetic sandbox counter.", self._counter("sandbox", name, rate, now), labels, kind="counter"))
        return samples

    def _vllm(self, now: float, value: dict[str, float], *, identity="vllm", labels=None) -> list[GaugeSample]:
        labels = labels or {"model_name": "synthetic-model", "engine": "0"}
        waiting = value.get("waiting", 8 if value["busy"] > .5 else 1)
        samples = [GaugeSample(name, "Synthetic vLLM gauge.", number, labels) for name, number in {
            "vllm:num_requests_waiting": waiting, "vllm:num_requests_running": 4,
            "vllm:kv_cache_usage_perc": value.get('kv_util', .92 if waiting > 1 else .45)}.items()]
        # Reason categories must be explicit producer inputs, not inferred from
        # queue depth or interpreted as KV causality.
        for reason in ('capacity','deferred'):
            key='waiting_'+reason
            if key in value:
                samples.append(GaugeSample('vllm:num_requests_waiting_by_reason', 'Synthetic producer-reported waiting reason.',
                    value[key], {**labels,'reason':reason}))
        for name, rate in {"vllm:num_preemptions_total": .2 if waiting > 1 else 0,
                           "vllm:prompt_tokens_total": 800, "vllm:generation_tokens_total": value["tokens"],
                           "vllm:prefix_cache_queries_total": 100,
                           "vllm:prefix_cache_hits_total": 100 * value.get("kv_hit", .7),
                           "vllm:external_prefix_cache_queries_total": 50,
                           "vllm:external_prefix_cache_hits_total": 20,
                           "vllm:kv_offload_allocation_failure_total": .05 if waiting > 1 else 0}.items():
            samples.append(GaugeSample(name, "Synthetic vLLM counter.", self._counter(identity, name, rate, now), labels, kind="counter"))
        for direction, rate in (("GPU_to_CPU", .2 * _GIB), ("CPU_to_GPU", .1 * _GIB)):
            samples.append(GaugeSample("vllm:kv_offload_total_bytes_total", "Synthetic KV offload transfer counter.",
                self._counter(identity, direction, rate, now), {**labels, "transfer_type": direction}, kind="counter"))
        for direction, bytes_rate in (("store", .2 * _GIB), ("load", .1 * _GIB)):
            for suffix, rate in (("bytes_total", bytes_rate), ("time_total", .04), ("size_count", 10)):
                name = f"vllm:kv_offload_{direction}_{suffix}"
                samples.append(GaugeSample(name, "Synthetic flat KV offload counter.",
                    self._counter(identity, name, rate, now), labels, kind="counter"))
        for reason, rate in (("stop", 8), ("length", 2), ("abort", 0)):
            samples.append(GaugeSample("vllm:request_success_total", "Synthetic finished request counter.",
                self._counter(identity, "finished-" + reason, rate, now), {**labels, "finished_reason": reason}, kind="counter"))
        # Cumulative bucket COUNTERS, not percentile gauges. Rates preserve the
        # ordering needed by the dashboards' real histogram_quantile queries.
        distributions = {
            "time_to_first_token_seconds": (.25, .65, .85, .99, 1) if waiting > 1 else (.7, .96, .99, 1, 1),
            "request_queue_time_seconds": (.4, .75, .9, .99, 1) if waiting > 1 else (.85, .99, 1, 1, 1),
            "e2e_request_latency_seconds": (.05, .2, .55, .97, 1),
            "request_prefill_time_seconds": (.4, .8, .98, 1, 1),
            "request_decode_time_seconds": (.1, .4, .8, .99, 1),
            "inter_token_latency_seconds": (.98, .999, 1, 1, 1),
            "kv_offload_lookup_sync_delay_seconds": (.99, 1, 1, 1, 1),
            "kv_offload_lookup_async_delay_seconds": (.8, .95, .99, 1, 1),
        }
        for name, fractions in distributions.items():
            for bound, fraction in zip(("0.1", "0.5", "1", "5", "+Inf"), fractions):
                metric = f"vllm:{name}_bucket"
                samples.append(GaugeSample(metric, "Synthetic cumulative latency bucket counter.",
                    self._counter(identity, name + bound, fraction * 10, now), {**labels, "le": bound}, kind="counter"))
        for operation, calls, byte_rate in (("save_exists", 4, 0), ("save_put", 4, .2 * _GIB),
                                            ("load_get", 2, .1 * _GIB), ("lookup_exists", 2, 0)):
            operation_labels = {**labels, "operation": operation, "status": "ok"}
            for name, rate in (("operation_total", calls), ("operation_keys_total", calls * 8),
                               ("operation_bytes_total", byte_rate), ("operation_failed_keys_total", 0)):
                samples.append(GaugeSample(f"vllm:mooncake_store_{name}", "Synthetic Mooncake store operation counter.",
                    self._counter(identity, operation + name, rate, now), operation_labels, kind="counter"))
            if self.scenario_state is not None or self.multi_job_state is not None:
                # Fixed bucket identities for the entire scenario replay. Only
                # nonnegative observation increments change between phases;
                # cumulative counters are never replaced by the current CDF.
                bounds = ("0.001", "0.0025", "0.005", "0.01", "0.02", "0.04", "0.08", "+Inf")
                fractions = ((.005, .02, .06, .25, .915, .985, 1, 1) if value.get("kv_slow")
                             else (.03, .15, .8, .995, 1, 1, 1, 1))
                previous_bound, previous_fraction, mean = 0., 0., 0.
                for bound, fraction in zip(bounds[:-1], fractions[:-1]):
                    mean += (float(bound) + previous_bound) / 2 * (fraction - previous_fraction)
                    previous_bound, previous_fraction = float(bound), fraction
                self._histogram(samples, identity, "vllm:mooncake_store_operation_time_seconds",
                                bounds, fractions, calls, now, operation_labels, mean=mean)
            else:
                self._histogram(samples, identity, "vllm:mooncake_store_operation_time_seconds",
                                ("0.001", "0.01", "0.1", "1", "+Inf"), (.1, .7, .95, 1, 1),
                                calls, now, operation_labels)
            for name in ("operation_total", "operation_failed_keys_total"):
                samples.append(GaugeSample(f"vllm:mooncake_store_{name}", "Synthetic Mooncake store operation counter.",
                    self._counter(identity, operation + name + "error", 0, now),
                    {**operation_labels, "status": "error"}, kind="counter"))
        return samples

    def _histogram(self, samples: list[GaugeSample], endpoint: str, name: str,
                   bounds: tuple[str, ...], fractions: tuple[float, ...], calls: float,
                   now: float, labels: dict[str, str], *, mean: float | None = None) -> None:
        """Emit cumulative buckets and count with one consistent label identity."""
        identity = name + str(sorted(labels.items()))
        for bound, fraction in zip(bounds, fractions):
            samples.append(GaugeSample(name + "_bucket", "Synthetic cumulative Mooncake latency bucket counter.",
                self._counter(endpoint, identity + bound, fraction * calls, now),
                {**labels, "le": bound}, kind="counter"))
        if mean is not None:
            samples.append(GaugeSample(name + "_sum", "Synthetic Mooncake latency sum counter.",
                self._counter(endpoint, identity + "sum", mean * calls, now), labels, kind="counter"))
        samples.append(GaugeSample(name + "_count", "Synthetic Mooncake latency observation counter.",
            self._counter(endpoint, identity + "count", calls, now),
            {} if endpoint == "mooncake-client" else labels, kind="counter"))

    def _mooncake_master(self, now: float) -> list[GaugeSample]:
        samples = [GaugeSample(name, "Synthetic Mooncake master gauge.", number) for name, number in {
            "master_allocated_bytes": 6 * _GIB, "master_total_capacity_bytes": 16 * _GIB}.items()]
        for name, rate in {"mem_cache_hit_nums_": 12, "file_cache_hit_nums_": 4,
                           "valid_get_nums_": 16, "total_get_nums_": 20,
                           "master_put_start_failures_total": 0}.items():
            samples.append(GaugeSample(name, "Synthetic Mooncake master counter.",
                self._counter("mooncake-master", name, rate, now), kind="counter"))
        return samples

    def _mooncake_client(self, now: float) -> list[GaugeSample]:
        labels = {"client_mode": "real", "cluster_id": "synthetic-store"}
        samples = []
        for direction, byte_rate, calls in (("read", .1 * _GIB, 16), ("write", .2 * _GIB, 32)):
            for name, rate in ((f"mooncake_dfs_{direction}_bytes_total", byte_rate),
                               (f"mooncake_dfs_{direction}_ops_total", calls)):
                samples.append(GaugeSample(name, "Synthetic successful DFS key counter.",
                    self._counter("mooncake-client", name, rate, now), labels, kind="counter"))
            self._histogram(samples, "mooncake-client", f"mooncake_dfs_{direction}_latency_us",
                            ("100", "1000", "10000", "100000", "+Inf"),
                            (.1, .7, .95, 1, 1), calls / 8, now, labels)
            errors = f"mooncake_dfs_{direction}_errors_total"
            error = "FILE_READ_FAIL" if direction == "read" else "FILE_WRITE_FAIL"
            samples.append(GaugeSample(errors, "Synthetic DFS failed key counter by error code.",
                self._counter("mooncake-client", errors, 0, now), {**labels, "error": error}, kind="counter"))
        self._histogram(samples, "mooncake-client", "mooncake_dfs_write_staging_latency_us",
                        ("50", "100", "1000", "10000", "+Inf"), (.1, .7, .95, 1, 1), 4, now, labels)
        samples.append(GaugeSample("mooncake_dfs_writes_skipped_total", "Synthetic unattempted DFS key counter.",
            self._counter("mooncake-client", "skipped", 0, now), labels, kind="counter"))
        return samples

    def _ray(self, now: float, value: dict[str, float]) -> list[GaugeSample]:
        labels = {"SessionName": "synthetic-session"}
        samples = []
        for state, count in (("RUNNING", 8), ("PENDING_ARGS_AVAIL", 12 if value.get("kv_slow") else 2)):
            samples.append(GaugeSample("ray_tasks", "Synthetic Ray state gauge.", count,
                                       {**labels, "State": state, "IsRetry": "0"}))
            samples.append(GaugeSample("ray_tasks", "Synthetic Ray state gauge.", 1,
                                       {**labels, "State": state, "IsRetry": "1"}))
        for state, count in (("ALIVE_RUNNING_TASKS", 8), ("ALIVE_IDLE", 2)):
            samples.append(GaugeSample("ray_actors", "Synthetic Ray state gauge.", count, {**labels, "State": state}))
        for state, count in (("CREATED", 4), ("PENDING", 1)):
            samples.append(GaugeSample("ray_placement_groups", "Synthetic Ray placement group state.", count,
                                       {**labels, "State": state}))
        for name, number in (("CPU", 16), ("GPU", 8)):
            for state, fraction in (("AVAILABLE", .25), ("USED", .75)):
                samples.append(GaugeSample("ray_resources", "Synthetic Ray resource gauge.", number * fraction,
                                           {**labels, "Name": name, "State": state}))
        samples.extend([
            GaugeSample("ray_object_store_memory", "Synthetic Ray object store bytes.", 4 * _GIB, {**labels, "Location": "MMAP_SHM"}),
            GaugeSample("ray_memory_manager_worker_eviction_total", "Synthetic Ray eviction counter.",
                        self._counter("ray", "evictions", .05 if value["busy"] > .5 else 0, now), {**labels, "Type": "task"}, kind="counter"),
        ])
        return samples

    def _dcgm(self, now: float, value: dict[str, float]) -> list[GaugeSample]:
        """Independent native fixture; never combine it with sampler device totals."""
        samples = []
        for gpu in range(self.gpu["gpus_per_node"]):
            labels = {"gpu": str(gpu), "UUID": f"SYNTHETIC-DCGM-{gpu}"}
            for name, number in {
                "DCGM_FI_DEV_GPU_UTIL": value["gpu"],
                "DCGM_FI_PROF_PIPE_TENSOR_ACTIVE": .6,
                "DCGM_FI_PROF_DRAM_ACTIVE": .4,
                "DCGM_FI_PROF_PCIE_RX_BYTES": 1e9,
                "DCGM_FI_PROF_PCIE_TX_BYTES": 2e9,
                "DCGM_FI_DEV_XID_ERRORS": 0,
                "DCGM_FI_DEV_FB_USED": 160 * 1024,
                "DCGM_FI_DEV_FB_FREE": 128 * 1024,
            }.items():
                samples.append(GaugeSample(name, "Synthetic DCGM gauge.", number, labels))
            for name, rate in {
                "DCGM_FI_DEV_PCIE_REPLAY_COUNTER": .1,
                "DCGM_FI_DEV_POWER_VIOLATION": .01 * 1e9,
                "DCGM_FI_DEV_THERMAL_VIOLATION": 0,
            }.items():
                samples.append(GaugeSample(name, "Synthetic DCGM counter.",
                    self._counter("dcgm", f"{gpu}/{name}", rate, now), labels, kind="counter"))
        return samples

    def _storage_node(self, node: str, now: float, phase: str, value: dict[str, float], *, devices: bool = True) -> list[GaugeSample]:
        samples = self._host(node, now, value)
        samples.extend(self._filesystem_health())
        samples.extend([
            GaugeSample("node_filesystem_size_bytes", "Synthetic data filesystem size.", 64 * 1024 * _GIB, {"mountpoint": "/mnt/data", "fstype": "xfs"}),
            GaugeSample("node_filesystem_avail_bytes", "Synthetic data filesystem free space.", 39 * 1024 * _GIB, {"mountpoint": "/mnt/data", "fstype": "xfs"}),
        ])
        for name, rate in (("node_network_receive_bytes_total", value["rx"] * 1e9 / 8),
                           ("node_network_transmit_bytes_total", value["tx"] * 1e9 / 8)):
            samples.append(GaugeSample(name, "Synthetic node network counter.", self._counter(node,name,rate,now), {'device':'roce0'},kind='counter'))
        for index in range(self.storage["ssds_per_node"] if devices else 0):
            labels = {"device": f"nvme{index}n1"}
            samples.extend(self._disk_pressure(node, labels["device"], now, value))
            for name, rate in (("node_disk_read_bytes_total", value["read"] * _GIB),
                               ("node_disk_written_bytes_total", value["write"] * _GIB),
                               ("node_disk_reads_completed_total", value["read"] * _GIB / (256 * 1024)),
                               ("node_disk_writes_completed_total", value["write"] * _GIB / (256 * 1024)),
                               ("node_disk_io_time_seconds_total", value["busy"])):
                samples.append(GaugeSample(name, "Synthetic disk counter.", self._counter(node, name + str(index), rate, now), labels, kind="counter"))
        return samples

    def _topology(self) -> list[GaugeSample]:
        samples = []
        for node in self.gpu["gpu_nodes"]:
            samples.append(GaugeSample("telemetry_topology_component_info", "Synthetic topology component.", 1, {"kind": "compute", "component": node, "role": "gpu-node"}))
            for gpu in range(self.gpu["gpus_per_node"]):
                component = f"{node}/gpu-{gpu}"
                samples.append(GaugeSample("telemetry_topology_component_info", "Synthetic topology component.", 1, {"kind": "compute", "component": component, "role": self.gpu["gpu_model"]}))
                for peer in range(self.gpu["gpus_per_node"]):
                    if gpu != peer:
                        samples.append(GaugeSample("telemetry_topology_edge_info", "Synthetic topology edge.", 1, {"kind": "compute", "source": component, "destination": f"{node}/gpu-{peer}", "relation": self.gpu["intra_node_interconnect"]}))
            samples.append(GaugeSample("telemetry_topology_edge_info", "Synthetic topology edge.", 1, {"kind": "compute", "source": node, "destination": "roce-fabric", "relation": self.network}))
        samples.append(GaugeSample("telemetry_topology_component_info", "Synthetic topology component.", 1, {"kind": "compute", "component": "roce-fabric", "role": "network"}))
        for node in self.storage["storage_nodes"]:
            samples.append(GaugeSample("telemetry_topology_component_info", "Synthetic topology component.", 1, {"kind": "storage", "component": node, "role": "data", "resource_node": node, "storage_system": "synthetic-3fs"}))
            for ssd in range(self.storage["ssds_per_node"]):
                samples += [
                    GaugeSample("telemetry_topology_component_info", "Synthetic topology component.", 1, {"kind": "storage", "component": f"{node}/nvme{ssd}n1", "role": "ssd", "resource_node": node, "device": f"nvme{ssd}n1", "storage_system": "synthetic-3fs"}),
                    GaugeSample("telemetry_topology_edge_info", "Synthetic topology edge.", 1, {"kind": "storage", "source": node, "destination": f"{node}/nvme{ssd}n1", "relation": "attached"}),
                ]
            samples.append(GaugeSample("telemetry_topology_edge_info", "Synthetic topology edge.", 1, {"kind": "storage", "source": node, "destination": "roce-fabric", "relation": self.network}))
        for node in self.storage.get('metadata_nodes', []):
            samples.append(GaugeSample('telemetry_topology_component_info','Synthetic topology component.',1,
                {'kind':'storage','component':node,'role':'metadata','resource_node':node,'storage_system':'synthetic-3fs'}))
        samples.append(GaugeSample("telemetry_topology_component_info", "Synthetic topology component.", 1, {"kind": "storage", "component": "roce-fabric", "role": "network"}))
        return samples


def prometheus_config(demo: Demo, address: str, cluster: str = "demo-b300") -> str:
    if not _NAME.fullmatch(cluster):
        raise ValueError("invalid demo cluster name")
    def targets(job: str, endpoints: list[str], extra: str = "") -> list[str]:
        lines = [f"  - job_name: {job}", "    static_configs:"]
        for endpoint in endpoints:
            lines += [f"      - targets: ['{address}']", "        labels:", f"          cluster: {cluster}", "          data_origin: synthetic", f"          instance: {endpoint}", f"          nodename: {endpoint}", f"          __metrics_path__: /metrics/{endpoint}"]
            if extra:
                lines.append(extra)
        return lines
    lines = ["global:", "  scrape_interval: 2s", "scrape_configs:"]
    lines += targets("telemetry", [*demo.gpu["gpu_nodes"], *demo.storage["storage_nodes"], *demo.storage.get('metadata_nodes', []), "topology"])
    lines += ["  - job_name: native", "    static_configs:"]
    for endpoint, source in (("vllm", "vllm"), ("ray", "ray"), ("dcgm", "dcgm"),
                             ("mooncake-master", "mooncake"), ("mooncake-client", "mooncake")):
        if endpoint == 'vllm' and demo.multi_job_state is not None:
            continue
        node = demo.gpu["gpu_nodes"][0]
        lines += [f"      - targets: ['{address}']", "        labels:", f"          cluster: {cluster}",
                  "          data_origin: synthetic", f"          telemetry_source: {source}",
                  f"          node: {node}", f"          nodename: {node}", f"          instance: synthetic-{endpoint}-0",
                  f"          component: {endpoint}-0", f"          __metrics_path__: /metrics/{endpoint}"]
    if demo.multi_job_state is not None:
        from .multi_job import MODELS
        endpoints = [('synthetic-job-' + name, demo.gpu['gpu_nodes'][0]) for name, _ in MODELS]
        if demo.multi_job_state.exists():
            from .multi_job import load_schedule
            for job in load_schedule(demo.multi_job_state)['jobs']:
                endpoints += [(row['instance'], row['endpoint_node']) for row in job.get('rollout_replicas', []) if row['instance'] != job['instance']]
        for endpoint, endpoint_node in endpoints:
            lines += [f"      - targets: ['{address}']", '        labels:', f'          cluster: {cluster}',
                      '          data_origin: synthetic', '          telemetry_source: vllm',
                      f"          node: {endpoint_node}", f"          nodename: {endpoint_node}",
                      f'          instance: {endpoint}', f'          component: {endpoint}',
                      f'          __metrics_path__: /metrics/{endpoint}']
    return "\n".join(lines) + "\n"


def serve(demo: Demo, listen: str) -> None:
    host, separator, raw_port = listen.rpartition(":")
    if not separator or not host or not raw_port.isdigit():
        raise ValueError("--listen must be host:port")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            endpoint = urlparse(self.path).path.removeprefix("/metrics/")
            try:
                body = format_gauges(demo.metrics(endpoint)).encode()
            except ValueError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            pass

    ThreadingHTTPServer((host, int(raw_port)), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology-dir", type=Path, default=assets_root() / "examples" / "live-demo")
    parser.add_argument("--cluster", default="demo-b300")
    parser.add_argument("--listen", default="127.0.0.1:19110")
    parser.add_argument("--write-prometheus-config", type=Path)
    parser.add_argument("--multi-job-state", type=Path, help="Bounded concurrent three-job synthetic inputs")
    parser.add_argument("--scenario-state", type=Path, help="Optional bounded synthetic schedule written atomically by the App demo")
    args = parser.parse_args()
    try:
        demo = Demo(args.topology_dir, scenario_state=args.scenario_state, multi_job_state=args.multi_job_state)
        if args.write_prometheus_config:
            args.write_prometheus_config.write_text(prometheus_config(demo, args.listen, args.cluster), encoding="utf-8")
            return
        serve(demo, args.listen)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
