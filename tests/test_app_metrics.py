import json
from pathlib import Path

from post_training_telemetry.metrics import Metric, MetricEmitter


def test_emitter_replaces_worker_snapshot_atomically(tmp_path: Path) -> None:
    emitter = MetricEmitter(
        tmp_path,
        run_id="run-1",
        producer="verl",
        role="trainer",
        worker_id="0",
        node="trainer-0",
        local_rank=1,
        cuda_visible_devices="2,5",
        clock=lambda: 100.0,
    )
    path = emitter.emit(
        step=7,
        samples=[
            Metric("training_loss", 1.25),
            Metric("agent_tool_call_errors_total", 2, "counter", {"tool": "python"}),
        ],
    )

    assert path == tmp_path / "verl-trainer-0.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 2,
        "run_id": "run-1",
        "producer": "verl",
        "role": "trainer",
        "worker_id": "0",
        "node": "trainer-0",
        "rank": None,
        "local_rank": 1,
        "gpu": None,
        "cuda_visible_devices": "2,5",
        "step": 7,
        "observed_at": 100.0,
        "samples": [
            {"name": "training_loss", "kind": "gauge", "value": 1.25, "labels": {}},
            {
                "name": "agent_tool_call_errors_total",
                "kind": "counter",
                "value": 2,
                "labels": {"tool": "python"},
            },
        ],
    }
    assert not list(tmp_path.glob(".*.tmp"))


def test_emitter_disables_after_invalid_sample(tmp_path: Path, capsys) -> None:
    emitter = MetricEmitter(
        tmp_path,
        run_id="run-1",
        producer="agent-app",
        role="agent",
        worker_id="worker-0",
    )

    assert emitter.emit(step=None, samples=[Metric("bad metric", 1)]) is None
    assert emitter.emit(step=1, samples=[Metric("training_loss", 1)]) is None
    assert capsys.readouterr().err.count("export disabled") == 1
    assert not list(tmp_path.iterdir())


def test_from_env_requires_both_settings(monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELEMETRY_RUN_ID", "run-1")

    assert MetricEmitter.from_env(producer="trl", role="trainer") is None
    assert "must be set together" in capsys.readouterr().err


def test_from_env_preserves_node_rank_and_physical_gpu(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEMETRY_METRICS_DIR", str(tmp_path))
    monkeypatch.setenv("TELEMETRY_RUN_ID", "run-1")
    monkeypatch.setenv("TELEMETRY_NODE", "rollout-0")
    monkeypatch.setenv("RANK", "4")
    monkeypatch.setenv("LOCAL_RANK", "1")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,5")

    emitter = MetricEmitter.from_env(producer="verl", role="rollout")

    assert emitter is not None
    assert emitter.node == "rollout-0"
    assert emitter.worker_id == "4"
    assert emitter.rank == 4
    assert emitter.local_rank == 1
    assert emitter.gpu == "5"
    assert emitter.cuda_visible_devices == "2,5"


def test_producer_roles_have_distinct_snapshots(tmp_path: Path) -> None:
    for role in ("trainer", "rollout", "agent"):
        emitter = MetricEmitter(
            tmp_path,
            run_id="run-1",
            producer="verl",
            role=role,
            worker_id="0",
        )
        emitter.emit(step=1, samples=[Metric("training_loss", 1)])

    assert {path.name for path in tmp_path.iterdir()} == {
        "verl-agent-0.json",
        "verl-rollout-0.json",
        "verl-trainer-0.json",
    }
