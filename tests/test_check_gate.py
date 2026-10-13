"""The check gate must propagate failures and leave existing Git hooks intact."""

from pathlib import Path
import os
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_fast_gate_propagates_selected_test_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "tests").mkdir()
    shutil.copy2(ROOT / "scripts/check.sh", repo / "scripts/check.sh")
    (repo / "tests/test_failure.py").write_text("def test_failure(): pass\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "base"], check=True)
    (repo / "tests/test_failure.py").write_text("def test_failure(): assert False\n")
    fake_python = tmp_path / "python"
    fake_python.write_text("#!/bin/sh\ncase \"$*\" in *'-m pytest'*) exit 17;; esac\nexit 0\n")
    fake_python.chmod(0o755)
    result = subprocess.run(["bash", "scripts/check.sh", "fast"], cwd=repo,
                            env=os.environ | {"PYTHON": str(fake_python)},
                            capture_output=True, text=True)
    assert result.returncode == 17, result.stderr


def test_hook_installer_preserves_existing_hook_and_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/install_check_hooks.sh", repo / "scripts/install_check_hooks.sh")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    hooks = repo / "custom-hooks"
    hooks.mkdir()
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", "custom-hooks"], check=True)
    original = "#!/bin/sh\nexit 0\n"
    (hooks / "pre-commit").write_text(original)
    for _ in range(2):
        result = subprocess.run(["bash", "scripts/install_check_hooks.sh"], cwd=repo,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert (hooks / "pre-commit").read_text() == original
        assert "xlayer-check-hook v1" in (hooks / "pre-push").read_text()
        assert not (repo / ".git/hooks/pre-push").exists()


def test_clean_checkout_checks_real_contract_targets(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / ".venv/bin").mkdir(parents=True)
    (repo / ".gitignore").write_text(".venv/\n")
    shutil.copy2(ROOT / "scripts/check.sh", repo / "scripts/check.sh")
    for name in ("test_schema.py", "test_artifact_reader_contracts.py"):
        (repo / "tests" / name).write_text("def test_contract(): pass\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "base"], check=True)
    calls = tmp_path / "calls"
    fake_python = repo / ".venv/bin/python"
    fake_python.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CALLS\"\n")
    fake_python.chmod(0o755)
    result = subprocess.run(["bash", "scripts/check.sh", "default"], cwd=repo,
                            env={key: value for key, value in os.environ.items() if key != "PYTHON"} | {"CALLS": str(calls)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "-m pytest -q tests/test_schema.py tests/test_artifact_reader_contracts.py" in calls.read_text()


def test_linked_worktree_uses_common_development_python(tmp_path: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_") and key != "PYTHON"}
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / ".gitignore").write_text(".venv/\n")
    shutil.copy2(ROOT / "scripts/check.sh", repo / "scripts/check.sh")
    (repo / "tests/test_schema.py").write_text("def test_schema(): pass\n")
    (repo / "tests/test_artifact_reader_contracts.py").write_text("def test_artifact(): pass\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "base"], check=True, env=env)
    python = repo / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CALLS\"\n")
    python.chmod(0o755)
    worktree = tmp_path / "linked"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(worktree)], check=True, env=env)
    calls = tmp_path / "calls"
    result = subprocess.run(["bash", "scripts/check.sh", "fast"], cwd=worktree,
                            env=env | {"CALLS": str(calls)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "-m pytest -q tests/test_schema.py tests/test_artifact_reader_contracts.py" in calls.read_text()
