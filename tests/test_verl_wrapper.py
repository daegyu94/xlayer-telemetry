import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[1]


def test_wrapper_adds_non_invasive_verl_logger_and_collects_last_step(
    tmp_path: Path,
) -> None:
    fake_verl = tmp_path / "fake-verl"
    arguments = tmp_path / "arguments.txt"
    fake_verl.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$ARGS_FILE"
printf '%s\\n' '{"step":1,"data":{"timing_s/gen":0.5,"perf/throughput":10.0}}' > "$VERL_FILE_LOGGER_PATH"
""",
        encoding="utf-8",
    )
    fake_verl.chmod(0o755)
    output = tmp_path / "run"

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "run_verl_with_telemetry.sh"),
            "--output",
            str(output),
            "--run-id",
            "grpo-quickstart",
            "--node",
            "gpu-a",
            "--source",
            "vllm=http://gpu-a:8000/metrics",
            "--set",
            "model_id=tiny",
            "--",
            str(fake_verl),
            "-m",
            "verl.trainer.main_ppo",
            "data.train_batch_size=2",
        ],
        cwd=ROOT,
        env=os.environ
        | {
            "TELEMETRY_PYTHON": sys.executable,
            "ARGS_FILE": str(arguments),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    passed = arguments.read_text(encoding="utf-8").splitlines()
    assert 'trainer.logger=["console","file"]' in passed
    assert "trainer.project_name=agent-rl" in passed
    assert "trainer.experiment_name=grpo-quickstart" in passed
    snapshot = json.loads(
        (output / "telemetry-metrics" / "verl-trainer-driver.json").read_text(
            encoding="utf-8"
        )
    )
    assert snapshot["step"] == 1
    assert snapshot["node"] == "gpu-a"
    assert {
        sample["name"] for sample in snapshot["samples"]
    } == {"rl_stage_duration_seconds", "training_tokens_per_second_per_gpu"}
    manifest = json.loads(
        (output / "telemetry-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["sources"]["vllm"] == "http://gpu-a:8000/metrics"
    assert manifest["configuration"]["model_id"] == "tiny"


def test_wrapper_rejects_logger_override_without_file(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "run_verl_with_telemetry.sh"),
            "--output",
            str(tmp_path / "run"),
            "--",
            "true",
            "verl.trainer.main_ppo",
            'trainer.logger=["console"]',
        ],
        cwd=ROOT,
        env=os.environ | {"TELEMETRY_PYTHON": sys.executable},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "must include file" in result.stderr


def test_wrapper_does_not_mistake_profile_for_file_logger(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "run_verl_with_telemetry.sh"),
            "--output",
            str(tmp_path / "run"),
            "--",
            "true",
            "verl.trainer.main_ppo",
            'trainer.logger=["console","profile"]',
        ],
        cwd=ROOT,
        env=os.environ | {"TELEMETRY_PYTHON": sys.executable},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "must include file" in result.stderr


def test_wrapper_detects_async_mode_and_runs_external_diagnostics(tmp_path: Path) -> None:
    fake_verl = tmp_path / "fake-verl"
    fake_verl.write_text(
        """#!/usr/bin/env bash
printf '%s\n' '{"step":2,"data":{"timing_s/step":2.0,"timing_s/gen":1.0}}' > "$VERL_FILE_LOGGER_PATH"
""",
        encoding="utf-8",
    )
    fake_verl.chmod(0o755)
    config = tmp_path / "diagnostics.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "prometheus": {
                    "url": "http://127.0.0.1:1",
                    "timeout_seconds": 0.01,
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "run"

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "run_verl_with_telemetry.sh"),
            "--output",
            str(output),
            "--run-id",
            "async-run",
            "--diagnostics-config",
            str(config),
            "--",
            str(fake_verl),
            "-m",
            "verl.trainer.main_ppo",
            "actor_rollout_ref.rollout.mode=async",
        ],
        cwd=ROOT,
        env=os.environ | {"TELEMETRY_PYTHON": sys.executable},
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    history = json.loads(
        (output / "telemetry-events" / "verl-steps.jsonl").read_text(encoding="utf-8")
    )
    assert history["execution_mode"] == "async"
    assert history["boundary_scope"] == "trainer_update"
    diagnosis = json.loads(
        (output / "diagnostics" / "latest.json").read_text(encoding="utf-8")
    )
    assert diagnosis["step"] == 2
    assert diagnosis["verdict"] == "insufficient_data"
    assert diagnosis["boundary_scope"] == "trainer_update"
    manifest = json.loads(
        (output / "telemetry-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["configuration"]["execution_mode"] == "async"
    assert manifest["artifacts"]["diagnostics"] == str(output / "diagnostics")
