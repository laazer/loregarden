import type { ReviewBuild, ReviewItem } from "../api/reviewApi";

export function reviewBuildKey(build: ReviewBuild): string {
  return `${build.workspace_slug}:${build.ref}`;
}

export interface ReviewSelection {
  build: ReviewBuild | null;
  index: number;
  item: ReviewItem | null;
}

function findApproval(
  builds: ReviewBuild[],
  approvalId: string | null,
): { build: ReviewBuild; index: number } | null {
  if (!approvalId) return null;
  for (const build of builds) {
    const index = build.items.findIndex((item) => item.approval.id === approvalId);
    if (index >= 0) return { build, index };
  }
  return null;
}

/**
 * Which build and ticket the review page's query string shows.
 *
 * `?approval=` (an inbox card's link) names the gate itself and wins over the
 * positional `build`/`item`; a gate already resolved falls through.
 *
 * One function because two things ask: the page draws the answer, and the chat
 * and terminal bars, which sit outside the route, bind to it. Two readings
 * would let the bars name a ticket the page is not showing.
 */
export function selectReview(builds: ReviewBuild[], params: URLSearchParams): ReviewSelection {
  const linked = findApproval(builds, params.get("approval"));
  const build =
    linked?.build ??
    builds.find((b) => reviewBuildKey(b) === params.get("build")) ??
    builds[0] ??
    null;
  const count = build?.items.length ?? 0;
  const requested = linked?.index ?? Number(params.get("item") ?? 0);
  const index =
    count === 0
      ? 0
      : Math.min(Math.max(0, Number.isFinite(requested) ? requested : 0), count - 1);
  return { build, index, item: build?.items[index] ?? null };
}
