import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
DASHBOARDS = (
    "run-overview.json",
    "compute-communication.json",
    "data-storage.json",
    "agent-rl-stages.json",
)
NAV_DASHBOARD = "start-here.json"


def _panels(dashboard):
    """Include panels inside collapsed optional rows."""
    for panel in dashboard["panels"]:
        yield panel
        if panel.get("panels"):
            yield from _panels(panel)


def test_dashboard_guidance_is_korean_and_layout_and_links_are_valid():
    paths = sorted((ROOT / "examples/dashboards").glob("*.json"))
    dashboards = [json.loads(path.read_text()) for path in paths]
    uids = {dashboard["uid"] for dashboard in dashboards}
    for dashboard in dashboards:
        assert re.search("[가-힣]", dashboard["description"])
        for variable in dashboard.get("templating", {}).get("list", []):
            assert re.search("[가-힣]", variable["description"])
        panels = list(_panels(dashboard))
        assert len({panel["id"] for panel in panels}) == len(panels)
        for panel in panels:
            assert not re.search("[가-힣]", panel["title"])
            content = (panel["options"]["content"] if panel["type"] == "text"
                       else panel["description"])
            assert re.search("[가-힣]", content)
            if panel["type"] == "text":
                for url in re.findall(r"\]\((/d/[^)]+)\)", content):
                    assert url.split("?", 1)[0].split("/")[2] in uids
                    assert "${__url_time_range}" in url
            pos = panel["gridPos"]
            assert pos["x"] >= 0 and pos["y"] >= 0
            assert pos["w"] > 0 and pos["h"] > 0 and pos["x"] + pos["w"] <= 24
        siblings = dashboard["panels"]
        for index, first in enumerate(siblings):
            a = first["gridPos"]
            for second in siblings[index + 1:]:
                b = second["gridPos"]
                assert not (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
                            and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


def test_optional_rows_keep_real_metric_panels_and_distinct_units():
    for name, expected in (("agent-rl-stages.json", "sandbox_io_pressure_ratio"),
                           ("data-storage.json", "smartctl_device_temperature")):
        dashboard = json.loads((ROOT / "examples/dashboards" / name).read_text())
        row = next(panel for panel in dashboard["panels"] if panel["type"] == "row")
        assert row["collapsed"] is True
        assert expected in str(row["panels"])
        assert all(panel["datasource"]["uid"] == "telemetry-prometheus" for panel in row["panels"])
    dashboard = json.loads((ROOT / "examples/dashboards/agent-rl-stages.json").read_text())
    live = next(panel for panel in dashboard["panels"] if panel["id"] == 9)
    cache = next(item for item in live["fieldConfig"]["overrides"]
                 if item["matcher"] == {"id": "byFrameRefID", "options": "B"})
    assert {prop["id"]: prop["value"] for prop in cache["properties"]}["unit"] == "percentunit"


def test_telemetry_dashboards_have_unique_uids_and_shared_cluster_filter() -> None:
    payloads = [
        json.loads((ROOT / "examples" / "dashboards" / name).read_text())
        for name in DASHBOARDS
    ]

    assert [payload["uid"] for payload in payloads] == [
        "telemetry-overview",
        "xlayer-compute-communication",
        "xlayer-data-storage",
        "agent-rl-stage-correlation",
    ]
    for payload in payloads:
        assert {item["name"] for item in payload["templating"]["list"]} >= {"cluster", "node"}
        assert all(
            panel.get("datasource", {}).get("uid") in (
                {"telemetry-prometheus", "telemetry-loki"} if payload["uid"] == "telemetry-overview"
                else {"telemetry-prometheus"})
            for panel in _panels(payload)
            if panel["type"] not in {"text", "row"}
        )
    assert "${node:queryparam}" in next(link["url"] for link in payloads[0]["links"] if link["title"] == "Compute & Communication")
    assert any(link["title"] == "Run Logs" for link in payloads[0]["links"])
    assert "${run_id:queryparam}" in next(link["url"] for link in payloads[2]["links"] if link["title"] == "Run Overview")
    storage_variables = {item["name"] for item in payloads[2]["templating"]["list"]}
    assert {"storage_system", "storage_node", "ssd"} <= storage_variables
    storage_titles = {panel["title"] for panel in _panels(payloads[2])}
    assert {
        "SSDs with critical warnings",
        "Maximum SSD temperature",
        "Maximum endurance used",
        "Minimum available spare",
        "NVMe media errors",
        "Lifetime host bytes written by SSD",
        "SSD inventory and SMART status",
    } <= storage_titles
    matrix = payloads[1]["panels"][0]
    assert "telemetry_gpu_sample_timestamp_seconds" in matrix["targets"][0]["expr"]
    assert matrix["transformations"][0]["options"]["rowField"] == "node"
    agent_rl = payloads[3]
    assert any(link["title"] == "Run Overview" for link in agent_rl["links"])
    assert {"phase", "role", "worker"} <= {
        item["name"] for item in agent_rl["templating"]["list"]
    }
    assert "Completed RL stage duration (step boundary)" in {
        panel["title"] for panel in agent_rl["panels"]
    }
    assert "Live rollout engine signals" in {
        panel["title"] for panel in agent_rl["panels"]
    }
    agent_rl_variables = {
        item["name"]: item for item in agent_rl["templating"]["list"]
    }
    assert "nodename" in agent_rl_variables["node"]["query"]
    live_rollout = next(
        panel for panel in agent_rl["panels"]
        if panel["title"] == "Live rollout engine signals"
    )
    assert all('node=~"$node"' in target["expr"] for target in live_rollout["targets"])
    completed_panels = {
        "Completed RL step",
        "Reward mean",
        "Completed RL stage duration (step boundary)",
        "Completed step throughput and response length",
    }
    for panel in agent_rl["panels"]:
        if panel["title"] in completed_panels:
            assert all(
                "training_sample_timestamp_seconds" not in target["expr"]
                for target in panel["targets"]
            )

    logs = json.loads(
        (ROOT / "examples/dashboards/run-logs.json").read_text()
    )
    assert logs["uid"] == "xlayer-run-logs"
    assert logs["panels"][0]["datasource"]["uid"] == "telemetry-loki"
    assert "| unpack | run_id=~" in logs["panels"][0]["targets"][0]["expr"]
    step_index = json.loads((ROOT / "examples/dashboards/run-overview.json").read_text())
    step_table = next(panel for panel in step_index["panels"] if panel["id"] == 20)
    step_detail = json.loads((ROOT / "examples/dashboards/cross-layer-timeline.json").read_text())
    assert step_index["uid"] == "telemetry-overview"
    assert step_detail["uid"] == "xlayer-cross-layer-timeline"
    assert 'signal="verl_step"' in step_table["targets"][0]["expr"]
    step_override = next(item for item in step_table["fieldConfig"]["overrides"]
                         if item["matcher"]["options"] == "step" and
                         any(prop["id"] == "links" for prop in item["properties"]))
    step_link = next(prop["value"][0]["url"] for prop in step_override["properties"]
                     if prop["id"] == "links")
    assert all(value in step_link for value in ('window_start_ms', 'window_end_ms', 'record_id'))
    organize = next(item for item in step_table["transformations"]
                    if item["id"] == "organize")
    assert not organize["options"]["excludeByName"]
    assert {"window_start_ms", "window_end_ms", "record_id"} <= {
        override["matcher"]["options"] for override in step_table["fieldConfig"]["overrides"]
        if any(property_["id"] == "custom.hidden" for property_ in override["properties"])
    }
    assert {panel.get("datasource", {}).get("uid") for panel in step_detail["panels"]} == {None, "telemetry-loki", "telemetry-prometheus"}
    timeline = json.loads((ROOT / "examples/dashboards/cross-layer-timeline.json").read_text())
    node_variable = next(item for item in timeline["templating"]["list"]
                         if item["name"] == "node")
    assert "nodename" in node_variable["query"]
    resource_panels = [panel for panel in timeline["panels"]
                       if panel["title"] in {"GPU utilization", "RDMA bytes per second",
                                             "Device busy ratio", "Sandbox I/O pressure (sampled)"}]
    assert all('nodename=~"$node"' in target["expr"]
               for panel in resource_panels for target in panel["targets"])
    sandbox_panels = {panel["title"]: panel for panel in _panels(agent_rl)
                      if panel["title"].startswith("Sandbox")}
    assert "sandbox_cpu_pressure_ratio" in str(sandbox_panels["Sandbox worker and device pressure"])
    assert "sandbox_oom_kill_total" in str(sandbox_panels["Sandbox worker CPU and OOM"])


def test_dashboard_list_has_a_task_based_entry_point_and_clear_order() -> None:
    payloads = [json.loads((ROOT / "examples/dashboards" / name).read_text()) for name in (*DASHBOARDS, "run-logs.json")]
    start = json.loads((ROOT / "examples/dashboards" / NAV_DASHBOARD).read_text())
    assert start["uid"] == "xlayer-start-here"
    assert start["title"] == "00 · Start Here"
    assert sorted(item["title"] for item in payloads) == [
        "01 · Run Overview",
        "02 · Agent RL Stage Correlation",
        "05 · Compute & Communication",
        "06 · Data & Storage",
        "07 · Run Logs",
    ]
    content = "\n".join(panel["options"]["content"] for panel in start["panels"]
                        if panel["type"] == "text")
    for item in payloads:
        assert f"/d/{item['uid']}" in content
        assert len(item["tags"]) == 2
        assert item["links"][0]["title"] == "Start Here"
        assert item["links"][0]["url"].startswith("/d/xlayer-start-here?")
        assert item["links"][0]["keepTime"]
    assert "/d/telemetry-overview" in content


def test_server_config_accepts_an_arbitrary_named_target_list(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "run_telemetry.sh"
    environment = os.environ | {
        "CLUSTER_NAME": "next-cluster",
        "TELEMETRY_TARGETS": "trainer-0=10.0.0.10,rollout-0=rollout.example",
        "STORAGE_TARGETS": "storage-0=10.0.1.10,storage-1=storage.example",
        "STORAGE_SYSTEM": "3fs",
        "SERVER_CONFIG_ONLY": "1",
        "OUTPUT_DIR": str(tmp_path / "monitoring"),
    }

    result = subprocess.run(
        ["bash", str(script), "server"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config = (tmp_path / "monitoring" / "prometheus.yml").read_text()
    assert "targets: ['10.0.0.10:19100']" in config
    assert "targets: ['rollout.example:19100']" in config
    assert "cluster: next-cluster" in config
    assert "nodename: trainer-0" in config
    assert "nodename: rollout-0" in config
    assert "job_name: storage-smart" in config
    assert "targets: ['10.0.1.10:19633']" in config
    assert "targets: ['storage.example:19633']" in config
    assert "storage_system: 3fs" in config
    assert "nodename: storage-0" in config
    assert {
        path.name for path in (tmp_path / "monitoring" / "dashboards").iterdir()
    } == set(DASHBOARDS) | {NAV_DASHBOARD, "workspace-overview.json", "workspace-focus.json"}
    provisioned = json.loads((tmp_path / "monitoring" / "dashboards" / "agent-rl-stages.json").read_text())
    assert not any(link["title"] == "Bottleneck Summary" for link in provisioned["links"])
    start = json.loads((tmp_path / "monitoring/dashboards/start-here.json").read_text())
    assert "requires Loki" in str(start)
    assert not re.search(r"\]\(/d/xlayer-bottleneck-summary[?)&]", str(start))


def test_server_config_rejects_duplicate_target_names(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "run_telemetry.sh"
    environment = os.environ | {
        "TELEMETRY_TARGETS": "worker=10.0.0.10,worker=10.0.0.11",
        "SERVER_CONFIG_ONLY": "1",
        "OUTPUT_DIR": str(tmp_path / "monitoring"),
    }

    result = subprocess.run(
        ["bash", str(script), "server"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "node names must be unique" in result.stderr


def test_server_log_config_provisions_loki_and_dashboard(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "run_telemetry.sh"
    loki = tmp_path / "loki"
    loki.write_text("#!/usr/bin/env bash\nexit 0\n")
    loki.chmod(0o755)
    output = tmp_path / "monitoring"
    environment = os.environ | {
        "CLUSTER_NAME": "training-cluster",
        "TELEMETRY_TARGETS": "trainer-0=10.0.0.10,rollout-0=10.0.0.11",
        "ENABLE_LOGS": "1",
        "LOKI": str(loki),
        "LOKI_LISTEN_ADDR": "192.168.0.1",
        "SERVER_CONFIG_ONLY": "1",
        "OUTPUT_DIR": str(output),
    }

    result = subprocess.run(
        ["bash", str(script), "server"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "http_listen_address: 192.168.0.1" in (output / "loki.yaml").read_text()
    assert "retention_period: 168h" in (output / "loki.yaml").read_text()
    assert "uid: telemetry-loki" in (
        output / "provisioning/datasources/default.yaml"
    ).read_text()
    assert "url: http://192.168.0.1:13100" in (
        output / "provisioning/datasources/default.yaml"
    ).read_text()
    assert {path.name for path in (output / "dashboards").iterdir()} == set(DASHBOARDS) | {NAV_DASHBOARD, "workspace-overview.json", "workspace-focus.json", "run-logs.json", "bottleneck-summary.json", "cross-layer-timeline.json"}


def test_provisioning_loki_toggle_removes_dangling_links_and_preserves_custom_files(tmp_path):
    script = ROOT / 'scripts/provision_dashboards.py'
    import sys
    subprocess.run([sys.executable, str(script), '--output', str(tmp_path), '--enable-logs'], check=True)
    assert (tmp_path / 'bottleneck-summary.json').exists()
    assert len(list(tmp_path.glob('*.json'))) == 10
    for retired in ('step-explorer.json', 'step-detail.json'):
        (tmp_path / retired).write_text('{"uid":"retired"}\n')
    custom = tmp_path / 'my-dashboard.json'
    custom.write_text('{"uid":"custom"}\n')
    subprocess.run([sys.executable, str(script), '--output', str(tmp_path)], check=True)
    assert not (tmp_path / 'bottleneck-summary.json').exists()
    assert not (tmp_path / 'step-explorer.json').exists()
    assert not (tmp_path / 'step-detail.json').exists()
    assert len(list(tmp_path.glob('*.json'))) == 8  # Seven owned plus the custom file.
    assert custom.read_text() == '{"uid":"custom"}\n'
    for path in tmp_path.glob('*.json'):
        if path == custom:
            continue
        assert path.stat().st_mode & 0o777 == 0o644
        dashboard = json.loads(path.read_text())
        assert not any('/d/xlayer-bottleneck-summary' in link.get('url', '') for link in dashboard['links'])
        assert all(panel.get('datasource', {}).get('uid') != 'telemetry-loki' for panel in _panels(dashboard))
        for panel in _panels(dashboard):
            if panel['type'] == 'text':
                assert not re.search(r'\]\(/d/xlayer-(?:step|bottleneck|cross-layer|run-logs)',
                                     panel['options']['content'])
    subprocess.run([sys.executable, str(script), '--output', str(tmp_path), '--enable-logs'], check=True)
    agent = json.loads((tmp_path / 'agent-rl-stages.json').read_text())
    assert any('/d/xlayer-bottleneck-summary' in link['url'] for link in agent['links'])
    overview = json.loads((tmp_path / 'run-overview.json').read_text())
    assert any(panel['id'] == 20 for panel in overview['panels'])
    assert len(list(tmp_path.glob('*.json'))) == 11  # Ten owned plus custom.


def test_node_log_config_accepts_multiple_local_workload_roots(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "run_telemetry.sh"
    alloy = tmp_path / "alloy"
    alloy.write_text("#!/usr/bin/env bash\nexit 0\n")
    alloy.chmod(0o755)
    trl = tmp_path / "trl"
    verl = tmp_path / "verl"
    trl.mkdir()
    verl.mkdir()
    output = tmp_path / "monitoring"
    environment = os.environ | {
        "NODE_ADDR": "127.0.0.1",
        "NODE_NAME": "trainer-0",
        "CLUSTER_NAME": "training-cluster",
        "LOKI_PUSH_URL": "http://192.168.0.1:13100/loki/api/v1/push",
        "TELEMETRY_LOG_ROOTS": f"trl={trl},verl={verl}",
        "ALLOY": str(alloy),
        "NODE_CONFIG_ONLY": "1",
        "OUTPUT_DIR": str(output),
    }

    result = subprocess.run(
        ["bash", str(script), "node"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config = (output / "alloy.alloy").read_text()
    assert f'{trl}/*/logs/**/*.log' in config
    assert f'{verl}/*/logs/**/*.log' in config
    assert 'workload = "trl"' in config
    assert 'workload = "verl"' in config
    assert 'labels = ["filename", "run_id", "log_file"]' in config
    assert 'ignore_older_than = "24h"' in config
    assert f'{verl}/*/telemetry-events/verl-steps*.jsonl' in config
    assert f'{verl}/*/telemetry/telemetry-events/verl-steps*.jsonl' in config
    assert 'signal = "verl_step"' in config
    assert 'format = "Unix"' in config
    assert f'{verl}/*/diagnostics/investigation/*.jsonl' in config
    assert 'signal = "xlayer_diagnosis"' in config
    assert 'signal = "xlayer_event"' in config
    assert '__path_exclude__' in config
    assert 'format = "UnixNs"' in config
    assert config.count('stage.label_drop') == 2


def test_investigation_dashboards_keep_boundary_accuracy_and_navigation():
    summary = json.loads((ROOT / "examples/dashboards/bottleneck-summary.json").read_text())
    timeline = json.loads((ROOT / "examples/dashboards/cross-layer-timeline.json").read_text())
    assert {summary["uid"], timeline["uid"]} == {"xlayer-bottleneck-summary", "xlayer-cross-layer-timeline"}
    assert any("missing_evidence_summary" in str(panel) for panel in summary["panels"])
    evidence = next(panel for panel in _panels(summary) if panel["type"] == "table" and panel["title"].startswith("Evidence details"))
    assert 'row_kind="evidence"' in evidence["targets"][0]["expr"]
    assert any(item["matcher"]["options"] == "signal" for item in evidence["fieldConfig"]["overrides"])
    assert any("window_start_ms" in str(panel) and "record_id" in str(panel) for panel in summary["panels"])
    assert [panel["type"] for panel in timeline["panels"]].count("state-timeline") == 2
    assert 'record_type="span"' in next(p for p in timeline["panels"] if p["id"] == 2)["targets"][0]["expr"]
    assert "Approximate" in next(p for p in timeline["panels"] if p["id"] == 3)["title"]
    names = (NAV_DASHBOARD, *DASHBOARDS, "run-logs.json",
             "bottleneck-summary.json", "cross-layer-timeline.json")
    dashboards = [json.loads((ROOT / "examples/dashboards" / name).read_text()) for name in names]
    assert len({item["uid"] for item in dashboards}) == len(dashboards)
    assert [item["title"] for item in sorted(dashboards, key=lambda item: item["title"])] == [
        "00 · Start Here", "01 · Run Overview", "02 · Agent RL Stage Correlation",
        "03 · Bottleneck Summary", "04 · Cross-Layer Timeline",
        "05 · Compute & Communication",
        "06 · Data & Storage", "07 · Run Logs",
    ]


def test_storage_role_starts_smartctl_exporter_with_slow_polling(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "run_telemetry.sh"
    smartctl = tmp_path / "smartctl"
    exporter = tmp_path / "smartctl_exporter"
    arguments = tmp_path / "arguments.txt"
    smartctl.write_text("#!/usr/bin/env bash\nexit 0\n")
    exporter.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$EXPORTER_ARGS"\n')
    smartctl.chmod(0o755)
    exporter.chmod(0o755)
    environment = os.environ | {
        "NODE_ADDR": "127.0.0.1",
        "SMARTCTL": str(smartctl),
        "SMARTCTL_EXPORTER": str(exporter),
        "EXPORTER_ARGS": str(arguments),
        "OUTPUT_DIR": str(tmp_path / "monitoring"),
    }

    result = subprocess.run(
        ["bash", str(script), "storage"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert arguments.read_text().splitlines() == [
        f"--smartctl.path={smartctl}",
        "--smartctl.interval=60s",
        "--web.listen-address=127.0.0.1:19633",
    ]


def test_storage_role_wraps_smartctl_with_sudo_when_forced(tmp_path: Path) -> None:
    # /dev/nvmeN (the admin-passthrough device SMART needs) stays root:root
    # 0600 even when the sibling block device is disk-group readable, so
    # smartctl_exporter gets "Permission denied" and reports no SMART fields
    # as a plain user. SMARTCTL_SUDO=1
    # forces the sudo wrapper without depending on the test host's own sudo
    # configuration.
    script = ROOT / "scripts" / "run_telemetry.sh"
    smartctl = tmp_path / "smartctl"
    exporter = tmp_path / "smartctl_exporter"
    arguments = tmp_path / "arguments.txt"
    smartctl.write_text("#!/usr/bin/env bash\nexit 0\n")
    exporter.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$EXPORTER_ARGS"\n')
    smartctl.chmod(0o755)
    exporter.chmod(0o755)
    sudo = tmp_path / "sudo"
    sudo.write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n')
    sudo.chmod(0o755)
    output_dir = tmp_path / "monitoring"
    environment = os.environ | {
        "NODE_ADDR": "127.0.0.1",
        "SMARTCTL": str(smartctl),
        "SMARTCTL_EXPORTER": str(exporter),
        "SMARTCTL_SUDO": "1",
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "EXPORTER_ARGS": str(arguments),
        "OUTPUT_DIR": str(output_dir),
    }

    result = subprocess.run(
        ["bash", str(script), "storage"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    wrapper = output_dir / "smartctl-sudo"
    assert arguments.read_text().splitlines()[0] == f"--smartctl.path={wrapper}"
    assert os.access(wrapper, os.X_OK)
    wrapper_text = wrapper.read_text()
    assert "sudo -n" in wrapper_text
    assert str(smartctl) in wrapper_text


def test_storage_role_skips_sudo_when_smartctl_already_has_permission(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "run_telemetry.sh"
    smartctl = tmp_path / "smartctl"
    exporter = tmp_path / "smartctl_exporter"
    arguments = tmp_path / "arguments.txt"
    smartctl.write_text(
        '#!/usr/bin/env bash\n'
        'case "$1" in\n'
        '  --scan) echo "/dev/nvme0 -d nvme # comment" ;;\n'
        '  -i) exit 0 ;;\n'
        '  *) exit 0 ;;\n'
        'esac\n'
    )
    exporter.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$EXPORTER_ARGS"\n')
    smartctl.chmod(0o755)
    exporter.chmod(0o755)
    environment = os.environ | {
        "NODE_ADDR": "127.0.0.1",
        "SMARTCTL": str(smartctl),
        "SMARTCTL_EXPORTER": str(exporter),
        "EXPORTER_ARGS": str(arguments),
        "OUTPUT_DIR": str(tmp_path / "monitoring"),
    }

    result = subprocess.run(
        ["bash", str(script), "storage"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert arguments.read_text().splitlines()[0] == f"--smartctl.path={smartctl}"


def test_dashboards_keep_matrix_and_freshness_scopes_separate() -> None:
    """A fresh rank/cluster must not mask another rank's stale or colliding cell."""
    for name in DASHBOARDS:
        payload = json.loads((ROOT / "examples/dashboards" / name).read_text())
        variables = {v["name"]: v for v in payload["templating"]["list"]}
        for panel in payload["panels"]:
            for target in panel.get("targets", []):
                expr = target["expr"]
                assert 'cluster=~"$cluster"' in expr
                if "telemetry_topology_" in expr:
                    assert "max by (cluster," in expr
                    assert '$node' not in expr  # The publisher need not be the selected node.
                if "$training_max_age" in expr:
                    assert variables["training_max_age"]["current"]["value"] == "300"
                    assert "and on(cluster, instance, run_id, producer, role, worker_id, node, local_rank)" in expr
                if "telemetry_gpu_" in expr and "sample age" not in panel["title"].lower():
                    assert "telemetry_gpu_sample_timestamp_seconds" in expr
                    assert "< 30" in expr
            if panel["title"] == "GPU allocation matrix":
                expr = panel["targets"][0]["expr"]
                assert '"cluster", "instance", "run_id", "producer", "role", "worker_id"' in expr
                assert "$training_max_age" in expr
            if panel["title"] in {"Compute topology matrix", "Storage topology matrix"}:
                expr = panel["targets"][0]["expr"]
                assert '"cluster", "source"' in expr and '"cluster", "destination"' in expr
                options = panel["transformations"][0]["options"]
                assert (options["rowField"], options["columnField"]) == ("source_key", "destination_key")
            if panel["title"] == "Training sample age by worker":
                assert "max(" not in panel["targets"][0]["expr"]
                assert "$training_max_age" not in panel["targets"][0]["expr"]
            if panel["type"] == "stat":
                assert all(t.get("instant") for t in panel["targets"])


def test_storage_role_fails_before_exporter_when_sudo_denied(tmp_path: Path) -> None:
    smartctl = tmp_path / "smartctl"
    exporter = tmp_path / "exporter"
    sudo = tmp_path / "sudo"
    marker = tmp_path / "started"
    smartctl.write_text("#!/bin/sh\nexit 0\n")
    sudo.write_text("#!/bin/sh\nexit 1\n")
    exporter.write_text('#!/bin/sh\ntouch "$MARKER"\n')
    for path in (smartctl, exporter, sudo):
        path.chmod(0o755)
    env = os.environ | {
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "SMARTCTL": str(smartctl),
        "SMARTCTL_EXPORTER": str(exporter),
        "SMARTCTL_SUDO": "1",
        "NODE_ADDR": "127.0.0.1",
        "OUTPUT_DIR": str(tmp_path / "output"),
        "MARKER": str(marker),
    }
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/run_telemetry.sh"), "storage"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "preflight failed" in result.stderr
    assert not marker.exists()


def test_healthy_ssd_count_preserves_zero_without_faking_missing_data() -> None:
    dashboard = json.loads(
        (ROOT / "examples/dashboards/data-storage.json").read_text()
    )
    panel = next(p for p in _panels(dashboard) if p["title"] == "SSDs with critical warnings")
    expr = panel["targets"][0]["expr"]
    assert expr.startswith("sum(") and "!= bool 0" in expr
    assert "or vector(0)" not in expr


def test_investigation_links_preserve_selected_interval_and_identity():
    index = json.loads((ROOT / 'examples/dashboards/run-overview.json').read_text())
    table = next(panel for panel in _panels(index) if panel['type'] == 'table')
    for field, destination in [('step', 'xlayer-cross-layer-timeline'),
                               ('step_duration_seconds', 'xlayer-bottleneck-summary'),
                               ('stage_summary', 'xlayer-cross-layer-timeline')]:
        item = next(item for item in table['fieldConfig']['overrides']
                    if item['matcher']['options'] == field and
                    any(prop['id'] == 'links' for prop in item['properties']))
        links = next(prop['value'] for prop in item['properties'] if prop['id'] == 'links')
        assert len(links) == 1  # Grafana tables use one click for a single link.
        assert links[0]['url'].split('?')[0] == '/d/' + destination
        for identity in ('window_start_ms', 'window_end_ms', 'run_id', 'record_id', 'observer_node', 'cluster'):
            assert '${__data.fields["' + identity + '"]}' in links[0]['url']
        assert '${node:queryparam}' in links[0]['url']  # Resource node stays separate.
        assert 'var-source_node=${__data.fields["observer_node"]}' in links[0]['url']
    assert table['options']['sortBy'] == [{'displayName': 'Duration', 'desc': True}]

    summary = json.loads((ROOT / 'examples/dashboards/bottleneck-summary.json').read_text())
    comparison = next(panel for panel in _panels(summary) if panel['id'] == 4)
    signal = next(item for item in comparison['fieldConfig']['overrides']
                  if item['matcher']['options'] == 'signal')
    links = next(p['value'] for p in signal['properties'] if p['id'] == 'links')
    assert 'window_start_ms' in links[0]['url']
    assert all(field in links[1]['url'] for field in ('baseline_start_ms', 'baseline_end_ms', 'baseline_record_id'))


def test_investigation_ui_distinguishes_missing_evidence_and_respects_trace_filter():
    summary = json.loads((ROOT / 'examples/dashboards/bottleneck-summary.json').read_text())
    assert not any(panel['type'] == 'row' for panel in summary['panels'])
    comparison = next(panel for panel in summary['panels'] if panel['id'] == 4)
    assert comparison['gridPos']['y'] < next(
        panel for panel in summary['panels'] if panel['id'] == 3)['gridPos']['y']
    evidence = next(panel for panel in summary['panels'] if panel['id'] == 6)
    assert 'row_kind="evidence"' in evidence['targets'][0]['expr']
    assert summary['panels'].index(evidence) == summary['panels'].index(
        next(panel for panel in summary['panels'] if panel['id'] == 3)) + 1
    verdict = next(item for item in summary['panels'][1]['fieldConfig']['overrides']
                   if item['matcher']['options'] == 'verdict')
    values = next(p['value'] for p in verdict['properties'] if p['id'] == 'mappings')[0]['options']
    assert values['insufficient_data']['color'] == 'gray'
    assert values['no_anomaly_observed']['color'] != 'green'
    assert all(value['text'] == key for key, value in values.items())
    timeline = json.loads((ROOT / 'examples/dashboards/cross-layer-timeline.json').read_text())
    for panel in _panels(timeline):
        for target in panel.get('targets', []):
            if 'record_type="span"' in target['expr'] or 'record_type="event"' in target['expr']:
                assert 'trace_id=~"$trace_id"' in target['expr']
    detail = json.loads((ROOT / 'examples/dashboards/cross-layer-timeline.json').read_text())
    expr = detail['panels'][1]['targets'][0]['expr']
    assert 'run_id=~"$run_id"' in expr and 'record_id=~"$record_id"' in expr


def test_readable_panels_and_short_step_samples():
    for path in (ROOT / 'examples/dashboards').glob('*.json'):
        dashboard = json.loads(path.read_text())
        for panel in _panels(dashboard):
            if panel['type'] == 'text':
                assert 'font-size:16px;line-height:1.65' in panel['options']['content']
            if panel['type'] == 'table':
                assert panel['options']['cellHeight'] == 'lg'
            if panel['type'] == 'stat':
                assert panel['options']['text']['titleSize'] >= 18
                assert panel['options']['text']['valueSize'] <= 40
            if path.name in {'step-detail.json', 'cross-layer-timeline.json'} and panel['type'] == 'timeseries':
                assert panel['fieldConfig']['defaults']['custom']['showPoints'] != 'never'


def test_navigation_round_trip_keeps_observer_resource_and_selected_record():
    paths = sorted((ROOT / 'examples/dashboards').glob('*.json'))
    dashboards = {item['uid']: item for item in
                  (json.loads(path.read_text()) for path in paths)}
    context = {'cluster', 'node', 'source_node', 'record_id', 'trace_id',
               'diagnosis_method', 'gpu', 'engine', 'sandbox_node'}
    for dashboard in dashboards.values():
        variables = {item['name'] for item in dashboard['templating']['list']}
        assert context <= variables
        for link in dashboard['links']:
            assert link['keepTime']
            for name in context:
                assert '${' + name + ':queryparam}' in link['url']
            referenced = set(re.findall(r'\$\{([a-z_]+):', link['url']))
            assert referenced <= variables, (dashboard['uid'], referenced - variables)
            if dashboard['uid'] != 'xlayer-run-logs' and '/d/xlayer-run-logs?' not in link['url']:
                assert '${run_id:queryparam}' in link['url']
        assert all('var-source_node=${node' not in link['url'] for link in dashboard['links'])

    summary = dashboards['xlayer-bottleneck-summary']
    for panel in _panels(summary):
        for target in panel.get('targets', []):
            assert 'node=~"$source_node"' in target['expr']
            assert 'node=~"$node"' not in target['expr']
    evidence = next(panel for panel in summary['panels'] if panel['id'] == 6)
    for override in evidence['fieldConfig']['overrides']:
        for property_ in override['properties']:
            if property_['id'] == 'links':
                assert all('${node:queryparam}' in link['url'] for link in property_['value'])


def test_collection_health_does_not_fabricate_workload_health_or_require_loki():
    start = json.loads((ROOT / 'examples/dashboards/start-here.json').read_text())
    assert len(start['panels']) < 9
    stats = {panel['title']: panel for panel in start['panels'] if panel['type'] == 'stat'}
    assert set(stats) == {'Collector health', 'Application age',
                          'GPU sample age', 'Completed step'}
    for panel in start['panels']:
        if panel['type'] == 'text':
            continue
        assert panel['datasource']['uid'] == 'telemetry-prometheus'
        assert all('or vector(0)' not in target['expr'] for target in panel['targets'])
        assert panel['fieldConfig']['defaults']['noValue'] == 'N/A'
    assert stats['GPU sample age']['fieldConfig']['defaults']['thresholds']['steps'][2]['value'] == 30
    assert stats['Application age']['fieldConfig']['defaults']['thresholds']['steps'][2]['value'] == 300
    overview = json.loads((ROOT / 'examples/dashboards/run-overview.json').read_text())
    assert not any('GPU utilization matrix' in panel['title'] for panel in _panels(overview))
    optional = next(panel for panel in overview['panels']
                    if panel['title'] == 'Application SDK signals (optional)')
    assert optional['collapsed']
    assert 'training_tokens_per_second' in str(optional)
    assert 'training_loss' in str(optional)


def test_consolidated_inventory_preserves_selection_and_temporal_evidence():
    dashboards = [json.loads(path.read_text())
                  for path in (ROOT / 'examples/dashboards').glob('*.json')]
    assert len(dashboards) == 8
    for dashboard in dashboards:
        assert 'xlayer-step-explorer' not in str(dashboard)
        assert 'xlayer-step-detail' not in str(dashboard)
        assert len(dashboard['links']) <= 7
        destinations = [link['url'].split('?', 1)[0] for link in dashboard['links']]
        assert len(destinations) == len(set(destinations))
        assert '/d/' + dashboard['uid'] not in destinations
    overview = next(item for item in dashboards if item['uid'] == 'telemetry-overview')
    table = next(panel for panel in overview['panels'] if panel['id'] == 20)
    assert table['type'] == 'table' and table['datasource']['uid'] == 'telemetry-loki'
    timeline = next(item for item in dashboards if item['uid'] == 'xlayer-cross-layer-timeline')
    selected = next(panel for panel in timeline['panels'] if panel['id'] == 20)
    assert 'record_id=~"$record_id"' in selected['targets'][0]['expr']
    rows = {panel['title']: panel for panel in timeline['panels'] if panel['type'] == 'row'}
    assert all(panel['collapsed'] for panel in rows.values())
    assert 'Host CPU busy' in str(rows['Host / network / local storage'])
    assert 'node_network_receive_bytes_total' in str(rows['Host / network / local storage'])
    assert 'node_disk_written_bytes_total' in str(rows['Host / network / local storage'])
    assert 'record_type="event"' in str(rows['Span and event records']).replace('\\"', '"')
    assert 'node_timex_sync_status' in str(rows['Clock quality'])


def test_consolidated_timeline_retains_independent_resource_selectors():
    timeline = json.loads((ROOT / 'examples/dashboards/cross-layer-timeline.json').read_text())
    panels = {panel['id']: panel for panel in _panels(timeline)}
    assert 'nodename=~"$source_node"' not in panels[4]['targets'][0]['expr']
    assert 'gpu=~"$gpu"' in panels[4]['targets'][0]['expr']
    assert 'instance=~"$engine"' in panels[5]['targets'][0]['expr']
    assert 'nodename=~"$sandbox_node"' in panels[11]['targets'][0]['expr']
    variables = {item['name']: item for item in timeline['templating']['list']}
    assert not variables['sandbox_node'].get('hide')
    assert not variables['source_node'].get('hide')


def test_sandbox_node_and_engine_filters_do_not_change_rollout_context():
    agent = json.loads((ROOT / 'examples/dashboards/agent-rl-stages.json').read_text())
    variables = {item['name']: item for item in agent['templating']['list']}
    assert 'instance)' in variables['engine']['query']
    row = next(panel for panel in agent['panels'] if panel['type'] == 'row')
    for panel in row['panels']:
        assert all('nodename=~"$sandbox_node"' in target['expr'] for target in panel['targets'])
        assert all('nodename=~"$node"' not in target['expr'] for target in panel['targets'])
    for panel in agent['panels']:
        if panel['id'] in {9, 10}:
            assert all('node=~"$node",instance=~"$engine"' in target['expr']
                       for target in panel['targets'])
        if panel['id'] == 4:
            assert 'nodename=~"$source_node"' in panel['targets'][0]['expr']
            assert '$sandbox_node' not in panel['targets'][0]['expr']


def test_log_directory_and_application_context_remain_independent():
    logs = json.loads((ROOT / 'examples/dashboards/run-logs.json').read_text())
    variables = {item['name']: item for item in logs['templating']['list']}
    assert variables['run_id']['label'] == 'Log directory'  # Existing URLs still filter logs.
    assert variables['telemetry_run_id']['label'] == 'Run context'
    assert '| run_id=~"$run_id"' in logs['panels'][0]['targets'][0]['expr']
    assert 'signal!="xlayer_diagnosis"' in logs['panels'][0]['targets'][0]['expr']
    assert all('var-run_id=${telemetry_run_id:percentencode}' in link['url']
               and 'var-log_run_id=${run_id:percentencode}' in link['url'] for link in logs['links'])
    summary = json.loads((ROOT / 'examples/dashboards/bottleneck-summary.json').read_text())
    link = next(link for panel in summary['panels'] if panel['type'] == 'text'
                for link in re.findall(r'\]\((/d/xlayer-run-logs[^)]+)\)', panel['options']['content']))
    assert 'var-telemetry_run_id=${run_id:percentencode}' in link
    assert 'var-run_id=${log_run_id:percentencode}' in link


def test_annotations_show_observed_completion_without_inventing_phase_boundaries():
    for name in ('cross-layer-timeline',):
        dashboard = json.loads((ROOT / f'examples/dashboards/{name}.json').read_text())
        annotation, = dashboard['annotations']['list']
        assert 'approximate' in annotation['name']
        assert annotation['datasource']['uid'] == 'telemetry-loki'
        assert annotation['enable'] and not annotation['hide']
        assert 'signal="verl_step"' in annotation['expr']
        assert 'record_id=~"$record_id"' in annotation['expr']
        assert 'node=~"$source_node"' in annotation['expr']
        assert 'logger observation' in annotation['expr']
        assert annotation['maxLines'] <= 100
    # No optional Loki dependency is introduced into metrics-only deployments.
    for name in (NAV_DASHBOARD, *DASHBOARDS):
        dashboard = json.loads((ROOT / 'examples/dashboards' / name).read_text())
        assert not dashboard['annotations']['list']


def test_loki_row_pivots_use_stream_cluster_and_collector_identity():
    for path in (ROOT / 'examples/dashboards').glob('*.json'):
        dashboard = json.loads(path.read_text())
        for panel in _panels(dashboard):
            if panel['type'] != 'table' or panel['datasource']['uid'] != 'telemetry-loki':
                continue
            query = panel['targets'][0]['expr']
            assert '" .cluster .node (__line__)' in query
            assert panel['transformations'][1]['options']['source'] == 'record'
            assert not panel['transformations'][1]['options']['replace']
            for override in panel['fieldConfig']['overrides']:
                for prop in override['properties']:
                    if prop['id'] == 'links':
                        assert all('var-cluster=${__data.fields["cluster"]}' in link['url']
                                   for link in prop['value'])
    timeline = json.loads((ROOT / 'examples/dashboards/cross-layer-timeline.json').read_text())
    for panel in timeline['panels']:
        if panel['type'] == 'state-timeline':
            keep = panel['transformations'][-1]
            assert keep['id'] == 'filterFieldsByName'
            assert set(keep['options']['include']['names']) == {
                'Start Time', 'End Time', 'Phase' if panel['id'] == 2 else 'Step'}
    spans = next(panel for panel in _panels(timeline) if panel['id'] == 9)
    links = next(prop['value'] for override in spans['fieldConfig']['overrides']
                 for prop in override['properties'] if prop['id'] == 'links')
    summary = next(link for link in links if '/d/xlayer-bottleneck-summary?' in link['url'])
    assert '${__url_time_range}' in summary['url']  # A subspan must not hide its step diagnosis.
    assert '${record_id:queryparam}' in summary['url']
    assert '${source_node:queryparam}' in summary['url']


def test_start_health_queries_with_real_promtool(tmp_path):
    tool = os.environ.get('PROMTOOL') or shutil.which('promtool')
    if not tool:
        pytest.skip('set PROMTOOL to validate real dashboard PromQL')
    start = json.loads((ROOT / 'examples/dashboards/start-here.json').read_text())
    stats = {panel['title']: panel for panel in start['panels'] if panel['type'] == 'stat'}

    def expr(name, run='actual-run'):
        query = stats[name]['targets'][0]['expr']
        return query.replace('$cluster', 'cluster-a').replace('$node', 'gpu-0').replace(
            '$source_node', 'gpu-0').replace('$run_id', run)

    # Different scrape addresses, node names and cluster names are deliberate.
    lines = ['evaluation_interval: 1m', 'tests:', '  - interval: 1m', '    input_series:']
    series = [('up', 'cluster-a', 'gpu-0', '', '1+0x20'),
              ('training_sample_timestamp_seconds', 'cluster-a', 'gpu-0', 'actual-run', '240+0x20'),
              ('training_sample_timestamp_seconds', 'cluster-b', 'gpu-0', 'actual-run', '0+0x20'),
              ('training_step', 'cluster-a', 'gpu-0', 'actual-run', '2+0x20')]
    for metric, cluster, node, run, values in series:
        labels = f'job="telemetry",cluster="{cluster}",nodename="{node}",instance="10.0.0.1:19100"'
        if run:
            labels += f',run_id="{run}"'
        lines += [f"      - series: '{metric}{{{labels}}}'", f"        values: '{values}'"]
    lines.append('    promql_expr_test:')
    cases = [(expr('Application age'), 960), (expr('Completed step'), 2),
             (expr('GPU sample age'), None), (expr('Application age', 'missing-run'), None)]
    for query, value in cases:
        lines += [f"      - expr: '{query}'", '        eval_time: 20m']
        if value is None:
            lines.append('        exp_samples: []')
        else:
            lines += ['        exp_samples:', "          - labels: '{}'", f'            value: {value}']
    fixture = tmp_path / 'health-queries.yaml'
    fixture.write_text('\n'.join(lines) + '\n')
    result = subprocess.run([tool, 'test', 'rules', str(fixture)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_baseline_pivot_clears_current_trace_without_losing_resource_context():
    dashboard = json.loads((ROOT / "examples/dashboards/bottleneck-summary.json").read_text())
    comparison = next(panel for panel in _panels(dashboard) if panel["id"] == 4)
    signal = next(item for item in comparison["fieldConfig"]["overrides"]
                  if item["matcher"]["options"] == "signal")
    current, baseline = next(p["value"] for p in signal["properties"] if p["id"] == "links")
    assert "${trace_id:queryparam}" in current["url"]
    assert "${trace_id:queryparam}" not in baseline["url"]
    assert "var-trace_id=.*" in baseline["url"]
    assert "baseline_record_id" in baseline["url"]
    for key in ("node", "gpu", "engine", "sandbox_node"):
        assert "${" + key + ":queryparam}" in baseline["url"]
    order = comparison["transformations"][-1]["options"]["indexByName"]
    assert [order[key] for key in ("signal", "current", "baseline", "delta_percent")] == list(range(4))


def test_compute_prioritizes_memory_and_communication_before_hardware_detail():
    dashboard = json.loads((ROOT / "examples/dashboards/compute-communication.json").read_text())
    panels = {panel["id"]: panel for panel in dashboard["panels"]}
    assert panels[6]["gridPos"]["y"] < panels[8]["gridPos"]["y"] < panels[4]["gridPos"]["y"]


def test_subsystem_rows_work_without_run_and_preserve_native_semantics():
    dashboard = json.loads((ROOT / 'examples/dashboards/agent-rl-stages.json').read_text())
    panels = {p['id']: p for p in _panels(dashboard)}
    for panel_id in range(20, 27):
        panel = panels[panel_id]
        for target in panel['targets']:
            assert 'run_id' not in target['expr']
            assert 'cluster=~"$cluster"' in target['expr']
            assert 'node=~"$node"' in target['expr']
        assert panel['fieldConfig']['defaults']['noValue'] == 'N/A'
    assert 'histogram_quantile' in str(panels[21]['targets'])
    assert 'instance, model_name, le' in str(panels[21]['targets'])
    assert all('rate(' not in str(panels[i]['targets']) for i in [22, 23, 24, 25])
    assert 'Name=~"CPU|GPU"' in panels[24]['targets'][0]['expr']
    assert panels[25]['fieldConfig']['defaults']['unit'] == 'bytes'
    variables = {v['name']: v for v in dashboard['templating']['list']}
    assert variables['cluster']['query'] == 'label_values(up, cluster)'
    assert 'job="native"' in variables['node']['query']
    assert 'nodename' in variables['node']['query']


@pytest.mark.parametrize('logs', [False, True])
def test_generated_views_reuse_panels_and_switch_context(tmp_path, logs):
    import sys
    subprocess.run([sys.executable, str(ROOT / 'scripts/provision_dashboards.py'),
                    '--output', str(tmp_path)] + (['--enable-logs'] if logs else []), check=True)
    dashboards = [json.loads(p.read_text()) for p in tmp_path.glob('*.json')]
    by_uid = {d['uid']: d for d in dashboards}
    for dashboard in dashboards:
        links = dashboard['links'][:3]
        assert [l['title'] for l in links] == ['View: Guided', 'View: Overview', 'View: Focus']
        for link in links:
            assert link['keepTime'] and not link['targetBlank']
            assert link['url'].split('?')[0].split('/')[2] in by_uid
            assert '${cluster:queryparam}' in link['url']
            assert '${node:queryparam}' in link['url']
            assert '${record_id:queryparam}' in link['url']
            if dashboard['uid'] == 'xlayer-run-logs':
                assert 'var-run_id=${telemetry_run_id:percentencode}' in link['url']
            else:
                assert '${run_id:queryparam}' in link['url']
    for style in ['overview', 'focus']:
        dashboard = by_uid['xlayer-workspace-' + style]
        flat = list(_panels(dashboard))
        assert len({p['id'] for p in flat}) == len(flat)
        variables = {v['name'] for v in dashboard['templating']['list']}
        for panel in flat:
            for target in panel.get('targets', []):
                referenced = set(re.findall(r'\$([a-z][a-z_]+)', target['expr']))
                assert referenced <= variables
            if panel['type'] == 'timeseries':
                original = next(p for p in _panels(by_uid[panel['links'][0]['url'].split('?')[0].split('/')[2]])
                                if p.get('targets') == panel['targets'])
                assert panel['fieldConfig'] == original['fieldConfig']
        series = [p for p in flat if p['type'] == 'timeseries']
        assert len(series) == 6
        assert {p['gridPos']['w'] for p in series} == ({8} if style == 'overview' else {24})
        if style == 'focus':
            assert sum(p.get('collapsed', False) for p in flat) == 5


@pytest.mark.parametrize('logs', [False, True])
def test_color_presets_preserve_context_and_drilldown(tmp_path, logs):
    import sys
    subprocess.run([sys.executable, str(ROOT / 'scripts/provision_dashboards.py'),
                    '--output', str(tmp_path)] + (['--enable-logs'] if logs else []), check=True)
    for path in tmp_path.glob('*.json'):
        dashboard = json.loads(path.read_text())
        theme = next(v for v in dashboard['templating']['list'] if v['name'] == 'xlayer_theme')
        assert theme['hide'] == 2 and theme['current']['value'] == ''
        header = next(p for p in dashboard['panels'] if p['type'] == 'text' and p['gridPos']['y'] == 0)
        colors = [(name, url) for url, name in re.findall(
            r'<a target="_top" href="([^"]+)">Color: ([^<]+)</a>', header['options']['content'])]
        assert [name for name, url in colors] == ['Dark', 'Sapphire', 'Desert']
        for (_, url), value in zip(colors, ['dark', 'sapphiredusk', 'desertbloom']):
            assert url.startswith('/d/' + dashboard['uid'] + '?')
            assert f'&theme={value}&var-xlayer_theme={value}' in url
            assert url.count('var-xlayer_theme=') == 1
            assert '${run_id:queryparam}' in url and '${node:queryparam}' in url
            assert '${__url_time_range}' in url
        for link in dashboard['links']:
            if link['url'].startswith('/d/'):
                assert '&theme=${xlayer_theme:percentencode}' in link['url']
                assert '${xlayer_theme:queryparam}' in link['url']
        for panel in _panels(dashboard):
            for link in panel.get('links', []):
                if link['url'].startswith('/d/'):
                    assert '${xlayer_theme:queryparam}' in link['url']


def test_investigation_tables_hide_unknown_metadata_without_dropping_link_fields():
    for name, title, fields in (
        ("run-overview", "Completed steps", ("step", "step_duration_seconds")),
        ("bottleneck-summary", "Measured changes", ("current", "baseline", "delta_percent", "observation_scope")),
    ):
        dashboard = json.loads((ROOT / f"examples/dashboards/{name}.json").read_text())
        panel = next(p for p in dashboard["panels"] if p["title"].startswith(title))
        assert panel["fieldConfig"]["defaults"]["custom"]["hidden"] is True
        allowed = panel["fieldConfig"]["overrides"][0]["matcher"]["options"]
        assert all(re.fullmatch(allowed, field) for field in fields)
        assert not re.fullmatch(allowed, "new_internal_metadata")
        # Hidden record/time/context fields remain in the frame for Grafana data links.
        assert all(not t.get("options", {}).get("excludeByName") for t in panel["transformations"])
        assert "window_start_ms" in json.dumps(panel["fieldConfig"])
    for name in ("start-here", "run-overview"):
        dashboard = json.loads((ROOT / f"examples/dashboards/{name}.json").read_text())
        panel = next(p for p in dashboard["panels"] if p["title"] == "Collector health")
        assert panel["targets"][0]["expr"].startswith("min(up{")


def test_workspace_header_reserves_space_for_wrapped_context():
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("dashboard_views", ROOT / "scripts/dashboard_views.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    sources = {path: json.loads(path.read_text())
               for path in (ROOT / "examples/dashboards").glob("*.json")}
    for view in module.build_views(sources).values():
        header = view["panels"][0]
        assert header["gridPos"]["h"] == 4
        assert min(panel["gridPos"]["y"] for panel in view["panels"][1:]) == 4
        module.add_color_links(view)
        assert header["gridPos"]["h"] == 5
        assert min(panel["gridPos"]["y"] for panel in view["panels"][1:]) == 5
