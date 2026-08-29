/**
 * The focus behaviour every `role="dialog"` in this app claimed and none had.
 *
 * Twenty-eight components draw a dialog over a backdrop, and eleven of them
 * claim `aria-modal="true"` — a promise to assistive technology that the rest
 * of the document is inert while the dialog is up, which nothing was keeping.
 * Shift+Tab from the first control in a modal landed on the page behind the
 * overlay, which is still there, still focusable, and no longer reachable by
 * mouse. Closing the dialog then dropped focus on `<body>`, so a keyboard
 * operator restarted from the top of the document every time.
 *
 * Three obligations, which is the whole of what a trap owes:
 *
 * 1. **Take focus on open**, unless something inside already has it — a panel
 *    with an `autoFocus` field has already chosen better than a generic "first
 *    focusable" rule can.
 * 2. **Keep it**, by wrapping Tab and Shift+Tab at the edges.
 * 3. **Give it back on close**, to whatever had it before, so the button that
 *    opened the dialog is where the operator resumes.
 *
 * ## Escape is deliberately not here
 *
 * Seven of these dialogs already close on Escape, with conditions of their own
 * — a run log only while it is open, a details modal only when it was given an
 * `onClose`. Folding that in would either double-fire against the handlers that
 * exist or quietly override their conditions, and the twenty-one that do not
 * close on Escape are a separate decision from this one. This hook moves focus
 * and nothing else.
 *
 * ## Why a callback ref rather than `useRef`
 *
 * Most of these dialogs render nothing until they have something to show —
 * `if (!view) return null` — so the element the trap needs does not exist when
 * the component first mounts, and a `useRef` + `useEffect([])` pair would look
 * at `null`, return, and never run again. The ref is a state setter, so the
 * effect is keyed on the node itself: it runs when the node attaches and tears
 * down when it goes.
 *
 * ## Why the opener is read during render
 *
 * React applies `autoFocus` while committing the host node — a *child* of the
 * component holding this hook — so it runs before any effect here, layout
 * effects included. An effect that read `document.activeElement` on the way in
 * would find the dialog's own search field, remember *that* as the opener, and
 * on close try to restore focus to a node it had just unmounted: focus lands on
 * `<body>`, which is the bug this hook exists to fix. The read below happens on
 * renders where no panel is mounted — while the trigger still holds focus — and
 * stops the moment one is.
 *
 * ## Why a stack
 *
 * Dialogs nest here: a pane's settings editor can open the primitive picker,
 * and a grid of panes can have two pickers mounted at once. Every mounted trap
 * would otherwise listen on `document` and fight over the same Tab press. Only
 * the most recently mounted one acts; the rest wait their turn.
 */

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * What the platform will hand a Tab press to, before per-element filtering.
 *
 * `[tabindex]` is matched broadly and narrowed below by the element's resolved
 * `tabIndex`, because `tabindex="-1"` is programmatically focusable but not
 * *tabbable*, and this is a tab order.
 */
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "area[href]",
  "button",
  "input",
  "select",
  "textarea",
  "iframe",
  "audio[controls]",
  "video[controls]",
  // Plain, not `:not([contenteditable="false"])`: the `tabIndex >= 0` filter
  // below already excludes a non-editable one, so the narrowing was a
  // refinement no test could tell apart from its absence.
  "[contenteditable]",
  "[tabindex]",
].join(",");

/**
 * The mounted traps, innermost last.
 *
 * Module-level because the contest is between separate React trees — a portalled
 * picker over a panel is not the picker's ancestor — so there is no component
 * either side could hang shared state on.
 */
const trapStack: HTMLElement[] = [];

/**
 * Whether the platform will refuse `element` a tab stop for being turned off.
 *
 * `disabled` is read as an attribute rather than a property so one filter
 * covers every tag — `HTMLInputElement` has the property and `<a>` does not —
 * and `fieldset[disabled]` because disabling a fieldset disables its controls
 * without marking any of them.
 */
function isDisabled(element: HTMLElement): boolean {
  return (
    element.hasAttribute("disabled") ||
    element.getAttribute("aria-disabled") === "true" ||
    element.closest("fieldset[disabled]") !== null
  );
}

