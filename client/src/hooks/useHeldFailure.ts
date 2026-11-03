/**
 * The last failure a query reported, held across the query's own re-read.
 *
 * query-core puts a failed query with no data back to `status: "pending"` and
 * clears `error` the moment `refetch()` is called (5.101.2 `query.ts:719-728`),
 * so a branch guarded on `isError` alone unmounts under the operator's own
 * Retry click and takes the reason they were retrying with it. The clear here
 * is therefore keyed on `!isFetching` — a read that *settled* without failing —
 * and never on `!isError`, which is exactly the state a refetch produces.
 *
 * The hold is keyed to the query's subject: whatever its `queryKey` varies on.
 * Switching subjects starts a brand-new observer (`isError` false, `isFetching`
 * true), so the clear is skipped, and an unkeyed hold would render the previous
 * subject's failure under the new subject's name — the defect
 * lg-milestone-that-1434's architecture review found in `GateStudioPanel`.
 * A mismatch *withholds* rather than discards: the failure is still true for
 * the subject it was held for, so coming back to it before anything settled
 * shows it again.
 *
 * Subjects are compared with `Object.is`, so callers pass a primitive — a slug,
 * or the constant a query key is pinned to.
 */

import { useRef } from "react";

export interface HeldFailure<TSubject> {
  subject: TSubject;
  error: unknown;
}

export function useHeldFailure<TSubject>(
  query: { isError: boolean; error: unknown; isFetching: boolean },
  subject: TSubject,
): HeldFailure<TSubject> | null {
  const held = useRef<HeldFailure<TSubject> | null>(null);
  // Unconditional on `isFetching`: query-core keeps `error` when `data` is
  // defined, so a failed poll on a query that has loaded once arrives with the
  // next fetch already in flight.
  if (query.isError) held.current = { subject, error: query.error };
  else if (!query.isFetching) held.current = null;
  const entry = held.current;
  return entry && Object.is(entry.subject, subject) ? entry : null;
}
