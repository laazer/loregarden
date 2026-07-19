#!/usr/bin/env node
/**
 * Visit every app surface, screenshot it, and report a per-surface verdict.
 *
 * Reads the same route list as src/lib/visualQa.ts so the two cannot drift.
 * Exits non-zero if any surface fails or was never reached — one bad surface
 * fails the run, because a green check over a broken route is evidence of
 * something untrue.
 *
 *   npm run visual-qa -- --base-url http://localhost:5173 --out .visual-qa
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const routes = JSON.parse(
  readFileSync(resolve(here, "../src/lib/visualQaRoutes.json"), "utf8"),
);

function arg(flag, fallback) {
  const i = process.argv.indexOf(flag);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const baseUrl = arg("--base-url", "http://localhost:5173").replace(/\/$/, "");
const outDir = resolve(process.cwd(), arg("--out", ".visual-qa"));

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  console.error(
    "playwright is not installed. Run: npm install && npx playwright install chromium",
  );
  process.exit(2);
}

mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch();
const results = [];

for (const route of routes) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  // Console errors are the cheapest signal that a surface rendered but is broken.
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  page.on("pageerror", (err) => errors.push(String(err)));
  // "Failed to load resource: 404" alone does not say which resource, which is
  // the first thing anyone reading the report needs.
  page.on("response", (res) => {
    if (res.status() >= 400) errors.push(`HTTP ${res.status()} ${res.url()}`);
  });

  const result = { name: route.name, path: route.path, errors };
  try {
    await page.goto(`${baseUrl}${route.path}`, {
      waitUntil: "networkidle",
      timeout: 30_000,
    });
    const shot = join(outDir, `${route.name}.png`);
    await page.screenshot({ path: shot, fullPage: true });
    result.screenshot = shot;
  } catch (err) {
    result.loadError = String(err instanceof Error ? err.message : err);
  }
  results.push(result);
  await page.close();
}

await browser.close();

const failed = results.filter((r) => r.loadError || r.errors.length > 0);
const seen = new Set(results.map((r) => r.name));
const missing = routes.filter((r) => !seen.has(r.name)).map((r) => r.name);
const summary = { ok: failed.length === 0 && missing.length === 0, checked: results.length, failed, missing, results };

writeFileSync(join(outDir, "summary.json"), JSON.stringify(summary, null, 2));
for (const r of results) {
  const status = r.loadError ? "UNREACHABLE" : r.errors.length ? "ERRORS" : "ok";
  console.log(`${status.padEnd(11)} ${r.name.padEnd(18)} ${r.path}`);
  for (const e of r.errors.slice(0, 3)) console.log(`             ${e}`);
  if (r.loadError) console.log(`             ${r.loadError}`);
}
console.log(`\n${summary.ok ? "PASS" : "FAIL"} — ${results.length} surfaces, ${failed.length} failing, ${missing.length} unvisited`);
console.log(`screenshots + summary.json in ${outDir}`);
process.exit(summary.ok ? 0 : 1);
