import sys

import pytest

from xlayer_telemetry import gpu_sampler


@pytest.mark.parametrize("value", ["[N/A]", "Not Supported", "nan", ""])
def test_unavailable_is_null(value):
    assert gpu_sampler.optional_number(value) is None


def test_zero_is_still_a_measurement():
    assert gpu_sampler.optional_number("0") == 0


def test_device_and_process_memory_are_independent(monkeypatch):
    def fake_query(kind, fields):
        if kind == "gpu":
            return [["0", "0", "5", "45", "208", "[N/A]", "[N/A]"]]
        return [["GPU-example", "123", "python", "412"]]

    monkeypatch.setattr(gpu_sampler, "query", fake_query)
    value = gpu_sampler.snapshot()
    assert value["gpus"][0]["memory.used"] is None
    assert value["compute_processes"][0]["used_gpu_memory_mib"] == 412


def test_sampler_runs_without_a_default_deadline(tmp_path, monkeypatch):
    output = tmp_path / "gpu.jsonl"
    samples = iter(
        (
            {"timestamp": 1.0, "host_memory": {}, "gpus": [], "compute_processes": []},
            RuntimeError("stop after first sample"),
        )
    )

    def snapshot():
        value = next(samples)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(gpu_sampler, "snapshot", snapshot)
    monkeypatch.setattr(gpu_sampler.time, "sleep", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["gpu_sampler", "--output", str(output)])

    with pytest.raises(RuntimeError, match="stop after first sample"):
        gpu_sampler.main()

    assert output.read_text().count("\n") == 1