/**
 * Radios Tab as a group: the browser stops on the checked one, or on the first
 * of a group with none checked, and skips the rest. Named groups only — an
 * unnamed radio is a group of one and is reached like any other control.
 *
 * It matters here because the wrap is computed from the *edges* of this list. A
 * `role="radiogroup"` of eight states — `UpdateStateModal` has one — would
 * otherwise put seven phantom stops between the real ones, and Tab would appear
 * to stick.
 */
function isSkippedRadio(element: HTMLElement, root: HTMLElement): boolean {
  if (!(element instanceof HTMLInputElement) || element.type !== "radio") return false;
  if (element.name === "" || element.checked) return false;
  const group = Array.from(
    root.querySelectorAll<HTMLInputElement>(
      `input[type="radio"][name="${CSS.escape(element.name)}"]`,
    ),
  );
  return group.some((radio) => radio.checked) || group[0] !== element;
}

/** The tabbable descendants of `root`, in document order. */
export function tabbableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) =>
      element.tabIndex >= 0 &&
      !isDisabled(element) &&
      element.getAttribute("aria-hidden") !== "true" &&
      // Not visibility: jsdom has no layout engine and reports every element at
      // zero, so a size test would make this hook untestable and — worse —
      // silently empty in the suite while working in a browser. `[inert]` is
      // the declarative form and does work here.
      element.closest("[hidden],[inert]") === null &&
      !isSkippedRadio(element, root),
  );
}

/**
 * Trap keyboard focus inside the returned element for as long as it is mounted.
 *
 * Attach the ref to the element carrying `role="dialog"`, not the overlay: the
 * overlay is a sibling backdrop, and trapping on it would make everything the
 * dialog contains unreachable.
 */
export function useDialogFocusTrap<T extends HTMLElement>(): (node: T | null) => void {
  const [container, setContainer] = useState<T | null>(null);

  // Read during render, not in the effect: see the note above.
  const openerRef = useRef<Element | null>(null);
  if (container === null) openerRef.current = document.activeElement;
  // Stable, so React attaches and detaches the node rather than tearing the
  // trap down and rebuilding it on every render of the dialog.
  const containerRef = useCallback((node: T | null) => setContainer(node), []);

  useEffect(() => {
    if (container === null) return;

    // Re-checked on the way out rather than trusted: the opener can be
    // unmounted by the same interaction that closed the dialog, and focusing a
    // detached node silently sends focus to `<body>`.
    const previouslyFocused = openerRef.current;

    // A dialog with nothing tabbable in it still has to hold focus, or Tab
    // walks straight out into the page the overlay is covering. `-1` is
    // programmatic-only, so this adds a focus target without adding a tab stop.
    if (!container.hasAttribute("tabindex")) container.setAttribute("tabindex", "-1");

    trapStack.push(container);

    if (!container.contains(document.activeElement)) {
      const [first] = tabbableWithin(container);
      (first ?? container).focus();
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Tab" || event.defaultPrevented) return;
      // Only the innermost dialog steers. A trap further down the stack is
      // behind an overlay of its own and has no business moving focus.
      if (trapStack[trapStack.length - 1] !== container) return;

      const tabbable = tabbableWithin(container);
      const active = document.activeElement;
      const outside = !container.contains(active);

      if (tabbable.length === 0) {
        event.preventDefault();
        container.focus();
        return;
      }

      const first = tabbable[0];
      const last = tabbable[tabbable.length - 1];

      if (event.shiftKey) {
        // `outside` covers the case the trap exists for: focus that reached the
        // page behind the overlay by any route — a click through a gap, a
        // programmatic focus, the container itself holding focus — is pulled
        // back to the far edge rather than left there.
        if (outside || active === first || active === container) {
          event.preventDefault();
          last.focus();
        }
        return;
      }

      if (outside || active === last || active === container) {
        event.preventDefault();
        first.focus();
      }
    }

    // Bubble phase, deliberately. A capturing listener would reach Tab before
    // the focused control does, and an editor that owns Tab — Monaco, which the
    // `edit` chat primitive mounts and `PrimitiveSlot` can hoist into a modal
    // overlay — would lose it to focus-cycling mid-keystroke. Listening last
    // means `defaultPrevented` above is a real answer: something inside the
    // dialog has already claimed this press.
    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const index = trapStack.lastIndexOf(container);
      if (index !== -1) trapStack.splice(index, 1);

      if (previouslyFocused instanceof HTMLElement && previouslyFocused.isConnected) {
        previouslyFocused.focus();
      }
    };
  }, [container]);

  return containerRef;
}
