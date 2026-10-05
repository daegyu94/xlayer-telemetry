"""Resolve runtime data from the distribution that supplied the imported code."""

from importlib import metadata

import pytest

from xlayer_telemetry.operations import config


def _installed_distribution(tmp_path, monkeypatch, *, same_package=True, target=False):
    prefix = tmp_path / "user"
    site = prefix if target else prefix / "lib/python3.12/site-packages"
    module = site / "xlayer_telemetry/operations/config.py"
    module.parent.mkdir(parents=True)
    module.touch()
    scripts = prefix / "share/xlayer-telemetry/scripts"
    scripts.mkdir(parents=True)
    (scripts / "verl_local.sh").touch()
    info = site / "xlayer_telemetry-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: xlayer-telemetry\nVersion: 0.1.0\n")
    (info / "RECORD").write_text("xlayer_telemetry/operations/config.py,,\n"
        "../../../share/xlayer-telemetry/scripts/verl_local.sh,,\n")
    distribution = metadata.Distribution.at(info)
    monkeypatch.setattr(config, "__file__", str(module if same_package else tmp_path / "other/lib/xlayer_telemetry/operations/config.py"))
    monkeypatch.setattr(metadata, "distribution", lambda name: distribution)
    return prefix / "share/xlayer-telemetry"


def test_assets_root_follows_user_distribution_record(tmp_path, monkeypatch):
    assets = _installed_distribution(tmp_path, monkeypatch)
    assert config.assets_root() == assets


def test_assets_root_does_not_use_other_distribution_resources(tmp_path, monkeypatch):
    _installed_distribution(tmp_path, monkeypatch, same_package=False)
    with pytest.raises(config.ConfigError, match="Runtime assets are missing"):
        config.assets_root()


def test_assets_root_follows_target_install_layout(tmp_path, monkeypatch):
    assets = _installed_distribution(tmp_path, monkeypatch, target=True)
    assert config.assets_root() == assets
