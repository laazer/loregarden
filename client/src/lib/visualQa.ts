import routes from "./visualQaRoutes.json";
import { summarizeVisualQa as summarize, type VisualQaSummary, type VisualQaResult } from "./visualQaVerdict";

export {
  surfaceFailed,
  VISUAL_QA_EXIT,
  type VisualQaResult,
  type VisualQaSummary,
  type VisualQaVerdict,
} from "./visualQaVerdict";

/** A surface the visual check must visit. Shared with scripts/visual-qa.mjs,
 *  which reads the same JSON so the list cannot drift between them. */
export interface VisualQaRoute {
  name: string;
  path: string;
}

export const VISUAL_QA_ROUTES: VisualQaRoute[] = routes;

/** Verdict over every surface; see `visualQaVerdict.ts`. */
export function summarizeVisualQa(
  results: VisualQaResult[],
  expected: VisualQaRoute[] = VISUAL_QA_ROUTES,
): VisualQaSummary {
  return summarize(
    results,
    expected.map((r) => r.name),
  );
}
