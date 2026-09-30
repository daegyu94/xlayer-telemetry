import json
from pathlib import Path

from xlayer_telemetry.show_run import summarize


def test_summarize_reads_metadata_and_application_metrics_without_rank_duplicates(tmp_path: Path) -> None:
    (tmp_path / "run-metadata-train.json").write_text(
        json.dumps({"model_id": "zai-org/GLM-4.7-Flash", "configuration": {"max_steps": 1}}), encoding="utf-8"
    )
    (tmp_path / "run-metadata-train-rank-0.json").write_text(json.dumps({"model_id": "duplicate"}), encoding="utf-8")
    (tmp_path / "summary-train.json").write_text(json.dumps({"model_id": "trl-model", "train_seconds": 12.5}), encoding="utf-8")
    metrics_dir = tmp_path / "telemetry-metrics"
    metrics_dir.mkdir()
    (metrics_dir / "megatron-trainer-0.json").write_text(
        json.dumps({
            "schema_version": 2,
            "producer": "megatron",
            "role": "trainer",
            "worker_id": "0",
            "step": 7,
            "samples": [{"name": "training_loss", "value": 1.5}],
        }),
        encoding="utf-8",
    )

    output = summarize(tmp_path)

    assert "zai-org/GLM-4.7-Flash" in output
    assert "duplicate" not in output
    assert "trl-model" in output
    assert "[megatron/trainer worker 0] step 7: training_loss=1.5" in output


def test_summarize_notes_missing_sources(tmp_path: Path) -> None:
    output = summarize(tmp_path)
    assert "no run-metadata" in output
    assert "no telemetry-metrics" in output


def test_declared_source_is_metadata_and_does_not_expose_url_credentials(tmp_path: Path) -> None:
    (tmp_path / "telemetry-manifest.json").write_text(json.dumps({
        "sources": {"vllm": "http://user:secret@gpu:8000/metrics?token=private#fragment"},
    }))
    output = summarize(tmp_path)
    assert "endpoints were not queried" in output
    assert "vllm: http://gpu:8000/metrics" in output
    assert "secret" not in output
    assert "private" not in output
    assert "metrics were not enabled" not in output
