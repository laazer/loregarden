import { renderHook } from "@testing-library/react";
import { createRef, type RefObject } from "react";

import { useFocusOnOperatorIntent } from "../useFocusOnOperatorIntent";

/**
 * The one-shot latch, alone — a plain `div` ref, no panel and no query client.
 * Covers AC-S3 of lg-milestone-that-2205.
 *
 * The hook is `(key, ready, target)` with effect deps exactly `[key, ready]`,
 * and that dependency list is the fix: `ready` is `source.isSuccess`, a
 * primitive from react-query's own status which does **not** change across a
 * background refetch (`fetchStatus` is what changes). A byte-identical refetch
 * cannot flip a boolean that is already true, so the effect does not re-run at
 * all — which is why no query payload and no object derived from one appears
 * here. The effect the fix replaces had `[source.data]`, the payload's object
 * identity, and that is the defect.
 *
 * The latch is a `string | null` ref keyed by `key`, not a boolean, so it
 * survives StrictMode's double-invoke and a new `key` is the one thing that
 * re-arms it. Effect body, in order: return without touching the latch when
 * `!ready`; return when the latch already holds `key`; set the latch to `key`;
 * call `focusOnOperatorIntent(key, target.current)`.
 *
 * These tests run the real `lib/focusIntent` rather than mocking it — it is part
 * of the unit under test, and mocking it would turn every case below into an
 * assertion about a call rather than about focus. Every case keeps
 * `document.activeElement` on `<body>`, so the rule's arm (b) permits each move
 * the hook asks for and a missing focus call is the hook's own decision.
 *
 * Keeping `<body>` active is what `mockImplementation(() => {})` below is for,
 * and it is load-bearing rather than tidiness. `jest.spyOn` **calls through** by
 * default, so a bare spy lets the first fire really focus the target; every
 * later attempt then meets `document.activeElement === target`, which satisfies
 * neither arm, and the rule declines for its own reasons before the latch is
 * ever consulted. That makes the latch untestable here: a hook with no latch at
 * all would pass "does not fire again on the same key" and "stays spent across
 * a `ready` dip", because the *rule* refuses the second move. An inert
 * `focus()` is the only way these cases are about the hook.
 */

function mountTarget(): { ref: RefObject<HTMLElement>; focus: jest.SpyInstance } {
  const element = document.createElement("div");
  element.tabIndex = -1;
  document.body.append(element);
  const ref = createRef<HTMLElement>() as RefObject<HTMLElement>;
  (ref as { current: HTMLElement | null }).current = element;
  // Inert, not merely observed: see the docstring. A calling-through spy makes
  // the latch cases pass for the rule's reasons rather than the hook's.
  return { ref, focus: jest.spyOn(element, "focus").mockImplementation(() => {}) };
}

function render(target: RefObject<HTMLElement>, initial: { key: string; ready: boolean }) {
  return renderHook(({ key, ready }: { key: string; ready: boolean }) => useFocusOnOperatorIntent(key, ready, target), {
    initialProps: initial,
  });
}

afterEach(() => {
  document.body.innerHTML = "";
  jest.restoreAllMocks();
});

it("fires once when `ready` turns true", () => {
  const { ref, focus } = mountTarget();
  const view = render(ref, { key: "abc", ready: false });
  expect(focus).not.toHaveBeenCalled();

  view.rerender({ key: "abc", ready: true });

  expect(focus).toHaveBeenCalledTimes(1);
});

it("does not fire again on a re-render with the same key", () => {
  const { ref, focus } = mountTarget();
  const view = render(ref, { key: "abc", ready: true });
  expect(focus).toHaveBeenCalledTimes(1);

  view.rerender({ key: "abc", ready: true });
  view.rerender({ key: "abc", ready: true });

  // This is the shape a successful background refetch takes: the same key, and
  // `isSuccess` already true. The payload changed; nothing the hook reads did.
  expect(focus).toHaveBeenCalledTimes(1);
});

it("re-arms on a new key", () => {
  const { ref, focus } = mountTarget();
  const view = render(ref, { key: "abc", ready: true });
  expect(focus).toHaveBeenCalledTimes(1);

  view.rerender({ key: "xyz", ready: true });

  expect(focus).toHaveBeenCalledTimes(2);
});

it("does not consume the latch while `!ready`, so an error→Retry→data load still focuses", () => {
  // The cold load that errored first: several renders pass through
  // `ready: false` (pending, then error, then pending again on Retry) before
  // the first success. A latch set on any of those would leave the operator
  // whose retry just worked on `<body>`.
  const { ref, focus } = mountTarget();
  const view = render(ref, { key: "abc", ready: false });

  view.rerender({ key: "abc", ready: false });
  view.rerender({ key: "abc", ready: false });
  expect(focus).not.toHaveBeenCalled();

  view.rerender({ key: "abc", ready: true });

  expect(focus).toHaveBeenCalledTimes(1);
});

it("stays spent for the same key once it has fired, even across a `ready` dip", () => {
  // A failed refetch over good data drops `isSuccess`, and its retry restores
  // it. The operator asked for this source once; the second success is not a
  // second activation.
  const { ref, focus } = mountTarget();
  const view = render(ref, { key: "abc", ready: true });
  expect(focus).toHaveBeenCalledTimes(1);

  view.rerender({ key: "abc", ready: false });
  view.rerender({ key: "abc", ready: true });

  expect(focus).toHaveBeenCalledTimes(1);
});

it("does not fire for a new target ref while the key is unchanged", () => {
  // The deps are `[key, ready]`, so a ref whose identity changed — the kind of
  // churn a parent re-render produces — is not an edge the hook reacts to.
  const first = mountTarget();
  const view = renderHook(
    ({ key, ready, target }: { key: string; ready: boolean; target: RefObject<HTMLElement> }) =>
      useFocusOnOperatorIntent(key, ready, target),
    { initialProps: { key: "abc", ready: true, target: first.ref } },
  );
  expect(first.focus).toHaveBeenCalledTimes(1);

  const second = mountTarget();
  view.rerender({ key: "abc", ready: true, target: second.ref });

  expect(second.focus).not.toHaveBeenCalled();
  expect(first.focus).toHaveBeenCalledTimes(1);
});

it("declines silently when the target has not mounted", () => {
  const empty = createRef<HTMLElement>() as RefObject<HTMLElement>;
  expect(() => render(empty, { key: "abc", ready: true })).not.toThrow();
  expect(document.activeElement).toBe(document.body);
});
