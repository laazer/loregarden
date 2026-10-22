#!/usr/bin/env node
/**
 * Measure how many lines a string renders to inside `.toast__message`, against
 * the real stylesheets and the real font, and say whether the clamp cuts it.
 *
 * `.toast__message` sets `max-height` with `overflow: hidden` and no ellipsis,
 * so a string that overflows is cut mid-word with nothing on screen saying so.
 * Line count is NOT linear in character count — `overflow-wrap: anywhere` plus
 * whatever break opportunities the string happens to carry decide where it
 * wraps — so wording that has to survive the clamp is measured, never counted.
 *
 *   node scripts/measure-toast-clamp.mjs --width 272 --width 296 --string-file s.json
 *
 * `--string-file` is JSON: a string, an array of strings, or an array of
 * `{ label, string }`. One JSON row is printed per (string, width) carrying
 * `{ label, chars, boxWidthPx, lineHeightPx, maxHeightPx, renderedHeightPx,
 * lines, fits }`, where `lines = renderedHeightPx / lineHeightPx` and
 * `fits = renderedHeightPx <= maxHeightPx`.
 *
 * Exits non-zero when the measurement cannot be trusted rather than printing a
 * reasonable-looking number: if the webfont did not load (layout would silently
 * fall back to system-ui), or if a forced width did not take.
 */
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

/** The family `--ui` resolves to first. Everything after it is a fallback. */
const FONT_FAMILY = "IBM Plex Sans";
const FONT = `12px "${FONT_FAMILY}"`;
/** What `--ui` falls back to. The probe below must NOT lay out the same as it. */
const FONT_FALLBACK = "system-ui";
/** Tolerance on reading the forced width back out of the layout. */
const WIDTH_EPSILON_PX = 0.5;

/**
 * Stop with a non-zero exit. Thrown rather than exited so a failure found
 * mid-measurement still closes the browser and the server on its way out.
 */
class MeasurementUntrustworthy extends Error {}
function fail(message) {
  throw new MeasurementUntrustworthy(message);
}

function argValues(flag) {
  const out = [];
  for (let i = 0; i < process.argv.length - 1; i += 1) {
    if (process.argv[i] === flag) out.push(process.argv[i + 1]);
  }
  return out;
}

process.on("uncaughtException", (err) => {
  if (err instanceof MeasurementUntrustworthy) {
    console.error(`measure-toast-clamp: ${err.message}`);
    process.exit(1);
  }
  throw err;
});

const widths = argValues("--width").map((raw) => {
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) fail(`--width ${raw} is not a positive number`);
  return n;
});
if (widths.length === 0) fail("no --width given; pass at least one");

const [stringFile] = argValues("--string-file");
if (!stringFile) fail("no --string-file given");

let parsed;
try {
  parsed = JSON.parse(readFileSync(resolve(process.cwd(), stringFile), "utf8"));
} catch (err) {
  fail(`could not read --string-file ${stringFile} as JSON: ${err.message}`);
}
const subjects = (Array.isArray(parsed) ? parsed : [parsed]).map((entry, i) => {
  if (typeof entry === "string") return { label: `string[${i}]`, string: entry };
  if (entry && typeof entry.string === "string") {
    return { label: entry.label ?? `string[${i}]`, string: entry.string };
  }
  return fail(`--string-file entry ${i} is neither a string nor {label, string}`);
});

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  fail("playwright is not installed. Run: npm install && npx playwright install chromium");
}

// The stylesheets are read from the tree and served UNMODIFIED. Nothing here
// adds a rule touching `.toast__message`'s font-size, line-height, max-height
// or overflow-wrap — those four are the clamp, and overriding any of them would
// measure a toast this app does not have.
const sheets = ["../src/index.css", "../src/components/ToastHost.css"].map((rel) =>
  readFileSync(resolve(here, rel), "utf8"),
);

// Served from a real origin, not `page.setContent` on about:blank: index.html
// pulls IBM Plex Sans from the Google Fonts CDN, and a document with no origin
// never fetches that subresource.
const fontLink = readFileSync(resolve(here, "../index.html"), "utf8")
  .split("\n")
  .filter((line) => line.includes("fonts.googleapis.com") || line.includes("fonts.gstatic.com"))
  .join("\n");

// The markup `ToastHost.tsx` renders: the string is the sole child of
// `.toast__message`, inside `.toast__body`, inside `.toast`.
const html = `<!doctype html>
<html lang="en"><head><meta charset="UTF-8" />
${fontLink}
<style>${sheets.join("\n")}</style>
</head><body>
  <div class="toast-host">
    <div class="toast toast--error" role="alert">
      <span class="toast__glyph" aria-hidden="true">!</span>
      <div class="toast__body">
        <div class="toast__title">Delete session failed</div>
        <div class="toast__message" id="subject"></div>
      </div>
    </div>
  </div>
</body></html>`;

