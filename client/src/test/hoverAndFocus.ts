/**
 * Pointer-over and focus-within, as React actually sees them.
 *
 * Two traps make the obvious spelling of these a silent no-op, and both would
 * turn a pause-on-interaction assertion into a test that passes because the
 * handler never ran:
 *
 * 1. React synthesises `onPointerEnter`/`onPointerLeave` from `pointerover`
 *    and `pointerout` (its EnterLeaveEventPlugin). `fireEvent.pointerEnter`
 *    dispatches a non-bubbling `pointerenter`, which React listens to nowhere,
 *    so the handler is never called and nothing pauses.
 * 2. jsdom implements no `PointerEvent`, and RTL falls back to a bare `Event`
 *    when the constructor is missing — which drops `relatedTarget`, the field
 *    React reads to decide whether the pointer crossed the element's boundary.
 *
 * `relatedTarget` is explicit on both sides for the same reason: entering from
 * `null` (outside the document) and leaving to `document.body` are the two
 * crossings that make React dispatch enter and leave respectively.
 */

import { fireEvent } from "@testing-library/react";

/** The pointer arrives over `element` from outside the document. */
export function pointerIn(element: Element): void {
  fireEvent.pointerOver(element, { relatedTarget: null });
}

/** The pointer leaves `element` for `document.body`, outside its subtree. */
export function pointerOut(element: Element): void {
  fireEvent.pointerOut(element, { relatedTarget: document.body });
}

/**
 * Focus arrives on `element`.
 *
 * `focusin`/`focusout` rather than `focus`/`blur`: React 17+ attaches its
 * `onFocus`/`onBlur` to the bubbling pair, which is what lets a handler on a
 * toast's root see focus entering its copy or close button.
 */
export function focusIn(element: Element): void {
  fireEvent.focusIn(element);
}

/** Focus leaves `element` for `document.body`, outside its subtree. */
export function focusOut(element: Element): void {
  fireEvent.focusOut(element, { relatedTarget: document.body });
}
