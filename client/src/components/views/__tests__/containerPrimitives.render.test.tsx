/**
 * AC3 (terminal) and AC4 (an existing panel, prop-driven).
 *
 * Both primitives are exercised through `ContainerPrimitiveHost` rather than
 * imported directly, because that is the only path a view has: the host reads
 * `primitive_id` out of `settings`, resolves the entry, and hands the
 * *parsed* settings to the component. Testing the component directly would
 * skip `parseSettings`, which is where bug 444 lives.
 *
 * The socket layer is faked (as `TerminalPanel.test.tsx` does), not the panel
 * — a test that mocks `TerminalPanel` away proves the primitive renders *a*
 * component, not that it renders a shell. `@xterm/*` is already mapped to
 * `src/test/xtermMock.ts` by jest.config.cjs.
 */

import fs from "fs";
import path from "path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";

import { api } from "../../../api/client";
import type { LedgerVisit } from "../../../api/types";
import { ContainerPrimitiveHost } from "../primitives/registry";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

/** Sockets opened during a test, newest last. */
const opened: FakeSocket[] = [];
/** ResizeObserver targets and the callback each would receive. */
const observed: { target: Element; fire: () => void }[] = [];

class FakeSocket {
  static readonly OPEN = 1;
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

  /** The handshake completing, which is when a resize can actually be sent. */
  connect(): void {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
  }
}

