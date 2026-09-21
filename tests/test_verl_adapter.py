import json
from pathlib import Path

from post_training_telemetry.adapters.verl import (
    VerlMetricsAdapter,
    bridge_records,
)
from post_training_telemetry.metrics import MetricEmitter


def test_translate_verl_stage_and_scalar_metrics() -> None:
    samples = VerlMetricsAdapter.translate(
        {
            "timing_s/gen": 1.25,
            "timing_s/update_actor": 0.5,
            "timing_s/update_weights": 0.2,
            "timing_per_token_ms/gen": 2.0,
            "perf/throughput": 321.0,
            "critic/score/mean": 0.75,
            "response_length/mean": 24.0,
            "unknown/native_metric": 9.0,
            "not_scalar": [1, 2],
        }
    )

    by_name = {}
    for sample in samples:
        by_name.setdefault(sample.name, []).append(sample)
    assert {
        sample.labels["phase"]
        for sample in by_name["rl_stage_duration_seconds"]
    } == {"rollout", "actor_update", "weight_sync"}
    assert by_name["rl_stage_time_per_token_seconds"][0].value == 0.002
    assert by_name["training_tokens_per_second_per_gpu"][0].value == 321.0
    assert by_name["reward_score_mean"][0].value == 0.75
    assert "unknown/native_metric" not in by_name


def test_bridge_verl_file_records_to_worker_snapshot(tmp_path: Path) -> None:
    emitter = MetricEmitter(
        tmp_path,
        run_id="grpo-001",
        producer="verl",
        role="trainer",
        worker_id="driver",
        node="gpu-a",
    )
    records = [
        {"step": 1, "data": {"timing_s/gen": 1.0}},
        {"step": "bad", "data": {"timing_s/gen": 2.0}},
        {
            "step": 2,
            "data": {
                "timing_s/gen": 0.8,
                "timing_s/reward": 0.1,
                "perf/time_per_step": 1.5,
            },
        },
    ]

    assert bridge_records(records, VerlMetricsAdapter(emitter)) == 2
    snapshot = json.loads(
        (tmp_path / "verl-trainer-driver.json").read_text(encoding="utf-8")
    )
    assert snapshot["step"] == 2
    assert {
        (sample["name"], sample["labels"].get("phase"))
        for sample in snapshot["samples"]
    } == {
        ("rl_stage_duration_seconds", "rollout"),
        ("rl_stage_duration_seconds", "reward"),
        ("training_step_time_seconds", "rl_step"),
    }
