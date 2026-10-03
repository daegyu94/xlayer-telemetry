"""Keep lifecycle/config regression fixtures out of the developer's real home."""

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Default paths remain exercised, but every test owns its writable HOME.

    Tests may override HOME again when checking specific config behavior.
    Subprocesses inherit the same isolation; production defaults are unchanged.
    """
    # Use a sibling fixture directory: many tests assert their tmp_path stays empty.
    home = tmp_path_factory.mktemp("xlayer-home")
    monkeypatch.setenv("HOME", str(home))
