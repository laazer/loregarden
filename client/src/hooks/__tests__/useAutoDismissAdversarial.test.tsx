/**
 * S1 and S4.4 under the mutations the per-criterion cases leave open.
 *
 * `hooks/__tests__/useAutoDismiss.test.tsx` pins the arithmetic the spec
 * states. Each case here is a wrong implementation that satisfies all of it:
 *
 * - A deadline that depends on `onDismiss`'s identity passes S4.3, because
 *   that case re-renders with the SAME callback. `ToastItem` has to pass a
 *   closure over the toast's id, so in the real host the identity changes on
 *   every render and the deadline would restart forever — the 9-second toast
 *   that never leaves, which is the defect this ticket is about wearing the
 *   opposite sign.
 * - Resetting the interaction state when the revision changes passes S4.4,
 *   because that case re-raises an UN-hovered toast. A repeat failure arriving
 *   while the operator's pointer is on the toast is the common case (a failing
 *   poll re-reports every few seconds), and resetting would resume the clock
 *   under the pointer — which is S4.2's whole reason for keeping the node.
 * - Bookkeeping that trusts the events to arrive in pairs passes everything,
 *   because every stated case fires them in pairs.
 */

import { act, render } from "@testing-library/react";

import { focusIn, focusOut, pointerIn, pointerOut } from "../../test/hoverAndFocus";
import { installPointerEvents } from "../../test/pointerEvents";
import { useAutoDismiss } from "../useAutoDismiss";

installPointerEvents();

function Harness({
  duration,
  revision = 1,
  onDismiss,
}: {
  duration: number;
  revision?: number;
  onDismiss: () => void;
}) {
  const handlers = useAutoDismiss({ duration, revision, onDismiss });
  return (
    <div data-testid="target" {...handlers}>
      <button type="button" data-testid="first">
        copy
      </button>
      <button type="button" data-testid="second">
        close
      </button>
    </div>
  );
}

/** Advance the fake clock to an absolute time. */
function clock() {
  let now = 0;
  return (to: number) => {
    act(() => {
      jest.advanceTimersByTime(to - now);
    });
    now = to;
  };
}

