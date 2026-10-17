/**
 * S1 and S2.5 on the real host — the same arithmetic as
 * `hooks/__tests__/useAutoDismiss.test.tsx`, but through `ToastHost` and the
 * real store, because what the criteria are about is a toast the operator is
 * reading, not a hook in isolation. The hook could be perfect and the host
 * could still forget to spread its handlers onto the toast root.
 *
 * Rendered without `LazyMotion` deliberately, as the existing auto-dismiss
 * test in `ToastHost.test.tsx` is: with no motion features loaded the exit is
 * immediate, so "absent at t=12000" is a statement about the deadline and not
 * about how long an animation takes.
 */

import { act, render, screen } from "@testing-library/react";

import { pushToast, useToastStore } from "../../state/toastStore";
import { focusIn, focusOut, pointerIn, pointerOut } from "../../test/hoverAndFocus";
import { installPointerEvents } from "../../test/pointerEvents";
import { ToastHost } from "../ToastHost";

installPointerEvents();

beforeEach(() => {
  useToastStore.getState().clear();
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

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

function raise(input: Parameters<typeof pushToast>[0]) {
  act(() => {
    pushToast(input);
  });
}

const alert = () => screen.getByRole("alert");
const status = () => screen.getByRole("status");
const noAlert = () => expect(screen.queryByRole("alert")).not.toBeInTheDocument();
const noStatus = () => expect(screen.queryByRole("status")).not.toBeInTheDocument();

it("S1.2 — a hovered error toast spends only un-hovered time", () => {
  render(<ToastHost />);
  raise({ tone: "error", title: "Delete session failed", message: "a path", duration: 9000 });
  const at = clock();

  at(2000);
  act(() => pointerIn(alert()));
  at(5000);
  act(() => pointerOut(alert()));

  at(11999);
  expect(alert()).toBeInTheDocument();
  at(12000);
  noAlert();
});

it("S1.3 — focus inside the toast suspends it, with no pointer involved", () => {
  render(<ToastHost />);
  raise({ tone: "error", title: "Delete session failed", message: "a path", duration: 9000 });
  const at = clock();

  const close = screen.getByRole("button", { name: "Dismiss: Delete session failed" });
  at(3000);
  act(() => focusIn(close));
  at(8000);
  act(() => focusOut(close));

  at(13999);
  expect(alert()).toBeInTheDocument();
  at(14000);
  noAlert();
});

it("S1.4 — the pointer leaving while focus is still inside resumes nothing", () => {
  render(<ToastHost />);
  raise({ tone: "error", title: "Delete session failed", message: "a path", duration: 9000 });
  const at = clock();

  const close = screen.getByRole("button", { name: "Dismiss: Delete session failed" });
  at(1000);
  act(() => pointerIn(alert()));
  at(2000);
  act(() => focusIn(close));
  at(3000);
  act(() => pointerOut(alert()));

  at(50000);
  expect(alert()).toBeInTheDocument();

  act(() => focusOut(close));
  at(57999);
  expect(alert()).toBeInTheDocument();
  at(58000);
  noAlert();
});

it("S1.6 — the pause is tone-blind: an info toast pauses the same way", () => {
  render(<ToastHost />);
  raise({ tone: "info", title: "Queued", message: "waiting for capacity", duration: 5000 });
  const at = clock();

  at(1000);
  act(() => pointerIn(status()));
  at(4000);
  act(() => pointerOut(status()));

  at(7999);
  expect(status()).toBeInTheDocument();
  at(8000);
  noStatus();
});

it("S1.6 — and does it without asking the tone", () => {
  // UX6. A `tone === "error"` conditional is strictly more code whose only
  // effect is to let a success toast vanish out from under a reader, so the
  // success tone gets the same assertion rather than being left to inference.
  render(<ToastHost />);
  raise({ tone: "success", title: "Branch pushed", message: "3 commits", duration: 4000 });
  const at = clock();

  at(500);
  act(() => pointerIn(status()));
  at(3500);
  act(() => pointerOut(status()));

  at(6999);
  expect(status()).toBeInTheDocument();
  at(7000);
  noStatus();
});

it("S1.8 — holds for an error toast given an explicit duration (UX5's live case)", () => {
  render(<ToastHost />);
  raise({ tone: "error", title: "Load tickets failed", message: "database is locked", duration: 9000 });
  const at = clock();

  at(2000);
  act(() => pointerIn(alert()));
  at(5000);
  act(() => pointerOut(alert()));

  at(11999);
  expect(alert()).toBeInTheDocument();
  at(12000);
  noAlert();
});

it("S2.1 — an error toast with no explicit duration never auto-dismisses", () => {
  render(<ToastHost />);
  raise({ tone: "error", title: "Delete session failed", message: "a path the operator needs" });
  const at = clock();

  at(600000);
  expect(alert()).toBeInTheDocument();
});

it("S2.5 — an explicit zero wins over any default", () => {
  render(<ToastHost />);
  raise({ tone: "info", title: "Working", duration: 0 });
  const at = clock();

  at(600000);
  expect(status()).toBeInTheDocument();
});

it("S4.4 — re-raising the same failure restarts its full duration", () => {
  render(<ToastHost />);
  raise({ tone: "info", title: "Queued", message: "waiting for capacity", duration: 5000 });
  const at = clock();

  at(4000);
  raise({ tone: "info", title: "Queued", message: "waiting for capacity", duration: 5000 });

  at(8999);
  expect(status()).toBeInTheDocument();
  at(9000);
  noStatus();
});

it("S1.7 — a paused toast the host unmounts dismisses nothing afterwards", () => {
  const dismiss = jest.spyOn(useToastStore.getState(), "dismiss");
  try {
    const view = render(<ToastHost />);
    raise({ tone: "error", title: "Delete session failed", message: "a path", duration: 9000 });
    const at = clock();

    at(2000);
    act(() => pointerIn(alert()));
    view.unmount();

    at(600000);
    expect(dismiss).not.toHaveBeenCalled();
  } finally {
    dismiss.mockRestore();
  }
});