/**
 * jsdom measures every element at 0px, and the panel deliberately refuses to
 * fit a zero-sized box, so a test that wants a fit has to say how big the
 * surface is.
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
  jest.clearAllMocks();
  opened.length = 0;
  observed.length = 0;
  layOut(800, 400);
  jest
    .spyOn(globalThis, "requestAnimationFrame")
    .mockImplementation((cb) => ((cb as FrameRequestCallback)(0), 1));
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeSocket;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    callback: () => void;
    constructor(callback: () => void) {
      this.callback = callback;
    }
    observe(target: Element): void {
      observed.push({ target, fire: this.callback });
    }
    disconnect(): void {}
    unobserve(): void {}
  };
});

afterEach(() => {
  jest.restoreAllMocks();
});

function renderHost(containerId: string, settings: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContainerPrimitiveHost containerId={containerId} settings={settings} />
    </QueryClientProvider>,
  );
}

function renderOpenedTerminalHost(containerId: string, settings: Record<string, unknown>) {
  const rendered = renderHost(containerId, settings);
  act(() => jest.runOnlyPendingTimers());
  return rendered;
}

describe("AC3 — a terminal primitive renders a working shell via TerminalPanel", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("opens a shell for the workspace named in settings", () => {
    renderOpenedTerminalHost("c1", { primitive_id: "terminal", workspace_slug: "loregarden" });

    expect(opened).toHaveLength(1);
    expect(opened[0].url).toMatch(/\/terminal\/loregarden$/);
    // TerminalPanel labels itself by workspace; nothing else in the tree does.
    expect(screen.getByLabelText(/Terminal for loregarden/i)).toBeInTheDocument();
  });

  it("takes the workspace from settings and nowhere else", () => {
    renderOpenedTerminalHost("c1", { primitive_id: "terminal", workspace_slug: "blobert" });
    expect(opened[0].url).toMatch(/\/terminal\/blobert$/);
  });

  it("reaps its shell when the container goes away", () => {
    // AC3's "the container owns one shell": unmounting the container must end
    // it, or a closed pane leaves a live shell on the server.
    const { unmount } = renderOpenedTerminalHost("c1", {
      primitive_id: "terminal",
      workspace_slug: "loregarden",
    });
    expect(opened[0].closed).toBe(false);
    unmount();
    expect(opened[0].closed).toBe(true);
  });

  it("sends nothing to a socket that has not opened yet", () => {
    // The handshake is not instant, and a container is mounted the moment a
    // pane appears. Framing a resize into a CONNECTING socket throws
    // InvalidStateError and takes the pane down with it, so the readyState
    // guard has to survive being wrapped as a primitive.
    renderOpenedTerminalHost("c1", {
      primitive_id: "terminal",
      workspace_slug: "loregarden",
    });
    const socket = opened[0];
    expect(socket.readyState).not.toBe(FakeSocket.OPEN);
    expect(socket.sent).toEqual([]);

    // …and a container closed before its shell ever opened still reaps it.
    observed[observed.length - 1]?.fire();
    expect(socket.sent).toEqual([]);
  });

  it("does not fit against a container the browser has not sized", () => {
    // The reflow assertion below cannot tell a correct fit from a fit against a
    // 0x0 box, because the xterm mock reports the same geometry either way.
    // This is the half that is observable: a collapsed pane is an ordinary
    // state, and fitting it pins the shell to a 2-column geometry that nothing
    // re-measures away.
    renderOpenedTerminalHost("c1", {
      primitive_id: "terminal",
      workspace_slug: "loregarden",
    });
    const socket = opened[0];
    socket.connect();
    socket.sent.length = 0;

    layOut(0, 0);
    observed[observed.length - 1].fire();
    expect(socket.sent.filter((frame) => frame.includes('"type":"resize"'))).toEqual([]);
  });

  it("re-fits when its container is resized (AC8's reflow half, for the terminal)", () => {
    renderOpenedTerminalHost("c1", {
      primitive_id: "terminal",
      workspace_slug: "loregarden",
    });
    const socket = opened[0];
    socket.connect();
    socket.sent.length = 0;

    layOut(400, 200);
    expect(observed.length).toBeGreaterThan(0);
    observed[observed.length - 1].fire();

    const resizes = socket.sent.filter((frame) => frame.includes('"type":"resize"'));
    expect(resizes.length).toBeGreaterThan(0);
  });
});

describe("AC4 — RunLedgerPanel is embeddable as a primitive, taking its input as a prop", () => {
  function visit(overrides: Partial<LedgerVisit> = {}): LedgerVisit {
    return {
      stage_key: "implement",
      visit_number: 1,
      status: "succeeded",
      is_parallel: false,
      attempts: [
        {
          run_id: "r1",
          run_code: "run_a",
          agent_id: "backend_implementer",
          skill_name: "",
          status: "succeeded",
          started_at: "2026-08-14T09:00:00",
          finished_at: "2026-08-14T09:00:30",
          duration_seconds: 30,
        },
      ],
      ...overrides,
    };
  }

  it("renders the ledger for the ticket named in settings", async () => {
    mockApi.ticketLedger.mockResolvedValue({
      visits: [visit({ stage_key: "verify" })],
      total_runs: 1,
      reworked_stages: [],
      total_seconds: 30,
    });

    renderHost("c1", { primitive_id: "run_ledger", ticket_id: "t-42" });

    expect(await screen.findByText("verify")).toBeInTheDocument();
    expect(mockApi.ticketLedger).toHaveBeenCalledWith("t-42");
  });

  it("mounts outside every store provider without throwing", () => {
    // Necessary but nowhere near sufficient — see the source check below.
    // Nothing here mounts a router, so a primitive reaching for `useNavigate`
    // does fail on mount; a zustand store is a bare hook with no provider and
    // would not.
    mockApi.ticketLedger.mockResolvedValue({
      visits: [],
      total_runs: 0,
      reworked_stages: [],
      total_seconds: 0,
    });
    expect(() => renderHost("c1", { primitive_id: "run_ledger", ticket_id: "t-42" })).not.toThrow();
  });

  it("reads no page-level state — no primitive module reaches for one", () => {
    // AC4's actual clause: "taking its inputs as props rather than reading
    // page-level state". This repo's page-level state is zustand (`state/`),
    // two React contexts, and the router — and zustand stores are plain hooks
    // with no provider, so *reading one outside a provider does not throw*.
    // A render-level assertion therefore cannot see this at all; the import is
    // the only observable.
    const primitivesDir = path.resolve(__dirname, "../primitives");
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (/\.tsx?$/.test(entry.name)) files.push(full);
      }
    };
    walk(primitivesDir);
    expect(files.length).toBeGreaterThan(0);

    const FORBIDDEN = [
      "state/uiStore",
      "state/QueueStatusContext",
      "state/composerQueueStore",
      "state/notificationStore",
      "react-router",
      "useAppNavigation",
    ];
    for (const file of files) {
      const source = fs.readFileSync(file, "utf8");
      for (const moduleName of FORBIDDEN) {
        const imported = new RegExp(`from\\s*["'][^"']*${moduleName}`).test(source);
        expect({ file, moduleName, imported }).toEqual({ file, moduleName, imported: false });
      }
    }
  });

  it("does not fetch when its ticket setting is absent, and still renders a pane", () => {
    // Not a clause of AC4 on its own — it is what AC1's settings schema implies
    // for a required field with an empty default. A container the operator has
    // just dropped in has no ticket yet, and `api.ticketLedger("")` is a
    // request for a ticket that cannot exist. It must not answer by rendering
    // nothing, either: an empty pane is indistinguishable from a broken one.
    const { container } = renderHost("c1", { primitive_id: "run_ledger" });
    expect(mockApi.ticketLedger).not.toHaveBeenCalled();
    const host = container.querySelector("[data-container-id='c1']");
    expect(host).not.toBeNull();
    expect(host).toHaveAttribute("data-primitive-id", "run_ledger");
    expect(host?.textContent?.trim().length).toBeGreaterThan(0);
  });

  it("survives a ledger fetch that fails", async () => {
    // The panel has an error branch; the container has to let it render rather
    // than propagating to an error boundary that takes the whole view with it.
    mockApi.ticketLedger.mockRejectedValue(new Error("boom"));
    const { container } = renderHost("c1", { primitive_id: "run_ledger", ticket_id: "t-42" });

    // Waiting on the call, not on copy: no spec pins the words a failed fetch
    // shows, only that the pane is still standing and still says what it is.
    await waitFor(() => expect(mockApi.ticketLedger).toHaveBeenCalledWith("t-42"));
    await waitFor(() => {
      const host = container.querySelector("[data-container-id='c1']");
      expect(host).toHaveAttribute("data-primitive-id", "run_ledger");
      expect(host?.textContent?.trim().length).toBeGreaterThan(0);
    });
  });
});
