#!/usr/bin/env node
/** Generate genuine Excalidraw scenes/SVGs from the canonical D2 SVG geometry. */
import { build } from "esbuild";
import { chromium } from "playwright";
import { createServer } from "node:http";
import { readFile, writeFile, readdir, mkdir, mkdtemp, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve, join } from "node:path";
import { tmpdir } from "node:os";
import { createHash } from "node:crypto";

const directory = dirname(fileURLToPath(import.meta.url));
const root = resolve(directory, "../..");
const source = join(root, "docs/figures/diagrams");
const output = join(root, "docs/_static/excalidraw");
const version = "0.18.1";
const check = process.argv.includes("--check");
if (process.argv.slice(2).some((arg) => arg !== "--check")) {
  throw new Error("Usage: node scripts/diagram-tools/render.mjs [--check]");
}
const temporary = await mkdtemp(join(tmpdir(), "xlayer-excalidraw-"));
let browser;
let server;
const drift = [];
try {
  await build({
    stdin: {
      contents: 'import {convertToExcalidrawElements, exportToSvg} from "@excalidraw/excalidraw"; window.xlayerExport = {convertToExcalidrawElements, exportToSvg};',
      resolveDir: directory,
    },
    outfile: join(temporary, "export.js"), bundle: true, minify: true,
    format: "iife", define: { "process.env.NODE_ENV": '"production"' },
  });
  server = createServer(async (request, response) => {
    try {
      const name = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      const base = name.startsWith("/fonts/")
        ? join(directory, "node_modules/@excalidraw/excalidraw/dist/prod") : temporary;
      const path = resolve(base, `.${name}`);
      if (!path.startsWith(base + "/")) throw new Error("Invalid path");
      response.setHeader("Content-Type", name.endsWith(".js") ? "text/javascript" : name.endsWith(".woff2") ? "font/woff2" : "text/html");
      response.end(await readFile(path));
    } catch { response.writeHead(404); response.end(); }
  });
  await writeFile(join(temporary, "index.html"), '<!doctype html><meta charset="utf-8"><style>body{margin:0}</style><script>window.EXCALIDRAW_ASSET_PATH="/";</script><script src="export.js"></script>');
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  // SVG text geometry varies by browser version. Generation/check always use
  // the Chromium pinned by package-lock.json, never a host-specific Chrome.
  browser = await chromium.launch({ args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  const page = await browser.newPage({ viewport: { width: 4096, height: 4096 } });
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
  await page.waitForFunction(() => window.xlayerExport);
  await page.evaluate(async () => {
    const font = new FontFace("Virgil", 'url("/fonts/Virgil/Virgil-Regular.woff2")');
    document.fonts.add(await font.load());
  });
  await mkdir(output, { recursive: true });
  const names = (await readdir(source)).filter((name) => name.endsWith(".svg")).sort();
  for (const name of names) {
    const original = await readFile(join(source, name), "utf8");
    const digest = createHash("sha256").update(original).update(version).digest("hex");
    const result = await page.evaluate(async ({ original, digest, name, version }) => {
      const holder = document.createElement("div");
      holder.innerHTML = original;
      document.body.appendChild(holder);
      await document.fonts.ready;
      const svg = holder.querySelector("svg");
      const origin = svg.getBoundingClientRect();
      let serial = 0;
      const hash = (text) => Array.from(text).reduce((n, char) => ((n * 31 + char.charCodeAt(0)) >>> 0), 17);
      const common = (type) => {
        const id = `${name.replace(/\.svg$/, "")}-${serial++}`;
        return { id, type, seed: hash(id), strokeWidth: 2, roughness: 1,
          fillStyle: "solid", strokeColor: "#334155", backgroundColor: "transparent",
          versionNonce: hash(id + "nonce"), updated: 0 };
      };
      const bounds = (element) => {
        const r = element.getBoundingClientRect();
        // Room for Excalidraw handwriting without changing topology or labels.
        const scale = 1.5;
        return { x: (r.x - origin.x) * scale, y: (r.y - origin.y) * scale, width: r.width * scale, height: r.height * scale };
      };
      const rectangles = [...svg.querySelectorAll("g.shape")].map((group) => {
        const shape = group.querySelector("rect");
        if (!shape || group.children.length !== 1) throw new Error(`Unsupported D2 shape in ${name}`);
        return { ...common("rectangle"), ...bounds(shape),
          backgroundColor: shape.getAttribute("fill"), strokeColor: shape.getAttribute("stroke"),
          strokeStyle: getComputedStyle(shape).strokeDasharray !== "none" ? "dashed" : "solid", roundness: { type: 3 } };
      });
      const contains = (a, b) => a !== b && a.x <= b.x && a.y <= b.y
        && a.x + a.width >= b.x + b.width && a.y + a.height >= b.y + b.height;
      const containers = rectangles.filter((a) => rectangles.some((b) => contains(a, b)));
      const leaves = rectangles.filter((a) => !containers.includes(a));
      // Simplify sampled path geometry without changing endpoints or direction.
      const simplify = (points) => {
        if (points.length <= 2) return points;
        const [a, b] = [points[0], points.at(-1)];
        const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
        let maximum = 0, index = 0;
        for (let i = 1; i < points.length - 1; i++) {
          const p = points[i];
          const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
          const distance = Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
          if (distance > maximum) { maximum = distance; index = i; }
        }
        return maximum > 1 ? [...simplify(points.slice(0, index + 1)).slice(0, -1), ...simplify(points.slice(index))] : [a, b];
      };
      const connections = [...svg.querySelectorAll("path.connection")].map((path) => {
        const length = path.getTotalLength();
        const count = Math.max(1, Math.ceil(length / 8));
        const matrix = path.getScreenCTM();
        const sampled = Array.from({ length: count + 1 }, (_, i) => {
          const p = path.getPointAtLength(length * i / count).matrixTransform(matrix);
          return [(p.x - origin.x) * 1.5, (p.y - origin.y) * 1.5];
        });
        const points = simplify(sampled);
        const start = points[0];
        return { ...common(path.hasAttribute("marker-end") || path.hasAttribute("marker-start") ? "arrow" : "line"),
          x: start[0], y: start[1], points: points.map((p) => [p[0] - start[0], p[1] - start[1]]),
          strokeColor: path.getAttribute("stroke"), strokeStyle: getComputedStyle(path).strokeDasharray !== "none" ? "dashed" : "solid",
          startArrowhead: path.hasAttribute("marker-start") ? "arrow" : null,
          endArrowhead: path.hasAttribute("marker-end") ? "arrow" : null };
      });
      const canvas = document.createElement("canvas").getContext("2d");
      const labels = [];
      const captions = [];
      for (const label of svg.querySelectorAll("text")) {
        const lines = [...label.querySelectorAll("tspan")];
        const text = lines.length ? lines.map((line) => line.textContent).join("\n") : label.textContent;
        const box = bounds(label);
        const candidate = label.parentElement.querySelector(":scope > g.shape > rect");
        const candidateBox = candidate && bounds(candidate);
        const shape = candidateBox && box.x >= candidateBox.x && box.y >= candidateBox.y
          && box.x + box.width <= candidateBox.x + candidateBox.width
          && box.y + box.height <= candidateBox.y + candidateBox.height ? candidate : null;
        const area = shape ? candidateBox : box;
        let fontSize = Math.round(parseFloat(label.style.fontSize) * (shape ? 1.4 : 1));
        const measure = () => {
          canvas.font = `${fontSize}px Virgil`;
          return Math.max(...text.split("\n").map((line) => canvas.measureText(line).width));
        };
        // Keep at least 20px text; fail rather than silently clipping a label.
        const available = shape ? area.width - 16 : Math.max(box.width + 20, 40);
        while (measure() > available && fontSize > 20) fontSize--;
        if (measure() > available + 1) throw new Error(`Label requires layout adjustment in ${name}: ${text} [${measure()} > ${available}; shape=${Boolean(shape)}; box=${JSON.stringify(box)}]`);
        const width = measure();
        const height = fontSize * 1.5 * text.split("\n").length;
        const x = box.x + box.width / 2 - width / 2;
        const y = box.y + box.height / 2 - height / 2;
        if (!shape) captions.push({ ...common("rectangle"), x: x - 3, y: y - 2, width: width + 6, height: height + 4,
          backgroundColor: "#ffffff", strokeColor: "transparent", roughness: 0, strokeWidth: 0 });
        labels.push({ ...common("text"), x, y, text, fontSize, fontFamily: 1,
          strokeColor: label.getAttribute("fill"), textAlign: "left", verticalAlign: "top", lineHeight: 1.5,
          roughness: 0, customData: { d2Label: text } });
      }
      const elements = window.xlayerExport.convertToExcalidrawElements([
        ...containers, ...connections, ...leaves, ...captions, ...labels,
      ], { regenerateIds: false });
      // The constructor uses current time/random nonces; normalize persisted data.
      for (const element of elements) { element.updated = 0; element.versionNonce = hash(element.id + "nonce"); }
      const scene = { type: "excalidraw", version: 2, source: "https://github.com/daegyu94/xlayer-telemetry",
        elements, appState: { viewBackgroundColor: "#ffffff", gridSize: null }, files: {},
        xlayer: { source: `docs/figures/diagrams/${name}`, sourceSha256: digest, excalidrawVersion: version } };
      const image = await window.xlayerExport.exportToSvg({ elements, appState: { ...scene.appState, exportBackground: true, exportWithDarkMode: false }, files: {}, exportPadding: 24 });
      const namespace = "http://www.w3.org/2000/svg";
      for (const tag of ["title", "desc"]) {
        const node = document.createElementNS(namespace, tag);
        node.id = `xlayer-excalidraw-${name.replace(/\.svg$/, "")}-${tag}`;
        node.textContent = svg.querySelector(`:scope > ${tag}`).textContent;
        image.prepend(node);
      }
      image.setAttribute("role", "img");
      image.setAttribute("aria-labelledby", `xlayer-excalidraw-${name.replace(/\.svg$/, "")}-title xlayer-excalidraw-${name.replace(/\.svg$/, "")}-desc`);
      image.prepend(document.createComment(` Generated with Excalidraw ${version}; source-sha256:${digest} `));
      holder.remove();
      return { scene: JSON.stringify(scene, null, 2) + "\n", svg: image.outerHTML + "\n" };
    }, { original, digest, name, version });
    for (const [file, contents] of [[name, result.svg], [name.replace(/\.svg$/, ".excalidraw"), result.scene]]) {
      const destination = join(output, file);
      let existing;
      try { existing = await readFile(destination, "utf8"); } catch (error) { if (error.code !== "ENOENT") throw error; }
      if (existing !== contents) {
        drift.push(file);
        if (!check) await writeFile(destination, contents);
      }
    }
  }
  if (check && drift.length) throw new Error(`Regenerate Excalidraw figures: ${drift.join(", ")}`);
  console.log(`Excalidraw ${version}: ${names.length} diagrams, ${drift.length} ${check ? "outdated" : "updated"} files`);
} finally {
  await browser?.close();
  if (server) await new Promise((resolve) => server.close(resolve));
  await rm(temporary, { recursive: true, force: true });
}
