import json
from pathlib import Path
from urllib.request import urlopen

import pytest

from xlayer_telemetry.step_explorer import StepExplorer, _signals, load_nodes, load_steps, make_handler


def _history(root: Path) -> None:
    path = root / "telemetry-events" / "verl-steps.jsonl"
    path.parent.mkdir()
    records = [
        {"record_type": "verl_step_observation", "record_id": "zero", "step": 0, "observed_at": 10,
         "analysis_window": {"start": None, "end": 10}, "worker_id": "driver", "node": "gpu-local"},
        {"record_type": "verl_step_observation", "record_id": "one", "step": 1, "observed_at": 20,
         "run_id": "run-a", "worker_id": "driver", "node": "gpu-local", "boundary_scope": "trainer_update",
         "step_duration_seconds": 10, "stage_durations_seconds": {"gen": 6, "update_actor": 3},
         "analysis_window": {"start": 10, "end": 20, "accuracy": "approximate"}},
    ]
    path.write_text("\n".join(json.dumps(item) for item in records) + "\ninvalid\n")


def _manifest(root: Path, *, name: str = "topology-manifest.json", roles=None, run_id: str = "run-a") -> Path:
    path = root / name
    path.write_text(json.dumps({"schema_version": 1, "run_id": run_id, "deployment": {"roles": roles or []}}))
    return path


def test_step_explorer_returns_real_step_window_and_resource_summary(tmp_path: Path, monkeypatch) -> None:
    _history(tmp_path)
    calls = []

    def fake_request(base, path, params):
        calls.append((base, path, params))
        if path.endswith("query_range") and "loki" in path:
            return {"status": "success", "data": {"result": [{"values": [["15000000000", "\u001b[31mline one\u001b[0m"]]}]}}
        return {"status": "success", "data": {"result": [{"metric": {}, "values": [[10, "2"], [15, "4"], [20, "6"]]}]}}

    monkeypatch.setattr("xlayer_telemetry.step_explorer._request_json", fake_request)
    explorer = StepExplorer(tmp_path, "http://prometheus", "cluster-a", "http://loki", "run-directory")
    assert [item["id"] for item in explorer.steps()] == ["one"]
    detail = explorer.detail("one")
    assert detail["step"]["accuracy"] == "approximate"
    assert detail["stages"]["gen"] == 6
    assert detail["nodes"] == [{"name": "gpu-local", "roles": ["step observer"]}]
    assert detail["node_data"]["gpu-local"]["signals"]["gpu"]["summary"] == {"min": 2, "mean": 4, "max": 6, "samples": 3}
    assert detail["node_data"]["gpu-local"]["logs"] == [[15, "line one"]]
    assert all(call[2]["start"] == 10 and call[2]["end"] == 20 for call in calls if call[1].endswith("query_range") and "loki" not in call[1])
    assert any('instance="gpu-local"' in call[2]["query"] for call in calls)
    assert any('run_id="run-directory"' in call[2]["query"] for call in calls)


def test_step_explorer_has_no_invented_resource_data_when_sources_are_absent(tmp_path: Path) -> None:
    _history(tmp_path)
    detail = StepExplorer(tmp_path, None, "cluster-a", None, None).detail("one")
    assert detail["node_data"]["gpu-local"]["signals"] == {}
    assert detail["node_data"]["gpu-local"]["logs"] == []
    assert load_steps(tmp_path)[0]["step"] == 1


