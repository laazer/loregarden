/**
 * The theme's text/background pairings clear WCAG AA.
 *
 * The body-text block below is the original scope. The Feature card block is
 * lg-baxter-chat-2742: `.chat-wb-status--running` shipped `--blue` on `--bll`
 * at 1.49:1 and `--blocked` shipped `--amb` on `--aml` at 1.35:1, because both
 * used a *light text* step as a background fill. The rules are read out of
 * `Workbench.css` rather than restated here, so a future pill that inverts a
 * light token into a background fails this suite instead of shipping.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  RESTING_SURFACES,
  channels,
  contrast,
  cssRules,
  resolveValue,
  token,
} from "../../test/contrast";

it("computes WCAG contrast the way the spec's reference pairs do", () => {
  expect(contrast(channels("#000000"), channels("#ffffff"))).toBeCloseTo(21, 5);
  expect(contrast(channels("#777777"), channels("#ffffff"))).toBeCloseTo(4.48, 2);
});

// --bg4 is the hover and selected fill, not a resting surface, so it is left out.
describe.each(RESTING_SURFACES)("on --%s", (surface) => {
  it.each(["tx", "txm", "txl"])("--%s clears AA for body text (4.5:1)", (text) => {
    expect(contrast(channels(token(text)), channels(token(surface)))).toBeGreaterThanOrEqual(4.5);
  });
});

const WORKBENCH_CSS = readFileSync(
  join(__dirname, "..", "..", "components", "chat", "workbench", "Workbench.css"),
  "utf8",
);

const STATUS_RULES = cssRules(WORKBENCH_CSS, /^\.chat-wb-status(--[a-z_]+)?$/);
const WARN_RULES = cssRules(WORKBENCH_CSS, /^\.chat-wb-warn$/);
/** [selector, rule], so each case names the selector it is about. */
const PAIRS = [...STATUS_RULES, ...WARN_RULES].map((rule) => [rule.selector, rule] as const);

describe("the Feature card's status pill and warning line", () => {
  // A CSS-parsing test that matches nothing is green and proves nothing. These
  // two counts are the guard: rename, delete or re-nest one of these rules and
  // the suite reds rather than passing vacuously. 3 = the base pill plus the
  // --running and --blocked variants; 1 = .chat-wb-warn.
  it("matches exactly the rules it claims to cover", () => {
    expect(STATUS_RULES.map((r) => r.selector).sort()).toEqual([
      ".chat-wb-status",
      ".chat-wb-status--blocked",
      ".chat-wb-status--running",
    ]);
    expect(WARN_RULES.map((r) => r.selector)).toEqual([".chat-wb-warn"]);
  });

  // The defect was an *inherited* colour meeting a declared background: the base
  // pill took --txl from .chat-wb-card-meta, so no single rule held the pair and
  // no test could read it. Every rule must now declare both halves itself.
  it.each(PAIRS)("%s declares both its colour and its background", (_selector, rule) => {
    expect(rule.color).not.toBeNull();
    expect(rule.background).not.toBeNull();
  });

  describe.each(RESTING_SURFACES)("composited over --%s", (surface) => {
    it.each(PAIRS)("%s clears 4.5:1", (_selector, rule) => {
      if (!rule.color || !rule.background) throw new Error(`${rule.selector} declares no pair`);
      const ratio = contrast(
        resolveValue(rule.color, surface),
        resolveValue(rule.background, surface),
      );
      expect(ratio).toBeGreaterThanOrEqual(4.5);
    });
  });

  // Contrast alone does not pin the convention: dark text on an opaque light
  // fill also clears 4.5:1, and would be a second convention competing with the
  // one StatusChip.css documents (light text over a 14-16% tint of the hue).
  // The tinted-background assertion is what keeps there being one.
  it.each([".chat-wb-status--running", ".chat-wb-status--blocked", ".chat-wb-warn"])(
    "%s tints its background rather than filling it with a light step",
    (selector) => {
      const rule = PAIRS.find(([s]) => s === selector)?.[1];
      if (!rule?.background) throw new Error(`${selector} declares no background`);
      expect(rule.background).toMatch(
        /^color-mix\(\s*in\s+srgb\s*,\s*var\(--[a-z0-9-]+\)\s+1[0-9]%\s*,\s*transparent\s*\)$/,
      );
    },
  );

  // Out of scope on this ticket, and named as out of scope: the same inversion
  // (--red on --rdl, 1.35:1) in a class that renders in ChangesCard. Pinned so
  // a fix that reaches past its ticket is visible rather than silent.
  it("leaves .chat-wb-tag--warn, which renders on another card, alone", () => {
    const [tag] = cssRules(WORKBENCH_CSS, /^\.chat-wb-tag--warn$/);
    expect(tag).toBeDefined();
    expect(tag.background).toBe("var(--rdl)");
    expect(tag.color).toBe("var(--red)");
  });
});

describe("the contrast helper refuses what it cannot read", () => {
  // Without this, an unreadable declaration could resolve to some default and
  // the pair above would be asserted against a colour nothing renders.
  it("throws on a value that is neither a token nor a srgb tint", () => {
    expect(() => resolveValue("#89c0ff", "bg1")).toThrow(/cannot resolve/);
    expect(() => resolveValue("linear-gradient(red, blue)", "bg1")).toThrow(/cannot resolve/);
  });

  it("throws on a token index.css does not define", () => {
    expect(() => token("not-a-token")).toThrow(/defines no hex/);
  });
});
