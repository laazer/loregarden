import {
  VISUAL_QA_ROUTES,
  summarizeVisualQa,
  surfaceFailed,
  type VisualQaResult,
} from "../visualQa";

const ok = (name: string): VisualQaResult => ({ name, path: "/x", errors: [] });

const all = (): VisualQaResult[] => VISUAL_QA_ROUTES.map((r) => ok(r.name));

describe("visual QA verdict", () => {
  it("passes only when every surface is clean", () => {
    expect(summarizeVisualQa(all()).ok).toBe(true);
  });

  it("fails the whole run when a single surface has console errors", () => {
    // "Most pages look fine" is the reading this exists to prevent.
    const results = all();
    results[2] = { ...results[2], errors: ["TypeError: x is not a function"] };
    const summary = summarizeVisualQa(results);
    expect(summary.ok).toBe(false);
    expect(summary.failed.map((f) => f.name)).toEqual([results[2].name]);
  });

  it("fails when a surface could not be reached", () => {
    const results = all();
    results[0] = { ...results[0], loadError: "net::ERR_CONNECTION_REFUSED" };
    expect(summarizeVisualQa(results).ok).toBe(false);
  });

  it("counts an unvisited route against the run", () => {
    // A check that silently skips a surface is evidence of something untrue.
    const summary = summarizeVisualQa(all().slice(0, 3));
    expect(summary.ok).toBe(false);
    expect(summary.missing.length).toBe(VISUAL_QA_ROUTES.length - 3);
  });

  it("treats a clean surface as passing", () => {
    expect(surfaceFailed(ok("console"))).toBe(false);
  });

  it("enumerates the app's real surfaces", () => {
    const paths = VISUAL_QA_ROUTES.map((r) => r.path);
    expect(paths).toContain("/");
    expect(paths).toContain("/studio/agents");
    expect(paths).toContain("/queue");
  });
});
