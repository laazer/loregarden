/**
 * The visual check's verdict over every surface.
 *
 * Self-contained, with no imports: scripts/visual-qa.mjs transpiles this file
 * and runs it, the way it runs usabilityCheck.ts, so the script and jest agree
 * on what passes by construction rather than by keeping two copies in step.
 */

export interface VisualQaResult {
  name: string;
  path: string;
  screenshot?: string;
  /** Console errors seen while the surface was loading. */
  errors: string[];
  /** Set when the surface could not be reached at all (refused, crashed, 404). */
  loadError?: string;
  /**
   * Set when the surface was reached but never settled within its budget, even
   * after a retry. A slow box produces this, so it is reported apart from a
   * usability failure and never counts as one.
   */
  unsettled?: string;
  /** Unusable shapes found in the rendered page (see `usabilityCheck`). */
  usability?: { kind: string; detail: string }[];
}

/**
 * `pass`: every surface was checked and is clean. `fail`: at least one surface
 * is broken or unusable, or was never visited. `inconclusive`: nothing failed,
 * but some surface never settled, so it went unchecked — a load problem, which
 * must not read as either a pass or a usability regression.
 */
export type VisualQaVerdict = "pass" | "fail" | "inconclusive";

export interface VisualQaSummary {
  ok: boolean;
  verdict: VisualQaVerdict;
  checked: number;
  failed: VisualQaResult[];
  /** Surfaces that never settled, by name. Unchecked, not failed. */
  unsettled: string[];
  /** Surfaces that were never visited — an unvisited route is not a pass. */
  missing: string[];
}

/** Exit status per verdict; 3 so CI can tell a slow run from a broken one. */
export const VISUAL_QA_EXIT: Record<VisualQaVerdict, number> = { pass: 0, fail: 1, inconclusive: 3 };

export function surfaceFailed(result: VisualQaResult): boolean {
  return Boolean(result.loadError) || result.errors.length > 0 || (result.usability?.length ?? 0) > 0;
}

/**
 * One bad surface fails the run. "Most pages look fine" is the reading this
 * exists to prevent — a check that passes while a route is broken is worse than
 * no check, because it is evidence of something untrue. A route that was never
 * visited counts against the run for the same reason.
 */
export function summarizeVisualQa(results: VisualQaResult[], expected: string[]): VisualQaSummary {
  const seen = new Set(results.map((r) => r.name));
  const missing = expected.filter((name) => !seen.has(name));
  const failed = results.filter(surfaceFailed);
  const unsettled = results.filter((r) => r.unsettled && !surfaceFailed(r)).map((r) => r.name);
  const verdict: VisualQaVerdict =
    failed.length > 0 || missing.length > 0 ? "fail" : unsettled.length > 0 ? "inconclusive" : "pass";
  return { ok: verdict === "pass", verdict, checked: results.length, failed, unsettled, missing };
}
