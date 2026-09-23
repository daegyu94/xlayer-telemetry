"""Local, read-only step drilldown for a recorded VERL run."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen


ASSET = Path(__file__).with_name("step_explorer.html")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def load_steps(run_root: Path) -> list[dict[str, Any]]:
    path = run_root / "telemetry-events" / "verl-steps.jsonl"
    steps: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            window = record.get("analysis_window") or {}
            if (
                record.get("record_type") != "verl_step_observation"
                or not isinstance(record.get("record_id"), str)
                or type(record.get("step")) is not int
                or not isinstance(window.get("start"), (int, float))
                or not isinstance(window.get("end"), (int, float))
                or not math.isfinite(window["start"])
                or not math.isfinite(window["end"])
                or window["start"] >= window["end"]
            ):
                continue
            steps.append(record)
    return sorted(steps, key=lambda item: (item["observed_at"], item["step"]))


def _request_json(base_url: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = base_url.rstrip("/") + path + "?" + urlencode(params)
    with urlopen(url, timeout=8) as response:
        return json.load(response)


def _series(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("status") != "success":
        raise ValueError(payload.get("error", "query failed"))
    result = []
    for item in payload.get("data", {}).get("result", []):
        points = []
        for timestamp, raw in item.get("values", []):
            try:
                value = float(raw)
                timestamp = float(timestamp)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and math.isfinite(timestamp):
                points.append([timestamp, value])
        if points:
            result.append({"labels": item.get("metric", {}), "points": points})
    return result


def _signals(cluster: str, node: str) -> dict[str, dict[str, str]]:
    telemetry = f'job="telemetry",cluster="{_label(cluster)}",instance="{_label(node)}"'
    native = f'job="native",cluster="{_label(cluster)}",node="{_label(node)}",telemetry_source="vllm"'
    return {
        "gpu": {"title": "GPU utilization", "unit": "%", "scope": "node", "query": f'avg(telemetry_gpu_utilization_percent{{{telemetry}}})'},
        "gpu_memory": {"title": "Compute-process GPU memory", "unit": "GiB", "scope": "node processes", "query": f'sum(telemetry_gpu_process_memory_bytes{{{telemetry}}}) / 1073741824'},
        "cpu": {"title": "Host CPU busy", "unit": "%", "scope": "node", "query": f'100 * (1 - avg(rate(node_cpu_seconds_total{{{telemetry},mode="idle"}}[1m])))'},
        "memory": {"title": "Host memory used", "unit": "GiB", "scope": "node", "query": f'(node_memory_MemTotal_bytes{{{telemetry}}} - node_memory_MemAvailable_bytes{{{telemetry}}}) / 1073741824'},
        "network_rx": {"title": "Network receive", "unit": "MiB/s", "scope": "node", "query": f'sum(rate(node_network_receive_bytes_total{{{telemetry},device!~"lo|docker.*|veth.*"}}[1m])) / 1048576'},
        "network_tx": {"title": "Network transmit", "unit": "MiB/s", "scope": "node", "query": f'sum(rate(node_network_transmit_bytes_total{{{telemetry},device!~"lo|docker.*|veth.*"}}[1m])) / 1048576'},
        "disk_read": {"title": "Disk read", "unit": "MiB/s", "scope": "node", "query": f'sum(rate(node_disk_read_bytes_total{{{telemetry}}}[1m])) / 1048576'},
        "disk_write": {"title": "Disk write", "unit": "MiB/s", "scope": "node", "query": f'sum(rate(node_disk_written_bytes_total{{{telemetry}}}[1m])) / 1048576'},
        "vllm_waiting": {"title": "vLLM waiting", "unit": "requests", "scope": "shared engine", "query": f'sum(vllm:num_requests_waiting{{{native}}})'},
        "kv_store": {"title": "KV offload store", "unit": "MiB/s", "scope": "shared engine", "query": f'sum(rate(vllm:kv_offload_total_bytes_total{{{native},transfer_type="GPU_to_CPU"}}[1m])) / 1048576'},
    }


def _summary(series: list[dict[str, Any]], start: float, end: float) -> dict[str, float] | None:
    values = [value for item in series for timestamp, value in item["points"] if start <= timestamp <= end]
    if not values:
        return None
    return {"min": min(values), "mean": statistics.fmean(values), "max": max(values), "samples": len(values)}


class StepExplorer:
    def __init__(self, run_root: Path, prometheus_url: str | None, cluster: str, loki_url: str | None, log_run_id: str | None):
        self.run_root = run_root
        self.prometheus_url = prometheus_url
        self.cluster = cluster
        self.loki_url = loki_url
        self.log_run_id = log_run_id

    def steps(self) -> list[dict[str, Any]]:
        return [
            {
                "id": item["record_id"], "step": item["step"], "worker": item["worker_id"],
                "node": item["node"], "run_id": item["run_id"], "scope": item["boundary_scope"],
                "start": item["analysis_window"]["start"], "end": item["analysis_window"]["end"],
                "accuracy": item["analysis_window"].get("accuracy", "unknown"),
                "duration": item.get("step_duration_seconds"),
            }
            for item in load_steps(self.run_root)
        ]

    def detail(self, record_id: str) -> dict[str, Any]:
        record = next((item for item in load_steps(self.run_root) if item["record_id"] == record_id), None)
        if record is None:
            raise KeyError(record_id)
        window = record["analysis_window"]
        start, end = window["start"], window["end"]
        node = record["node"]
        output: dict[str, Any] = {"step": next(item for item in self.steps() if item["id"] == record_id), "stages": record.get("stage_durations_seconds", {}), "signals": {}, "logs": [], "errors": {}}
        if self.prometheus_url:
            query_step = max(2, min(15, round((end - start) / 30)))
            def fetch_signal(item: tuple[str, dict[str, str]]) -> tuple[str, dict[str, Any] | None, str | None]:
                key, signal = item
                try:
                    payload = _request_json(self.prometheus_url, "/api/v1/query_range", {
                        "query": signal["query"], "start": start, "end": end, "step": query_step,
                    })
                    values = _series(payload)
                    return key, {"title": signal["title"], "unit": signal["unit"], "scope": signal["scope"], "series": values, "summary": _summary(values, start, end)}, None
                except (OSError, ValueError, TimeoutError) as exc:
                    return key, None, str(exc)

            with ThreadPoolExecutor(max_workers=5) as pool:
                for key, signal, error in pool.map(fetch_signal, _signals(self.cluster, node).items()):
                    if error is not None:
                        output["errors"][key] = error
                    else:
                        output["signals"][key] = signal
        if self.loki_url and self.log_run_id:
            try:
                selector = f'{{cluster="{_label(self.cluster)}",node="{_label(node)}"}} | unpack | run_id="{_label(self.log_run_id)}"'
                payload = _request_json(self.loki_url, "/loki/api/v1/query_range", {
                    "query": selector, "start": int(start * 1e9), "end": int(end * 1e9),
                    "limit": 100, "direction": "FORWARD",
                })
                if payload.get("status") != "success":
                    raise ValueError(payload.get("error", "Loki query failed"))
                output["logs"] = sorted(
                    [[int(ts) / 1e9, ANSI_ESCAPE.sub("", message)] for item in payload.get("data", {}).get("result", []) for ts, message in item.get("values", [])],
                    key=lambda item: item[0],
                )[:100]
            except (OSError, ValueError, TimeoutError) as exc:
                output["errors"]["logs"] = str(exc)
        return output


def make_handler(explorer: StepExplorer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path)
            try:
                if path.path == "/":
                    body, kind = ASSET.read_bytes(), "text/html; charset=utf-8"
                elif path.path == "/api/steps":
                    body, kind = json.dumps(explorer.steps()).encode(), "application/json"
                elif path.path == "/api/step":
                    record_id = parse_qs(path.query).get("id", [""])[0]
                    body, kind = json.dumps(explorer.detail(record_id)).encode(), "application/json"
                else:
                    self.send_error(404)
                    return
            except KeyError:
                self.send_error(404, "step not found")
                return
            except OSError as exc:
                self.send_error(503, str(exc))
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Explore recorded VERL steps and nearby resource signals")
    parser.add_argument("--run-root", type=Path, required=True, help="Directory containing telemetry-events/verl-steps.jsonl")
    parser.add_argument("--prometheus-url", help="Prometheus HTTP URL; omit for step-only review")
    parser.add_argument("--cluster", default="training-cluster", help="Prometheus/Loki cluster label")
    parser.add_argument("--loki-url", help="Optional Loki HTTP URL")
    parser.add_argument("--log-run-id", help="Loki run directory label, which can differ from telemetry run_id")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not (args.run_root / "telemetry-events" / "verl-steps.jsonl").is_file():
        parser.error("run root must contain telemetry-events/verl-steps.jsonl")
    if args.loki_url and not args.log_run_id:
        parser.error("--log-run-id is required with --loki-url")
    explorer = StepExplorer(args.run_root, args.prometheus_url, args.cluster, args.loki_url, args.log_run_id)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(explorer))
    print(f"Step Explorer: http://{args.host}:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
