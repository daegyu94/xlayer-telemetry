import sys
import subprocess
from types import SimpleNamespace

import pytest

from xlayer_telemetry.collectors import gpu_sampler


@pytest.mark.parametrize("value", ["[N/A]", "Not Supported", "nan", ""])
def test_unavailable_is_null(value):
    assert gpu_sampler.optional_number(value) is None


def test_zero_is_still_a_measurement():
    assert gpu_sampler.optional_number("0") == 0


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("nvidia-smi", 10), OSError("unavailable")])
def test_optional_process_query_failure_preserves_device_samples(monkeypatch, failure):
    def query(kind, fields):
        if kind == "gpu":
            return [["0", "47", "100", "45", "900", "[N/A]", "[N/A]", "GPU-example"]]
        raise failure
    monkeypatch.setattr(gpu_sampler, "query", query)
    value = gpu_sampler.snapshot()
    assert value["gpus"][0]["utilization.gpu"] == 47
    assert value["compute_processes"] == []
    assert value["compute_processes_error"] == type(failure).__name__


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


@pytest.fixture
def gpu_textfile(tmp_path, monkeypatch):
    def collect(device_rows, process_rows=()):
        monkeypatch.setattr(
            gpu_sampler, "query", lambda kind, fields: device_rows if kind == "gpu" else process_rows
        )
        monkeypatch.setattr(gpu_sampler, "time", SimpleNamespace(
            monotonic=iter((0, 0, 0, 2)).__next__, time=lambda: 123, sleep=lambda _: None,
        ))
        monkeypatch.setattr(sys, "argv", [
            "gpu_sampler", "--output", str(tmp_path / "gpu.jsonl"),
            "--textfile-dir", str(tmp_path), "--duration", "1",
        ])
        gpu_sampler.main()
        return (tmp_path / "gpu.prom").read_text()
    return collect


@pytest.mark.parametrize("used_mib", [0, 2048])
def test_device_memory_exports_bytes_and_omits_only_unavailable_fields(gpu_textfile, used_mib):
    text = gpu_textfile([
        ["0", "47", "100", "45", "900", str(used_mib), "8192", "GPU-first"],
        ["1", "60", "110", "46", "950", "[N/A]", "16384", "GPU-second"],
    ])
    measurements = {line.split(" ", 1)[0]: float(line.rsplit(" ", 1)[1])
                    for line in text.splitlines() if not line.startswith("#")}

    assert measurements['telemetry_gpu_memory_used_bytes{gpu="0",gpu_uuid="GPU-first"}'] == used_mib * 1024**2
    assert measurements['telemetry_gpu_memory_total_bytes{gpu="0",gpu_uuid="GPU-first"}'] == 8192 * 1024**2
    assert measurements['telemetry_gpu_memory_total_bytes{gpu="1",gpu_uuid="GPU-second"}'] == 16384 * 1024**2
    assert 'telemetry_gpu_memory_used_bytes{gpu="1",gpu_uuid="GPU-second"}' not in measurements
    assert measurements['telemetry_gpu_utilization_percent{gpu="1",gpu_uuid="GPU-second"}'] == 60
    assert measurements["telemetry_gpu_sample_timestamp_seconds"] == 123


def test_process_memory_gpu_selector_uses_device_uuid_and_preserves_unmapped_processes(gpu_textfile):
    text = gpu_textfile(
        [
            ["0", "47", "100", "45", "900", "[N/A]", "[N/A]", "GPU-first"],
            ["1", "60", "110", "46", "950", "[N/A]", "[N/A]", "GPU-second"],
        ],
        [
            ["GPU-second", "123", "python", "512"],
            ["GPU-first", "456", "python", "256"],
            ["GPU-unmapped", "789", "python", "128"],
        ],
    )
    measurements = {line.split(" ", 1)[0]: float(line.rsplit(" ", 1)[1])
                    for line in text.splitlines() if not line.startswith("#")}

    assert measurements['telemetry_gpu_process_memory_bytes{gpu="1",gpu_uuid="GPU-second",pid="123"}'] == 512 * 1024**2
    assert measurements['telemetry_gpu_process_memory_bytes{gpu="0",gpu_uuid="GPU-first",pid="456"}'] == 256 * 1024**2
    assert measurements['telemetry_gpu_process_memory_bytes{gpu_uuid="GPU-unmapped",pid="789"}'] == 128 * 1024**2
    assert not any(name.startswith("telemetry_gpu_memory_") for name in measurements)
