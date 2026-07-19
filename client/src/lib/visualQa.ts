import routes from "./visualQaRoutes.json";

/** A surface the visual check must visit. Shared with scripts/visual-qa.mjs,
 *  which reads the same JSON so the list cannot drift between them. */
export interface VisualQaRoute {
  name: string;
  path: string;
}

export interface VisualQaResult {
  name: string;
  path: string;
  screenshot?: string;
  /** Console errors seen while the surface was loading. */
  errors: string[];
  /** Set when the surface could not be reached at all. */
  loadError?: string;
}

export interface VisualQaSummary {
  ok: boolean;
  checked: number;
  failed: VisualQaResult[];
  /** Surfaces that were never visited — an unvisited route is not a pass. */
  missing: string[];
}

export const VISUAL_QA_ROUTES: VisualQaRoute[] = routes;

export function surfaceFailed(result: VisualQaResult): boolean {
  return Boolean(result.loadError) || result.errors.length > 0;
}

/**
 * Verdict over every surface.
 *
 * One bad surface fails the run. "Most pages look fine" is the reading this
 * exists to prevent — a check that passes while a route is broken is worse than
 * no check, because it is evidence of something untrue. A route that was never
 * visited counts against the run for the same reason.
 */
export function summarizeVisualQa(
  results: VisualQaResult[],
  expected: VisualQaRoute[] = VISUAL_QA_ROUTES,
): VisualQaSummary {
  const seen = new Set(results.map((r) => r.name));
  const missing = expected.filter((r) => !seen.has(r.name)).map((r) => r.name);
  const failed = results.filter(surfaceFailed);
  return {
    ok: failed.length === 0 && missing.length === 0,
    checked: results.length,
    failed,
    missing,
  };
}
