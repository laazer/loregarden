// @registry-check: runs on pre-commit (.lefthook/scripts/registry-checks.sh)
/**
 * A page is declared in four places nothing else ties together: `PAGE_PATHS`
 * and `pageFromPath` here, a `<Route>` in App.tsx, and an entry in
 * visualQaRoutes.json. `tsc -b` holds only the first (a `Record<AppPage, …>`).
 * A page added to the union with a path but no reader lit the wrong sidebar
 * row; one with no route redirected home; one with no visual-QA entry was never
 * opened by `npm run visual-qa`. This holds the other three to `PAGE_PATHS`.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { matchPath } from "react-router-dom";

import { APP_PAGES, pageFromPath, pathForPage, type AppPage } from "../appNavigation";
import { VISUAL_QA_ROUTES } from "../visualQa";

/** Page keys kept for links and agents whose path belongs to another page. */
const ALIASES: Partial<Record<AppPage, AppPage>> = { queue: "machine" };

function appRoutePatterns(): string[] {
  const source = readFileSync(resolve(__dirname, "..", "..", "App.tsx"), "utf8");
  const patterns = [...source.matchAll(/<Route\s[^>]*?path="([^"]+)"/g)].map((m) => m[1]);
  // The catch-all would match anything, and it redirects home.
  return patterns.filter((pattern) => pattern !== "*");
}

describe("page registry", () => {
  it("reads App.tsx's routes", () => {
    // An empty list would make every route check below pass vacuously.
    expect(appRoutePatterns().length).toBeGreaterThan(10);
  });

  it.each(APP_PAGES)("%s: its path reads back as the page", (page) => {
    expect(pageFromPath(pathForPage(page))).toBe(ALIASES[page] ?? page);
  });

  it.each(APP_PAGES)("%s: App.tsx routes its path", (page) => {
    const path = pathForPage(page);
    const routed = appRoutePatterns().some((pattern) => matchPath(pattern, path) !== null);
    expect(routed).toBe(true);
  });

  it.each(APP_PAGES)("%s: visual QA opens it", (page) => {
    const path = pathForPage(page);
    const opened = VISUAL_QA_ROUTES.some(
      (route) => route.path === path || route.path.startsWith(`${path}/`),
    );
    expect(opened).toBe(true);
  });
});
