from pathlib import Path

from xlayer_telemetry.live_demo import Demo, prometheus_config


ROOT = Path(__file__).parents[1]


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
    assert config.count("__metrics_path__:") == 13
    assert "nodename: gpu-node-0" in config
    assert "job_name: telemetry" in config
    assert "job_name: storage-smart" in config
