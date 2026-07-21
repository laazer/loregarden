import { render, screen, act } from "@testing-library/react";

import { TerminalPanel } from "../TerminalPanel";

/** Sockets opened during a test, so assertions can reach the last one. */
const opened: FakeSocket[] = [];

/** What the panel asked to watch, and the callback it would get. */
const observed: { target: Element; fire: () => void }[] = [];

class FakeSocket {
  static readonly OPEN = 1;
  // A real socket starts CONNECTING, and anything sent before the handshake
  // completes is dropped. A fake that is open from birth hides that entirely.
  readyState = 0;
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;

  url: string;

  constructor(url: string) {
    this.url = url;
    opened.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
  }
}

/**
 * jsdom reports a 0x0 box for everything, which is exactly the degenerate case
 * the panel must refuse to fit — so a test wanting a fit has to say how big the
 * surface is. Leaving this out is what let a 2-column terminal ship.
 */
function layOut(width: number, height: number) {
  Element.prototype.getBoundingClientRect = function () {
    return {
      width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0,
      toJSON: () => ({}),
    } as DOMRect;
  };
}

beforeEach(() => {
  opened.length = 0;
  layOut(800, 400);
  // The first fit is deferred to a frame; run it synchronously here.
  jest
    .spyOn(globalThis, "requestAnimationFrame")
    .mockImplementation((cb) => ((cb as FrameRequestCallback)(0), 1));
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeSocket;
  // jsdom implements neither, and the panel observes its own size to fit.
  observed.length = 0;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    callback: () => void;
    constructor(callback: () => void) {
      this.callback = callback;
    }
    observe(target: Element): void {
      observed.push({ target, fire: this.callback });
    }
    disconnect(): void {}
  };
});

describe("TerminalPanel", () => {
  it("connects to the terminal socket for the workspace it was given", () => {
    render(<TerminalPanel workspaceSlug="loregarden" />);

    expect(opened).toHaveLength(1);
    expect(opened[0].url).toBe("ws://127.0.0.1:8000/terminal/loregarden");
  });

  it("does not call itself a sandbox", () => {
    render(<TerminalPanel workspaceSlug="loregarden" />);

    // The comp said `pty · sandbox`. The shell runs with the control plane's
    // own privileges, so that badge would promise containment there is none of.
    expect(screen.getByText("pty · your shell")).toBeInTheDocument();
    expect(screen.queryByText(/sandbox/)).not.toBeInTheDocument();
  });

  it("names the workspace the shell is actually in", () => {
    render(<TerminalPanel workspaceSlug="loregarden" agent="implementer" />);

    expect(screen.getByText("▸ implementer — loregarden")).toBeInTheDocument();
  });

  it("shows the workspace alone when no agent is named", () => {
    render(<TerminalPanel workspaceSlug="loregarden" agent="  " />);

    expect(screen.getByText("▸ loregarden")).toBeInTheDocument();
  });

  it("never labels the shell with a branch", () => {
    // The shell opens in the workspace root, so a ticket's branch is true only
    // by coincidence — observed in the dock reading `feat/bootstrap-ui` over a
    // prompt on `u3d-terminal-dock`. The prompt is authoritative; it says so.
    render(<TerminalPanel workspaceSlug="loregarden" agent="implementer" />);

    const header = screen.getByText(/▸/).textContent ?? "";
    expect(header).toBe("▸ implementer — loregarden");
  });

  it("tells the shell its size once the socket is actually open", () => {
    render(<TerminalPanel workspaceSlug="loregarden" />);

    // The first fit happens while the socket is still CONNECTING, so the size
    // is dropped. Without a re-sync on open the shell keeps its default 80x24
    // while xterm renders a different geometry, and every line wraps in the
    // wrong place — the exact corruption resizing exists to prevent.
    act(() => {
      opened[0].readyState = 1;
      opened[0].onopen?.();
    });

    const resizes = opened[0].sent.map((f) => JSON.parse(f)).filter((m) => m.type === "resize");
    expect(resizes.length).toBeGreaterThan(0);
    expect(resizes[resizes.length - 1]).toEqual({ type: "resize", rows: 24, cols: 80 });
  });

  it("closes the socket on unmount so the server reaps the shell", () => {
    const { unmount } = render(<TerminalPanel workspaceSlug="loregarden" />);

    unmount();

    // Without this every navigation away leaks a login shell.
    expect(opened[0].closed).toBe(true);
  });

  it("explains an ended session and offers a new shell rather than a blank pane", () => {
    render(<TerminalPanel workspaceSlug="loregarden" />);

    act(() => {
      opened[0].onclose?.({ code: 1008, reason: "Unknown workspace" } as CloseEvent);
    });

    expect(screen.getByText("Unknown workspace")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start a new shell" })).toBeInTheDocument();
  });

  it("starts a genuinely new socket when asked, since the old shell is gone", () => {
    render(<TerminalPanel workspaceSlug="loregarden" />);

    act(() => {
      opened[0].onclose?.({ code: 1006, reason: "" } as CloseEvent);
    });
    act(() => {
      screen.getByRole("button", { name: "Start a new shell" }).click();
    });

    expect(opened).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Start a new shell" })).not.toBeInTheDocument();
  });
});

describe("re-fitting when the surface changes size", () => {
  it("watches the element xterm is mounted into", () => {
    const { container } = render(<TerminalPanel workspaceSlug="loregarden" />);

    expect(observed).toHaveLength(1);
    expect(observed[0].target).toBe(container.querySelector(".terminal-panel-surface"));
  });

  it("tells the shell the new size when the surface resizes", () => {
    // Asserted here because ResizeObserver cannot be exercised in the preview
    // browser — it delivers no callbacks there, not even the initial one — so
    // the wiring is pinned where it can be checked deterministically.
    render(<TerminalPanel workspaceSlug="loregarden" />);
    act(() => {
      opened[0].readyState = 1;
      opened[0].onopen?.();
    });
    opened[0].sent.length = 0;

    act(() => observed[0].fire());

    const resizes = opened[0].sent.map((f) => JSON.parse(f)).filter((m) => m.type === "resize");
    expect(resizes).toHaveLength(1);
  });

  it("stops watching on unmount", () => {
    // The callback fits and writes to a socket; leaving it attached after the
    // panel is gone means a resize touching a disposed terminal.
    const disconnects: number[] = [];
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe(): void {}
      disconnect(): void {
        disconnects.push(1);
      }
    };

    render(<TerminalPanel workspaceSlug="loregarden" />).unmount();

    expect(disconnects).toHaveLength(1);
  });
});

