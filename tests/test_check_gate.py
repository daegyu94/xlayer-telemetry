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
