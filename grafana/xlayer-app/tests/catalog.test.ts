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
      for(const id of [130,131,132])assert.ok(find(dashboards[DASHBOARD_UIDS.compute].panels,id));
      assert.equal(
        !!find(dashboards[DASHBOARD_UIDS.overview].panels, 20),
        logs,
      );
      assert.equal(!!dashboards[DASHBOARD_UIDS.summary], logs);
      for (const id of [101, 102, 103, 104])
        assert.equal(!!find(dashboards[DASHBOARD_UIDS.storage].panels, id), logs);
      if (logs) {
        const plot = find(dashboards[DASHBOARD_UIDS.storage].panels, 102);
        assert.equal(plot.type, 'timeseries');
        assert.equal(plot.fieldConfig.defaults.custom.drawStyle, 'points');
        assert.equal(plot.fieldConfig.defaults.custom.lineWidth, 0);
        assert.equal(plot.fieldConfig.defaults.custom.spanNulls, false);
        assert.ok(!plot.targets[0].expr.includes('storage_metric'));
        assert.ok(plot.transformations.some((t: any) => t.id === 'filterByValue' && t.options.filters[0].fieldName === 'metric_name'));
        assert.match(plot.targets[0].expr, /window_role="current"/);
        assert.ok(plot.targets[0].maxLines <= 2500);
        assert.ok(plot.transformations.some((t: any) => t.id === 'convertFieldType' && t.options.conversions.some((c: any) => c.targetField === 'sample_timestamp_ms' && c.destinationType === 'time')));
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
