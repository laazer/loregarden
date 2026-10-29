#!/usr/bin/env node
/**
 * Visit every app surface, screenshot it, and report a per-surface verdict.
 *
 * Reads the same route list as src/lib/visualQa.ts so the two cannot drift.
 * Exits 1 if any surface fails or was never reached — one bad surface fails the
 * run, because a green check over a broken route is evidence of something
 * untrue. Exits 3 when nothing failed but a surface never settled: that is the
 * box being slow, not the surface being unusable, and it must read as neither.
 *
 *   npm run visual-qa -- --base-url http://localhost:5173 --out .visual-qa
 *                        [--settle-timeout 30000]
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { loadavg } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

// The same unusable-surface checks jest runs over the prod-shape fixtures
// (src/lib/usabilityCheck.ts), run here inside each real page, and the same
// verdict jest tests (src/lib/visualQaVerdict.ts). Transpiled rather than
// duplicated so the two cannot drift; both files are self-contained for exactly
// this.
const ts = (await import("typescript")).default;
const transpile = (file) =>
  ts
    .transpileModule(readFileSync(resolve(here, "../src/lib", file), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
    })
    .outputText;
const usabilityCheck = transpile("usabilityCheck.ts").replace(/^export /gm, "");
const { summarizeVisualQa, VISUAL_QA_EXIT } = await import(
  `data:text/javascript;base64,${Buffer.from(transpile("visualQaVerdict.ts")).toString("base64")}`
);
const routes = JSON.parse(
  readFileSync(resolve(here, "../src/lib/visualQaRoutes.json"), "utf8"),
);

function arg(flag, fallback) {
  const i = process.argv.indexOf(flag);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const baseUrl = arg("--base-url", "http://localhost:5173").replace(/\/$/, "");
const outDir = resolve(process.cwd(), arg("--out", ".visual-qa"));
const settleTimeout = Number(arg("--settle-timeout", "30000"));
if (!Number.isFinite(settleTimeout) || settleTimeout <= 0) {
  console.error(`--settle-timeout must be a positive number of milliseconds, got ${arg("--settle-timeout")}`);
  process.exit(2);
}

let chromium;
let TimeoutError;
try {
  ({ chromium, errors: { TimeoutError } } = await import("playwright"));
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

// A surface is settled when the app has stopped asking its API for anything and
// stopped adding or removing elements (no fetch in flight and no structural DOM
// change for QUIET_MS; text-only updates such as a ticking clock do not count),
// and nothing on it says it is still loading (`aria-busy="true"`, which
// PaneSkeleton and the pending panes set). The DOM half closes the gap between a
// pane mounting and its query's effect firing the fetch. That is the
// app's own signal, where `networkidle` waits on every request the page makes:
// under machine load it timed out after 30s on whichever surfaces were slowest
// that run, and each such timeout used to fail the run as if the page were broken.
const QUIET_MS = 500;
const TRACK_FETCHES = `(() => {
  const state = { inflight: 0, last: performance.now() };
  window.__visualQa = state;
  const fetch = window.fetch.bind(window);
  window.fetch = (...args) => {
    state.inflight += 1;
    state.last = performance.now();
    return fetch(...args).finally(() => {
      state.inflight -= 1;
      state.last = performance.now();
    });
  };
  new MutationObserver(() => {
    state.last = performance.now();
  }).observe(document, { childList: true, subtree: true });
})()`;
const SETTLED = `(() => {
  const s = window.__visualQa;
  const root = document.getElementById("root");
  return document.readyState === "complete" && root !== null && root.childElementCount > 0 &&
    s.inflight === 0 && performance.now() - s.last >= ${QUIET_MS} &&
    document.querySelector('[aria-busy="true"]') === null;
})()`;

mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch();

class Unsettled extends Error {}

/** One visit. Throws `Unsettled` when the page never settles within `budget`. */
async function visit(route, budget) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.addInitScript(TRACK_FETCHES);
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
  const started = Date.now();
  try {
    await page.goto(`${baseUrl}${route.path.replace("{ticket}", ticketId)}`, {
      waitUntil: "domcontentloaded",
      timeout: budget,
    });
    await page.waitForFunction(SETTLED, undefined, { timeout: Math.max(1, budget - (Date.now() - started)) });
  } catch (err) {
    await page.close();
    if (err instanceof TimeoutError) throw new Unsettled(`not settled within ${budget}ms`);
    throw err;
  }
  const settledMs = Date.now() - started;
  const usability = await page.evaluate(
    `(() => { ${usabilityCheck}; return findUsabilityProblems(document.body); })()`,
  );
  const screenshot = join(outDir, `${route.name}.png`);
  await page.screenshot({ path: screenshot, fullPage: true });
  await page.close();
  return { errors, usability, screenshot, settledMs };
}

