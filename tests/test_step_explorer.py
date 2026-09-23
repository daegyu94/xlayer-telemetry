import json
from pathlib import Path
from urllib.request import urlopen

from xlayer_telemetry.step_explorer import StepExplorer, _signals, load_steps, make_handler


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
    assert detail["signals"]["gpu"]["summary"] == {"min": 2, "mean": 4, "max": 6, "samples": 3}
    assert detail["logs"] == [[15, "line one"]]
    assert calls[0][2]["start"] == 10
    assert calls[0][2]["end"] == 20
    assert 'instance="gpu-local"' in calls[0][2]["query"]
    assert 'run_id="run-directory"' in calls[-1][2]["query"]


def test_step_explorer_has_no_invented_resource_data_when_sources_are_absent(tmp_path: Path) -> None:
    _history(tmp_path)
    detail = StepExplorer(tmp_path, None, "cluster-a", None, None).detail("one")
    assert detail["signals"] == {}
    assert detail["logs"] == []
    assert load_steps(tmp_path)[0]["step"] == 1


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
