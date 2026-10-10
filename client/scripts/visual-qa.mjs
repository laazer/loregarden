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

// The same unusable-surface checks jest runs over the prod-shape fixtures
// (src/lib/usabilityCheck.ts), run here inside each real page. Transpiled
// rather than duplicated so the two cannot drift; the function is written to be
// self-contained for exactly this.
const ts = (await import("typescript")).default;
const usabilityCheck = ts
  .transpileModule(readFileSync(resolve(here, "../src/lib/usabilityCheck.ts"), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
  })
  .outputText.replace(/^export /gm, "");
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

// Ticket-scoped surfaces (the Monitor, a ticket's artifacts) need a real ticket
// id, and the same route list has to work on `task sandbox`'s copy of production
// and on the seeded scenario, whose ids differ — so ask the server for one.
let ticketId = "";
try {
  const res = await fetch(`${baseUrl}/api/tickets?limit=1`);
  ticketId = (await res.json())[0]?.id ?? "";
} catch (err) {
  console.error(`could not list tickets for {ticket} routes: ${err}`);
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
    if (route.path.includes("{ticket}") && !ticketId) {
      throw new Error("no ticket to open — the server listed none; is it running on a seeded or snapshot database?");
    }
    await page.goto(`${baseUrl}${route.path.replace("{ticket}", ticketId)}`, {
      waitUntil: "networkidle",
      timeout: 30_000,
    });
    result.usability = await page.evaluate(
      `(() => { ${usabilityCheck}; return findUsabilityProblems(document.body); })()`,
    );
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

// An unusable shape fails the surface like a console error does: both are
// things a clean screenshot hides. Run it against `task sandbox` — on an empty
// database there is nothing for these checks to find.
const failed = results.filter((r) => r.loadError || r.errors.length > 0 || r.usability?.length > 0);
const seen = new Set(results.map((r) => r.name));
const missing = routes.filter((r) => !seen.has(r.name)).map((r) => r.name);
const summary = { ok: failed.length === 0 && missing.length === 0, checked: results.length, failed, missing, results };

writeFileSync(join(outDir, "summary.json"), JSON.stringify(summary, null, 2));
for (const r of results) {
  const status = r.loadError ? "UNREACHABLE" : r.errors.length ? "ERRORS" : r.usability?.length ? "UNUSABLE" : "ok";
  console.log(`${status.padEnd(11)} ${r.name.padEnd(18)} ${r.path}`);
  for (const e of r.errors.slice(0, 3)) console.log(`             ${e}`);
  for (const p of (r.usability ?? []).slice(0, 3)) console.log(`             ${p.kind}: ${p.detail}`);
  if (r.loadError) console.log(`             ${r.loadError}`);
}
console.log(`\n${summary.ok ? "PASS" : "FAIL"} — ${results.length} surfaces, ${failed.length} failing, ${missing.length} unvisited`);
console.log(`screenshots + summary.json in ${outDir}`);
process.exit(summary.ok ? 0 : 1);
