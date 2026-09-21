import json
from pathlib import Path

from post_training_telemetry.show_run import summarize


def test_summarize_includes_manifest_context_and_recent_events(tmp_path: Path) -> None:
    (tmp_path / "telemetry-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "grpo-1",
                "sources": {"vllm": "http://rollout:8000/metrics"},
            }
        ),
        encoding="utf-8",
    )
    metrics = tmp_path / "telemetry-metrics"
    metrics.mkdir()
    (metrics / "verl-trainer-driver.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "producer": "verl",
                "role": "trainer",
                "worker_id": "driver",
                "node": "gpu-a",
                "rank": 0,
                "local_rank": 0,
                "gpu": "0",
                "step": 3,
                "samples": [
                    {
                        "name": "rl_stage_duration_seconds",
                        "value": 1.2,
                        "labels": {"phase": "rollout"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    events = tmp_path / "telemetry-events"
    events.mkdir()
    (events / "agent-agent-0.jsonl").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "record_type": "span",
                "run_id": "grpo-1",
                "role": "agent",
                "worker_id": "0",
                "phase": "tool_interaction",
                "step": 3,
                "name": "tool.call",
                "duration_seconds": 0.25,
                "status": "ok",
                "trace_id": "trace-1",
                "start_time_unix_nano": 10,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    output = summarize(tmp_path)

    assert "[telemetry-manifest.json]" in output
    assert "verl/trainer worker driver node=gpu-a rank=0 local_rank=0 gpu=0" in output
    assert "rl_stage_duration_seconds{phase=rollout}=1.2" in output
    assert "phase=tool_interaction agent/0 tool.call duration=0.25s" in output
    assert "trace_id=trace-1" in output
