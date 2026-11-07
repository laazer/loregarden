/**
 * May a focus move happen here — the whole rule, written once.
 *
 * A surface that focuses its own heading has to answer one question before it
 * does: is this move something the operator asked for, or is it a state
 * transition they never saw? Keying the move off a query payload answers
 * neither, which is the defect lg-milestone-that-2205 is about: react-query
 * refetches on window focus by default, so the operator alt-tabbed back and the
 * panel took the focus they had moved themselves.
 *
 * A move to `target` is permitted when **either**
 *
 *   (a) an unexpired, unconsumed operator intent names this `key`, and the
 *       element that recorded it is still `document.activeElement` — the
 *       operator asked for this surface and has not since moved the keyboard;
 *   (b) `document.activeElement === document.body` — a cold deep link, a
 *       reload, a remount, a successful Retry whose own button unmounted:
 *       nothing of the operator's is taken,
 *
 * and declined otherwise. The two arms are **disjoint**, not sequential: a
 * stamp that fails arm (a) is merely consumed, and arm (b) still answers for
 * itself. Arm (a) is needed because a source list and its detail panel are
 * siblings mounted at the same time, so after a row click the row's `<a>` is
 * still in the tree and still holds focus — a `<body>`-only guard would decline
 * the main path. Arm (b) is needed because it is the cold-mount case.
 *
 * One slot, not a map: two outstanding activations would both mount a panel and
 * whichever read resolved first would take the focus. The second ask replaces
 * the first, so the source the operator is now looking at is the only one that
 * can claim the move.
 */

/** An operator's ask: which surface, from which control, and when. */
type FocusIntent = { key: string; from: HTMLElement | null; at: number };

/**
 * How long an operator's ask stays good for.
 *
 * Deliberately **not** beluga's 800ms, which is sized to a
 * `requestAnimationFrame` retry. This window has to outlast an
 * `api.referenceSource` round trip, and the two costs are not symmetric: being
 * too generous costs nothing, while expiring before the fetch it is waiting for
 * silently declines the main path. The cost of expiry is a *declined* move —
 * focus stays on the row link the operator activated, a visible non-event —
 * never a wrong one.
 */
export const FOCUS_INTENT_MS = 5000;

/** The one outstanding ask, or none. */
let intent: FocusIntent | null = null;

/**
 * Read the clock through here, never `Date.now()` at the call site.
 *
 * `Date.now()` is called at call time rather than captured at module load, so
 * expiry is testable with an injected clock (`jest.spyOn(Date, "now")`).
 */
function now(): number {
  return Date.now();
}

/**
 * Record that the operator asked for `key`, from `from`.
 *
 * `from` is the activation element read off the event's `currentTarget`, not
 * `document.activeElement`, so the stamp does not depend on whether the browser
 * had focused the control by the time the handler ran. A site with no
 * activation element passes `null`; such a stamp can never satisfy arm (a)
 * (`document.activeElement` is never `null`) and that site relies on arm (b).
 */
export function recordFocusIntent(key: string, from: HTMLElement | null): void {
  intent = { key, from, at: now() };
}

/** Whether nothing of the operator's own currently holds focus. */
export function isIdleFocus(): boolean {
  return document.activeElement === document.body;
}

/**
 * Focus `target`, unless the operator is somewhere of their own.
 *
 * Restore, never take: anything other than `<body>` is focus the operator moved
 * while an action was in flight. Shared with `useRestoreFocusOnSettle`, which
 * is where it used to live — one rule, one copy.
 */
export function focusIfIdle(target: HTMLElement | null | undefined): void {
  if (!target) return;
  if (!isIdleFocus()) return;
  target.focus();
}

/**
 * Move focus to `target` for `key` if the rule permits it, and say whether it did.
 *
 * Consumes the outstanding intent on the first attempt whether it is honoured
 * or declined, so a decline is permanent rather than deferred to the next
 * successful read — a background refetch must not inherit an ask the operator
 * already spent.
 */
export function focusOnOperatorIntent(key: string, target: HTMLElement | null): boolean {
  const stamp = intent;
  intent = null;
  if (!target || !target.isConnected) return false;
  const asked =
    stamp !== null &&
    stamp.key === key &&
    // One-sided by design: a clock that stepped backwards reads as negative
    // elapsed and is permitted, because the operator did ask.
    now() - stamp.at <= FOCUS_INTENT_MS &&
    stamp.from !== null &&
    stamp.from.isConnected &&
    // `from === document.body` would make arm (a) fire on exactly the state arm
    // (b) is for, with no operator behind it.
    stamp.from !== document.body &&
    document.activeElement === stamp.from;
  if (!asked && !isIdleFocus()) return false;
  // No `preventScroll`: the heading can be below the fold on open, and focusing
  // something the operator cannot see is worse than scrolling.
  target.focus();
  return true;
}
