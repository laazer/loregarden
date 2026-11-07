import { useCallback, useEffect, useRef } from "react";

import { focusIfIdle } from "../lib/focusIntent";

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
 * The idle guard itself is `focusIfIdle` in `lib/focusIntent`, shared with the
 * surfaces that focus a heading on open rather than a control on settle — one
 * rule, one copy, which is what lg-milestone-that-2205 is about.
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
 * A surface that is *replaced* by its own settle is the one case restoring
 * cannot answer: the armed control is detached, so there is nothing to hand
 * focus back to, and the operator whose retry just worked is left on `<body>`
 * paying the same traverse this hook was written about. `landing` is the
 * caller's answer to "where instead" — a getter, not an element or a ref,
 * because the replacement surface does not exist at activation time and can
 * only be read inside the effect, after it has committed. `null` is a legal
 * "nothing focusable mounted", and no landing at all leaves the hook exactly
 * as its three other callers use it.
 *
 * @param pending whether the action is in flight — must be the same flag that
 *   disables the control, so the DOM is already operable when the effect runs.
 * @param landing read when, and only when, the armed control has left the tree
 *   by the time the action settles; the element focus lands on instead.
 */
export function useRestoreFocusOnSettle(pending: boolean, landing?: () => HTMLElement | null) {
  const activated = useRef<ActivatedControl | null>(null);

  const rememberActivated = useCallback((node: ActivatedControl | null) => {
    activated.current = node;
  }, []);

  useEffect(() => {
    if (pending) return;
    const node = activated.current;
    activated.current = null;
    // Three different states, and the order between the last two is
    // load-bearing. Nothing was armed: this settle is not an operator's — a
    // background poll shares the same flag on some callers — so neither a
    // restore nor a landing is owed.
    if (!node) return;
    // Gone from the tree: the surface that held the control was replaced, and
    // focusing a detached node would move focus to the document rather than
    // restoring it. This, and only this, is what the landing answers. It is
    // tested *before* `disabled` because React unmounts a control bound
    // `disabled={pending}` with that attribute still true, so the detached
    // node is both — and a `disabled`-first guard makes the landing dead code.
    if (!node.isConnected) {
      focusIfIdle(landing?.());
      return;
    }
    // Still in the tree and still releasing: it remains the right target, so
    // move nothing rather than take focus off the surface the operator is on.
    if (node.disabled) return;
    focusIfIdle(node);
  }, [pending, landing]);

  return rememberActivated;
}
