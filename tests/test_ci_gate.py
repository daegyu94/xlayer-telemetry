"""DCO validation must reject unsigned and mismatched commits individually."""

import os
import subprocess
from pathlib import Path

import pytest

from scripts.check_dco import check


@pytest.fixture(autouse=True)
def isolate_git_environment(monkeypatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args], text=True
    ).strip()


def test_dco_checks_every_commit_and_author(tmp_path: Path, monkeypatch) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "source").write_text("base\n")
    _git(tmp_path, "add", "source")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)

    (tmp_path / "source").write_text("signed\n")
    _git(tmp_path, "commit", "-qam", "signed\n\nSigned-off-by: Test User <test@example.com>")
    signed = _git(tmp_path, "rev-parse", "HEAD")
    assert check(base, signed) == []

    (tmp_path / "source").write_text("wrong signer\n")
    _git(tmp_path, "commit", "-qam", "wrong\n\nSigned-off-by: Other <other@example.com>")
    wrong = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "source").write_text("unsigned\n")
    _git(tmp_path, "commit", "-qam", "unsigned")
    unsigned = _git(tmp_path, "rev-parse", "HEAD")
    assert check(base, unsigned) == [unsigned, wrong]


def test_dco_checks_authored_merge_commits(tmp_path: Path, monkeypatch) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "source").write_text("base\n")
    _git(tmp_path, "add", "source")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    branch = _git(tmp_path, "branch", "--show-current")
    monkeypatch.chdir(tmp_path)

    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "feature").write_text("feature\n")
    _git(tmp_path, "add", "feature")
    _git(tmp_path, "commit", "-qm", "feature\n\nSigned-off-by: Test User <test@example.com>")
    _git(tmp_path, "checkout", "-q", branch)
    (tmp_path / "main").write_text("main\n")
    _git(tmp_path, "add", "main")
    _git(tmp_path, "commit", "-qm", "main\n\nSigned-off-by: Test User <test@example.com>")
    _git(tmp_path, "merge", "--no-ff", "-qm", "unsigned merge", "feature")
    merge = _git(tmp_path, "rev-parse", "HEAD")
    assert check(base, merge) == [merge]

    _git(tmp_path, "commit", "--amend", "-qm", "signed merge\n\nSigned-off-by: Test User <test@example.com>")
    assert check(base, _git(tmp_path, "rev-parse", "HEAD")) == []
