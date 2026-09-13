import { render } from "@testing-library/react";
import { useRef } from "react";

import { useStickToBottom } from "../useStickToBottom";

/** A hand-driven ResizeObserver: jsdom has none, and nothing here really resizes. */
class FakeResizeObserver {
  static callbacks: Array<() => void> = [];
  private readonly cb: () => void;
  constructor(cb: () => void) {
    this.cb = cb;
    FakeResizeObserver.callbacks.push(cb);
  }
  observe() {}
  disconnect() {
    FakeResizeObserver.callbacks = FakeResizeObserver.callbacks.filter((c) => c !== this.cb);
  }
  /** Pretend the observed element changed height. */
  static grow() {
    FakeResizeObserver.callbacks.forEach((cb) => cb());
  }
}

/**
 * A scroller with a list inside it, wired to the hook.
 *
 * `overflow-y: auto` via the style attribute so `getComputedStyle` reports it —
 * the hook walks up looking for exactly that.
 */
function Thread({ enabled = true, turn = 0 }: { enabled?: boolean; turn?: number }) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  useStickToBottom(listRef, bottomRef, enabled, [turn]);
  return (
    <div data-testid="scroller" style={{ overflowY: "auto" }}>
      <div ref={listRef}>
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function measure(scroller: HTMLElement, { scrollHeight = 1000, clientHeight = 400, scrollTop = 0 }) {
  Object.defineProperty(scroller, "scrollHeight", { value: scrollHeight, configurable: true });
  Object.defineProperty(scroller, "clientHeight", { value: clientHeight, configurable: true });
  scroller.scrollTop = scrollTop;
}

beforeEach(() => {
  FakeResizeObserver.callbacks = [];
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver;
});

afterEach(() => {
  delete (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
});

describe("useStickToBottom", () => {
  it("lands on the newest content the first time a loaded thread lays out", () => {
    // The failure this replaces: a thread opened from the archive rendered
    // scrolled to the top of its history, because the hook had already decided
    // the operator was "not at the bottom" before they had touched anything.
    const { getByTestId } = render(<Thread />);
    const scroller = getByTestId("scroller");
    measure(scroller, { scrollHeight: 1400, scrollTop: 0 });
    FakeResizeObserver.grow();

    expect(scroller.scrollTop).toBe(1400);
  });

  it("follows content that grows under a thread sitting at the bottom", () => {
    const { getByTestId } = render(<Thread />);
    const scroller = getByTestId("scroller");
    // First observation settles the baseline height and leaves it at the bottom.
    measure(scroller, { scrollHeight: 1000, scrollTop: 0 });
    FakeResizeObserver.grow();
    expect(scroller.scrollTop).toBe(1000);

    measure(scroller, { scrollHeight: 1400, scrollTop: 600 });
    FakeResizeObserver.grow();

    expect(scroller.scrollTop).toBe(1400);
  });

  it("leaves a thread the operator scrolled up in alone", () => {
    const { getByTestId } = render(<Thread />);
    const scroller = getByTestId("scroller");
    measure(scroller, { scrollHeight: 1000, scrollTop: 0 });
    FakeResizeObserver.grow();

    // The operator scrolls back up: 1000 - 100 - 400 = 500px off the bottom.
    measure(scroller, { scrollHeight: 1400, scrollTop: 100 });
    FakeResizeObserver.grow();

    expect(scroller.scrollTop).toBe(100);
  });

  it("starts following again once the operator scrolls back down", () => {
    const { getByTestId } = render(<Thread />);
    const scroller = getByTestId("scroller");
    measure(scroller, { scrollHeight: 1000, scrollTop: 0 });
    FakeResizeObserver.grow();
    measure(scroller, { scrollHeight: 1400, scrollTop: 100 });
    FakeResizeObserver.grow();
    expect(scroller.scrollTop).toBe(100);

    // Back to the bottom of the 1400px thread by hand: 1400 - 1000 - 400 = 0.
    measure(scroller, { scrollHeight: 1800, scrollTop: 1000 });
    FakeResizeObserver.grow();

    expect(scroller.scrollTop).toBe(1800);
  });

  it("re-arms when a new turn arrives, even from halfway up", () => {
    const { getByTestId, rerender } = render(<Thread turn={0} />);
    const scroller = getByTestId("scroller");
    measure(scroller, { scrollHeight: 1000, scrollTop: 0 });
    FakeResizeObserver.grow();
    measure(scroller, { scrollHeight: 1400, scrollTop: 100 });
    FakeResizeObserver.grow();
    expect(scroller.scrollTop).toBe(100);

    rerender(<Thread turn={1} />);
    measure(scroller, { scrollHeight: 1800, scrollTop: 100 });
    FakeResizeObserver.grow();

    expect(scroller.scrollTop).toBe(1800);
  });

  it("observes nothing on a surface that manages its own scrolling", () => {
    render(<Thread enabled={false} />);
    expect(FakeResizeObserver.callbacks).toHaveLength(0);
  });

  it("survives a browser with no ResizeObserver", () => {
    delete (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
    expect(() => render(<Thread />)).not.toThrow();
  });
});
