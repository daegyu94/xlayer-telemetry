from pathlib import Path
import subprocess
import sys

from xlayer_telemetry.demos.live import Demo, prometheus_config


ROOT = Path(__file__).parents[1]


def test_cli_finds_checkout_topology_without_an_explicit_directory(tmp_path) -> None:
    output = tmp_path / "prometheus.yml"
    result = subprocess.run(
        [sys.executable, "-m", "xlayer_telemetry.demos.live",
         "--write-prometheus-config", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    config = output.read_text()
    assert "nodename: gpu-node-0" in config
    assert "nodename: storage-node-0" in config
    assert config.count("__metrics_path__:") == 24


def test_demo_matches_the_b300_and_storage_topology() -> None:
    demo = Demo(ROOT / "examples" / "live-demo")

    gpu = demo.metrics("gpu-node-0")
    storage = demo.metrics("storage-node-0")
    topology = demo.metrics("topology")
    config = prometheus_config(demo, "127.0.0.1:19110")

    assert len([sample for sample in gpu if sample.name == "telemetry_gpu_utilization_percent"]) == 8
    agent = [sample for sample in gpu if sample.labels.get("run_id") == "verl-agent-demo"]
    assert {sample.name for sample in agent} >= {
        "agent_tool_call_duration_seconds",
        "policy_version_lag",
        "reward_mean",
        "rl_stage_duration_seconds",
        "training_step",
    }
    assert {sample.labels.get("phase") for sample in agent if sample.name == "rl_stage_duration_seconds"} == {
        "actor_update", "critic_update", "reward", "rl_step", "rollout", "weight_sync",
    }
    assert len([sample for sample in storage if sample.name == "smartctl_device"]) == 4
    assert len([sample for sample in topology if sample.labels.get("role") == "B300"]) == 32
    assert len([sample for sample in topology if sample.labels.get("role") == "ssd"]) == 32
    assert config.count("__metrics_path__:") == 24
    assert "nodename: gpu-node-0" in config
    assert "job_name: telemetry" in config
    assert "job_name: storage-smart" in config


def test_all_prometheus_dashboard_metrics_have_synthetic_producers():
    import json
    import re
    from xlayer_telemetry.metrics.prometheus import format_gauges
    demo = Demo(ROOT / "examples/live-demo")
    # These are emitted by the real Prometheus scrape loop, not by exporters.
    names = {"up", "scrape_duration_seconds", "scrape_samples_scraped",
             "scrape_samples_post_metric_relabeling"}
    for endpoint in [*demo.gpu["gpu_nodes"], *demo.storage["storage_nodes"], "topology", "vllm", "ray", "dcgm"]:
        samples = demo.metrics(endpoint)
        names.update(sample.name for sample in samples)
        format_gauges(samples)  # Reject conflicting definitions and invalid samples.
        identities = [(sample.name, tuple(sorted(sample.labels.items()))) for sample in samples]
        assert len(identities) == len(set(identities))
    def panels(items):
        for panel in items:
            yield panel
            yield from panels(panel.get("panels", []))
    for path in (ROOT / "examples/dashboards").glob("*.json"):
        for panel in panels(json.loads(path.read_text())["panels"]):
            if panel.get("datasource", {}).get("uid") == "telemetry-prometheus":
                for query in panel["targets"]:
                    required = set(re.findall(r"([a-zA-Z_:][a-zA-Z0-9_:]*)\{", query["expr"]))
                    assert required <= names, (path.name, panel["title"], required - names)


def test_native_and_gpu_labels_match_actual_dashboard_filters():
    demo = Demo(ROOT / "examples/live-demo")
    samples = demo.metrics("gpu-node-0")
    assert all("gpu" in s.labels for s in samples if s.name == "telemetry_gpu_process_memory_bytes")
    assert all("sandbox_id" not in s.labels and "trajectory_id" not in s.labels for s in samples)
    config = prometheus_config(demo, "127.0.0.1:19110")
    assert "job_name: native" in config
    assert "telemetry_source: vllm" in config and "instance: synthetic-vllm-0" in config
    assert "telemetry_source: ray" in config and "node: gpu-node-0" in config
    assert "telemetry_source: dcgm" in config and "instance: synthetic-dcgm-0" in config
    assert config.count("data_origin: synthetic") == 24


def test_new_pressure_and_dcgm_fixtures_are_bounded_and_monotonic(monkeypatch):
    import xlayer_telemetry.demos.live as live
    demo = Demo(ROOT / "examples/live-demo")
    origin = demo.started
    endpoints = ("gpu-node-0", "storage-node-0", "vllm", "ray", "dcgm")
    monkeypatch.setattr(live.time, "monotonic", lambda: origin + 1)
    first = {endpoint: demo.metrics(endpoint) for endpoint in endpoints}
    monkeypatch.setattr(live.time, "monotonic", lambda: origin + 41)
    for endpoint in endpoints:
        latest = demo.metrics(endpoint)
        before = {(s.name, tuple(sorted(s.labels.items()))): s for s in first[endpoint]}
        assert len(latest) < 300
        for sample in latest:
            assert "synthetic" in sample.help.lower()
            if sample.kind == "counter":
                assert sample.value >= before[(sample.name, tuple(sorted(sample.labels.items())))].value
    samples = demo.metrics("dcgm")
    assert len({s.labels["gpu"] for s in samples}) == 8
    assert all(s.kind == "gauge" for s in samples if s.name in {
        "DCGM_FI_PROF_PCIE_RX_BYTES", "DCGM_FI_PROF_PCIE_TX_BYTES", "DCGM_FI_DEV_XID_ERRORS"})


def test_synthetic_counters_increase_and_histogram_buckets_remain_cumulative(monkeypatch):
    import xlayer_telemetry.demos.live as live
    demo = Demo(ROOT / "examples/live-demo")
    origin = demo.started
    monkeypatch.setattr(live.time, "monotonic", lambda: origin + 1)
    first = demo.metrics("vllm")
    monkeypatch.setattr(live.time, "monotonic", lambda: origin + 3)
    second = demo.metrics("vllm")
    for before, after in zip(first, second):
        if after.kind == "counter":
            assert after.value >= before.value
    buckets = [s.value for s in second if s.name == "vllm:time_to_first_token_seconds_bucket"]
    assert sorted(buckets) == buckets and buckets[-1] > 0
    before = [s.value for s in demo.metrics("ray") if s.name.endswith("eviction_total")]
    monkeypatch.setattr(live.time, "monotonic", lambda: origin + 40)
    after = [s.value for s in demo.metrics("ray") if s.name.endswith("eviction_total")]
    assert after[0] > before[0]


def test_custom_demo_cluster_and_reserved_endpoint_validation(tmp_path):
    import json
    import pytest
    demo = Demo(ROOT / "examples/live-demo")
    assert "cluster: custom-demo" in prometheus_config(demo, "127.0.0.1:19110", "custom-demo")
    with pytest.raises(ValueError, match="cluster"):
        prometheus_config(demo, "127.0.0.1:19110", "bad\ncluster")
    for name in ("gpu_topology.yaml", "storage_topology.yaml"):
        (tmp_path / name).write_text((ROOT / "examples/live-demo" / name).read_text())
    path = tmp_path / "gpu_topology.yaml"
    topology = json.loads(path.read_text())
    topology["gpu_nodes"][0] = "vllm"
    path.write_text(json.dumps(topology))
    with pytest.raises(ValueError, match="unique"):
        Demo(tmp_path)
