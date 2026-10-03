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
from xlayer_telemetry.measurements import finite_number
from xlayer_telemetry.operations.config import assets_root


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
    if len(set(nodes)) != len(nodes) or set(nodes) & {"topology", "vllm", "ray"}:
        raise ValueError("demo node names must be unique and not topology, vllm, or ray")
    return gpu, storage


def _phase(elapsed: float) -> tuple[str, dict[str, float]]:
    cycle = elapsed % 100
    if cycle < 35:
        return "training", {"gpu": 94, "tokens": 7600, "step": 1.1, "read": .5, "write": .2, "rx": 180, "tx": 180, "busy": .10}
    if cycle < 55:
        return "data_wait", {"gpu": 61, "tokens": 4700, "step": 1.7, "read": 6, "write": .2, "rx": 420, "tx": 150, "busy": .82}
    if cycle < 75:
        return "collective", {"gpu": 75, "tokens": 5800, "step": 1.4, "read": .5, "write": .2, "rx": 330, "tx": 330, "busy": .15}
    if cycle < 90:
        return "checkpoint", {"gpu": 68, "tokens": 5100, "step": 1.6, "read": .3, "write": 5, "rx": 160, "tx": 320, "busy": .68}
    return "recovery", {"gpu": 87, "tokens": 6800, "step": 1.2, "read": 1, "write": .5, "rx": 220, "tx": 220, "busy": .24}


