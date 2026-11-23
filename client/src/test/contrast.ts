/**
 * WCAG contrast over the theme's own tokens, for the tests that assert it.
 *
 * Lives in `client/src/test/` rather than beside the tests that use it:
 * `jest.config.cjs` collects `src/**\/__tests__/**\/*.(test|spec).(ts|tsx)`, so a
 * helper placed in a `__tests__` directory is simply not collected and reads as
 * a suite that never runs.
 *
 * `themeContrast.test.ts` and `statusTone.test.ts` each carried their own copy
 * of this arithmetic. A third copy would have drifted from the other two; one
 * module is the reason either of them can be trusted to agree.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

const INDEX_CSS = readFileSync(join(__dirname, "..", "index.css"), "utf8");
const ROOT = /:root\s*\{([\s\S]*?)\n\}/.exec(INDEX_CSS)?.[1] ?? "";

/** The `#rrggbb` value `index.css` `:root` gives `--name`. Throws if it has none. */
export function token(name: string): string {
  const match = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6});`).exec(ROOT);
  if (!match) throw new Error(`index.css :root defines no hex --${name}`);
  return match[1];
}

/** `#rrggbb` to its three 0-255 channels. */
export function channels(hex: string): number[] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

/** WCAG 2.x relative luminance of an RGB triple. */
export function luminance(rgb: number[]): number {
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 2.x contrast ratio between two RGB triples. */
export function contrast(a: number[], b: number[]): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * `color-mix(in srgb, <hue> N%, transparent)` composited over an opaque
 * surface — the tint convention `StatusChip.css` documents: text takes the
 * hue's light step, the background takes a 14-16% tint of the base hue.
 *
 * Both arguments are `#rrggbb`, not token names, so a caller can composite a
 * measured colour that is not a token.
 */
export function tint(hue: string, percent: number, surface: string): number[] {
  const h = channels(hue);
  return channels(surface).map((s, i) => h[i] * (percent / 100) + s * (1 - percent / 100));
}

/** The resting surfaces a card can sit on. `--bg4` is hover/selected fill, not a surface. */
export const RESTING_SURFACES = ["bg0", "bg1", "bg2", "bg3"] as const;

/**
 * A CSS block comment.
 *
 * Stripped before any rule scan: `[^{}@]` happily swallows a comment sitting
 * above a rule into that rule's selector, so an ordinary explanatory comment
 * would otherwise hide the rule beneath it from every assertion — and a
 * commented-out rule would be read as a live one.
 */
export const COMMENT = /\/\*[\s\S]*?\*\//g;

/** The `color`/`background` a CSS rule declares, as written. */
export interface CssPair {
  selector: string;
  color: string | null;
  background: string | null;
}

/**
 * Every rule in `css` whose selector matches `selectorPattern`, with its own
 * `color` and `background` declarations.
 *
 * Deliberately a flat regex scan rather than a CSS parser: the rules under test
 * are hand-written single-selector blocks, and a parser dependency for four
 * rules would be the larger risk. A rule the scan cannot see is reported as a
 * missing match by the caller's rule-count assertion, never silently skipped.
 * Comments go first, for the reason `COMMENT` gives.
 */
export function cssRules(css: string, selectorPattern: RegExp): CssPair[] {
  const out: CssPair[] = [];
  const stripped = css.replace(COMMENT, "");
  // No `}` prefix in the pattern: a previous match consumes the closing brace,
  // so anchoring on one makes the scan skip every second rule in a run of
  // adjacent rules. `[^{}@]` already keeps a selector from swallowing a brace.
  const block = /([^{}@]+?)\s*\{([^{}]*)\}/g;
  let match: RegExpExecArray | null;
  while ((match = block.exec(stripped)) !== null) {
    const selector = match[1].trim();
    if (!selectorPattern.test(selector)) continue;
    const body = match[2];
    out.push({
      selector,
      color: /(?:^|[\s;])color:\s*([^;]+);/.exec(body)?.[1].trim() ?? null,
      background: /(?:^|[\s;])background:\s*([^;]+);/.exec(body)?.[1].trim() ?? null,
    });
  }
  return out;
}

/**
 * A declared colour resolved to RGB over `surface`.
 *
 * Handles the two forms the rules under test use: a plain `var(--x)`, and
 * `color-mix(in srgb, var(--x) N%, transparent)`. Anything else throws rather
 * than resolving to a default — a value this cannot read is a value the
 * assertion would otherwise pass over.
 */
export function resolveValue(value: string, surface: string): number[] {
  const plain = /^var\(--([a-z0-9-]+)\)$/.exec(value.trim());
  if (plain) return channels(token(plain[1]));
  const mixed = /^color-mix\(\s*in\s+srgb\s*,\s*var\(--([a-z0-9-]+)\)\s+([\d.]+)%\s*,\s*transparent\s*\)$/.exec(
    value.trim(),
  );
  if (mixed) return tint(token(mixed[1]), Number(mixed[2]), token(surface));
  throw new Error(
    `cannot resolve "${value}" — expected var(--x) or color-mix(in srgb, var(--x) N%, transparent)`,
  );
}