const results = [];
for (const route of routes) {
  const result = { name: route.name, path: route.path, errors: [] };
  try {
    if (route.path.includes("{ticket}") && !ticketId) {
      throw new Error("no ticket to open — the server listed none; is it running on a seeded or snapshot database?");
    }
    let visited;
    try {
      visited = await visit(route, settleTimeout);
    } catch (err) {
      if (!(err instanceof Unsettled)) throw err;
      // One retry, with twice the budget, before calling it: a single slow
      // settle is the box, not the surface. The retry's errors are the ones
      // reported, since the first visit's were cut off part way.
      result.retried = err.message;
      visited = await visit(route, settleTimeout * 2);
    }
    Object.assign(result, visited);
  } catch (err) {
    if (err instanceof Unsettled) result.unsettled = `${result.retried}, then ${err.message}`;
    else result.loadError = String(err instanceof Error ? err.message : err);
  }
  results.push(result);
}

await browser.close();

// An unusable shape fails the surface like a console error does: both are
// things a clean screenshot hides. Run it against `task sandbox` — on an empty
// database there is nothing for these checks to find.
const load = loadavg().map((n) => Number(n.toFixed(1)));
const summary = {
  ...summarizeVisualQa(results, routes.map((r) => r.name)),
  loadAverage: load,
  settleTimeoutMs: settleTimeout,
  results,
};

writeFileSync(join(outDir, "summary.json"), JSON.stringify(summary, null, 2));
for (const r of results) {
  const status = r.loadError
    ? "UNREACHABLE"
    : r.errors.length
      ? "ERRORS"
      : r.usability?.length
        ? "UNUSABLE"
        : r.unsettled
          ? "UNSETTLED"
          : r.retried
            ? "ok (slow)"
            : "ok";
  console.log(`${status.padEnd(11)} ${r.name.padEnd(20)} ${r.path}`);
  for (const e of r.errors.slice(0, 3)) console.log(`             ${e}`);
  for (const p of (r.usability ?? []).slice(0, 3)) console.log(`             ${p.kind}: ${p.detail}`);
  if (r.loadError) console.log(`             ${r.loadError}`);
  if (r.unsettled) console.log(`             ${r.unsettled} — unchecked, not failed`);
  else if (r.retried) console.log(`             first visit ${r.retried}; settled on retry in ${r.settledMs}ms`);
}
const verdictLine = {
  pass: "PASS",
  fail: "FAIL",
  inconclusive: `INCONCLUSIVE — ${summary.unsettled.length} surface(s) never settled, so went unchecked; this is load, not a usability failure`,
}[summary.verdict];
console.log(
  `\n${verdictLine}\n${results.length} surfaces, ${summary.failed.length} failing, ` +
    `${summary.unsettled.length} unsettled, ${summary.missing.length} unvisited; load average ${load.join(" ")}`,
);
console.log(`screenshots + summary.json in ${outDir}`);
process.exit(VISUAL_QA_EXIT[summary.verdict]);
