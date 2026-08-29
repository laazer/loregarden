/**
 * The client had no element-level `input { }` rule, so any control that named
 * no field class rendered as a white browser-default box on the #0b0f16 page.
 * That was the root cause of 555, and it was reachable from anywhere.
 *
 * jsdom loads none of the app's CSS — every element in it reports transparent,
 * at 0px — so a computed-style assertion here could only ever pass by asserting
 * an inline style, which is the bespoke-per-component styling the fix exists to
 * remove. The stylesheet itself is therefore the subject under test.
 */

import fs from "fs";
import path from "path";

const INDEX_CSS = path.resolve(__dirname, "../../index.css");

/** index.css with comments removed. */
function stylesheet(): string {
  return fs.readFileSync(INDEX_CSS, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
}

/** The declaration block of the baseline rule, or null if the rule is gone. */
function baselineRule(): { selector: string; body: string } | null {
  // Comments first: the rule's own commentary quotes the selector it explains,
  // and a match that landed there would vouch for the prose, not the CSS.
  const css = stylesheet();
  const match = css.match(/(input:not\([\s\S]*?\),\ntextarea,\nselect)\s*\{([^}]*)\}/);
  return match ? { selector: match[1], body: match[2] } : null;
}

describe("the baseline treatment for unclassed form controls", () => {
  it("states a rule at element level for input, textarea and select", () => {
    expect(baselineRule()).not.toBeNull();
  });

  it("paints from the app's tokens rather than literal colours", () => {
    const body = (baselineRule() as { body: string }).body;
    expect(body).toMatch(/background:\s*var\(--bg2\)/);
    expect(body).toMatch(/color:\s*var\(--tx\)/);
    expect(body).toMatch(/border:\s*1px solid var\(--bd\)/);
    expect(body).not.toMatch(/#[0-9a-fA-F]{3,8}/);
  });

  it("keeps its specificity below a single class, so the field classes still win", () => {
    // The regression this guards is subtle and total: written the obvious way,
    // `input:not([type="checkbox"])` inherits the attribute selector's weight
    // and scores (0,1,1) — above every one of the field classes below, all of
    // which are (0,1,0). The baseline would silently repaint all six. Wrapping
    // the exclusions in `:where()` scores them zero and keeps the rule at
    // (0,0,1).
    const selector = (baselineRule() as { selector: string }).selector;
    expect(selector).toContain(":not(:where(");
    expect(selector.replace(/:not\(:where\([\s\S]*?\)\)/g, "")).not.toMatch(/\[type=/);
  });

  it("excludes the control types a background would replace", () => {
    const selector = (baselineRule() as { selector: string }).selector;
    for (const type of ["checkbox", "radio", "range", "color", "file"]) {
      expect({ type, excluded: selector.includes(`[type="${type}"]`) }).toEqual({
        type,
        excluded: true,
      });
    }
  });

  it("sets no box metrics, which the flush-mounted bar inputs depend on", () => {
    // `.app-action-bar-input` and `.topbar-search input` are transparent and
    // borderless and sit flush in their bars; a baseline `padding` or `height`
    // would move the caret in both, and neither class overrides those.
    const body = (baselineRule() as { body: string }).body;
    expect(body).not.toMatch(/^\s*(padding|height|width|margin)\s*:/m);
  });
});

describe("the field classes the baseline must not outrank", () => {
  const FIELD_CLASSES = [
    "input",
    "studio-input",
    "studio-stage-input",
    "studio-describe-input",
    "ticket-search",
    "filter-select",
    "app-action-bar-input",
  ];

  it("still defines every one of them", () => {
    // A guard on the guard above: the specificity test is only meaningful while
    // these rules exist to be outranked.
    const css = stylesheet();
    for (const name of FIELD_CLASSES) {
      expect({ name, defined: new RegExp(`\\.${name}[\\s,:.{]`).test(css) }).toEqual({
        name,
        defined: true,
      });
    }
  });
});
