import { readFileSync } from "node:fs";
import { join } from "node:path";

import { DURATION, EASE_OUT } from "../motionTokens";

const css = readFileSync(join(__dirname, "..", "..", "index.css"), "utf8");

function rootToken(name: string): string {
  const match = new RegExp(`--${name}:\\s*([^;]+);`).exec(css);
  if (!match) throw new Error(`index.css defines no --${name}`);
  return match[1].trim();
}

function seconds(value: string): number {
  const match = /^(\d*\.?\d+)(ms|s)$/.exec(value);
  if (!match) throw new Error(`not a time: ${value}`);
  return match[2] === "ms" ? Number(match[1]) / 1000 : Number(match[1]);
}

it.each([
  ["t-fast", DURATION.fast],
  ["t-med", DURATION.med],
  ["t-slow", DURATION.slow],
  ["t-stagger", DURATION.stagger],
])("--%s matches the JS duration", (name, value) => {
  expect(seconds(rootToken(name))).toBe(value);
});

it("--ease-out matches the JS easing", () => {
  const points = /cubic-bezier\(([^)]*)\)/.exec(rootToken("ease-out"))?.[1].split(",").map(Number);
  expect(points).toEqual([...EASE_OUT]);
});
