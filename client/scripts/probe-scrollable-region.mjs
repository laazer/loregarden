#!/usr/bin/env node
/**
 * Measure, in a real browser, which shapes the `scrollable-region-focusable`
 * detector fires on — one page, nine shapes, the real `src/lib/usabilityCheck.ts`.
 *
 * The detector's question is "can a keyboard reach this scrollbar", and six of
 * the nine shapes below have no scrollbar at all because the browser never
 * renders them. Counting cannot replace measuring here, for two reasons that
 * are only visible in an engine:
 *
 *   1. In a `display: none` subtree `getComputedStyle` returns *computed*, not
 *      used, values — `max-height` is still the declared `60px` and `overflow`
 *      still the declared `auto` — so a declaration-reading check reports a
 *      region that clips nothing.
 *   2. A **closed** `<details>` is the opposite trap: its bounded pane IS laid
 *      out and DOES clip (clientHeight 60 / scrollHeight 1440), geometrically
 *      indistinguishable from the open one. No amount of reading geometry
 *      separates them; only the browser's own `checkVisibility` does.
 *
 * So the predicate is three independent questions — is there a layout engine,
 * does the browser render this region, does it actually clip — and this script
 * is the only place the second one is exercised, because this repo has no
 * playwright test runner and jsdom implements neither `checkVisibility` nor
 * layout.
 *
 *   node scripts/probe-scrollable-region.mjs
 *
 * One JSON row per shape is printed carrying `{ shape, selector, expected,
 * findings, clientHeight, scrollHeight, checkVisibility, checkVisibilityDefault,
 * computedMaxHeight, computedOverflow, ok }`.
 *
 * Exits non-zero when any shape disagrees with `expected`, **including a
 * control that fails to fire**: a probe whose controls are silent proves
 * nothing, because every shape being silent is also what a detector deleted
 * altogether would print. It also exits non-zero rather than printing a
 * reasonable-looking zero when the measurement cannot be trusted at all: no
 * layout engine in the page, no `Element.prototype.checkVisibility` to ask, or
 * a control pane whose geometry says it is not clipping.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

/** The viewport every shape is measured at. `visual-qa` uses the same size. */
const VIEWPORT = { width: 1280, height: 900 };
/** The bound every pane below carries, and the clip it has to produce. */
const PANE = "max-height: 60px; overflow: auto;";
const LINE_PX = 60;
/**
 * Block lines, not a wrapped inline run. 24 × 60px is scrollHeight 1440
 * against clientHeight 60; a wrapped paragraph of the same text only reaches
 * ~72, which clips but does not look like the real `.wm-log` panes this is
 * modelled on.
 */
const LINES = 24;

/**
 * Stop with a non-zero exit. Thrown rather than exited so a failure found
 * mid-measurement still closes the browser on its way out.
 */
class MeasurementUntrustworthy extends Error {}
function fail(message) {
  throw new MeasurementUntrustworthy(message);
}

process.on("uncaughtException", (err) => {
  if (err instanceof MeasurementUntrustworthy) {
    console.error(`probe-scrollable-region: ${err.message}`);
    process.exit(1);
  }
  throw err;
});

const lines = Array.from(
  { length: LINES },
  (_, i) => `<div style="height: ${LINE_PX}px">line ${i + 1}</div>`,
).join("");
const pane = (cls, extra = "") => `<div class="${cls}" style="${PANE}${extra}">${lines}</div>`;

/**
 * `expected` is the number of `scrollable-region-focusable` findings whose
 * detail names this shape's pane. Six zeros and two ones is the whole point:
 * the zeros are the defect, the ones are the proof the detector still works.
 */
const SHAPES = [
  {
    shape: "bounded scroller inside a display:none ancestor",
    selector: ".a-pane",
    expected: 0,
    html: `<div style="display: none"><div>${pane("a-pane")}</div></div>`,
  },
  {
    shape: "bounded scroller carrying display:none itself",
    selector: ".b-pane",
    expected: 0,
    html: pane("b-pane", " display: none;"),
  },
  {
    shape: "bounded scroller inside a [hidden] container",
    selector: ".c-pane",
    expected: 0,
    html: `<div hidden>${pane("c-pane")}</div>`,
  },
  {
    shape: "bounded scroller inside a closed <details>",
    selector: ".d-pane",
    expected: 0,
    html: `<details><summary>Log</summary>${pane("d-pane")}</details>`,
  },
  {
    shape: "bounded scroller under content-visibility:hidden",
    selector: ".e-pane",
    expected: 0,
    html: `<div style="content-visibility: hidden">${pane("e-pane")}</div>`,
  },
  {
    shape: "bounded scroller under visibility:hidden",
    selector: ".f-pane",
    expected: 0,
    html: `<div style="visibility: hidden">${pane("f-pane")}</div>`,
  },
  {
    shape: "laid-out clipping region that IS a focus stop (control)",
    selector: ".g-pane",
    expected: 0,
    control: true,
    html: `<div class="g-pane" style="${PANE}" tabindex="0">${lines}</div>`,
  },
  {
    shape: "bounded scroller inside an OPEN <details> (control)",
    selector: ".h-pane",
    expected: 1,
    control: true,
    html: `<details open><summary>Log</summary>${pane("h-pane")}</details>`,
  },
  {
    shape: "plain laid-out clipping region, no focus stop (control)",
    selector: ".i-pane",
    expected: 1,
    control: true,
    html: pane("i-pane"),
  },
];

