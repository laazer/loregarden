import { renderHook } from "@testing-library/react";
import { StrictMode, createRef, type RefObject } from "react";

import { focusOnOperatorIntent, recordFocusIntent } from "../../lib/focusIntent";
import { useFocusOnOperatorIntent } from "../useFocusOnOperatorIntent";

/**
 * The one-shot latch under the conditions it was designed for — the cases
 * `useFocusOnOperatorIntent.test.tsx` leaves open, for lg-milestone-that-2205.
 *
 * AC-S3 gives the latch two reasons to be a `string | null` ref rather than a
 * boolean: it must survive **StrictMode's double-invoke**, and a new `key` must
 * be the one thing that re-arms it. Neither claim is reachable from a suite
 * that never renders in StrictMode, and the first one is the whole justification
 * for the shape. React's dev-mode StrictMode runs an effect
 * setup → cleanup → setup on mount while keeping the fiber, so the ref holds
 * across the double-invoke and the second setup finds the latch spent. A hook
 * that latched in state, or in a variable scoped to the effect, would fire
 * twice here and once everywhere else.
 *
 * The other thing this suite settles is what the latch does across a real
 * unmount, which is a different question from a re-render and the one the app
 * actually asks: `ReferenceLibraryTab.tsx:248` passes `key={selectedId}`, so a
 * new source does not re-render the panel, it **replaces** it. A ref does not
 * survive that, so the fresh instance arrives unspent — which is correct, and
 * is why the app's main path does not depend on the `key`-change branch at all.
 * Pinning it here says so, rather than leaving the next reader to assume the
 * re-render branch is what carries the app.
 *
 * `focus()` is inert in every case, for the reason the sibling suite's
 * docstring gives: a calling-through spy moves `document.activeElement` onto
 * the target, after which the *rule* declines every later attempt and no case
 * below is about the hook any more.
 */

function mountTarget(): { element: HTMLElement; ref: RefObject<HTMLElement | null>; focus: jest.SpyInstance } {
  const element = document.createElement("div");
  element.tabIndex = -1;
  document.body.append(element);
  const ref = createRef<HTMLElement>() as RefObject<HTMLElement | null>;
  ref.current = element;
  return { element, ref, focus: jest.spyOn(element, "focus").mockImplementation(() => {}) };
}

function renderStrict(target: RefObject<HTMLElement | null>, initial: { key: string; ready: boolean }) {
  return renderHook(({ key, ready }: { key: string; ready: boolean }) => useFocusOnOperatorIntent(key, ready, target), {
    initialProps: initial,
    wrapper: StrictMode,
  });
}

afterEach(() => {
  // The rule holds one module-level slot; a stamp left behind would make the
  // next case's arm (a) fire for a key it never armed.
  focusOnOperatorIntent("drain", null);
  document.body.innerHTML = "";
  jest.restoreAllMocks();
});

describe("StrictMode's double-invoke", () => {
  it("fires exactly once when the hook mounts already ready", () => {
    const { focus } = mountTargetAndRender({ key: "abc", ready: true });
    expect(focus).toHaveBeenCalledTimes(1);
  });

  it("fires exactly once when `ready` turns true under a double-invoked effect", () => {
    const { ref, focus } = mountTarget();
    const view = renderStrict(ref, { key: "abc", ready: false });
    expect(focus).not.toHaveBeenCalled();

    view.rerender({ key: "abc", ready: true });

    expect(focus).toHaveBeenCalledTimes(1);
  });

  it("consumes the operator's stamp once, so the double-invoke's second pass has nothing to honour", () => {
    // The combination that matters in the app: the latch and the stamp are two
    // separate one-shots, and both are spent by the first pass. If the latch
    // failed to hold, the second pass would read an already-consumed stamp and
    // decline — so a missing latch is *invisible* here unless focus is inert and
    // the document is idle, which is exactly how this case is set up.
    const row = document.createElement("a");
    row.setAttribute("href", "#");
    document.body.append(row);
    const { ref, focus } = mountTarget();
    row.focus();
    recordFocusIntent("abc", row);

    renderStrict(ref, { key: "abc", ready: true });

    expect(focus).toHaveBeenCalledTimes(1);
  });

  it("re-arms on a new key under StrictMode too", () => {
    const { ref, focus } = mountTarget();
    const view = renderStrict(ref, { key: "abc", ready: true });
    expect(focus).toHaveBeenCalledTimes(1);

    view.rerender({ key: "xyz", ready: true });

    expect(focus).toHaveBeenCalledTimes(2);
  });

  function mountTargetAndRender(initial: { key: string; ready: boolean }) {
    const mounted = mountTarget();
    renderStrict(mounted.ref, initial);
    return mounted;
  }
});

describe("the latch across a real unmount, which is how the app changes source", () => {
  it("arrives unspent on a fresh mount for the same key", () => {
    // `key={selectedId}` means the panel is replaced rather than re-rendered,
    // so the ref is new and the new instance owes the operator a focus move.
    // The re-render branch (`key` changing on a live instance) is a contract of
    // the hook, not the path the app takes.
    const first = mountTarget();
    const view = renderStrict(first.ref, { key: "abc", ready: true });
    expect(first.focus).toHaveBeenCalledTimes(1);
    view.unmount();

    const second = mountTarget();
    renderStrict(second.ref, { key: "abc", ready: true });

    expect(second.focus).toHaveBeenCalledTimes(1);
  });

  it("re-arms when the key returns to one it has already seen", () => {
    // The latch stores the last key, not every key it has fired for. An
    // operator going source A → B → A on one mounted instance is asking three
    // times, and the third ask is as real as the first.
    const { ref, focus } = mountTarget();
    const view = renderStrict(ref, { key: "abc", ready: true });
    view.rerender({ key: "xyz", ready: true });
    expect(focus).toHaveBeenCalledTimes(2);

    view.rerender({ key: "abc", ready: true });

    expect(focus).toHaveBeenCalledTimes(3);
  });
});

describe("a declined move is never retried", () => {
  it("stays spent after a declined attempt, so a later re-entry does not try again", () => {
    // The effect body's order, from AC-S3: set the latch, *then* call the rule.
    // The latch therefore records "this key has had its one attempt", not "this
    // key got its move" — and that is the difference the case below measures.
    //
    // Why it matters, in the panel's own terms: a failed refetch over good data
    // drops `isSuccess` and its retry restores it, so the effect genuinely
    // re-enters with the same `sourceId`. An implementation that latched only on
    // a move that *succeeded* would re-attempt there, and the second attempt
    // runs at whatever `document.activeElement` happens to be — `<body>`, after
    // a control unmounted — and takes focus on a read nobody asked for. That is
    // the ticket's defect reintroduced through the latch instead of the deps.
    //
    // The first attempt is declined here by giving it nothing to focus: the ref
    // is empty, which is the cheapest decline there is and does not depend on
    // the rule's arms.
    const empty = createRef<HTMLElement>() as RefObject<HTMLElement | null>;
    const view = renderHook(
      ({ key, ready, target }: { key: string; ready: boolean; target: RefObject<HTMLElement | null> }) =>
        useFocusOnOperatorIntent(key, ready, target),
      { initialProps: { key: "abc", ready: true, target: empty } },
    );

    const late = mountTarget();
    empty.current = late.element;
    // `ready` dipping and returning is what makes the effect re-enter at all;
    // a re-render with `[key, ready]` unchanged never runs it a second time.
    view.rerender({ key: "abc", ready: false, target: empty });
    view.rerender({ key: "abc", ready: true, target: empty });

    expect(late.focus).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(document.body);
  });
});
