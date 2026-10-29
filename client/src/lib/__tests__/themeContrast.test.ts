import { readFileSync } from "node:fs";
import { join } from "node:path";

const css = readFileSync(join(__dirname, "..", "..", "index.css"), "utf8");
const root = /:root\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";

function rootToken(name: string): string {
  const match = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6});`).exec(root);
  if (!match) throw new Error(`index.css :root defines no hex --${name}`);
  return match[1];
}

/** WCAG 2.x relative luminance of a #rrggbb colour. */
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

it("computes WCAG contrast the way the spec's reference pairs do", () => {
  expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
  expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
});

// --bg4 is the hover and selected fill, not a resting surface, so it is left out.
describe.each(["bg0", "bg1", "bg2", "bg3"])("on --%s", (surface) => {
  it.each(["tx", "txm", "txl"])("--%s clears AA for body text (4.5:1)", (text) => {
    expect(contrast(rootToken(text), rootToken(surface))).toBeGreaterThanOrEqual(4.5);
  });
});
