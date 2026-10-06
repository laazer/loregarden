/**
 * The app frame must not be a scroll container.
 *
 * An `overflow: hidden` box still scrolls — by `scrollIntoView`, `focus()`,
 * find-in-page or assistive tech — and the frame always had room to: an
 * absolutely positioned `.visually-hidden` caption in a page takes the frame as
 * its containing block and escapes the page's scroller (measured on an
 * initiative plan at 800x600: the frame's scrollHeight was 1823, and one
 * `scrollIntoView` on that caption lifted the app 1247px). `overflow: clip`
 * makes it unscrollable by anything.
 *
 * jsdom loads no CSS, so the stylesheet is the subject, as in
 * formControlBaseline.test.ts.
 */

import fs from "fs";
import path from "path";

const INDEX_CSS = path.resolve(__dirname, "../../index.css");

function appFrameRule(): string | null {
  const css = fs.readFileSync(INDEX_CSS, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  return css.match(/(?:^|\n)\.app-frame\s*\{([^}]*)\}/)?.[1] ?? null;
}

test("the app frame clips its overflow rather than hiding it in a scroll container", () => {
  const body = appFrameRule();
  expect(body).not.toBeNull();
  expect(body).toMatch(/(?:^|[;\s])overflow:\s*clip\s*;/);
  expect(body).not.toMatch(/overflow(?:-[xy])?:\s*(?:hidden|auto|scroll)/);
});
