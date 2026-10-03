"""Exercise an existing synthetic investigation run in a real Grafana browser.

Requires optional Playwright/Chromium. This command reads an existing stack;
fixture creation and stack lifecycle remain explicit caller responsibilities.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
from urllib.parse import parse_qs, urlencode, urlsplit


def validate(args):
    from playwright.sync_api import sync_playwright
    args.output.mkdir(parents=True, exist_ok=False)
    results, errors, query_checks = [], [], []

    def timestamp(value):
        return int(value) if value.isdigit() else round(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=args.chromium or shutil.which("chromium") or shutil.which("google-chrome"),
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": args.width, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))

            def check_query(response):
                if "/api/ds/query" not in response.url:
                    return
                try:
                    payload = response.json()
                    failures = [value["error"] for value in payload.get("results", {}).values()
                                if value.get("error")]
                    query_checks.append({"http_status": response.status, "errors": failures})
                    if response.status >= 400 or failures:
                        errors.append("Datasource query failed: " + json.dumps(failures))
                except Exception as error:
                    errors.append("Cannot inspect datasource response: " + str(error))
            page.on("response", check_query)

            def record(name, *, step_context=False):
                page.wait_for_timeout(1200)
                body = page.locator("body").inner_text()
                for failure in ("An error occurred within the plugin", "Datasource was not found", "Panel plugin not found"):
                    assert failure not in body, (name, failure)
                query = parse_qs(urlsplit(page.url).query)
                assert query.get("var-cluster") == [args.cluster], (name, page.url)
                assert query.get("var-run_id") == [args.run_id], (name, page.url)
                assert query.get("var-node") == [args.node], (name, page.url)
                if step_context:
                    for key in ("var-record_id", "var-source_node", "from", "to"):
                        actual, expected = query.get(key), selected[key]
                        if key in {"from", "to"} and actual:
                            actual, expected = list(map(timestamp, actual)), list(map(timestamp, expected))
                        assert actual == expected, (name, key, page.url)
                page.screenshot(path=str(args.output / f"{name}.png"), animations="disabled")
                results.append({"screen": name, "url": page.url, "context_preserved": True})
                return body

            def click_link(name):
                page.get_by_role("link", name=name, exact=True).first.click()
                page.wait_for_load_state("domcontentloaded")

            query = urlencode({"var-cluster": args.cluster, "var-node": args.node,
                               "var-run_id": args.run_id, "refresh": "off"})
            page.goto(args.grafana_url.rstrip("/") + "/d/xlayer-start-here?" + query)
            page.get_by_role("link", name=args.run_id, exact=True).wait_for(timeout=15000)
            record("01-start-here")
            page.get_by_role("link", name=args.run_id, exact=True).click()
            page.wait_for_url("**/d/telemetry-overview/**", timeout=15000)
            record("02-run-overview")
            duration = page.get_by_text("18.4 s", exact=True).first
            duration.scroll_into_view_if_needed()
            duration.click()
            menu = page.get_by_text("Bottleneck Summary 열기", exact=True)
            if menu.count():
                menu.click()
            page.wait_for_url("**/d/xlayer-bottleneck-summary/**", timeout=15000)
            page.get_by_text("storage_queue_saturation", exact=True).first.wait_for(timeout=15000)
            selected = parse_qs(urlsplit(page.url).query)
            assert selected.get("var-record_id", [".*"])[0] != ".*"
            body = record("03-bottleneck-summary", step_context=True)
            assert "storage_queue_saturation" in body
            assert "synthetic" in body
            evidence = page.get_by_text("Evidence details: supporting, counter, and missing", exact=True)
            # Grafana mounts lower panels lazily; scroll as a user would before
            # locating the evidence row instead of treating virtualization as failure.
            for _ in range(5):
                if evidence.count():
                    break
                page.mouse.move(args.width - 250, 750)
                page.mouse.wheel(0, 500)
                page.wait_for_timeout(300)
            evidence.scroll_into_view_if_needed(timeout=5000)
            body = record("04-evidence", step_context=True)
            assert "Evidence type" in body and "supporting" in body
            click_link("Cross-Layer Timeline")
            record("05-timeline", step_context=True)
            click_link("Data & Storage")
            record("06-storage", step_context=True)
            click_link("View: Overview")
            record("07-overview-view", step_context=True)
            click_link("View: Focus")
            record("08-focus-view", step_context=True)
            # Focus retains resource filters, then pivots back to the guided path.
            click_link("View: Guided")
            record("09-guided-view", step_context=True)
            assert not errors, errors
        finally:
            browser.close()
    report = {"status": "passed", "data_origin": "synthetic", "viewport_width": args.width,
              "screens": results, "browser_errors": errors, "datasource_queries": query_checks,
              "limitations": ["Synthetic candidate values; not real VERL or storage causality validation.",
                              "Screenshots do not replace backend query validation."]}
    (args.output / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grafana-url", required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory for small screenshots/report")
    parser.add_argument("--width", type=int, choices=(900, 1440), default=1440)
    parser.add_argument("--chromium", help="Optional Chromium executable")
    validate(parser.parse_args())


if __name__ == "__main__":
    main()
