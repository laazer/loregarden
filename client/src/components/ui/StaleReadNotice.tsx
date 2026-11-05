/**
 * The one inline report of a failed read on a surface that still has content.
 *
 * `queryClient.ts:28-31` suppresses the query toast whenever
 * `query.state.data !== undefined`, so on a polling surface with rows already
 * on screen THIS IS THE ONLY REPORT the operator gets. Which is why the
 * properties below are hardcoded rather than offered as props:
 *
 *  - `role="status"`, never `alert`. The MCP activity feed polls at 5s; an
 *    assertive region there interrupts a screen-reader user every five seconds,
 *    denying them the surface rather than warning them. The first-load panes
 *    keep `role="alert"` — there the failure IS the content.
 *  - No timestamp, attempt counter or "retrying in Ns". Two identical
 *    consecutive failures render identical text, so React writes no DOM
 *    mutation and the live region announces once per episode rather than 720
 *    times an hour.
 *  - No control and no tab stop. The next read is already scheduled on the
 *    surface's own timer, so a Retry here would duplicate a timer the operator
 *    cannot see and add a tab stop that appears and disappears on one, changing
 *    a keyboard operator's focus order every 30 seconds.
 *  - The reason is unbounded and wrapping: no clamp here and none in
 *    `StaleReadNotice.css`. A multi-KB proxy-page reason pushes content down
 *    rather than being truncated, because clipping the only report of a failure
 *    is worse than the scroll.
 *
 * The lead sentence and the fallback are props, because the copy names the
 * surface's own subject. Everything else is the same on all five call sites.
 */

import { describeError, endSentence } from "../../state/toastStore";

import "./StaleReadNotice.css";

export function StaleReadNotice({
  lead,
  error,
  fallback,
  testId,
}: {
  /** The surface's own sentence: "These work items may be out of date — …". */
  lead: string;
  /** Whatever the failed read threw. */
  error: unknown;
  /** What to name when the throw carries no message: "GitHub did not answer." */
  fallback: string;
  /** Per-surface, so a page carrying two notices can still assert "exactly one". */
  testId?: string;
}) {
  return (
    <p role="status" className="stale-read-notice" data-testid={testId}>
      {lead} {endSentence(describeError(error, fallback))}
    </p>
  );
}
