#!/usr/bin/env python3
"""Provision dashboard JSON with links to the enabled investigation views."""

import argparse
import json
from pathlib import Path
import re
import tempfile


SOURCE = Path(__file__).resolve().parents[1] / "examples" / "dashboards"
METRICS = {"start-here", "run-overview", "compute-communication", "data-storage", "agent-rl-stages"}
RETIRED = {"step-explorer.json", "step-detail.json"}


def provision(output, enable_logs=False):
    dashboards = {path: json.loads(path.read_text()) for path in SOURCE.glob("*.json")}
    enabled = {path: value for path, value in dashboards.items()
               if enable_logs or path.stem in METRICS}
    unavailable = {value["uid"] for path, value in dashboards.items() if path not in enabled}

    def disabled(url):
        return url.startswith("/d/") and url.split("/", 3)[2].split("?", 1)[0] in unavailable

    def panels(items):
        for panel in items:
            yield panel
            yield from panels(panel.get("panels", []))

    output.mkdir(parents=True, exist_ok=True)
    for name in RETIRED:
        (output / name).unlink(missing_ok=True)
    for path, dashboard in dashboards.items():
        destination = output / path.name
        if path not in enabled:
            destination.unlink(missing_ok=True)  # Only repository-owned optional files.
            continue
        if not enable_logs:
            # Run Overview includes a Loki step table only when logs are enabled.
            removed_height = 0
            kept = []
            for panel in dashboard["panels"]:
                if panel.get("datasource", {}).get("uid") == "telemetry-loki":
                    removed_height += panel["gridPos"]["h"]
                    continue
                panel["gridPos"]["y"] -= removed_height
                for child in panel.get("panels", []):
                    child["gridPos"]["y"] -= removed_height
                kept.append(panel)
            dashboard["panels"] = kept
        dashboard["links"] = [link for link in dashboard.get("links", [])
                              if not disabled(link.get("url", ""))]
        for panel in panels(dashboard["panels"]):
            panel["links"] = [link for link in panel.get("links", [])
                              if not disabled(link.get("url", ""))]
            if panel["type"] == "text":
                panel["options"]["content"] = re.sub(
                    r"\[([^\]]+)\]\((/d/[^)]+)\)",
                    lambda match: match[1] + " (requires Loki)" if disabled(match[2]) else match[0],
                    panel["options"]["content"],
                )
            config = panel.get("fieldConfig", {}).get("defaults", {})
            if "links" in config:
                config["links"] = [link for link in config["links"] if not disabled(link.get("url", ""))]
            for override in panel.get("fieldConfig", {}).get("overrides", []):
                for prop in override["properties"]:
                    if prop["id"] == "links":
                        prop["value"] = [link for link in prop["value"] if not disabled(link.get("url", ""))]
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output,
                                         prefix=".dashboard-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(dashboard, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        try:
            # Grafana may run as a different UID in a read-only bind mount.
            temporary.chmod(0o644)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--enable-logs", action="store_true")
    args = parser.parse_args()
    provision(args.output, args.enable_logs)
