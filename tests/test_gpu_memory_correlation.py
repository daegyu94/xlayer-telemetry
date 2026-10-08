"""GPU pressure evidence must refer to the same explicitly observed device."""

import pytest

from xlayer_telemetry.analysis.diagnostics import DiagnosticEngine


def series(value, *, gpu="0", uuid="GPU-0", node="n", cluster="c", **labels):
    identity = {"cluster": cluster, "nodename": node, **labels}
    if gpu is not None:
        identity["gpu"] = gpu
    if uuid is not None:
        identity["gpu_uuid"] = uuid
    return {"labels": identity, "stats": {"min": value, "mean": value, "max": value}}


class DeviceMetrics:
    def __init__(self, current, baseline=None, *, aggregate_only=False):
        self.current = current
        self.baseline = baseline or {}
        self.aggregate_only = aggregate_only

    def query_range_detail(self, query, start, end, step):
        signal = next((name for needle, name in {
            "telemetry_gpu_memory_used_bytes": "gpu_memory_usage_ratio",
            "custom_evictions": "gpu_evictions_delta",
            "telemetry_gpu_utilization_percent": "gpu_utilization_percent",
        }.items() if needle in query), None)
        items = (self.current if end == 100 else self.baseline).get(signal, [])
        values = [item["stats"]["max"] for item in items]
        return {"aggregate": {"min": min(values), "mean": sum(values)/len(values), "max": max(values)} if values else None,
                "series": [] if self.aggregate_only else items}


def analyze(current, baseline=None, *, aggregate_only=False):
    record = {"run_id": "r", "node": "n", "worker_id": "w", "record_id": "current", "step": 2,
              "observed_at": 100, "step_duration_seconds": 10,
              "analysis_window": {"start": 90, "end": 100, "accuracy": "exact"}}
    prior = {**record, "record_id": "previous", "step": 1, "observed_at": 80,
             "analysis_window": {"start": 70, "end": 80, "accuracy": "exact"}}
    engine = DiagnosticEngine({"cluster": "c", "clock": {"enabled": False},
                               "prometheus": {"url": "http://unused", "queries": {
                                   "gpu_evictions_delta": 'custom_evictions{node="{compute_node}"}'}}},
                              prometheus=DeviceMetrics(current, baseline, aggregate_only=aggregate_only), clock=lambda: 101)
    return engine.analyze(record, [prior] if baseline is not None else [])


def pressure(report):
    return next(item for item in report["candidates"] if item["id"] == "gpu_memory_pressure")


def test_different_gpus_do_not_combine_memory_and_evictions():
    report = analyze({"gpu_memory_usage_ratio": [series(.99), series(.2, gpu="1", uuid="GPU-1")],
                      "gpu_evictions_delta": [series(0), series(7, gpu="1", uuid="GPU-1")]})
    candidate = pressure(report)
    assert candidate["state"] != "strong_signal"
    assert candidate["counter_evidence"][0]["value"] == 0
    assert candidate["related_devices"] == ["0"]


def test_matching_gpu_pressure_is_preserved_and_uses_its_own_device():
    report = analyze({"gpu_memory_usage_ratio": [series(.99), series(.96, gpu="1", uuid="GPU-1")],
                      "gpu_evictions_delta": [series(0), series(7, gpu="1", uuid="GPU-1")],
                      "gpu_utilization_percent": [series(10), series(90, gpu="1", uuid="GPU-1")]},
                     {"gpu_utilization_percent": [series(90), series(90, gpu="1", uuid="GPU-1")]})
    candidate = pressure(report)
    assert candidate["state"] == "supporting_signal"
    assert any('source_freshness_unknown' in item for item in candidate['missing_evidence'])
    assert {item["labels"]["gpu"] for item in candidate["evidence"]} == {"1"}
    assert candidate["related_devices"] == ["1"]


@pytest.mark.parametrize("eviction", [
    series(7, node="other"), series(7, cluster="other"),
    series(7, uuid="GPU-other"), series(7, gpu="1"),
    series(7, gpu=None), series(7, uuid=None),
    {"labels": {}, "stats": {"max": 7}},
])
def test_mismatched_or_incomplete_device_identity_remains_missing(eviction):
    report = analyze({"gpu_memory_usage_ratio": [series(.99)], "gpu_evictions_delta": [eviction]})
    candidate = pressure(report)
    assert candidate["state"] != "strong_signal"
    assert "gpu_evictions_delta" in candidate["missing_evidence"]
    assert "gpu_memory_entity_match" in candidate["missing_evidence"]
    assert "gpu:memory_entity_match" in report["missing_sources"]


def test_duplicate_process_series_are_not_silently_aggregated():
    report = analyze({"gpu_memory_usage_ratio": [series(.99)],
                      "gpu_evictions_delta": [series(7, pid="1"), series(0, pid="2")]})
    candidate = pressure(report)
    assert candidate["state"] != "strong_signal"
    assert "gpu_memory_entity_match" in candidate["missing_evidence"]


def test_aggregate_only_backend_does_not_claim_device_pressure():
    report = analyze({"gpu_memory_usage_ratio": [series(.99)], "gpu_evictions_delta": [series(7)]}, aggregate_only=True)
    candidate = pressure(report)
    assert candidate["state"] != "strong_signal"
    assert "gpu_memory_entity_match" in candidate["missing_evidence"]
    assert candidate["evidence"][0]["observation_scope"] == "unknown"
    assert candidate["observation_scope"] == "unknown"


def test_memory_pressure_without_eviction_keeps_missing_evidence():
    candidate = pressure(analyze({"gpu_memory_usage_ratio": [series(.99)]}))
    assert candidate["state"] == "weak_signal"
    assert "gpu_evictions_delta" in candidate["missing_evidence"]
    assert candidate["related_devices"] == ["0"]


def test_baseline_uses_same_device_only():
    current = {"gpu_memory_usage_ratio": [series(.99)], "gpu_evictions_delta": [series(7)]}
    other = {"gpu_memory_usage_ratio": [series(.1, uuid="GPU-new")],
             "gpu_evictions_delta": [series(0, uuid="GPU-new")]}
    report = analyze(current, other)
    rows = {row["signal"]: row for row in report["comparison"]["signals"]}
    for name in current:
        assert rows[name]["baseline"] is None
    same = analyze(current, {"gpu_memory_usage_ratio": [series(.5)], "gpu_evictions_delta": [series(0)]})
    rows = {row["signal"]: row for row in same["comparison"]["signals"]}
    assert rows["gpu_memory_usage_ratio"]["baseline"] == .5
    assert rows["gpu_evictions_delta"]["baseline"] == 0
