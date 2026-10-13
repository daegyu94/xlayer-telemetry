import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from tests._process_helpers import read_process, signal_process


ROOT = Path(__file__).parents[1]


def test_wrapper_does_not_invent_colocated_rollout_deployment(tmp_path):
    output = tmp_path / 'run'
    result = subprocess.run(['bash', str(ROOT / 'scripts/run_verl_with_telemetry.sh'),
        '--output', str(output), '--', 'true'], env=os.environ | {'TELEMETRY_PYTHON': sys.executable},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    roles = json.loads((output / 'telemetry-manifest.json').read_text())['deployment']['roles']
    assert len(roles) == 1 and roles[0]['role'] == 'trainer'


def test_missing_logger_warns_without_hiding_workload_exit(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "run_verl_with_telemetry.sh"),
         "--output", str(tmp_path / "run"), "--", "bash", "-c", "exit 7"],
        env=os.environ | {"TELEMETRY_PYTHON": sys.executable},
        capture_output=True, text=True,
    )
    assert result.returncode == 7
    assert "VERL_FILE_LOGGER_PATH" in result.stderr


@pytest.mark.parametrize(("sig", "expected_status"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_wrapper_terminates_owned_workload_children_only(tmp_path: Path, sig, expected_status) -> None:
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import os, subprocess, time\n"
        "child = subprocess.Popen(['sleep', '30'])\n"
        "open(os.environ['CHILD_PID_FILE'], 'w').write(str(child.pid))\n"
        "time.sleep(30)\n")
    child_file = tmp_path / "child.pid"
    unrelated = subprocess.Popen(["sleep", "30"])
    wrapper = subprocess.Popen(
        ["bash", str(ROOT / "scripts" / "run_verl_with_telemetry.sh"),
         "--output", str(tmp_path / "run"), "--", sys.executable, str(launcher)],
        cwd=ROOT, env=os.environ | {"TELEMETRY_PYTHON": sys.executable,
                                    "CHILD_PID_FILE": str(child_file)},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 8
        while not child_file.exists() and time.monotonic() < deadline:
            if wrapper.poll() is not None:
                break
            time.sleep(0.05)
        assert child_file.exists(), wrapper.stderr.read() if wrapper.poll() is not None else ""
        child_pid = int(child_file.read_text())
        wrapper.send_signal(sig)
        assert wrapper.wait(timeout=10) == expected_status
        state = Path(f"/proc/{child_pid}/stat")
        assert not state.exists() or state.read_text().split()[2] == "Z"
        assert unrelated.poll() is None
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait()
        if child_file.exists():
            try:
                os.kill(int(child_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        unrelated.terminate()
        unrelated.wait()


def test_wrapper_cleans_child_forked_during_shutdown(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    child_file = tmp_path / "late-child.pid"
    launcher = tmp_path / "launcher.py"
    child_code = (
        "import os,pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "stat=pathlib.Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(') ',1)[1].split(); "
        "pathlib.Path(os.environ['CHILD_PID_FILE']).write_text(f'{os.getpid()} {stat[19]}'); "
        "time.sleep(30)"
    )
    launcher.write_text(
        "import os, pathlib, signal, subprocess, sys, time\n"
        "def finish(sig, frame):\n"
        f" subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        " raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, finish)\n"
        "pathlib.Path(os.environ['READY_FILE']).touch()\n"
        "while True: time.sleep(30)\n"
    )
    wrapper = subprocess.Popen(
        ["bash", str(ROOT / "scripts/run_verl_with_telemetry.sh"),
         "--output", str(tmp_path / "run"), "--", sys.executable, str(launcher)],
        cwd=ROOT, env=os.environ | {"TELEMETRY_PYTHON": sys.executable,
                                    "READY_FILE": str(ready), "CHILD_PID_FILE": str(child_file)},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 8
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        wrapper.send_signal(signal.SIGTERM)
        assert wrapper.wait(timeout=10) == 143
        assert child_file.is_file(), "workload did not fork during signal handling"
        child_pid, start_time = map(int, child_file.read_text().split())
        state = read_process(child_pid)
        assert state is None or state.start_time != start_time or state.state == "Z"
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait()
        if child_file.is_file():
            child_pid, start_time = map(int, child_file.read_text().split())
            child = read_process(child_pid)
            if child is not None and child.start_time == start_time:
                signal_process(child, signal.SIGKILL)
        if wrapper.stderr is not None:
            wrapper.stderr.close()


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
        next((output / "telemetry-metrics").glob("verl-trainer-driver*.json")).read_text(
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
            "trainer.v1.trainer_mode=colocate_async",
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
    if history["analysis_window"]["accuracy"] == "unknown":
        assert history["window_start_ms"] is None
        assert history["window_end_ms"] is None
    else:
        assert history["window_start_ms"] == int(history["analysis_window"]["start"] * 1000)
        assert history["window_end_ms"] >= int(history["analysis_window"]["end"] * 1000)
    assert history["stage_summary"]
    assert history["boundary_accuracy"] in {"approximate", "unknown"}
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


def test_wrapper_keeps_sync_trainer_when_rollout_server_is_async(tmp_path: Path) -> None:
    fake_verl = tmp_path / "fake-verl"
    fake_verl.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' "
        "'{\"step\":1,\"data\":{\"timing_s/step\":2.0}}' "
        '> "$VERL_FILE_LOGGER_PATH"\n',
        encoding="utf-8",
    )
    fake_verl.chmod(0o755)
    output = tmp_path / "run"
    result = subprocess.run(
        [
            "bash", str(ROOT / "scripts" / "run_verl_with_telemetry.sh"),
            "--output", str(output), "--", str(fake_verl),
            "-m", "verl.trainer.main_ppo",
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
    assert history["execution_mode"] == "sync"
    assert history["boundary_scope"] == "rl_step"


def test_wrapper_reads_independent_peer_tail_before_final_export(tmp_path):
    output = tmp_path / "run"
    ready = tmp_path / "ready"
    peer_code = """import pathlib,sys,time
ready=pathlib.Path(sys.argv[1]); logger=pathlib.Path(sys.argv[2])
deadline=time.monotonic()+10
while not ready.exists():
 if time.monotonic()>deadline: sys.exit(2)
 time.sleep(.01)
for step in (2,3):
 time.sleep(.35)
 with logger.open('a') as stream:
  stream.write('{"step":'+str(step)+',"data":{"timing_s/gen":1}}\\n')
"""
    peer = subprocess.Popen([sys.executable, "-c", peer_code, str(ready),
                             str(output / "logs/verl-metrics.jsonl")])
    workload = tmp_path / "driver.py"
    workload.write_text('import os,pathlib\np=pathlib.Path(os.environ["VERL_FILE_LOGGER_PATH"])\n'
                        'p.write_text(\'{"step":1,"data":{"timing_s/gen":1}}\\n\')\n'
                        f'pathlib.Path({str(ready)!r}).touch()\n')
    try:
        result = subprocess.run(["bash", str(ROOT / "scripts/run_verl_with_telemetry.sh"),
            "--output", str(output), "--", sys.executable, str(workload)],
            env=os.environ | {"TELEMETRY_PYTHON": sys.executable,
                "TELEMETRY_LOGGER_SETTLE_SECONDS": ".6",
                "TELEMETRY_LOGGER_MAX_WAIT_SECONDS": "3"},
            capture_output=True, text=True, timeout=12)
        peer.wait(timeout=10)
        assert result.returncode == 0 and peer.returncode == 0, result.stderr
        history = [json.loads(row) for row in (output / "telemetry-events/verl-steps.jsonl").read_text().splitlines()]
        assert [row["step"] for row in history] == [1, 2, 3]
        health = json.loads((output / "telemetry-health.json").read_text())
        assert health["logger_settle"]["status"] == "settled"
        assert health["final_export"]["bridge"] == "ok"
        assert health["status"] == "complete"
    finally:
        if peer.poll() is None:
            peer.kill(); peer.wait()