/** The two shapes whose geometry proves the fixture really clips. */
const CLIPPING_CONTROLS = [".h-pane", ".i-pane"];

/**
 * test-break — the measurements the zeros above REST ON, asserted rather than
 * merely printed.
 *
 * A row that prints `expected 0, got 0` is not evidence by itself: a zero is
 * also what this prints when the browser stopped behaving the way the fix was
 * designed around, and then the probe passes for a reason that is no longer
 * true. Two of those reasons are load-bearing and neither was checked:
 *
 *   - The closed `<details>` is the whole argument for asking the browser at
 *     all. It counts only while it IS laid out and DOES clip; the day a
 *     Chromium reports 0 geometry for it, the sweep-level layout probe alone
 *     would silence it, AC2's "necessary and not sufficient" would be false,
 *     and this file would still print nine agreeing rows.
 *   - `visibility: hidden` is the whole argument for the `visibilityProperty`
 *     option. It counts only while the bare call says `true` and the option
 *     makes it `false`. If the default ever starts returning `false` the
 *     option is decorative, and nothing here would say so.
 *
 * And a hidden shape silent with `checkVisibility` TRUE was silenced by
 * something other than the predicate under test, which is the same failure in
 * the general case: the count is right and the mechanism is not.
 */
const CLAIMS = [
  {
    selector: ".d-pane",
    claim:
      "a closed <details> pane is laid out and clipping (clientHeight > 0, " +
      "scrollHeight > clientHeight), which is why geometry alone cannot separate " +
      "it from the open one",
    holds: (row) => row.clientHeight > 0 && row.scrollHeight > row.clientHeight,
  },
  {
    selector: ".d-pane",
    claim: "the browser reports the closed <details> pane as not rendered",
    holds: (row) => row.checkVisibility === false,
  },
  {
    selector: ".h-pane",
    claim:
      "the OPEN <details> pane reports the same geometry as the closed one and " +
      "differs only in checkVisibility",
    holds: (row) => row.checkVisibility === true,
  },
  {
    selector: ".f-pane",
    claim:
      "the visibilityProperty option is load-bearing: the bare checkVisibility() " +
      "returns true for a visibility:hidden pane and the option makes it false",
    holds: (row) => row.checkVisibilityDefault === true && row.checkVisibility === false,
  },
  ...[".a-pane", ".b-pane", ".c-pane", ".d-pane", ".e-pane", ".f-pane"].map((selector) => ({
    selector,
    claim: "this hidden shape is silent because the browser says it does not render",
    holds: (row) => row.checkVisibility === false,
  })),
];

// Transpiled and evaluated exactly the way `scripts/visual-qa.mjs` does, so
// this measures the file that ships rather than a copy of it: same
// `transpileModule` options, same `export` strip, same
// `findUsabilityProblems(document.body)` call inside the page.
const ts = (await import("typescript")).default;
const source = readFileSync(resolve(here, "../src/lib/usabilityCheck.ts"), "utf8");
const usabilityCheck = ts
  .transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
  })
  .outputText.replace(/^export /gm, "");
if (!/function findUsabilityProblems/.test(usabilityCheck)) {
  fail("the transpiled src/lib/usabilityCheck.ts declares no findUsabilityProblems");
}

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  fail("playwright is not installed. Run: npm install && npx playwright install chromium");
}

