/**
 * Screenshot pages of a running instance for the review page's Capture button.
 *
 *   node scripts/review-capture.mjs --base-url http://127.0.0.1:5181 --out DIR /path [/path …]
 *
 * Writes DIR/<n>.png per path and prints one JSON line to stdout:
 *   {"pages": [{"path": "/x", "file": "DIR/0.png", "error": ""}, …]}
 * A page that fails to load or settle is reported with its error, never
 * dropped, so the caller can say which page was not captured and why.
 * Exits 2 when playwright is missing or the arguments are wrong.
 */
import { mkdirSync } from "node:fs";
import { join, resolve } from "node:path";

import { SETTLED, TRACK_FETCHES } from "./lib/pageSettled.mjs";

const SETTLE_TIMEOUT_MS = 20000;
const VIEWPORT = { width: 1440, height: 900 };

const argv = process.argv.slice(2);
function take(flag) {
  const i = argv.indexOf(flag);
  if (i === -1 || !argv[i + 1]) return "";
  const [, value] = argv.splice(i, 2);
  return value;
}
const baseUrl = take("--base-url").replace(/\/$/, "");
const outDir = take("--out");
const paths = argv;
if (!baseUrl || !outDir || paths.length === 0 || paths.some((p) => !p.startsWith("/") || p.startsWith("//"))) {
  console.error("usage: review-capture.mjs --base-url URL --out DIR /path [/path …] (paths start with one '/')");
  process.exit(2);
}

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  console.error("playwright is not installed. Run: npm install && npx playwright install chromium");
  process.exit(2);
}

mkdirSync(resolve(outDir), { recursive: true });
const browser = await chromium.launch();
const pages = [];
try {
  for (const [index, path] of paths.entries()) {
    const file = join(resolve(outDir), `${index}.png`);
    const context = await browser.newContext({ viewport: VIEWPORT });
    const page = await context.newPage();
    await page.addInitScript(TRACK_FETCHES);
    try {
      await page.goto(`${baseUrl}${path}`, { waitUntil: "load", timeout: SETTLE_TIMEOUT_MS });
      let error = "";
      try {
        await page.waitForFunction(SETTLED, undefined, { timeout: SETTLE_TIMEOUT_MS });
      } catch {
        // Still worth a picture: say it may show a loading state.
        error = `did not settle in ${SETTLE_TIMEOUT_MS} ms; captured as it was`;
      }
      await page.screenshot({ path: file });
      pages.push({ path, file, error });
    } catch (err) {
      pages.push({ path, file: "", error: String(err).split("\n")[0] });
    } finally {
      await context.close();
    }
  }
} finally {
  await browser.close();
}
process.stdout.write(`${JSON.stringify({ pages })}\n`);
