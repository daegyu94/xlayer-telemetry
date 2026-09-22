import json
from pathlib import Path

import pytest

from xlayer_telemetry.events import CorrelationContext, EventRecorder


def test_span_records_correlation_duration_and_error(tmp_path: Path) -> None:
    times = iter((1_000_000_000, 1_250_000_000, 2_000_000_000, 2_100_000_000))
    recorder = EventRecorder(
        tmp_path,
        CorrelationContext(
            run_id="run-1",
            producer="verl",
            role="agent",
            worker_id="3",
            node="gpu-a",
            rank=3,
            local_rank=1,
            gpu="GPU-abc",
        ),
        clock_ns=lambda: next(times),
    )

    with recorder.span(
        "rollout.generate",
        phase="rollout",
        step=7,
        attributes={"request_id": "req-1"},
        trace_id="trace-1",
    ) as identity:
        assert identity.trace_id == "trace-1"

    with pytest.raises(RuntimeError):
        with recorder.span("tool.call", phase="tool_interaction", step=7):
            raise RuntimeError("tool failed")

    records = [
        json.loads(line)
        for line in recorder.path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records[0]["span_id"]) == 16
    assert records[0]["run_id"] == "run-1"
    assert records[0]["phase"] == "rollout"
    assert records[0]["rank"] == 3
    assert records[0]["gpu"] == "GPU-abc"
    assert records[0]["duration_seconds"] == 0.25
    assert records[0]["status"] == "ok"
    assert records[0]["attributes"] == {"request_id": "req-1"}
    assert records[1]["status"] == "error"
    assert records[1]["attributes"]["error_type"] == "RuntimeError"


def test_event_recorder_disables_after_unserializable_attribute(
    tmp_path: Path,
    capsys,
) -> None:
    recorder = EventRecorder(
        tmp_path,
        CorrelationContext(
            run_id="run-1",
            producer="agent",
            role="agent",
            worker_id="0",
            node="gpu-a",
        ),
    )

    recorder.event(
        "tool.result",
        phase="tool_interaction",
        attributes={"bad": object()},
    )
    recorder.event("tool.result", phase="tool_interaction")

    assert recorder.disabled
    assert capsys.readouterr().err.count("export disabled") == 1
    assert not recorder.path.exists()
