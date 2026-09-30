import json
from pathlib import Path

import pytest

from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.identity import producer_filename_stem


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

def test_producer_filename_encodes_component_boundaries() -> None:
    first = producer_filename_stem("a-b", "c", "d")
    second = producer_filename_stem("a", "b-c", "d")
    assert first != second
    assert producer_filename_stem("agent", "rollout", "0") == "agent-rollout-0"


def test_distinct_hyphenated_event_producers_do_not_share_stream(tmp_path: Path) -> None:
    recorders = [EventRecorder(tmp_path, CorrelationContext(
        run_id="r", producer=producer, role=role, worker_id="d", node="n"))
        for producer, role in (("a-b", "c"), ("a", "b-c"))]
    for recorder in recorders:
        recorder.event("tool.call", phase="tool")
    assert recorders[0].path != recorders[1].path
    assert [json.loads(recorder.path.read_text())["producer"] for recorder in recorders] == ["a-b", "a"]


def test_span_navigation_window_encloses_exact_nanosecond_boundary(tmp_path):
    import json
    from xlayer_telemetry.events import CorrelationContext, EventRecorder
    clock = iter((1000000200, 2000000800)).__next__
    recorder = EventRecorder(tmp_path, CorrelationContext(
        run_id='r', producer='app', role='rollout', worker_id='0', node='n'), clock_ns=clock)
    with recorder.span('tool.call', phase='environment'):
        pass
    record = json.loads(next(tmp_path.glob('*.jsonl')).read_text())
    assert record['start_time_ms'] == 1000
    assert record['end_time_ms'] == 2001
    assert record['start_time_unix_nano'] == 1000000200
    assert record['end_time_unix_nano'] == 2000000800
