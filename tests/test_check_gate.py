"""The check gate must propagate failures and leave existing Git hooks intact."""

from pathlib import Path
import os
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_git_environment(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)


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
        assert "xlayer-check-hook v2" in (hooks / "pre-push").read_text()
        assert 'scripts/check.sh" push' in (hooks / "pre-push").read_text()
        assert not (repo / ".git/hooks/pre-push").exists()


def test_clean_checkout_skips_implicit_tests_but_runs_explicit_targets(tmp_path: Path) -> None:
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
    assert not calls.exists()
    result = subprocess.run(["bash", "scripts/check.sh", "default", "tests/test_schema.py"], cwd=repo,
                            env={key: value for key, value in os.environ.items() if key != "PYTHON"} | {"CALLS": str(calls)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "-m pytest -q tests/test_schema.py" in calls.read_text()


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
    result = subprocess.run(["bash", "scripts/check.sh", "fast", "tests/test_schema.py"], cwd=worktree,
                            env=env | {"CALLS": str(calls)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "-m pytest -q tests/test_schema.py" in calls.read_text()


def test_owned_v1_hook_is_upgraded_without_replacing_custom_hooks(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'scripts').mkdir()
    shutil.copy2(ROOT / 'scripts/install_check_hooks.sh', tmp_path / 'scripts/install_check_hooks.sh')
    target = tmp_path / '.git/hooks/pre-push'
    target.write_text('#!/usr/bin/env bash\n# xlayer-check-hook v1\nset -euo pipefail\n'
                      'root=$(git rev-parse --show-toplevel)\nexec bash "$root/scripts/check.sh" default\n')
    subprocess.run(['bash', 'scripts/install_check_hooks.sh'], cwd=tmp_path, check=True)
    assert 'xlayer-check-hook v2' in target.read_text()
    assert 'scripts/check.sh" push' in target.read_text()
    custom = target.read_text() + 'echo user-customization\n'
    target.write_text(custom)
    subprocess.run(['bash', 'scripts/install_check_hooks.sh'], cwd=tmp_path, check=True)
    assert target.read_text() == custom


def test_actual_push_checks_outgoing_whitespace_without_running_python(tmp_path):
    repo, remote = tmp_path / 'repo', tmp_path / 'remote'
    (repo / 'scripts').mkdir(parents=True)
    for name in ('check.sh', 'install_check_hooks.sh'):
        shutil.copy2(ROOT / 'scripts' / name, repo / 'scripts' / name)
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
    for key, value in [('user.name', 'Test'), ('user.email', 'test@example.com')]:
        subprocess.run(['git', '-C', str(repo), 'config', key, value], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'base'], check=True)
    branch = subprocess.check_output(['git', '-C', str(repo), 'branch', '--show-current'], text=True).strip()
    subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin', str(remote)], check=True)
    subprocess.run(['bash', 'scripts/install_check_hooks.sh'], cwd=repo, check=True)
    env = os.environ | {'PYTHON': str(tmp_path / 'missing-python')}
    result = subprocess.run(['git', 'push', 'origin', branch], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    baseline = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    (repo / 'bad.py').write_text('trailing whitespace \n')
    subprocess.run(['git', 'add', 'bad.py'], cwd=repo, check=True)
    subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'bad'], cwd=repo, check=True)
    result = subprocess.run(['git', 'push', 'origin', branch], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'trailing whitespace' in (result.stdout + result.stderr).lower()
    head = subprocess.check_output(['git', '--git-dir', str(remote), 'rev-parse', branch], text=True).strip()
    assert head == baseline