const server = createServer((_req, res) => {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(html);
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const origin = `http://127.0.0.1:${server.address().port}/`;

const browser = await chromium.launch();
let rows;
try {
  const page = await browser.newPage();
  await page.goto(origin, { waitUntil: "load" });

  // `display=swap` resolves `document.fonts.ready` without ever loading a
  // family nothing on the page is using yet, so ask for this one by name and
  // then prove it arrived. This has to be fatal, and it has to be more than
  // `document.fonts.check()`: that returns TRUE for a family that does not
  // exist, because the fallback it would use counts as available. Measured —
  // with a bogus family name and no real load, every row below still printed,
  // and the control string laid out at 5 lines instead of 4. So the detector
  // is the FontFace status plus a layout probe against the fallback.
  const font = await page.evaluate(
    async ({ spec, family, fallback }) => {
      await document.fonts.load(spec);
      const faces = Array.from(document.fonts).filter((f) => f.family === family);
      const probe = document.createElement("span");
      probe.textContent = "Commit or discard this chat's unpublished work";
      probe.style.cssText = "position:absolute;white-space:pre;font-size:12px";
      document.body.append(probe);
      probe.style.fontFamily = `"${family}", ${fallback}`;
      const subjectWidth = probe.getBoundingClientRect().width;
      probe.style.fontFamily = fallback;
      const fallbackWidth = probe.getBoundingClientRect().width;
      probe.remove();
      return {
        loadedFaces: faces.filter((f) => f.status === "loaded").length,
        declaredFaces: faces.length,
        subjectWidth,
        fallbackWidth,
      };
    },
    { spec: FONT, family: FONT_FAMILY, fallback: FONT_FALLBACK },
  );
  if (font.loadedFaces === 0) {
    fail(
      `"${FONT_FAMILY}" has ${font.declaredFaces} declared and 0 loaded faces; ` +
        `layout would silently fall back to ${FONT_FALLBACK}`,
    );
  }
  if (font.subjectWidth === font.fallbackWidth) {
    fail(
      `"${FONT_FAMILY}" lays out identically to ${FONT_FALLBACK} ` +
        `(${font.subjectWidth}px); it is not actually being applied`,
    );
  }

  rows = [];
  for (const subject of subjects) {
    for (const width of widths) {
      const row = await page.evaluate(
        ({ text, width: w, epsilon }) => {
          const el = document.getElementById("subject");
          el.textContent = text;
          el.style.setProperty("width", `${w}px`, "important");
          el.style.setProperty("flex", "none", "important");
          const style = getComputedStyle(el);
          const lineHeightPx = Number.parseFloat(style.lineHeight);

          // Count line boxes, don't read a height back off the element. The
          // box is clipped to max-height, so its own rect reports every
          // overflowing string as exactly fitting; and `scrollHeight` is an
          // integer, so a string filling 4 lines of 17.4px reports 70 against
          // a 69.6px clamp and looks like an overflow it is not. A Range over
          // the contents yields one rect per line box, and `overflow: hidden`
          // clips painting rather than geometry, so the rects survive it.
          const range = document.createRange();
          range.selectNodeContents(el);
          const tops = new Set(
            Array.from(range.getClientRects()).map((r) => Math.round(r.top * 100)),
          );
          const lineBoxes = tops.size;

          return {
            chars: text.length,
            boxWidthPx: el.getBoundingClientRect().width,
            lineHeightPx,
            maxHeightPx: Number.parseFloat(style.maxHeight),
            renderedHeightPx: lineBoxes * lineHeightPx,
            lineBoxes,
            // Cross-check against the lossy measure: they may differ by the
            // integer rounding and no more. A wider gap means the line boxes
            // are not what this is assuming (an inline child, a nested block),
            // and the ratio would be measuring something else.
            scrollHeightPx: el.scrollHeight,
            requestedWidthPx: w,
            widthTookEffect: Math.abs(el.getBoundingClientRect().width - w) <= epsilon,
          };
        },
        { text: subject.string, width, epsilon: WIDTH_EPSILON_PX },
      );
      if (!row.widthTookEffect) {
        fail(
          `forced width did not take: asked for ${width}px, laid out at ` +
            `${row.boxWidthPx}px (over ${WIDTH_EPSILON_PX}px off)`,
        );
      }
      if (!Number.isFinite(row.lineHeightPx) || !Number.isFinite(row.maxHeightPx)) {
        fail("`.toast__message` has no numeric line-height or max-height to measure against");
      }
      if (row.lineBoxes < 1) fail("`.toast__message` laid out no line boxes to count");
      if (Math.abs(row.scrollHeightPx - row.renderedHeightPx) > 1) {
        fail(
          `${row.lineBoxes} line boxes of ${row.lineHeightPx}px is ` +
            `${row.renderedHeightPx}px, but scrollHeight is ${row.scrollHeightPx}px — ` +
            "the content is not the plain run of text this measures",
        );
      }
      rows.push({
        label: subject.label,
        chars: row.chars,
        boxWidthPx: row.boxWidthPx,
        lineHeightPx: row.lineHeightPx,
        maxHeightPx: row.maxHeightPx,
        renderedHeightPx: row.renderedHeightPx,
        lines: Number((row.renderedHeightPx / row.lineHeightPx).toFixed(2)),
        fits: row.renderedHeightPx <= row.maxHeightPx,
      });
    }
  }
} catch (err) {
  await browser.close();
  server.close();
  if (err instanceof MeasurementUntrustworthy) {
    console.error(`measure-toast-clamp: ${err.message}`);
    process.exit(1);
  }
  throw err;
}
await browser.close();
server.close();

for (const row of rows) console.log(JSON.stringify(row));
