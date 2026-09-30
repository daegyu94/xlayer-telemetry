"""Protect canonical entrypoints and lightweight SDK/package boundaries."""

import ast
from importlib.util import find_spec, resolve_name
import json
from pathlib import Path
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
CLI_MODULES = (
    "analysis.diagnostics", "analysis.clock_quality", "analysis.llm_diagnosis",
    "collectors.gpu_sampler", "collectors.resource_sampler",
    "collectors.sandbox_sampler", "collectors.topology_textfile",
    "demos.live", "demos.diagnosis",
)


def test_modules_have_one_canonical_path_without_root_wrappers() -> None:
    relocated = {**MODULES, "live_demo": "demos.live", "diagnosis_demo": "demos.diagnosis"}
    for old, group in relocated.items():
        target = f"{group}.{old}" if old in MODULES else group
        spec = find_spec(f"xlayer_telemetry.{target}")
        assert spec is not None, target
        assert Path(spec.origin) == ROOT / "xlayer_telemetry" / (target.replace(".", "/") + ".py")
        assert find_spec(f"xlayer_telemetry.{old}") is None, old


@pytest.mark.parametrize("module", CLI_MODULES)
def test_canonical_cli_help(module) -> None:
    result = subprocess.run([sys.executable, "-m", f"xlayer_telemetry.{module}", "--help"],
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert "usage:" in result.stdout


def test_metric_sdk_import_does_not_load_collectors_or_diagnosis() -> None:
    script = (
        "import json, sys\n"
        "from xlayer_telemetry.metrics import Metric, MetricEmitter\n"
        "from xlayer_telemetry.events import EventRecorder\n"
        "print(json.dumps(sorted(name for name in sys.modules if "
        "name.startswith(('xlayer_telemetry.analysis', 'xlayer_telemetry.collectors', "
        "'xlayer_telemetry.demos')))))\n"
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