class Demo:
    def __init__(self, topology_dir: Path) -> None:
        self.gpu, self.storage = load_topology(topology_dir)
        self.network = f"{self.gpu['network']['transport']}-{self.gpu['network']['bandwidth_gbps']:g}Gbps"
        if self.network != f"{self.storage['network']['transport']}-{self.storage['network']['bandwidth_gbps']:g}Gbps":
            raise ValueError("GPU and storage network topology must match")
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.counters: dict[tuple[str, str], tuple[float, float]] = {}

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
            if endpoint in self.gpu["gpu_nodes"]:
                return self._gpu_node(endpoint, now, phase, values)
            if endpoint in self.storage["storage_nodes"]:
                return self._storage_node(endpoint, now, phase, values)
            if endpoint == "topology":
                return self._topology()
            if endpoint == "vllm":
                return self._vllm(now, values)
            if endpoint == "ray":
                return self._ray(now, values)
        raise ValueError(f"unknown demo endpoint: {endpoint}")

    def _gpu_node(self, node: str, now: float, phase: str, value: dict[str, float]) -> list[GaugeSample]:
        samples = [
            GaugeSample("telemetry_gpu_sample_timestamp_seconds", "Synthetic GPU sample timestamp.", time.time()),
            GaugeSample("live_demo_phase_info", "Current synthetic demo phase.", 1, {"phase": phase}),
        ]
        samples.extend(self._host(node, now, value))
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
            samples.extend(self._agent_rl(now))
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
            GaugeSample("node_time_seconds", "Synthetic synchronized node clock.", time.time()),
            GaugeSample("node_timex_sync_status", "Synthetic clock synchronization status.", 1),
        ]
        for cpu in range(4):
            for mode, rate in (("idle", .65), ("user", .25), ("system", .10)):
                samples.append(GaugeSample("node_cpu_seconds_total", "Synthetic host CPU counter.",
                    self._counter(node, f"cpu-{cpu}-{mode}", rate, now), {"cpu": str(cpu), "mode": mode}, kind="counter"))
        for name in ("node_vmstat_pswpin", "node_vmstat_pswpout"):
            samples.append(GaugeSample(name, "Synthetic swap page counter.",
                                       self._counter(node, name, 2, now), kind="counter"))
        for name, rate in (("node_infiniband_port_data_received_bytes_total", value["rx"] * 1e9 / 8),
                           ("node_infiniband_port_data_transmitted_bytes_total", value["tx"] * 1e9 / 8)):
            samples.append(GaugeSample(name, "Synthetic RDMA port counter.",
                self._counter(node, name, rate, now), {"device": "mlx5_0", "port": "1"}, kind="counter"))
        return samples

    def _sandbox(self, now: float) -> list[GaugeSample]:
        labels = {"run_id": "verl-agent-demo", "producer": "synthetic", "role": "sandbox",
                  "worker_id": "pool-0", "runtime": "containerd", "filesystem": "overlayfs", "deployment": "colocated"}
        pressure = .02 if _phase(now - self.started)[0] == "training" else .43
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

    def _vllm(self, now: float, value: dict[str, float]) -> list[GaugeSample]:
        labels = {"model_name": "synthetic-model"}
        waiting = 8 if value["busy"] > .5 else 1
        samples = [GaugeSample(name, "Synthetic vLLM gauge.", number, labels) for name, number in {
            "vllm:num_requests_waiting": waiting, "vllm:num_requests_running": 4,
            "vllm:kv_cache_usage_perc": .92 if waiting > 1 else .45}.items()]
        for name, rate in {"vllm:num_preemptions_total": .2 if waiting > 1 else 0,
                           "vllm:prompt_tokens_total": 800, "vllm:generation_tokens_total": value["tokens"]}.items():
            samples.append(GaugeSample(name, "Synthetic vLLM counter.", self._counter("vllm", name, rate, now), labels, kind="counter"))
        for direction, rate in (("GPU_to_CPU", .2 * _GIB), ("CPU_to_GPU", .1 * _GIB)):
            samples.append(GaugeSample("vllm:kv_offload_total_bytes_total", "Synthetic KV offload transfer counter.",
                self._counter("vllm", direction, rate, now), {**labels, "transfer_type": direction}, kind="counter"))
        # Cumulative bucket COUNTERS, not percentile gauges. Rates preserve the
        # ordering needed by the dashboards' real histogram_quantile queries.
        distributions = {
            "time_to_first_token_seconds": (.25, .65, .85, .99, 1) if waiting > 1 else (.7, .96, .99, 1, 1),
            "request_queue_time_seconds": (.4, .75, .9, .99, 1) if waiting > 1 else (.85, .99, 1, 1, 1),
            "e2e_request_latency_seconds": (.05, .2, .55, .97, 1),
        }
        for name, fractions in distributions.items():
            for bound, fraction in zip(("0.1", "0.5", "1", "5", "+Inf"), fractions):
                metric = f"vllm:{name}_bucket"
                samples.append(GaugeSample(metric, "Synthetic cumulative latency bucket counter.",
                    self._counter("vllm", name + bound, fraction * 10, now), {**labels, "le": bound}, kind="counter"))
        return samples

    def _ray(self, now: float, value: dict[str, float]) -> list[GaugeSample]:
        labels = {"SessionName": "synthetic-session"}
        samples = []
        for metric in ("ray_tasks", "ray_actors"):
            for state, count in (("RUNNING", 8), ("PENDING_ARGS_AVAIL", 2)):
                samples.append(GaugeSample(metric, "Synthetic Ray state gauge.", count, {**labels, "State": state}))
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

    def _storage_node(self, node: str, now: float, phase: str, value: dict[str, float]) -> list[GaugeSample]:
        samples = self._host(node, now, value)
        samples.extend([
            GaugeSample("node_filesystem_size_bytes", "Synthetic data filesystem size.", 64 * 1024 * _GIB, {"mountpoint": "/mnt/data", "fstype": "xfs"}),
            GaugeSample("node_filesystem_avail_bytes", "Synthetic data filesystem free space.", 39 * 1024 * _GIB, {"mountpoint": "/mnt/data", "fstype": "xfs"}),
        ])
        for index in range(self.storage["ssds_per_node"]):
            labels = {"device": f"nvme{index}n1"}
            for name, rate in (("node_disk_read_bytes_total", value["read"] * _GIB),
                               ("node_disk_written_bytes_total", value["write"] * _GIB),
                               ("node_disk_reads_completed_total", value["read"] * _GIB / (256 * 1024)),
                               ("node_disk_writes_completed_total", value["write"] * _GIB / (256 * 1024)),
                               ("node_disk_io_time_seconds_total", value["busy"])):
                samples.append(GaugeSample(name, "Synthetic disk counter.", self._counter(node, name + str(index), rate, now), labels, kind="counter"))
            for name, rate in (("node_network_receive_bytes_total", value["rx"] * 1e9 / 8),
                               ("node_network_transmit_bytes_total", value["tx"] * 1e9 / 8)):
                if index == 0:
                    samples.append(GaugeSample(name, "Synthetic RoCE counter.", self._counter(node, name, rate, now), {"device": "roce0"}, kind="counter"))
            samples += [
                GaugeSample("smartctl_device", "Synthetic SSD inventory.", 1, {**labels, "protocol": "NVMe", "model_name": "Demo SSD", "firmware_version": "1.0"}),
                GaugeSample("smartctl_device_smart_status", "Synthetic SSD SMART status.", 1, labels),
                GaugeSample("smartctl_device_smartctl_exit_status", "Synthetic smartctl exit status.", 0, labels),
                GaugeSample("smartctl_device_critical_warning", "Synthetic SSD critical warning.", 0, labels),
                GaugeSample("smartctl_device_temperature", "Synthetic SSD temperature.", 38 + index, {**labels, "temperature_type": "current"}),
                GaugeSample("smartctl_device_percentage_used", "Synthetic SSD endurance used.", 3 + index, labels),
                GaugeSample("smartctl_device_available_spare", "Synthetic SSD available spare.", 100 - index, labels),
                GaugeSample("smartctl_device_media_errors", "Synthetic SSD media errors.", 0, labels),
                GaugeSample("smartctl_device_bytes_written", "Synthetic SSD host bytes written.", self._counter(node, f"ssd-{index}", value["write"] * _GIB / self.storage["ssds_per_node"], now), labels, kind="counter"),
            ]
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
            samples.append(GaugeSample("telemetry_topology_component_info", "Synthetic topology component.", 1, {"kind": "storage", "component": node, "role": "storage-node"}))
            for ssd in range(self.storage["ssds_per_node"]):
                samples += [
                    GaugeSample("telemetry_topology_component_info", "Synthetic topology component.", 1, {"kind": "storage", "component": f"{node}/nvme{ssd}n1", "role": "ssd"}),
                    GaugeSample("telemetry_topology_edge_info", "Synthetic topology edge.", 1, {"kind": "storage", "source": node, "destination": f"{node}/nvme{ssd}n1", "relation": "attached"}),
                ]
            samples.append(GaugeSample("telemetry_topology_edge_info", "Synthetic topology edge.", 1, {"kind": "storage", "source": node, "destination": "roce-fabric", "relation": self.network}))
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
    lines += targets("telemetry", [*demo.gpu["gpu_nodes"], *demo.storage["storage_nodes"], "topology"])
    lines += targets("storage-smart", demo.storage["storage_nodes"], "          storage_system: demo")
    lines += ["  - job_name: native", "    static_configs:"]
    for source in ("vllm", "ray"):
        node = demo.gpu["gpu_nodes"][0]
        lines += [f"      - targets: ['{address}']", "        labels:", f"          cluster: {cluster}",
                  "          data_origin: synthetic", f"          telemetry_source: {source}",
                  f"          node: {node}", f"          nodename: {node}", f"          instance: synthetic-{source}-0",
                  f"          __metrics_path__: /metrics/{source}"]
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
    args = parser.parse_args()
    try:
        demo = Demo(args.topology_dir)
        if args.write_prometheus_config:
            args.write_prometheus_config.write_text(prometheus_config(demo, args.listen, args.cluster), encoding="utf-8")
            return
        serve(demo, args.listen)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
