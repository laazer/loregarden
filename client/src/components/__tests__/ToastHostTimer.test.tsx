/**
 * lg-milestone-that-1316 AC17 / AC33 — the dismiss timer, pausable.
 *
 * Split out of `ToastHostRecovery.test.tsx` at the test-break stage, under
 * AC56's reporting route and AC61's rule that a suite past the 1200-line
 * `ts-organization` cap is split along a real seam rather than trimmed of
 * coverage. This is that seam: every case here drives `useDismissTimer` and
 * nothing else, all of them need fake timers, and none of them touch the
 * clipboard, the spans or the stylesheet.
 *
 * `DEFAULT_DURATION.error` is 9000ms against a 269-character message, which is
 * the arithmetic AC4 asked to be reconciled. AC17's answer is pause, not
 * sticky, and AC33 makes it executable: a deadline in a ref, a set of
 * outstanding pause reasons, a 3000ms floor on every resume, and the full
 * duration never restarted from scratch.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";

import { pushToast, useToastStore } from "../../state/toastStore";
import { ToastHost } from "../ToastHost";

import {
  CLASS_B_MESSAGE,
  LONG_MESSAGE_WITHOUT_PATH,
  pushClassB,
  showAll,
  toastEl,
  toastEls,
} from "./toastFixtures";

beforeEach(() => {
  useToastStore.getState().clear();
});

describe("two pause reasons at once (AC17, AC33)", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("stays held when the pointer leaves while the message is still expanded", () => {
    render(<ToastHost />);
    pushClassB({ duration: 9000 });

    // Both reasons taken at t=0, so the time left is 9000 on either bookkeeping.
    fireEvent.mouseEnter(toastEl());
    fireEvent.click(showAll());
    fireEvent.mouseLeave(toastEl());

    act(() => {
      jest.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show less" }));
    act(() => {
      jest.advanceTimersByTime(8999);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stays held when the message collapses while the pointer is still over it", () => {
    render(<ToastHost />);
    pushClassB({ duration: 9000 });

    fireEvent.click(showAll());
    fireEvent.mouseEnter(toastEl());
    fireEvent.click(screen.getByRole("button", { name: "Show less" }));

    act(() => {
      jest.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();

    fireEvent.mouseLeave(toastEl());
    act(() => {
      jest.advanceTimersByTime(8999);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not resume when focus moves between two controls in the same toast", () => {
    render(
      <>
        <button type="button" data-testid="outside">
          outside
        </button>
        <ToastHost />
      </>,
    );
    pushClassB({ duration: 9000 });

    const controls = Array.from(toastEl().querySelectorAll("button")) as HTMLButtonElement[];
    expect(controls.length).toBeGreaterThan(1);

    act(() => {
      controls[0].focus();
    });
    act(() => {
      controls[1].focus();
    });

    // focusout fired when focus left the first control. Resuming there would
    // start a timer while focus is still inside the toast.
    act(() => {
      jest.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();

    act(() => {
      screen.getByTestId("outside").focus();
    });
    act(() => {
      jest.advanceTimersByTime(8999);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not hand back time across repeated hover cycles", () => {
    render(<ToastHost />);
    pushClassB({ duration: 9000 });

    act(() => {
      jest.advanceTimersByTime(5000);
    });
    fireEvent.mouseEnter(toastEl());
    fireEvent.mouseLeave(toastEl()); // 4000 left, above the 3000 floor

    act(() => {
      jest.advanceTimersByTime(1000);
    });
    fireEvent.mouseEnter(toastEl());
    fireEvent.mouseLeave(toastEl()); // 3000 left, exactly at the floor

    act(() => {
      jest.advanceTimersByTime(2999);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(1);
    });
    // 5000 + 1000 + 3000 = the stated 9000, never restarted from scratch.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("pauses each toast on its own, not the stack", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Hovered", message: CLASS_B_MESSAGE, duration: 9000 });
    });
    act(() => {
      pushToast({
        tone: "error",
        title: "Untouched",
        message: LONG_MESSAGE_WITHOUT_PATH,
        duration: 9000,
      });
    });

    fireEvent.mouseEnter(toastEls()[0]);
    act(() => {
      jest.advanceTimersByTime(9000);
    });

    const titles = useToastStore.getState().toasts.map((t) => t.title);
    expect(titles).toEqual(["Hovered"]);
  });

  it("drops the timer with the toast rather than firing after it is gone", () => {
    render(<ToastHost />);
    let id = "";
    act(() => {
      id = pushToast({
        tone: "error",
        title: "Refresh from target failed",
        message: CLASS_B_MESSAGE,
        duration: 9000,
      });
    });

    fireEvent.mouseEnter(toastEl());
    act(() => {
      useToastStore.getState().dismiss(id);
    });

    // A resume scheduled against a toast that has left the store must not
    // dismiss whatever took its place.
    act(() => {
      pushToast({ tone: "error", title: "Next failure", message: CLASS_B_MESSAGE, duration: 0 });
    });
    act(() => {
      jest.advanceTimersByTime(600_000);
    });

    expect(useToastStore.getState().toasts.map((t) => t.title)).toEqual(["Next failure"]);
  });
});

describe("the same pause reason, taken twice (AC33)", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("does not re-record the time left on a second mouseenter", () => {
    render(<ToastHost />);
    pushClassB({ duration: 9000 });

    act(() => {
      jest.advanceTimersByTime(5000);
    });
    // Two enters without a leave between them. A pause that re-reads the
    // deadline on the second one reads a deadline it already cleared, computes
    // 0 left, and resumes at the 3000 floor instead of the 4000 that remain.
    fireEvent.mouseEnter(toastEl());
    fireEvent.mouseEnter(toastEl());
    fireEvent.mouseLeave(toastEl());

    act(() => {
      jest.advanceTimersByTime(3999);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("resumes on one leave, not on a matching count of them", () => {
    render(<ToastHost />);
    pushClassB({ duration: 9000 });

    fireEvent.mouseEnter(toastEl());
    fireEvent.mouseEnter(toastEl());
    fireEvent.mouseLeave(toastEl());

    // A counter-based pause would still be held here, and the toast would
    // never dismiss once the pointer had entered twice.
    act(() => {
      jest.advanceTimersByTime(9000);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ignores a leave that was never preceded by an enter", () => {
    render(<ToastHost />);
    pushClassB({ duration: 9000 });

    act(() => {
      jest.advanceTimersByTime(5000);
    });
    // A resume for a reason that is not outstanding must not restart the
    // timer, or a stray pointerleave hands the toast 4000ms it had spent.
    fireEvent.mouseLeave(toastEl());

    act(() => {
      jest.advanceTimersByTime(3999);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});