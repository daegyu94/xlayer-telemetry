import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { MATRIX_SPECS } from "../src/matrix-contract";
import { DASHBOARD_UIDS } from "../src/context";
test("provisioned canonical panel/reference contracts exist in Loki and metrics-only profiles", () => {
  const temporary = mkdtempSync(join(tmpdir(), "xlayer-app-contract-"));
  const root = resolve(process.cwd(), "../..");
  function find(panels: any[], id: number): any {
    for (const p of panels) {
      if (p.id === id) return p;
      const child = find(p.panels || [], id);
      if (child) return child;
    }
  }
  try {
    for (const logs of [false, true]) {
      const dir = join(temporary, logs ? "loki" : "metrics");
      const result = spawnSync(
        process.env.PYTHON || "python3",
        [
          join(root, "scripts/provision_dashboards.py"),
          "--output",
          dir,
          ...(logs ? ["--enable-logs"] : []),
        ],
        { encoding: "utf8" },
      );
      assert.equal(result.status, 0, result.stderr);
      const dashboards = Object.fromEntries(
        readdirSync(dir)
          .map((file) => JSON.parse(readFileSync(join(dir, file), "utf8")))
          .map((d) => [d.uid, d]),
      );
      for (const spec of Object.values(MATRIX_SPECS)) {
        const panel = find(
          dashboards[DASHBOARD_UIDS[spec.dashboard]].panels,
          spec.panel,
        );
        assert.ok(panel, spec.label);
        for (const ref of spec.refs)
          assert.ok(
            panel.targets.some((t: any) => t.refId === ref),
            spec.label + " / " + ref,
          );
        assert.ok(panel.datasource.uid);
        assert.ok(panel.fieldConfig.defaults.unit !== undefined, spec.label);
      }
      for (const id of [30, 31, 33, 34, 40, 41, 42, 44])
        assert.ok(find(dashboards[DASHBOARD_UIDS.overview].panels, id));
      assert.equal(
        !!find(dashboards[DASHBOARD_UIDS.overview].panels, 20),
        logs,
      );
      assert.equal(!!dashboards[DASHBOARD_UIDS.summary], logs);
      if (logs) {
        for (const id of [2, 3, 4, 6])
          assert.ok(find(dashboards[DASHBOARD_UIDS.summary].panels, id));
        for (const id of [2, 3, 9])
          assert.ok(find(dashboards[DASHBOARD_UIDS.timeline].panels, id));
      }
    }
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});
