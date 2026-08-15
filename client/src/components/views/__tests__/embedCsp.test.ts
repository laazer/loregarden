/**
 * AC6's other half: the packaged shell has to *allow* the frame the embed
 * primitive renders.
 *
 * The dev build Vite serves has no CSP at all, so every other test in this
 * directory would keep passing while the shipping desktop app rendered a blank
 * pane: `src-tauri/tauri.conf.json` declares `default-src 'self'`, and
 * `frame-src` falls back through `child-src` to `default-src` when it is not
 * declared. So the embed policy is written in two places — `embedUrl.ts` and
 * this config — and they have to agree.
 *
 * No browser runs under jest, so this asserts on the parsed config rather than
 * on a load. What it pins is the *decision*: that `frame-src` is declared at
 * all, that it admits the http(s) origins `safeEmbedUrl` allows, and that it
 * does not re-admit the `data:`/`blob:` frames that function refuses — which is
 * what `frame-src *` would silently do.
 */

import fs from "fs";
import path from "path";

const CONFIG_PATH = path.resolve(__dirname, "../../../../..", "src-tauri/tauri.conf.json");

function directives(): Map<string, string[]> {
  const config = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
  const csp: unknown = config?.app?.security?.csp;
  expect(typeof csp).toBe("string");

  const parsed = new Map<string, string[]>();
  for (const clause of String(csp).split(";")) {
    const [name, ...sources] = clause.trim().split(/\s+/).filter(Boolean);
    if (name !== undefined) parsed.set(name.toLowerCase(), sources);
  }
  return parsed;
}

describe("the desktop shell's CSP admits the web embed", () => {
  it("declares frame-src rather than inheriting default-src", () => {
    // Inheriting is the bug: `default-src 'self'` with no `frame-src` blocks
    // every external embed in the packaged app, and nothing in the dev build
    // says so.
    expect(directives().has("frame-src")).toBe(true);
  });

  it("admits https origins, so an allowed embed URL can load", () => {
    const sources = directives().get("frame-src") ?? [];
    expect(sources.some((source) => source === "https:" || source.startsWith("https://"))).toBe(
      true,
    );
  });

  it("does not admit the schemes safeEmbedUrl refuses", () => {
    const sources = directives().get("frame-src") ?? [];
    // `frame-src *` is the tempting shortcut and it re-admits `data:` and
    // `blob:` frames, putting the CSP and the URL allowlist in disagreement.
    expect(sources).not.toContain("*");
    for (const scheme of ["data:", "blob:", "filesystem:", "'unsafe-inline'"]) {
      expect(sources).not.toContain(scheme);
    }
  });

  it("does not buy the embed by loosening default-src", () => {
    // The frame is allowed by naming `frame-src`, never by widening the
    // fallback every other resource type also reads.
    expect(directives().get("default-src")).toEqual(["'self'"]);
  });
});
