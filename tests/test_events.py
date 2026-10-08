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


def test_worker_policy_applied_is_explicit_and_cannot_be_relabelled(tmp_path):
    context = CorrelationContext(run_id="r", producer="native", role="rollout", worker_id="worker-3", node="gpu-b", policy_version=999)
    recorder = EventRecorder(tmp_path, context, clock_ns=lambda: 1230000000)
    recorder.policy_applied(7, step=5, trace_id="worker-trace", attributes={"policy_scope": "trainer_produced", "source": "worker_callback"})
    record = json.loads(recorder.path.read_text())
    assert record["name"] == "weights.applied" and record["phase"] == "weight_sync"
    assert record["policy_version"] == 7 and record["policy_version_source"] == "producer_reported"
    assert record["node"] == "gpu-b" and record["worker_id"] == "worker-3"
    assert record["attributes"] == {"policy_scope": "worker_applied", "source": "worker_callback"}
    assert record["step"] == 5 and record["trace_id"] == "worker-trace"


@pytest.mark.parametrize("version", [None, -1, True, 3.0, "3"])
def test_policy_applied_rejects_missing_or_inferred_version(tmp_path, version):
    recorder = EventRecorder(tmp_path, CorrelationContext(run_id="r", producer="native", role="rollout", worker_id="0", node="gpu-a", policy_version=999))
    with pytest.raises(ValueError, match="nonnegative integer"):
        recorder.policy_applied(version)
    assert not recorder.path.exists()


def test_policy_applied_reuses_existing_bounded_sdk_failure_behavior(tmp_path, capsys):
    recorder = EventRecorder(tmp_path, CorrelationContext(run_id="r", producer="native", role="rollout", worker_id="0", node="gpu-a"))
    recorder.policy_applied(0, attributes={"bad": object()})
    recorder.policy_applied(1)
    assert recorder.disabled and not recorder.path.exists()
    assert capsys.readouterr().err.count("export disabled") == 1


@pytest.mark.parametrize("field", ["node", "worker_id"])
def test_policy_applied_requires_explicit_worker_and_node_context(tmp_path, field):
    context = CorrelationContext(run_id="r", producer="native", role="rollout", worker_id="0", node="gpu-a")
    # A malformed external context must not bypass the helper's identity contract.
    object.__setattr__(context, field, "")
    recorder = EventRecorder(tmp_path, context)
    with pytest.raises(ValueError, match="explicit node and worker"):
        recorder.policy_applied(7)
    assert not recorder.path.exists()