describe("a surface the browser has not laid out yet", () => {
  it("does not fit against a zero-sized box", () => {
    // fit() turns a 0px box into a 2-column terminal and xterm keeps it, so
    // every line wraps after two characters. Nothing re-measures to undo that,
    // so the terminal stays unusable until it is remounted — which is exactly
    // what shipped: on first paint with the dock already open, the prompt came
    // back one or two characters per line.
    layOut(0, 0);
    render(<TerminalPanel workspaceSlug="loregarden" />);

    act(() => {
      opened[0].readyState = 1;
      opened[0].onopen?.();
    });

    const resizes = opened[0].sent.map((f) => JSON.parse(f)).filter((m) => m.type === "resize");
    expect(resizes).toHaveLength(0);
  });

  it("fits once the box has a size", () => {
    layOut(0, 0);
    render(<TerminalPanel workspaceSlug="loregarden" />);
    act(() => {
      opened[0].readyState = 1;
      opened[0].onopen?.();
    });
    expect(opened[0].sent).toHaveLength(0);

    // The layout settles, and the observer reports it.
    layOut(800, 400);
    act(() => observed[0].fire());

    const resizes = opened[0].sent.map((f) => JSON.parse(f)).filter((m) => m.type === "resize");
    expect(resizes).toHaveLength(1);
  });

  it("defers the first fit past the frame the mount is created in", () => {
    // Measuring in the same tick reads a box that has not been laid out.
    const frames: FrameRequestCallback[] = [];
    (globalThis.requestAnimationFrame as jest.Mock).mockImplementation((cb: FrameRequestCallback) => {
      frames.push(cb);
      return 1;
    });

    render(<TerminalPanel workspaceSlug="loregarden" />);

    expect(frames).toHaveLength(1);
    expect(opened[0].sent).toHaveLength(0);
  });
});
