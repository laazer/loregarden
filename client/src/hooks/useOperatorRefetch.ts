import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Turn a polled query's `refetch` into an operator-initiated action that
 * carries its own pending flag.
 *
 * A query that polls has exactly one `isFetching`, and it cannot say who asked.
 * So a control bound to it goes disabled and relabels itself "…ing" once per
 * `refetchInterval` with no operator action — and query-core gates the tick on
 * `focusManager.isFocused()`, so that happens precisely while the operator is
 * looking at the one control the pane offers. The click the native `disabled`
 * attribute eats is then a double-click guard firing for a read nobody asked
 * for, the keyboard operator's Enter does nothing, `disabled` drops the button
 * out of the tab order mid-traverse, and the label asserts an operator retry is
 * in flight when what is in flight is a poll.
 *
 * `pending` is the operator's own re-read, and nothing else: it is latched by
 * `start()` and released only in the settle of the promise that call's own
 * `refetch()` returned. The latch is deliberately the promise's lifetime and
 * not `armed && query.isFetching`: between the activation and the next render
 * `isFetching` may already be true from a poll, and when a retired fetch
 * resolves `isFetching` can blip false and re-enable the control mid-retry.
 * Keyed to the promise, a read the operator did not start can neither arm nor
 * disarm the flag.
 *
 * The returned flag is render-visible, which is the whole point — it drives the
 * control's `disabled` attribute, its label, and `useRestoreFocusOnSettle`'s
 * `pending` argument, so the disabled window and the focus restoration cannot
 * drift apart.
 *
 * @param refetch the query's own `refetch`, whose returned promise settles when
 *   that fetch does.
 */
export function useOperatorRefetch(refetch: () => Promise<unknown>) {
  const [pending, setPending] = useState(false);
  // A retry that settles after this pane unmounts (a ticket switch, a tab
  // change) must not set state on a gone component.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const start = useCallback(() => {
    setPending(true);
    const settle = () => {
      if (mounted.current) setPending(false);
    };
    // `refetch()` rejects only where the query is configured to throw; settling
    // on both arms is what makes a failed retry release the control rather than
    // strand it disabled. The pane keeps showing the failure either way.
    void refetch().then(settle, settle);
  }, [refetch]);

  return { pending, start };
}
