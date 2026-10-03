"""Optional SDK setup must not interrupt the workload on invalid configuration."""

import io
import json
import sys

import pytest

from xlayer_telemetry.events import CorrelationContext, EventRecorder
from xlayer_telemetry.metrics.emitter import Metric, MetricEmitter


@pytest.fixture(params=[EventRecorder, MetricEmitter], ids=["events", "metrics"])
def sdk_environment(request, monkeypatch, tmp_path):
    for name in (
        "TELEMETRY_EVENTS_DIR",
        "TELEMETRY_METRICS_DIR",
        "TELEMETRY_RUN_ID",
        "TELEMETRY_NODE",
        "TELEMETRY_ASYNC_IO",
        "TELEMETRY_IO_QUEUE_CAPACITY",
        "TELEMETRY_IO_QUEUE_BYTES",
        "TELEMETRY_IO_FLUSH_TIMEOUT",
        "RANK",
        "LOCAL_RANK",
        "CUDA_VISIBLE_DEVICES",
    ):
        monkeypatch.delenv(name, raising=False)
    sdk_class = request.param
    directory_key = (
        "TELEMETRY_EVENTS_DIR" if sdk_class is EventRecorder else "TELEMETRY_METRICS_DIR"
    )
    monkeypatch.setenv(directory_key, str(tmp_path))
    monkeypatch.setenv("TELEMETRY_RUN_ID", "run-1")
    monkeypatch.setenv("TELEMETRY_NODE", "node-1")
    return sdk_class, directory_key


@pytest.mark.parametrize(
    "overrides,diagnostic",
    [
        ({"LOCAL_RANK": "-2", "CUDA_VISIBLE_DEVICES": "0"}, "local_rank"),
        ({"LOCAL_RANK": "not-a-rank"}, "invalid literal"),
    ],
    ids=["negative-local-rank-outside-list", "nonnumeric-local-rank"],
)
def test_from_env_disables_for_invalid_configuration(
    sdk_environment, monkeypatch, overrides, diagnostic, capsys, tmp_path
):
    sdk_class, _ = sdk_environment
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)

    assert sdk_class.from_env(producer="app", role="worker") is None

    warning = capsys.readouterr().err
    assert warning.count("export disabled") == 1
    assert diagnostic in warning
    assert not list(tmp_path.iterdir())


class FailingStderr:
    def write(self, text):
        raise OSError("stderr is unavailable")


@pytest.mark.parametrize("failure", ["closed", "oserror"])
@pytest.mark.parametrize("configuration", ["invalid-rank", "missing-run"])
def test_from_env_warning_failure_does_not_interrupt_initialization(
    sdk_environment, monkeypatch, failure, configuration
):
    sdk_class, _ = sdk_environment
    if configuration == "invalid-rank":
        monkeypatch.setenv("LOCAL_RANK", "not-a-rank")
    else:
        monkeypatch.delenv("TELEMETRY_RUN_ID")
    stderr = FailingStderr()
    if failure == "closed":
        stderr = io.StringIO()
        stderr.close()

    with monkeypatch.context() as patch:
        patch.setattr(sys, "stderr", stderr)
        assert sdk_class.from_env(producer="app", role="worker") is None


def test_from_env_is_silent_when_not_configured(sdk_environment, monkeypatch, capsys):
    sdk_class, directory_key = sdk_environment
    monkeypatch.delenv(directory_key)
    monkeypatch.delenv("TELEMETRY_RUN_ID")

    assert sdk_class.from_env(producer="app", role="worker") is None
    assert capsys.readouterr().err == ""


def test_from_env_preserves_context_in_exported_records(
    sdk_environment, monkeypatch, capsys
):
    sdk_class, _ = sdk_environment
    monkeypatch.setenv("RANK", "3")
    monkeypatch.setenv("LOCAL_RANK", "1")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", " GPU-first , GPU-second ")

    sdk = sdk_class.from_env(producer="app", role="worker")
    assert sdk is not None
    try:
        if isinstance(sdk, EventRecorder):
            sdk.event("work.complete", phase="training")
            destination = sdk.path
        else:
            destination = sdk.emit(step=1, samples=[Metric("work_total", 1, kind="counter")])
        assert destination is not None
        record = json.loads(destination.read_text(encoding="utf-8"))
        assert record["run_id"] == "run-1"
        assert record["node"] == "node-1"
        assert record["rank"] == 3
        assert record["worker_id"] == "3"
        assert record["local_rank"] == 1
        assert record["gpu"] == "GPU-second"
        assert not sdk.disabled
        assert capsys.readouterr().err == ""
    finally:
        assert sdk.close()


def test_context_from_env_reports_invalid_rank_as_validation_error(monkeypatch):
    monkeypatch.setenv("TELEMETRY_RUN_ID", "run-1")
    monkeypatch.setenv("TELEMETRY_NODE", "node-1")
    monkeypatch.delenv("RANK", raising=False)
    monkeypatch.setenv("LOCAL_RANK", "-2")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")

    with pytest.raises(ValueError, match="local_rank must be nonnegative"):
        CorrelationContext.from_env(producer="app", role="worker")


@pytest.mark.parametrize("constructor", [CorrelationContext, MetricEmitter], ids=["context", "emitter"])
def test_direct_constructor_preserves_validation(constructor, tmp_path):
    arguments = dict(
        run_id="run-1", producer="app", role="worker", worker_id="0", node="node-1", local_rank=-2
    )
    if constructor is MetricEmitter:
        arguments["directory"] = tmp_path

    with pytest.raises(ValueError, match="local_rank must be nonnegative"):
        constructor(**arguments)
