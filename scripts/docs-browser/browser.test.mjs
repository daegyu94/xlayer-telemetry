/** Browser regressions for D2 geometry, responsive figures and reader actions. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, readdir } from "node:fs/promises";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
let browser, server, address;
const errors = [];
before(async () => {
  const names = (await readdir(join(root, "docs/figures/diagrams"))).filter((n) => n.endsWith(".svg"));
  const html = `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/_static/xlayer.css">
    <style>body{margin:18px}article{max-width:52rem}img{max-width:100%;height:auto}</style>
    <style>.theme-toggle-header{display:none}@media(max-width:700px){.theme-toggle-header{display:flex}.theme-toggle-content{display:none}}</style>
    <div class="theme-toggle-container theme-toggle-header"><button>Theme</button></div>
    <div class="theme-toggle-container theme-toggle-content"><button>Theme</button></div>
    <article>${names.map((name) => `<p><img alt="${name}" src="/_images/${name}"></p>`).join("")}</article>
    <script src="/_static/diagrams.js"></script>`;
  server = createServer(async (request, response) => {
    try {
      const path = new URL(request.url, "http://localhost").pathname;
      if (path === "/") { response.setHeader("Content-Type", "text/html"); response.end(html); return; }
      if (path.startsWith("/site/") && process.env.XLAYER_DOCS_SITE) {
        // npm --prefix changes cwd; documented relative paths start at the repo.
        const base = resolve(root, process.env.XLAYER_DOCS_SITE);
        const target = resolve(base, path.slice(6));
        if (!target.startsWith(base + "/")) throw new Error("Invalid site path");
        response.setHeader("Content-Type", path.endsWith(".js") ? "text/javascript" : path.endsWith(".svg") ? "image/svg+xml" : path.endsWith(".css") ? "text/css" : path.endsWith(".png") ? "image/png" : "text/html");
        response.end(await readFile(target));
        return;
      }
      const relative = path.startsWith("/_images/") ? `docs/figures/diagrams/${path.slice(9)}` : `docs/${path.slice(1)}`;
      const target = resolve(root, relative);
      if (!target.startsWith(root + "/")) throw new Error("Invalid path");
      response.setHeader("Content-Type", path.endsWith(".js") ? "text/javascript" : path.endsWith(".svg") ? "image/svg+xml" : path.endsWith(".css") ? "text/css" : "application/json");
      response.end(await readFile(target));
    } catch { response.writeHead(404); response.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  address = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });
});

test("node collector paths do not cross unrelated metric sources", async () => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(address + "/_images/node-metrics.svg");
  const crossings = await page.evaluate(() => {
    const sources = [...document.querySelectorAll("g.source > g.shape > rect")].map((r) => r.getBoundingClientRect());
    const paths = [...document.querySelectorAll("path.connection")];
    const inside = (p, r, margin = 0) => p.x > r.left + margin && p.x < r.right - margin && p.y > r.top + margin && p.y < r.bottom - margin;
    const failures = [];
    for (const path of paths) {
      const length = path.getTotalLength();
      const matrix = path.getScreenCTM();
      const at = (n) => path.getPointAtLength(n).matrixTransform(matrix);
      for (const box of sources) {
        // Exclude the source/target attached to this path, including arrow padding.
        if (inside(at(0), box, -6) || inside(at(length), box, -6)) continue;
        for (let n = 0; n <= length; n += 8) {
          if (inside(at(n), box, 2)) { failures.push(path.getAttribute("d")); break; }
        }
      }
    }
    return failures;
  });
  assert.deepEqual(crossings, [], "Parallel inputs must not look like a serial pipeline through another source");
  await page.close();
});

test("documentation uses D2 SVGs without a style selector", async () => {
  const page = await browser.newPage();
  await page.goto(address);
  await loaded(page);
  assert.equal(await page.locator("select").count(), 0);
  for (const image of await page.locator("article img").all()) {
    const src = await image.evaluate((i) => i.src);
    assert.ok(new URL(src).pathname.startsWith("/_images/"));
    assert.equal(await image.locator("..").getAttribute("href"), src);
  }
  await page.close();
});

test("built documentation has full D2 figures and captions at every breakpoint", { skip: !process.env.XLAYER_DOCS_SITE }, async () => {
  const documents = ["index", "agent-rl", "architecture", "cli", "dashboards", "diagnosis", "grafana-ui-ux-review", "local-llm", "monitoring", "time-alignment", "validation/e2e-user-experience", "verl-quickstart"];
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const width of [320, 390, 430, 900, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const document of documents) {
      await page.goto(`${address}/site/${document}.html`);
      assert.equal(await page.locator(".xlayer-figure-control").count(), 0, `${width} ${document}`);
      await page.waitForFunction(() => [...window.document.querySelectorAll(".xlayer-diagram-image")].every((i) => i.complete && i.naturalWidth));
      const figures = await page.locator("figure.xlayer-diagram").all();
      for (const figure of figures) {
        const image = figure.locator("img");
        assert.equal(await figure.locator("figcaption").textContent(), await image.getAttribute("alt"));
        const src = await image.evaluate((i) => i.src);
        assert.ok(new URL(src).pathname.includes("/_images/"));
        assert.equal(await figure.locator(".xlayer-diagram-link").getAttribute("href"), src);
        assert.match(await figure.locator(".xlayer-diagram-link").getAttribute("aria-label"), /새 탭/);
        assert.equal(await figure.locator(".xlayer-diagram-actions").count(), 0);
      }
      const clipped = await page.locator(".xlayer-diagram-image").evaluateAll((images) => images.filter((i) => {
        const image = i.getBoundingClientRect(), article = i.closest("article").getBoundingClientRect();
        return image.left < article.left - 1 || image.right > article.right + 1;
      }).map((i) => i.alt));
      assert.deepEqual(clipped, [], `${width} ${document}`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `${width} ${document}`);
    }
  }
  assert.deepEqual(errors, []);
  await page.close();
});
after(async () => {
  await browser?.close();
  if (server) await new Promise((resolve) => server.close(resolve));
});
async function loaded(page) {
  await page.waitForFunction(() => [...document.querySelectorAll("article img")].every((i) => i.complete && i.naturalWidth > 0));
}
async function fits(page) {
  const failures = await page.locator("article img").evaluateAll((images) => images.filter((i) => {
    const box = i.getBoundingClientRect();
    const article = i.closest("article").getBoundingClientRect();
    return box.width > article.width + 1 || box.right > article.right + 1 || box.left < article.left - 1;
  }).map((i) => i.alt));
  assert.deepEqual(failures, [], "Every diagram must fit completely, not just hide page overflow");
}

test("all 25 D2 figures fit mobile/desktop and open their SVG", async () => {
  const page = await browser.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(address);
  assert.equal(await page.locator("article img").count(), 25);
  for (const width of [320, 390, 900, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await loaded(page);
    await fits(page);
    for (const image of await page.locator("article img").all()) {
      const src = await image.evaluate((i) => i.src);
      assert.ok(new URL(src).pathname.startsWith("/_images/"));
      assert.equal(await image.locator("..").getAttribute("href"), src);
    }
  }
  const popup = page.waitForEvent("popup");
  await page.locator(".xlayer-diagram-link").first().click();
  const svg = await popup;
  await svg.waitForLoadState();
  assert.equal(await svg.locator("svg[role='img']").count(), 1);
  await svg.close();
  assert.deepEqual(errors, []);
  await page.close();
});

test("D2 figures have captions and one accessible enlargement action", async () => {
  const page = await browser.newPage({ viewport: { width: 320, height: 900 } });
  await page.goto(address);
  await loaded(page);
  const figures = await page.locator("article figure.xlayer-diagram").all();
  assert.equal(figures.length, 25);
  for (const figure of figures) {
    const name = await figure.locator("img").getAttribute("alt");
    assert.equal(await figure.locator("figcaption").textContent(), name);
    const link = figure.locator(".xlayer-diagram-link");
    assert.equal(await link.getAttribute("aria-label"), `${name} — 원본 SVG 확대 (새 탭)`);
    assert.equal(await figure.locator("a").count(), 1);
  }
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  const link = page.locator(".xlayer-diagram-link").first();
  await link.focus();
  assert.equal(await link.evaluate((a) => a === document.activeElement), true);
  const popup = page.waitForEvent("popup");
  await page.keyboard.press("Enter");
  const svg = await popup;
  await svg.waitForLoadState();
  assert.equal(await svg.locator("svg[role='img']").count(), 1);
  await svg.close();
  await page.close();
});

test("reader actions do not depend on local storage", async () => {
  const page = await browser.newPage();
  await page.addInitScript(() => Object.defineProperty(window, "localStorage", { get() { throw new Error("Storage disabled"); } }));
  await page.goto(address);
  await loaded(page);
  assert.equal(await page.locator("figure.xlayer-diagram").count(), 25);
  assert.equal(await page.locator(".xlayer-diagram-link").count(), 25);
  await page.close();
});

test("without JavaScript, canonical figures still load and fit", async () => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 320, height: 900 } });
  const page = await context.newPage();
  await page.goto(address);
  await loaded(page);
  await fits(page);
  await context.close();
});

test("D2 SVGs render without overlapping labels or text outside the SVG", async () => {
  const names = (await readdir(join(root, "docs/figures/diagrams"))).filter((n) => n.endsWith(".svg"));
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  for (const name of names) {
    await page.goto(address + "/_images/" + name);
    await page.evaluate(() => document.fonts.ready);
    const issues = await page.evaluate(() => {
      const area = document.querySelector("svg").getBoundingClientRect();
      const labels = [...document.querySelectorAll("svg text")].map((e) => ({ text: e.textContent, box: e.getBoundingClientRect() }));
      const issues = [];
      for (const [i, a] of labels.entries()) {
        if (a.box.left < area.left - 1 || a.box.top < area.top - 1 || a.box.right > area.right + 1 || a.box.bottom > area.bottom + 1) issues.push(`Outside SVG: ${a.text}`);
        for (const b of labels.slice(i + 1)) {
          if (Math.min(a.box.right, b.box.right) - Math.max(a.box.left, b.box.left) > 2
            && Math.min(a.box.bottom, b.box.bottom) - Math.max(a.box.top, b.box.top) > 2) issues.push(`Overlapping: ${a.text} / ${b.text}`);
        }
      }
      return issues;
    });
    assert.deepEqual(issues, [], name);
  }
  await page.close();
});