beforeEach(() => {
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

it("a fresh onDismiss identity on every render does not restart the deadline", () => {
  // What `ToastItem` passes: a closure over the toast's id, rebuilt each render.
  // A hook that lists it as a dependency re-arms its timer on every re-render,
  // and an app that re-renders more often than once per `duration` would hold
  // the toast forever. S4.3 cannot see this: it re-renders with one function.
  const calls: string[] = [];
  const view = render(<Harness duration={9000} onDismiss={() => calls.push("first")} />);
  const at = clock();

  at(3000);
  view.rerender(<Harness duration={9000} onDismiss={() => calls.push("second")} />);
  at(6000);
  view.rerender(<Harness duration={9000} onDismiss={() => calls.push("third")} />);

  at(8999);
  expect(calls).toEqual([]);
  at(9000);
  // Unchanged deadline, and the newest callback — a hook that froze the first
  // render's closure would dismiss the wrong toast once ids are reused in place.
  expect(calls).toEqual(["third"]);
});

it("a re-raise while the pointer is still on the toast does not resume the clock", () => {
  // The repeat-failure case with the operator's pointer where the spec's own
  // reasoning puts it: on the toast, mid-selection. S4.4 re-raises an
  // un-hovered toast, so an implementation that clears `pointerOver` whenever
  // the revision changes passes it and dismisses this toast at t=6000 —
  // out from under the pointer, which is what keeping the node was for.
  const onDismiss = jest.fn();
  const view = render(<Harness duration={5000} revision={1} onDismiss={onDismiss} />);
  const at = clock();
  const target = view.getByTestId("target");

  at(1000);
  act(() => pointerIn(target));
  at(4000);
  view.rerender(<Harness duration={5000} revision={2} onDismiss={onDismiss} />);

  at(600000);
  expect(onDismiss).not.toHaveBeenCalled();

  // Leaving then spends the full new duration, not the 4000 the first raise
  // had already consumed.
  act(() => pointerOut(target));
  at(604999);
  expect(onDismiss).not.toHaveBeenCalled();
  at(605000);
  expect(onDismiss).toHaveBeenCalledTimes(1);
});

it("a re-raise while focus is inside the toast does not resume the clock either", () => {
  const onDismiss = jest.fn();
  const view = render(<Harness duration={5000} revision={1} onDismiss={onDismiss} />);
  const at = clock();
  const first = view.getByTestId("first");

  at(1000);
  act(() => focusIn(first));
  at(4000);
  view.rerender(<Harness duration={5000} revision={2} onDismiss={onDismiss} />);

  at(600000);
  expect(onDismiss).not.toHaveBeenCalled();

  act(() => focusOut(first));
  at(604999);
  expect(onDismiss).not.toHaveBeenCalled();
  at(605000);
  expect(onDismiss).toHaveBeenCalledTimes(1);
});

it("a leave with no matching enter neither restarts the deadline nor arms a second timer", () => {
  // Pointer events do not arrive in guaranteed pairs: a `pointerout` fires with
  // no `pointerover` of its own when the pointer was already over the element
  // at mount (the toast is raised under a resting mouse — the refusal case,
  // where the operator's hand is still on the control they just clicked).
  // `remaining -= elapsed` bookkeeping that runs on every leave loses 1000ms
  // here; an unguarded "resume" arms a second timer and dismisses twice.
  const onDismiss = jest.fn();
  const view = render(<Harness duration={9000} onDismiss={onDismiss} />);
  const at = clock();
  const target = view.getByTestId("target");

  at(1000);
  act(() => pointerOut(target));
  act(() => focusOut(view.getByTestId("first")));

  at(8999);
  expect(onDismiss).not.toHaveBeenCalled();
  at(9000);
  expect(onDismiss).toHaveBeenCalledTimes(1);
  at(600000);
  expect(onDismiss).toHaveBeenCalledTimes(1);
});

it("focus moving between the toast's own two controls spends no time and restores none", () => {
  // React's onFocus/onBlur are delegated focusin/focusout, so tabbing from the
  // copy button to the close button fires BOTH on the toast root. The pause has
  // to survive the round trip: an implementation whose blur handler resets the
  // remaining time to the full duration hands the operator a longer toast every
  // time they move between its controls, and one that resumes on blur alone
  // starts the clock while focus is still inside.
  const onDismiss = jest.fn();
  const view = render(<Harness duration={9000} onDismiss={onDismiss} />);
  const at = clock();
  const first = view.getByTestId("first");
  const second = view.getByTestId("second");

  at(2000);
  act(() => focusIn(first));
  at(5000);
  act(() => {
    // The crossing, in the order a browser dispatches it.
    focusOut(first);
    focusIn(second);
  });
  at(7000);
  act(() => focusOut(second));

  at(13999);
  expect(onDismiss).not.toHaveBeenCalled();
  at(14000);
  expect(onDismiss).toHaveBeenCalledTimes(1);
});

it("a revision that turns a sticky toast into a bounded one starts scheduling", () => {
  // Reachable through the store: the same failure raised once with no duration
  // (sticky, S2.1) and again with an explicit one (S2.4's cold-cache path)
  // dedupes onto one toast, which takes the new input's duration. A hook that
  // read `duration` only on mount would leave it sticky forever.
  const onDismiss = jest.fn();
  const view = render(<Harness duration={0} revision={1} onDismiss={onDismiss} />);
  const at = clock();

  at(1000);
  view.rerender(<Harness duration={9000} revision={2} onDismiss={onDismiss} />);

  // The new deadline runs from the re-raise, not from the mount.
  at(9999);
  expect(onDismiss).not.toHaveBeenCalled();
  at(10000);
  expect(onDismiss).toHaveBeenCalledTimes(1);
});

it("a revision that turns a bounded toast sticky stops scheduling", () => {
  const onDismiss = jest.fn();
  const view = render(<Harness duration={9000} revision={1} onDismiss={onDismiss} />);
  const at = clock();

  at(1000);
  view.rerender(<Harness duration={0} revision={2} onDismiss={onDismiss} />);

  at(600000);
  expect(onDismiss).not.toHaveBeenCalled();
});
