import { render, screen, act } from "@testing-library/react";

import { TerminalPanel } from "../TerminalPanel";

/** Sockets opened during a test, so assertions can reach the last one. */
const opened: FakeSocket[] = [];

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

beforeEach(() => {
  opened.length = 0;
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeSocket;
  // jsdom implements neither, and the panel observes its own size to fit.
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe(): void {}
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

  it("shows the agent and branch it was handed", () => {
    render(<TerminalPanel workspaceSlug="loregarden" agent="implementer" branch="u3c-terminal-ui" />);

    expect(screen.getByText("▸ implementer — u3c-terminal-ui")).toBeInTheDocument();
  });

  it("falls back to the workspace when there is no agent to name", () => {
    render(<TerminalPanel workspaceSlug="loregarden" branch="  " />);

    expect(screen.getByText("▸ loregarden")).toBeInTheDocument();
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
