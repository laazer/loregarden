/**
 * index.css styled no bare `a`. Its only anchor selector in ~6000 lines was
 * `.markdown-preview a`, so any anchor that named no class of its own rendered
 * in the browser default — #0000EE, visited purple — on the #0b0f16 page. That
 * is the same root cause as 555's unclassed form controls, for the one
 * structural element the browser still paints.
 *
 * jsdom loads none of the app's CSS, so a computed-style assertion here could
 * only ever pass against an inline style, which is the per-component styling
 * the fix exists to remove. The stylesheet itself is the subject under test,
 * as in `formControlBaseline.test.ts`.
 */

import fs from "fs";
import path from "path";

const INDEX_CSS = path.resolve(__dirname, "../../index.css");

/** index.css with comments removed. */
function stylesheet(): string {
  return fs.readFileSync(INDEX_CSS, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
}

/** The declaration block of the element-level anchor rule, or null if it is gone. */
function baselineRule(): string | null {
  // Comments are stripped first: the rule's own commentary names the selector it
  // explains, and a match that landed there would vouch for the prose, not the CSS.
  const match = stylesheet().match(/(?:^|\n)a\s*\{([^}]*)\}/);
  return match ? match[1] : null;
}

describe("the baseline treatment for unclassed anchors", () => {
  it("states a rule at element level for a", () => {
    expect(baselineRule()).not.toBeNull();
  });

  it("paints from the app's tokens rather than a literal colour", () => {
    const body = baselineRule() as string;
    expect(body).toMatch(/color:\s*var\(--[\w-]+\)/);
    expect(body).not.toMatch(/#[0-9a-fA-F]{3,8}/);
    expect(body).not.toMatch(/\b(?:rgb|hsl)a?\(/);
  });

  it("keeps its specificity at one element, so a component class still wins", () => {
    // The regression this guards is total: written as `a:not(.foo)` the rule
    // would inherit the class selector's weight and outrank every anchor class
    // in the app. A bare type selector scores (0,0,1) and loses to all of them.
    const css = stylesheet();
    const match = css.match(/(?:^|\n)(a)\s*\{/);
    expect(match && match[1]).toBe("a");
  });

  it("underlines on hover and on keyboard focus, so the affordance is not colour alone", () => {
    const css = stylesheet();
    const rule = css.match(/\na:hover,\na:focus-visible\s*\{([^}]*)\}/);
    expect(rule).not.toBeNull();
    expect((rule as RegExpMatchArray)[1]).toMatch(/text-decoration:\s*underline/);
  });
});