def test_multi_node_uses_topology_roles_and_keeps_signals_and_logs_separate(tmp_path: Path, monkeypatch) -> None:
    _history(tmp_path)
    _manifest(tmp_path, roles=[{"role": "trainer", "node": "gpu-local"}, {"role": "rollout", "node": "rollout-b"}])
    calls = []

    def fake_request(base, path, params):
        calls.append((path, params["query"]))
        node = "rollout-b" if 'node="rollout-b"' in params["query"] or 'instance="rollout-b"' in params["query"] else "gpu-local"
        if path.startswith("/loki"):
            return {"status": "success", "data": {"result": [{"values": [["15000000000", f"log from {node}"]]}]}}
        value = "9" if node == "rollout-b" else "2"
        return {"status": "success", "data": {"result": [{"metric": {}, "values": [[15, value]]}]}}

    monkeypatch.setattr("xlayer_telemetry.step_explorer._request_json", fake_request)
    detail = StepExplorer(tmp_path, "http://prom", "cluster-a", "http://loki", "run-directory").detail("one")
    assert detail["nodes"] == [
        {"name": "gpu-local", "roles": ["step observer", "trainer"]},
        {"name": "rollout-b", "roles": ["rollout"]},
    ]
    assert detail["node_data"]["gpu-local"]["signals"]["gpu"]["summary"]["mean"] == 2
    assert detail["node_data"]["rollout-b"]["signals"]["gpu"]["summary"]["mean"] == 9
    assert "vllm_waiting" not in detail["node_data"]["gpu-local"]["signals"]
    assert detail["node_data"]["rollout-b"]["signals"]["vllm_waiting"]["summary"]["mean"] == 9
    assert detail["node_data"]["gpu-local"]["logs"] == [[15, "log from gpu-local"]]
    assert detail["node_data"]["rollout-b"]["logs"] == [[15, "log from rollout-b"]]
    assert sum(path.startswith("/loki") for path, _ in calls) == 2


def test_explicit_topology_overrides_default_and_rejects_wrong_run(tmp_path: Path) -> None:
    _history(tmp_path)
    _manifest(tmp_path, name="telemetry-manifest.json", roles=[{"role": "rollout", "node": "stale-driver"}])
    topology = _manifest(tmp_path, name="actual.json", roles=[{"role": "rollout", "node": "rollout-b"}])
    assert {item["name"] for item in load_nodes(tmp_path, "run-a", "gpu-local", topology)} == {"gpu-local", "rollout-b"}
    with pytest.raises(ValueError, match="run_id"):
        load_nodes(tmp_path, "other-run", "gpu-local", topology)


def test_implicit_topology_takes_priority_over_wrapper_manifest(tmp_path: Path) -> None:
    _history(tmp_path)
    _manifest(tmp_path, name="telemetry-manifest.json", roles=[{"role": "rollout", "node": "gpu-local"}])
    _manifest(tmp_path, roles=[{"role": "trainer", "node": "gpu-local"}, {"role": "rollout", "node": "rollout-b"}])
    nodes = load_nodes(tmp_path, "run-a", "gpu-local")
    assert nodes == [
        {"name": "gpu-local", "roles": ["step observer", "trainer"]},
        {"name": "rollout-b", "roles": ["rollout"]},
    ]


def test_missing_remote_source_does_not_hide_trainer_samples(tmp_path: Path, monkeypatch) -> None:
    _history(tmp_path)
    _manifest(tmp_path, roles=[{"role": "trainer", "node": "gpu-local"}, {"role": "rollout", "node": "rollout-b"}])

    def fake_request(base, path, params):
        if 'instance="rollout-b"' in params["query"] or 'node="rollout-b"' in params["query"]:
            raise OSError("remote source unavailable")
        return {"status": "success", "data": {"result": [{"metric": {}, "values": [[15, "42"]]}]}}

    monkeypatch.setattr("xlayer_telemetry.step_explorer._request_json", fake_request)
    detail = StepExplorer(tmp_path, "http://prom", "cluster-a", None, None).detail("one")
    assert detail["node_data"]["gpu-local"]["signals"]["gpu"]["summary"]["mean"] == 42
    assert detail["node_data"]["rollout-b"]["signals"] == {}
    assert detail["node_data"]["rollout-b"]["errors"]["gpu"] == "remote source unavailable"


def test_step_explorer_http_endpoints(tmp_path: Path) -> None:
    from http.server import ThreadingHTTPServer
    from threading import Thread

    _history(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(StepExplorer(tmp_path, None, "cluster-a", None, None)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/api/steps") as response:
            assert json.load(response)[0]["step"] == 1
        with urlopen(base + "/api/step?id=one") as response:
            assert json.load(response)["step"]["id"] == "one"
        with urlopen(base) as response:
            assert b"Step Explorer" in response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_prometheus_label_values_are_escaped() -> None:
    expression = _signals('a"b', "node\\one")["gpu"]["query"]
    assert 'cluster="a\\"b"' in expression
    assert 'instance="node\\\\one"' in expression
