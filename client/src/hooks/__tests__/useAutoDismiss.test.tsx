/**
 * S1 — the deadline that spends only un-interacted time.
 *
 * The arithmetic is the whole criterion. "Does not dismiss while hovered" is
 * satisfied by an implementation that restarts the full duration on resume,
 * and that implementation is wrong in the way that matters: a 9-second toast
 * the operator brushes past at t=2000 would then sit for 14 seconds, and one
 * they keep brushing would never leave. Every case below therefore pins both
 * edges — present at deadline-1, absent at deadline — so a restart-on-resume
 * build fails rather than passes late.
 *
 * The hook is exercised here through a harness that spreads its returned props
 * onto an element and fires real DOM events at it, so the tests bind to the
 * contract (`{ duration, revision, onDismiss }` in, handler props out) and not
 * to any particular internal spelling of the timer.
 */

import fs from "fs";
import path from "path";

import { act, render } from "@testing-library/react";

import { installPointerEvents } from "../../test/pointerEvents";
import { focusIn, focusOut, pointerIn, pointerOut } from "../../test/hoverAndFocus";
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
      <button type="button" data-testid="inner">
        a control inside
      </button>
    </div>
  );
}

function mount(duration: number, revision = 1) {
  const onDismiss = jest.fn();
  const view = render(<Harness duration={duration} revision={revision} onDismiss={onDismiss} />);
  return {
    onDismiss,
    view,
    target: view.getByTestId("target"),
    inner: view.getByTestId("inner"),
  };
}

/** Advance the fake clock to an absolute time, tracking what has already run. */
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

describe("S1 — pointer and focus suspend the deadline", () => {
  it("S1.2 — resumes from the remaining time after the pointer leaves", () => {
    const { onDismiss, target } = mount(9000);
    const at = clock();

    at(2000);
    act(() => pointerIn(target));
    at(5000);
    act(() => pointerOut(target));

    at(11999);
    expect(onDismiss).not.toHaveBeenCalled();
    at(12000);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("S1.3 — does the same for focus, with no pointer event at all", () => {
    const { onDismiss, inner } = mount(9000);
    const at = clock();

    at(3000);
    act(() => focusIn(inner));
    at(8000);
    act(() => focusOut(inner));

    at(13999);
    expect(onDismiss).not.toHaveBeenCalled();
    at(14000);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("S1.4 — resumes only once NEITHER the pointer nor focus is on it", () => {
    const { onDismiss, target, inner } = mount(9000);
    const at = clock();

    at(1000);
    act(() => pointerIn(target));
    at(2000);
    act(() => focusIn(inner));
    at(3000);
    act(() => pointerOut(target));

    // Focus is still inside, so the pointer leaving resumes nothing.
    at(50000);
    expect(onDismiss).not.toHaveBeenCalled();

    act(() => focusOut(inner));
    at(57999);
    expect(onDismiss).not.toHaveBeenCalled();
    at(58000);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("S1.4 — a second enter while already paused does not double-count", () => {
    // The guard against a naive "remaining -= elapsed" on every enter: two
    // enters with no leave between them must spend 1000ms, not 2000.
    const { onDismiss, target, inner } = mount(9000);
    const at = clock();

    at(1000);
    act(() => pointerIn(target));
    act(() => pointerIn(target));
    at(4000);
    act(() => focusIn(inner));
    act(() => pointerOut(target));
    act(() => focusOut(inner));

    at(11999);
    expect(onDismiss).not.toHaveBeenCalled();
    at(12000);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("S1.5 — a duration of 0 schedules nothing, interacted with or not", () => {
    const { onDismiss, target, inner } = mount(0);
    const at = clock();

    at(1000);
    act(() => pointerIn(target));
    act(() => focusIn(inner));
    at(2000);
    act(() => pointerOut(target));
    act(() => focusOut(inner));

    at(600000);
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("S1.5 — a negative duration schedules nothing either", () => {
    const { onDismiss } = mount(-1);
    const at = clock();

    at(600000);
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("S1.7 — unmounting while paused fires nothing and warns about nothing", () => {
    const warn = jest.spyOn(console, "error").mockImplementation(() => {});
    try {
      const { onDismiss, target, view } = mount(9000);
      const at = clock();

      at(2000);
      act(() => pointerIn(target));
      view.unmount();

      at(600000);
      expect(onDismiss).not.toHaveBeenCalled();
      expect(warn).not.toHaveBeenCalled();
    } finally {
      warn.mockRestore();
    }
  });

  it("S4.4 — a new revision starts a fresh full deadline", () => {
    const onDismiss = jest.fn();
    const view = render(<Harness duration={5000} revision={1} onDismiss={onDismiss} />);
    const at = clock();

    at(4000);
    view.rerender(<Harness duration={5000} revision={2} onDismiss={onDismiss} />);

    at(8999);
    expect(onDismiss).not.toHaveBeenCalled();
    at(9000);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("S4.3 — an unchanged revision does not restart the deadline on re-render", () => {
    // The sibling of the case above, and the one that makes a stable toast id
    // safe: a parent re-rendering for an unrelated reason must not hand the
    // operator another 5 seconds.
    const onDismiss = jest.fn();
    const view = render(<Harness duration={5000} revision={1} onDismiss={onDismiss} />);
    const at = clock();

    at(4000);
    view.rerender(<Harness duration={5000} revision={1} onDismiss={onDismiss} />);

    at(5000);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});

describe("S1 — the seam it is built on", () => {
  const HOOK = path.resolve(__dirname, "../useAutoDismiss.ts");
  const TOAST_HOST = path.resolve(__dirname, "../../components/ToastHost.tsx");

  function source(file: string): string {
    return fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  }

  it("S1.1 — exists as its own hook and exports useAutoDismiss", () => {
    expect(fs.existsSync(HOOK)).toBe(true);
    expect(source(HOOK)).toMatch(/export function useAutoDismiss\b/);
  });

  it("S1.1 — is ToastItem's only dismissal scheduling", () => {
    const host = source(TOAST_HOST);
    expect(host).toMatch(/useAutoDismiss/);
    // ToastHost.tsx:20-24's own setTimeout is what the hook replaces. The host
    // keeps `useScreenAreaCorner`, which schedules nothing.
    expect(host).not.toMatch(/setTimeout/);
  });

  it("S1.9 — reads the pointer and focus from props, not from the document", () => {
    const hook = source(HOOK);
    for (const escape of ["document.addEventListener", "window.addEventListener", ":focus-within"]) {
      expect(hook).not.toContain(escape);
    }
  });

  it("S1.9 — returns exactly the four handler props the toast root spreads", () => {
    const seen: string[] = [];
    function Probe() {
      const handlers = useAutoDismiss({ duration: 1000, revision: 1, onDismiss: () => {} });
      seen.push(...Object.keys(handlers));
      return null;
    }
    render(<Probe />);

    expect(seen.sort()).toEqual(["onBlur", "onFocus", "onPointerEnter", "onPointerLeave"]);
  });
});
