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
    value = gpu_sampler.snapshot(include_processes=True)
    assert value["gpus"][0]["utilization.gpu"] == 47
    assert value["compute_processes"] == []
    assert value["compute_processes_error"] == type(failure).__name__


def test_device_and_process_memory_are_independent(monkeypatch):
    def fake_query(kind, fields):
        if kind == "gpu":
            return [["0", "0", "5", "45", "208", "[N/A]", "[N/A]"]]
        return [["GPU-example", "123", "python", "412"]]

    monkeypatch.setattr(gpu_sampler, "query", fake_query)
    value = gpu_sampler.snapshot(include_processes=True)
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

    def snapshot(**kwargs):
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
    def collect(device_rows, process_rows=(), *, process_metrics=True):
        monkeypatch.setattr(
            gpu_sampler, "query", lambda kind, fields: device_rows if kind == "gpu" else process_rows
        )
        monkeypatch.setattr(gpu_sampler, "time", SimpleNamespace(
            monotonic=iter((0, 0, 0, 2)).__next__, time=lambda: 123, sleep=lambda _: None,
        ))
        monkeypatch.setattr(sys, "argv", [
            "gpu_sampler", "--output", str(tmp_path / "gpu.jsonl"),
            "--textfile-dir", str(tmp_path), "--duration", "1",
        ] + (["--process-metrics"] if process_metrics else []))
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


def test_default_collection_does_not_query_or_export_pids(gpu_textfile):
    text = gpu_textfile([["0", "47", "100", "45", "900", "1024", "8192", "GPU-1"]],
                        [["GPU-1", "1", "python", "256"]], process_metrics=False)
    assert "telemetry_gpu_process_memory_bytes" not in text
    assert "telemetry_gpu_process_collection_enabled 0" in text
    assert "telemetry_gpu_process_samples_truncated" not in text


def test_process_collection_cap_and_malformed_device_identity(monkeypatch):
    calls = []
    def query(kind, fields):
        calls.append(kind)
        if kind == "gpu":
            return [["N/A"], ["0"]]
        return [["GPU-1", str(pid), "python", "100"] for pid in range(1, 6)]
    monkeypatch.setattr(gpu_sampler, "query", query)
    value = gpu_sampler.snapshot()
    assert calls == ["gpu"]
    assert len(value["gpus"]) == 1 and value["gpus"][0]["memory.used"] is None
    value = gpu_sampler.snapshot(include_processes=True, max_processes=2)
    assert len(value["compute_processes"]) == 2
    assert value["compute_processes_truncated"] == 3


@pytest.mark.parametrize("argument,value", [("--interval", "nan"), ("--interval", "inf"),
    ("--duration", "nan"), ("--duration", "inf"), ("--max-processes", "0"), ("--max-processes", "4097")])
def test_invalid_collection_budget_fails_before_creating_output(tmp_path, monkeypatch, argument, value):
    output = tmp_path / "gpu.jsonl"
    monkeypatch.setattr(sys, "argv", ["gpu_sampler", "--output", str(output), argument, value])
    with pytest.raises(SystemExit):
        gpu_sampler.main()
    assert not output.exists()
