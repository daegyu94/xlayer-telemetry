"""Keep the first investigation usable for step-less SDK runs and capture tools."""
import ast
import json
from pathlib import Path
import re

import pytest

from xlayer_telemetry.metrics.textfile import build_metrics

ROOT = Path(__file__).parents[1]


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get("panels", []))


@pytest.mark.parametrize("name", ["start-here", "run-overview", "compute-communication", "data-storage", "agent-rl-stages"])
def test_run_selector_discovers_sdk_run_without_global_step(name):
    samples = build_metrics([{
        "schema_version": 2, "run_id": "async-worker", "producer": "agent",
        "role": "rollout", "worker_id": "0", "node": "n", "step": None,
        "observed_at": 100.0,
        "samples": [{"name": "agent_tool_call_duration_seconds", "kind": "gauge",
                     "value": 1.0, "labels": {"tool": "python"}}],
    }])
    metric_names = {sample.name for sample in samples}
    assert "training_step" not in metric_names
    dashboard = json.loads((ROOT / "examples/dashboards" / f"{name}.json").read_text())
    variable = next(item for item in dashboard["templating"]["list"] if item["name"] == "run_id")
    discovery_metric = re.search(r"label_values\((\w+)\{", variable["query"]).group(1)
    assert discovery_metric in metric_names
    assert variable["query"] == variable["definition"]


def test_demo_capture_targets_existing_rows_or_visible_panels():
    dashboards = [json.loads(path.read_text()) for path in (ROOT / "examples/dashboards").glob("*.json")]
    by_title = {panel["title"]: panel for dashboard in dashboards for panel in panels(dashboard["panels"])}
    source = ast.parse((ROOT / "scripts/capture_dashboard_demo.py").read_text())
    targets = [(call.func.id, call.args[0].value) for call in ast.walk(source)
               if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
               and call.func.id in {"expand", "show_panel"} and call.args
               and isinstance(call.args[0], ast.Constant)]
    assert targets
    for action, title in targets:
        assert title in by_title, f"Capture target no longer exists: {title}"
        if action == "expand":
            assert by_title[title]["type"] == "row", f"Visible panels must not be clicked as collapsed rows: {title}"
        else:
            assert by_title[title]["type"] != "row"


@pytest.mark.parametrize("panel_id", [4, 6])
def test_comparison_and_evidence_tables_show_unit_and_window_statistic(panel_id):
    dashboard = json.loads((ROOT / "examples/dashboards/bottleneck-summary.json").read_text())
    panel = next(item for item in dashboard["panels"] if item["id"] == panel_id)
    indexes = next(item["options"]["indexByName"] for item in panel["transformations"] if item["id"] == "organize")
    for field, title in [("unit", "Unit"), ("window_statistic", "Statistic")]:
        assert field in indexes
        override = next(item for item in panel["fieldConfig"]["overrides"]
                        if item["matcher"] == {"id": "byName", "options": field})
        properties = {item["id"]: item["value"] for item in override["properties"]}
        assert properties["custom.hidden"] is False
        assert properties["displayName"] == title
    assert indexes["baseline"] < indexes["unit"] < indexes["window_statistic"]
