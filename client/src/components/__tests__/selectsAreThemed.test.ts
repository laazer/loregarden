/**
 * Every <select> in the app goes through components/ui/Select, which replaces
 * the OS-drawn menu with a themed one. Checked against the source tree: the
 * diff-scoped theme gate only sees changed lines, which is how 37 raw selects
 * opened macOS's grey menu inside the dark app until they were moved over.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = join(__dirname, "..", "..");
const PRIMITIVE = join("components", "ui", "Select.tsx");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      return entry === "__tests__" || entry === "node_modules" ? [] : sourceFiles(path);
    }
    return path.endsWith(".tsx") ? [path] : [];
  });
}

/** A JSX `<select` tag on a line that is not a comment. */
const RAW_SELECT = /^(?!\s*(\/\/|\*|\/\*)).*<select(\s|>|$)/m;

describe("selects", () => {
  const files = sourceFiles(SRC).map((path) => ({
    path: path.slice(SRC.length + 1),
    source: readFileSync(path, "utf8"),
  }));

  it("scans the tree and finds the Select primitive's callers", () => {
    // A scan that matched nothing would pass the assertion below.
    expect(files.filter(({ source }) => source.includes("<Select")).length).toBeGreaterThan(30);
  });

  it("draws every <select> through components/ui/Select", () => {
    const raw = files.filter(({ path, source }) => path !== PRIMITIVE && RAW_SELECT.test(source)).map(({ path }) => path);
    expect(raw).toEqual([]);
  });
});
