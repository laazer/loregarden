/**
 * Escape closes the dialog — the half of the keyboard contract the focus trap
 * deliberately left open.
 *
 * `useDialogFocusTrap` says so in its own header: it holds focus inside the
 * dialog and stops there, because folding Escape in would have overridden the
 * conditions the handful of dialogs that already had it were using. What that
 * left behind is worse than it sounds. Nineteen of twenty-four modals here
 * close on a backdrop click and on nothing else, so once the trap is in place a
 * keyboard operator is not merely inconvenienced — they are held inside a
 * dialog whose only exit is a mouse.
 *
 * The hand-rolled version this replaces was a `useEffect` per component
 * listening on `document`, which is also why two stacked dialogs both closed on
 * one Escape: nothing decided whose press it was.
 *
 * Pass `null` to stand down — a dialog that is closed, or one given no
 * `onClose`, registers nothing rather than registering a handler that does
 * nothing. The distinction matters for the stack below: a no-op that stayed
 * registered would swallow Escape from the dialog underneath it.
 */

import { useEffect, useRef } from "react";

/**
 * The mounted dismissers, innermost last.
 *
 * Module-level for the same reason the focus trap's stack is: the contest is
 * between separate React trees — a portalled picker over a panel is not the
 * picker's ancestor — so neither side has a component to hang shared state on.
 */
const dismissStack: symbol[] = [];

/** Whether `token` is the innermost mounted dialog, and so owns this press. */
function isInnermost(token: symbol): boolean {
  return dismissStack[dismissStack.length - 1] === token;
}

/**
 * Close the calling dialog when Escape is pressed and it is the innermost one.
 *
 * @param onDismiss What Escape should do, or `null`/`undefined` to register
 *   nothing — for a dialog that is currently closed, or has no dismiss action.
 */
export function useDialogDismiss(onDismiss: (() => void) | null | undefined): void {
  // The callback identity changes on nearly every render of a dialog that
  // renders its own state. Reading it through a ref keeps the listener — and
  // the stack entry beneath it — stable across those renders, so a re-render
  // mid-interaction cannot reorder who owns the next press.
  const handlerRef = useRef(onDismiss);
  handlerRef.current = onDismiss;

  const active = Boolean(onDismiss);

  useEffect(() => {
    if (!active) return;
    const token = Symbol("dialog-dismiss");
    dismissStack.push(token);

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (!isInnermost(token)) return;
      const handler = handlerRef.current;
      if (!handler) return;
      // Claimed, so a dialog further down the stack does not also act on it if
      // it is listening through some other route.
      event.preventDefault();
      handler();
    }

    // Bubble phase, matching the focus trap: a capturing listener would take
    // Escape before the focused control does, and a control that owns it — a
    // combobox closing its own popup, Monaco dismissing a suggestion widget —
    // would have its press stolen. Listening last makes `defaultPrevented`
    // above a real answer.
    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const index = dismissStack.lastIndexOf(token);
      if (index !== -1) dismissStack.splice(index, 1);
    };
  }, [active]);
}
