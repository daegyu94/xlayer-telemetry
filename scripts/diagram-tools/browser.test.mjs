/** Browser regressions for full-figure fit, global choice and progressive enhancement. */
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
      if (path === "/" || path === "/next.html") { response.setHeader("Content-Type", "text/html"); response.end(html); return; }
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

test("all 25 figures fit mobile/desktop in both styles and open the selected SVG", async () => {
  const page = await browser.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(address);
  assert.equal(await page.locator("article img").count(), 25);
  for (const width of [320, 390, 900, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.locator(".xlayer-figure-control:visible").count(), 1, "Hidden theme controls must not leak duplicate selectors");
    for (const style of ["d2", "excalidraw"]) {
      await page.locator(".xlayer-figure-control:visible select").selectOption(style);
      await loaded(page);
      await fits(page);
      for (const image of await page.locator("article img").all()) {
        const src = await image.getAttribute("src");
        assert.equal(new URL(src).pathname.startsWith("/_static/excalidraw/"), style === "excalidraw");
        assert.equal(await image.locator("..").getAttribute("href"), src);
      }
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

test("style persists across document navigation and reload; editable downloads are real scenes", async () => {
  const page = await browser.newPage();
  await page.goto(address);
  await page.locator(".xlayer-figure-control:visible select").selectOption("excalidraw");
  await page.goto(address + "/next.html");
  await loaded(page);
  assert.equal(await page.locator(".xlayer-figure-control:visible select").inputValue(), "excalidraw");
  await page.reload();
  await loaded(page);
  const source = await page.locator(".xlayer-diagram-download").first().getAttribute("href");
  assert.equal((await (await page.request.get(source)).json()).type, "excalidraw");
  await page.locator(".xlayer-figure-control:visible select").selectOption("d2");
  assert.equal(await page.locator(".xlayer-diagram-download:visible").count(), 0);
  await page.close();
});

test("unavailable storage does not disable switching, and failed alternates fall back to D2", async () => {
  const page = await browser.newPage();
  await page.addInitScript(() => Object.defineProperty(window, "localStorage", { get() { throw new Error("Storage disabled"); } }));
  await page.goto(address);
  await page.locator(".xlayer-figure-control:visible select").selectOption("excalidraw");
  await loaded(page);
  assert.ok((await page.locator("article img").first().getAttribute("src")).includes("excalidraw"));
  await page.locator(".xlayer-figure-control:visible select").selectOption("d2");
  await loaded(page);
  await page.route("**/_static/excalidraw/*.svg", (route) => route.fulfill({ status: 404, body: "missing" }));
  await page.locator(".xlayer-figure-control:visible select").selectOption("excalidraw");
  await page.waitForFunction(() => [...document.querySelectorAll("article img")].every((i) => i.src.includes("/_images/") && i.complete && i.naturalWidth));
  assert.equal(await page.locator(".xlayer-diagram-download:visible").count(), 0);
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

test("both exports render without overlapping labels or text outside the SVG", async () => {
  const names = (await readdir(join(root, "docs/figures/diagrams"))).filter((n) => n.endsWith(".svg"));
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  for (const prefix of ["/_images/", "/_static/excalidraw/"]) {
    for (const name of names) {
      await page.goto(address + prefix + name);
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
      assert.deepEqual(issues, [], prefix + name);
    }
  }
  await page.close();
});
