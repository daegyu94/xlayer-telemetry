#!/usr/bin/env python3
"""Record the current Grafana investigation workflow as a GIF.

Requires optional Playwright (with Chromium) and ffmpeg/ffprobe.
Telemetry and timestamps come from the supplied Grafana context; this tool
does not create observations or start a workload.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlencode


def capture(context, output):
    from playwright.sync_api import sync_playwright

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe are required")
    frames = []
    errors = []
    variables = {"var-" + key: value for key, value in context["variables"].items()}
    common = {**variables, "from": context["from_ms"], "to": context["to_ms"], "refresh": "off"}
    selected = {**common, "from": context["step_from_ms"], "to": context["step_to_ms"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="xlayer-gif-") as temporary:
        directory = Path(temporary)
        with sync_playwright() as playwright:
            executable = shutil.which("google-chrome") or shutil.which("chromium")
            browser = playwright.chromium.launch(
                headless=True, executable_path=executable,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))

            def visit(uid, window=common):
                page.goto(context["grafana_url"].rstrip("/") + "/d/" + uid + "?" + urlencode(window))
                page.wait_for_timeout(2200)

            def scroll(amount):
                page.mouse.move(1100, 750)
                page.mouse.wheel(0, amount)
                page.wait_for_timeout(1100)

            def show_panel(title):
                # Grafana lazily mounts lower panels; reveal the current table
                # without treating its heading as a collapsed-row toggle.
                heading = page.get_by_text(title, exact=True).first
                for _ in range(10):
                    if heading.count():
                        break
                    scroll(500)
                heading.scroll_into_view_if_needed(timeout=5000)

            def expand(title):
                scroll(5000)
                heading = page.get_by_text(title, exact=True)
                heading.click()
                page.wait_for_timeout(1500)
                box = heading.bounding_box()
                if box:
                    scroll(box["y"] - 390)

            def frame(label, seconds=5):
                body = page.locator("body").inner_text()
                if "Datasource was not found" in body or "An error occurred within the plugin" in body:
                    raise RuntimeError("Grafana failed to render " + label)
                path = directory / f"frame-{len(frames):03d}.png"
                page.screenshot(path=str(path), animations="disabled")
                markers = [word for word in ("observed", "synthetic", "no_anomaly_observed",
                           "storage_queue_saturation", "sandbox.exec", "tool.call",
                           "Sandbox worker and device pressure", "TaskRunnerV1") if word in body]
                frames.append({"label": label, "seconds": seconds, "path": path,
                               "visible_markers": markers})

            visit("xlayer-start-here")
            frame("Collection health / selected run")
            visit("telemetry-overview")
            frame("Run Overview / step duration")
            show_panel("Completed steps · select to investigate")
            frame("Completed steps / investigation links", 6)
            visit("xlayer-bottleneck-summary", selected)
            frame("Selected symptom / diagnosis result", 7)
            scroll(450)
            frame("Supporting / counter / missing evidence", 7)
            show_panel("What changed? · Same-run baseline")
            frame("Same-run baseline / sample quality", 7)
            visit("xlayer-cross-layer-timeline", selected)
            frame("Selected step / exact and approximate lanes", 7)
            expand("Span and event records")
            frame("Tool / sandbox spans and correlation", 7)
            visit("xlayer-cross-layer-timeline", selected)
            expand("Host / network / local storage")
            frame("Host resource window")
            scroll(850)
            frame("Ethernet / local disk window")
            visit("agent-rl-stage-correlation")
            frame("Completed VERL stages / rollout")
            scroll(800)
            frame("vLLM engine / queue / KV signals")
            expand("Sandbox signals (optional)")
            frame("Sandbox worker / local device pressure", 7)
            visit("xlayer-compute-communication")
            frame("GPU matrix / device detail")
            visit("xlayer-data-storage")
            frame("Local disk / filesystem detail")
            log_context = {**common, "var-run_id": variables["var-log_run_id"],
                           "var-telemetry_run_id": variables["var-run_id"]}
            visit("xlayer-run-logs", log_context)
            frame("Workload logs / independent log directory", 7)
            browser.close()
        if errors:
            raise RuntimeError("Browser errors: " + "; ".join(errors))
        # Fixed frame filenames keep the concat manifest independent of the temporary path.
        concat = directory / "frames.txt"
        concat.write_text("".join(f"file '{f['path'].name}'\nduration {f['seconds']}\n" for f in frames)
                          + f"file '{frames[-1]['path'].name}'\n")
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat), "-filter_complex",
            "[0:v]split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
            "-fps_mode", "vfr", "-loop", "0", str(output),
        ], check=True)
    encoded = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height,nb_frames",
        "-of", "json", str(output),
    ]))
    return {"captured_context": context, "frames": [{k: v for k, v in f.items() if k != "path"}
                                                     for f in frames],
            "encoded": encoded, "page_errors": errors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--metadata", type=Path)
    args = parser.parse_args()
    result = capture(json.loads(args.context.read_text()), args.output)
    if args.metadata:
        args.metadata.parent.mkdir(parents=True, exist_ok=True)
        args.metadata.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["encoded"]))
