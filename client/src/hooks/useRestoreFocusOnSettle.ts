import { useCallback, useEffect, useRef } from "react";

/** A control that can be disabled while its own action is in flight. */
type ActivatedControl = HTMLElement & { disabled?: boolean };

/**
 * Keep focus on the control the operator activated across a pending action.
 *
 * A control disabled for the duration of its own mutation is blurred by the
 * browser when it goes disabled, and nothing puts focus back when it
 * re-enables — so the answer the operator has to read (a refusal, a parked
 * checkout) arrives with `document.activeElement` on `<body>`, and the next Tab
 * starts from the top of the dialog. In a 250-branch list that is the whole
 * list again before the operator can retry.
 *
 * Remember the element on activation, and focus it again the moment the action
 * settles and it is operable. This restores focus rather than taking it: the
 * refusal block stays `role="status"` / `aria-live="polite"` and is announced
 * without ever being focused. Keeping the `disabled` attribute is what makes a
 * second activation mid-flight impossible, so the idempotence does not move.
 *
 * Restoring is also why focus is only handed back when `document.activeElement`
 * is `<body>`. That is the state the browser leaves behind when it blurs the
 * control it disabled, and the only state in which nothing of the operator's
 * own is being taken. Focus they moved while the action was in flight — to a
 * sibling control, a link, another dialog — is theirs, and a settle that
 * yanked it back would be taking focus rather than restoring it. The guard is
 * unconditional, not an option: a caller that wanted to steal focus would not
 * be using this hook. It is also what makes the arming itself the trigger
 * rather than the pending transition — a read nobody asked for (a background
 * poll on a freshly loaded page) settles with `<body>` active too, and is only
 * declined because `activated.current` was cleared by the settle before it.
 *
 * @param pending whether the action is in flight — must be the same flag that
 *   disables the control, so the DOM is already operable when the effect runs.
 */
export function useRestoreFocusOnSettle(pending: boolean) {
  const activated = useRef<ActivatedControl | null>(null);

  const rememberActivated = useCallback((node: ActivatedControl | null) => {
    activated.current = node;
  }, []);

  useEffect(() => {
    if (pending) return;
    const node = activated.current;
    activated.current = null;
    // Still disabled (nothing left to act on) or gone from the tree: there is
    // no control to hand focus back to, and focusing a detached node moves
    // focus to the document rather than restoring it.
    if (!node || !node.isConnected || node.disabled) return;
    // Restore, never take: anything other than <body> is focus the operator
    // moved themselves while the action was in flight.
    if (document.activeElement !== document.body) return;
    node.focus();
  }, [pending]);

  return rememberActivated;
}
