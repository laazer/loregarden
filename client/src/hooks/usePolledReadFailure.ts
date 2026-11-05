/**
 * Which pane a polled read's failure justifies: the full one, or an inline
 * notice beside the content it still has.
 *
 * THE POLICY, in full, because it has one home and this is it.
 *
 * A failed read is SUPPRESSED when both of two conditions hold, and SURFACED
 * when either fails:
 *
 *  1. There is already content on screen. This is `queryClient.ts:28-31`'s
 *     reason, in its own words: "a failed background refetch over good data
 *     still shows the operator real numbers, and toasting it would fire on a
 *     timer". So `queryCache.onError` returns early whenever
 *     `query.state.data !== undefined`.
 *  2. The read will be attempted again on its own timer — a `refetchInterval`
 *     the operator never has to ask for.
 *
 * Where neither holds, the failure reports itself: a first read, or a read on a
 * surface that has never succeeded, names its reason even though there is no
 * content to protect, because there is nothing else to show.
 *
 * Where BOTH hold, the toast is suppressed by condition 1 and the next tick is
 * guaranteed by condition 2 — and that is exactly the regime where the branch
 * must NOT be "render the error instead". query-core keeps `data` and sets
 * `status: "error"` when a *refetch* fails over data already present, so a
 * surface whose error branch returns above its data render replaces whatever
 * the operator was reading with an error line, and the next successful poll
 * brings it back. Nothing was lost; the foreground content was thrown away.
 * The suppressed toast is also why the inline notice matters so much here: on
 * such a surface it is the ONLY report of the failed read.
 *
 * `queryClient.ts` cannot state this half, which is why it is written here. At
 * `queryCache.onError` there is no way to know whether a `refetchInterval` is
 * set; it sees only `data !== undefined`, which is one of the two conditions.
 *
 * TWO RULES THE CALLERS FOLLOW, both of which a plausible reading gets wrong.
 *
 * `hasContent`, NOT `isSuccess`. A failed refetch over good data reports
 * `isSuccess: false`, so a control gated on the status disappears at exactly
 * the moment the operator reaches for it — the fix would keep the rows and
 * remove the only thing anyone can do with them. The question is "is there
 * something to lose", and status does not answer it.
 *
 * `hasContent` is what the READ produced, not the filtered projection of it.
 * The board's rows after a milestone filter are empty whenever the operator has
 * filtered to a milestone with no matches, and a failed poll must not replace
 * that legitimate "Nothing matches these filters." view with a first-load error
 * pane. A read that SUCCEEDED with zero rows and then failed has nothing to
 * protect either, and takes the full pane — which `(data?.length ?? 0) > 0`
 * gives for free.
 *
 * `hasContent` is therefore a PARAMETER and not a predicate: what counts as
 * something to lose legitimately differs per surface. Five call sites computing
 * it is not divergence; five call sites each deciding what to render from
 * `isError` is, and that is the defect this module exists to end.
 *
 * The hold across the operator's own retry is `useHeldFailure`'s, whose
 * docblock owns that reasoning and is not repeated here. Subjects are compared
 * with `Object.is`, so callers pass a primitive.
 */

import { useHeldFailure } from "./useHeldFailure";

export interface PolledReadFailure {
  /** Nothing is on screen and the read failed: render the full error pane. */
  blocking: { error: unknown } | null;
  /** Content is on screen and the last read failed: render `<StaleReadNotice>`. */
  stale: { error: unknown } | null;
}

/** Shared, so "no failure" is one value rather than a fresh object per render. */
const NO_FAILURE: PolledReadFailure = { blocking: null, stale: null };

export function usePolledReadFailure<TSubject>(
  query: { isError: boolean; error: unknown; isFetching: boolean },
  subject: TSubject,
  hasContent: boolean,
): PolledReadFailure {
  const held = useHeldFailure(query, subject);
  if (!held) return NO_FAILURE;
  // Mutually exclusive by construction: one held failure, split on one
  // boolean. No call site can produce the fourth combination, which is what
  // makes "the five predicates do not diverge" a property of this module
  // rather than a convention five authors are each asked to remember.
  return hasContent
    ? { blocking: null, stale: { error: held.error } }
    : { blocking: { error: held.error }, stale: null };
}
