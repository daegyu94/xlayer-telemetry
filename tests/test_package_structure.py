"""Protect historical SDK imports, CLI commands and optional package boundaries."""

import importlib
import ast
from importlib.util import resolve_name
import json
from pathlib import Path
import pickle
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]
MODULES = {
    "diagnostics": "analysis",
    "diagnosis_analysis": "analysis",
    "clock_quality": "analysis",
    "evidence_quality": "analysis",
    "llm_diagnosis": "analysis",
    "llm_investigation": "analysis",
    "gpu_sampler": "collectors",
    "resource_sampler": "collectors",
    "sandbox_sampler": "collectors",
    "topology_textfile": "collectors",
}


@pytest.mark.parametrize("legacy_first", [True, False])
def test_legacy_and_canonical_imports_share_one_module_in_either_order(legacy_first) -> None:
    # Fresh interpreters exercise import order independently from test collection.
    script = (
        "import importlib, sys\n"
        f"modules={MODULES!r}\n"
        "for name, group in modules.items():\n"
        "    old='xlayer_telemetry.'+name\n"
        "    new='xlayer_telemetry.'+group+'.'+name\n"
        f"    first, second = (old, new) if {legacy_first!r} else (new, old)\n"
        "    a=importlib.import_module(first); b=importlib.import_module(second)\n"
        "    assert a is b, name\n"
        "    assert sys.modules[old] is sys.modules[new], name\n"
    )
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True,
                   text=True, check=True, timeout=10)


@pytest.mark.parametrize("name", ["diagnostics", "clock_quality", "llm_diagnosis",
                                  "gpu_sampler", "resource_sampler", "sandbox_sampler", "topology_textfile"])
def test_legacy_cli_help_matches_implementation(name) -> None:
    def help_for(module):
        result = subprocess.run([sys.executable, "-m", module, "--help"], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert not result.stderr
        assert "usage:" in result.stdout
        return result.stdout
    assert help_for(f"xlayer_telemetry.{name}") == help_for(f"xlayer_telemetry.{MODULES[name]}.{name}")


def test_existing_pickled_symbol_paths_still_resolve() -> None:
    # Protocol 0 GLOBAL records reproduce pre-refactor class/function references.
    for name, symbol in (("diagnostics", "DiagnosticEngine"), ("gpu_sampler", "snapshot")):
        saved = f"cxlayer_telemetry.{name}\n{symbol}\n.".encode("ascii")
        canonical = importlib.import_module(f"xlayer_telemetry.{MODULES[name]}.{name}")
        assert pickle.loads(saved) is getattr(canonical, symbol)


def test_metric_sdk_import_does_not_load_collectors_or_diagnosis() -> None:
    script = (
        "import json, sys\n"
        "from xlayer_telemetry.metrics import Metric, MetricEmitter\n"
        "from xlayer_telemetry.events import EventRecorder\n"
        "print(json.dumps(sorted(name for name in sys.modules if "
        "name.startswith(('xlayer_telemetry.analysis', 'xlayer_telemetry.collectors')))))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(result.stdout) == []


def test_collectors_do_not_import_diagnosis_or_framework_adapters() -> None:
    for path in (ROOT / "xlayer_telemetry/collectors").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [resolve_name("." * node.level + (node.module or ""),
                                        "xlayer_telemetry.collectors")]
            else:
                continue
            assert not any(module.startswith(("xlayer_telemetry.analysis", "xlayer_telemetry.adapters"))
                           for module in modules), (path, modules)
