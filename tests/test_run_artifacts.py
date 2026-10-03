"""Saved run identity and freshness must not depend on copy time or host state."""

import json
import os

import pytest

from xlayer_telemetry.operations.run_artifacts import read_run_state


def write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def manifest(root, **settings):
    write(root, "telemetry-manifest.json", {"run_id": "run-1", "configuration": settings})


def sample(**values):
    return {"schema_version": 2, "run_id": "run-1", "producer": "verl", "role": "trainer",
            "worker_id": "driver", "node": "gpu-1", "observed_at": 990, "step": 10, **values}


def test_saved_run_keeps_context_outcome_and_producer_time(tmp_path):
    manifest(tmp_path, cluster="original", observer_node="gpu-1", execution_mode="async")
    write(tmp_path, "telemetry-health.json", {"status": "complete", "observed_at": 991,
          "workload": {"status": "finished", "exit_code": 7}})
    write(tmp_path, "telemetry-metrics/current.json", sample())
    stale = write(tmp_path, "telemetry-metrics/copied.json", sample(observed_at=100, step=2))
    os.utime(stale, (2000, 2000))
    write(tmp_path, "telemetry-metrics/other-node.json", sample(node="gpu-2", observed_at=999, step=30))
    write(tmp_path, "telemetry-metrics/other-run.json", sample(run_id="run-2", observed_at=999, step=40))
    write(tmp_path, "telemetry-metrics/other-producer.json", sample(producer="test", observed_at=999, step=50))
    state = read_run_state(tmp_path, now=1000)
    assert (state["cluster"], state["observer_node"], state["execution_mode"]) == ("original", "gpu-1", "async")
    assert state["step"] == 10
    assert state["workload"] == {"status": "finished", "exit_code": 7, "recorded_at": 991}
    assert state["telemetry"] == "complete"
    assert state["snapshot"]["scope"] == "stored_artifact"
    assert state["snapshot"]["age_seconds"] == 10
    assert state["snapshot"]["identity"] == "verified"


def test_missing_observer_does_not_choose_between_nodes(tmp_path):
    manifest(tmp_path)
    write(tmp_path, "telemetry-metrics/a.json", sample())
    write(tmp_path, "telemetry-metrics/b.json", sample(node="gpu-2", observed_at=999, step=30))
    state = read_run_state(tmp_path, now=1000)
    assert state["step"] is None
    assert state["snapshot"]["health"] == "unavailable"
    assert state["snapshot"]["identity"] == "ambiguous"


def test_legacy_snapshot_is_explicitly_unverified_and_never_beats_verified(tmp_path):
    manifest(tmp_path)
    write(tmp_path, "telemetry-metrics/verl-trainer-driver.json", {"step": 15, "observed_at": 999})
    write(tmp_path, "telemetry-metrics/verl-trainer-driver@other.json", {"step": 20, "observed_at": 1000})
    state = read_run_state(tmp_path, now=1000)
    assert state["step"] == 15
    assert state["snapshot"]["identity"] == "legacy_unverified"
    write(tmp_path, "telemetry-metrics/identified.json", sample())
    state = read_run_state(tmp_path, now=1000)
    assert state["step"] == 10
    assert state["snapshot"]["identity"] == "verified"
    write(tmp_path, "telemetry-metrics/verl-trainer-driver.json", {"run_id": "wrong", "step": 15, "observed_at": 999})
    (tmp_path / "telemetry-metrics/identified.json").unlink()
    assert read_run_state(tmp_path, now=1000)["step"] is None


@pytest.mark.parametrize("timestamp,expected", [(100, "stale"), (990, "fresh"), (1010, "clock_skew")])
def test_snapshot_age_does_not_change_recorded_workload_state(tmp_path, timestamp, expected):
    manifest(tmp_path)
    write(tmp_path, "telemetry-health.json", {"status": "observed", "observed_at": 500,
          "workload": {"status": "running", "exit_code": None}})
    write(tmp_path, "telemetry-metrics/a.json", sample(observed_at=timestamp))
    state = read_run_state(tmp_path, now=1000)
    assert state["snapshot"]["health"] == expected
    assert state["workload"]["status"] == "running"
    assert state["workload"]["recorded_at"] == 500


def test_missing_corrupt_and_invalid_records_are_unavailable(tmp_path):
    assert read_run_state(tmp_path, now=1000)["workload"]["status"] == "unknown"
    write(tmp_path, "telemetry-manifest.json", {"configuration": []})
    write(tmp_path, "telemetry-health.json", {"workload": [], "status": ["complete"]})
    for name, data in (("list", []), ("nan", sample(observed_at=float("nan"))),
                       ("bool", sample(observed_at=True)), ("version", sample(schema_version=99))):
        write(tmp_path, f"telemetry-metrics/{name}.json", data)
    (tmp_path / "telemetry-metrics/corrupt.json").write_bytes(b'\xff{')
    state = read_run_state(tmp_path, now=1000)
    assert state["snapshot"]["health"] == "unavailable"
    assert state["step"] is None
    assert state["telemetry"] is None


@pytest.mark.parametrize("field,value", [("timestamp", 990), ("timestamp_unix_seconds", 990),
                                         ("timestamp_unix_nano", 990000000000)])
def test_legacy_timestamp_units(tmp_path, field, value):
    manifest(tmp_path)
    write(tmp_path, "telemetry-metrics/verl-trainer-driver.json", {"step": 1, field: value})
    assert read_run_state(tmp_path, now=1000)["snapshot"]["age_seconds"] == 10
