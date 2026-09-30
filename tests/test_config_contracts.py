"""Validate shared contracts against standard JSON Schema and real producer output."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator
import pytest

from xlayer_telemetry.diagnosis_demo import generate
from xlayer_telemetry.diagnostics import DiagnosticEngine
from xlayer_telemetry.schema import ALLOWED_CATEGORIES, EVENT_ONLY_LABELS, load_schema


ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config"


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONFIG / name).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_metric_contract_matches_standard_schema_and_runtime_categories() -> None:
    validator = _validator("metrics.schema.json")
    contract = load_schema(CONFIG / "metrics.json")
    validator.validate(contract)
    properties = validator.schema["properties"]
    assert set(properties["metrics"]["items"]["properties"]["category"]["enum"]) == ALLOWED_CATEGORIES
    assert set(properties["recommended_labels"]["items"]["not"]["enum"]) == EVENT_ONLY_LABELS
    assert {"runtime", "filesystem", "deployment", "status", "tool"} <= set(contract["recommended_labels"])
    assert "filesystem_path" in contract["manifest_only_fields"]
    assert "filesystem" not in contract["manifest_only_fields"]
    assert "environment" in contract["phase_vocabulary"]


@pytest.mark.parametrize("change", [
    lambda data: data.update(schema_version=True),
    lambda data: data.update(unrecognized={}),
    lambda data: data.pop("$schema"),
    lambda data: data.update({"$schema": "other.schema.json"}),
    lambda data: data["metrics"][0].update(scope="  \t"),
    lambda data: data["metrics"][0].update(source=None),
    lambda data: data["metrics"][0].update(category="typo"),
    lambda data: data["metrics"][0].update(unrecognized=True),
    lambda data: data["recommended_labels"].append("container_id"),
    lambda data: data["phase_vocabulary"].append("Invalid-Phase"),
])
def test_runtime_and_standard_schema_both_reject_invalid_contracts(tmp_path: Path, change) -> None:
    data = json.loads((CONFIG / "metrics.json").read_text())
    change(data)
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_schema(path)
    assert list(_validator("metrics.schema.json").iter_errors(data))


@pytest.mark.parametrize("change,reason", [
    (lambda data: data["metrics"].append(deepcopy(data["metrics"][0])), "Duplicate metric name"),
    (lambda data: data["recommended_labels"].append("git_commit"), "vocabularies overlap"),
])
def test_semantic_checks_supplement_json_schema(tmp_path: Path, change, reason: str) -> None:
    data = json.loads((CONFIG / "metrics.json").read_text())
    change(data)
    _validator("metrics.schema.json").validate(data)
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=reason):
        load_schema(path)


def test_metric_contract_cli_reports_validation_failure_without_traceback(tmp_path: Path) -> None:
    command = [sys.executable, "-m", "xlayer_telemetry.schema"]
    result = subprocess.run(command + [str(CONFIG / "metrics.json")], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["metrics"] == len(load_schema(CONFIG / "metrics.json")["metrics"])
    invalid = tmp_path / "metrics.json"
    invalid.write_text('{"schema_version": true}')
    result = subprocess.run(command + [str(invalid)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 2
    assert "Invalid metric contract" in result.stderr
    assert "Traceback" not in result.stderr


def test_synthetic_diagnosis_and_counter_evidence_validate(tmp_path: Path) -> None:
    report = generate(tmp_path / "synthetic", run_id="contract-check", clock=lambda: 100)
    validator = _validator("diagnosis.schema.json")
    validator.validate(report)
    counter = next(item for candidate in report["candidates"] for item in candidate["counter_evidence"])
    counter.pop("observation_scope")
    assert list(validator.iter_errors(report))


class NoDataPrometheus:
    def query_range(self, query, start, end, step):
        return None


@pytest.mark.parametrize("window", [None, {"start": 80, "end": 90, "accuracy": "approximate"},
                                  {"start": None, "end": None, "accuracy": "unknown"}])
def test_live_periodic_and_unknown_window_diagnoses_validate(window) -> None:
    engine = DiagnosticEngine({"schema_version": 1, "run_id": "contract-check",
                               "prometheus": {"url": "http://unused"}},
                              prometheus=NoDataPrometheus(), clock=lambda: 100)
    current = None if window is None else {
        "run_id": "contract-check", "node": "n", "worker_id": "driver",
        "step": 1, "record_id": "record-1", "step_duration_seconds": 10,
        "analysis_window": window,
    }
    report = engine.analyze(current, [])
    _validator("diagnosis.schema.json").validate(report)
    assert report["verdict"] == "insufficient_data"
    assert report["candidates"] == []
    report.update(analysis_status="provisional", revision=1, first_attempt_at=100, retry_at=110)
    _validator("diagnosis.schema.json").validate(report)
    report.update(analysis_status="final", revision=2, retry_at=None)
    _validator("diagnosis.schema.json").validate(report)