const browser = await chromium.launch();
let rows;
try {
  const page = await browser.newPage({ viewport: VIEWPORT });
  await page.setContent("<!doctype html><html lang=\"en\"><body></body></html>");

  const engine = await page.evaluate(() => ({
    bodyWidth: document.body.getBoundingClientRect().width,
    hasCheckVisibility: typeof Element.prototype.checkVisibility === "function",
    userAgent: navigator.userAgent,
  }));
  if (!(engine.bodyWidth > 0)) {
    fail(
      `document.body lays out at ${engine.bodyWidth}px wide — there is no layout ` +
        "engine here, so every shape below would take the no-layout fallback and " +
        "this would measure jsdom's answer in a browser's clothing",
    );
  }
  if (!engine.hasCheckVisibility) {
    fail(
      `${engine.userAgent} has no Element.prototype.checkVisibility; the ` +
        "rendered-ness predicate under test cannot be exercised in this browser",
    );
  }

  rows = [];
  for (const subject of SHAPES) {
    const row = await page.evaluate(
      ({ html, selector, check }) => {
        document.body.innerHTML = html;
        // Force a layout before reading, so a geometry of 0 means "not
        // rendered" rather than "not measured yet".
        void document.body.offsetHeight;
        const el = document.querySelector(selector);
        if (!el) return { missing: true };
        const style = getComputedStyle(el);
        const supported = typeof el.checkVisibility === "function";
        const found = new Function(`${check}; return findUsabilityProblems(document.body);`)()
          .filter((p) => p.kind === "scrollable-region-focusable")
          .map((p) => p.detail);
        return {
          findings: found.filter((detail) => detail.includes(selector.slice(1))).length,
          allFindings: found,
          clientHeight: el.clientHeight,
          scrollHeight: el.scrollHeight,
          checkVisibility: supported ? el.checkVisibility({ visibilityProperty: true }) : null,
          checkVisibilityDefault: supported ? el.checkVisibility() : null,
          computedMaxHeight: style.maxHeight,
          computedOverflow: style.overflow,
        };
      },
      { html: subject.html, selector: subject.selector, check: usabilityCheck },
    );
    if (row.missing) fail(`${subject.selector} is not in this shape's markup`);
    rows.push({
      shape: subject.shape,
      selector: subject.selector,
      expected: subject.expected,
      ...row,
      ok: row.findings === subject.expected,
    });
  }
} catch (err) {
  await browser.close();
  if (err instanceof MeasurementUntrustworthy) {
    console.error(`probe-scrollable-region: ${err.message}`);
    process.exit(1);
  }
  throw err;
}
await browser.close();

for (const row of rows) console.log(JSON.stringify(row));

// The fixture has to be the shape this claims to measure before its zeros mean
// anything. A control pane that is not clipping would make every shape silent
// for a reason that has nothing to do with the predicate under test.
const notClipping = rows.filter(
  (row) =>
    CLIPPING_CONTROLS.includes(row.selector) &&
    !(row.clientHeight > 0 && row.scrollHeight > row.clientHeight),
);
for (const row of notClipping) {
  console.error(
    `probe-scrollable-region: ${row.selector} reports clientHeight ` +
      `${row.clientHeight} / scrollHeight ${row.scrollHeight} — it is not clipping, ` +
      "so this fixture never reaches the branch under test",
  );
}

const broken = [];
for (const { selector, claim, holds } of CLAIMS) {
  const row = rows.find((candidate) => candidate.selector === selector);
  if (!row) {
    broken.push(`${selector} is not among the measured shapes, so "${claim}" is unchecked`);
    continue;
  }
  if (!holds(row)) {
    broken.push(
      `${selector} no longer shows that ${claim} — clientHeight ${row.clientHeight}, ` +
        `scrollHeight ${row.scrollHeight}, checkVisibility({visibilityProperty:true}) ` +
        `${row.checkVisibility}, checkVisibility() ${row.checkVisibilityDefault}`,
    );
  }
}
for (const message of broken) console.error(`probe-scrollable-region: ${message}`);

const mismatched = rows.filter((row) => !row.ok);
for (const row of mismatched) {
  console.error(
    `probe-scrollable-region: ${row.selector} (${row.shape}) expected ` +
      `${row.expected} finding(s), got ${row.findings} — clientHeight ` +
      `${row.clientHeight}, scrollHeight ${row.scrollHeight}, ` +
      `checkVisibility({visibilityProperty:true}) ${row.checkVisibility}, ` +
      `computed max-height ${row.computedMaxHeight}`,
  );
}
if (notClipping.length > 0 || broken.length > 0 || mismatched.length > 0) process.exit(1);
console.log(
  `probe-scrollable-region: all ${rows.length} shapes agree ` +
    `(${rows.filter((r) => r.expected === 0).length} silent, ` +
    `${rows.filter((r) => r.expected > 0).length} firing)`,
);
