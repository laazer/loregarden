/**
 * AC7 — "two instances of the same primitive can be mounted in one view
 * without shared-state or duplicate-DOM-id collisions."
 *
 * Asserted on observable behaviour: each instance renders *its own* data. An
 * id-generation scheme is one way to get there and is not what the AC asks
 * for, so nothing here reaches for a generated id. The duplicate-DOM-id half
 * is checked directly, by collecting every `id` attribute in the tree — which
 * is a real collision (a label's `htmlFor` binds to the first match) and not
 * an implementation detail.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";

import { api } from "../../../api/client";
import type { TicketLedger } from "../../../api/types";
import { ContainerPrimitiveHost } from "../primitives/registry";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

const opened: { url: string; closed: boolean }[] = [];

class FakeSocket {
  static readonly OPEN = 1;
  readyState = 0;
  entry: { url: string; closed: boolean };
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.entry = { url, closed: false };
    opened.push(this.entry);
  }

  send(): void {}
  close(): void {
    this.entry.closed = true;
  }
}

beforeEach(() => {
  jest.clearAllMocks();
  opened.length = 0;
  jest
    .spyOn(globalThis, "requestAnimationFrame")
    .mockImplementation((cb) => ((cb as FrameRequestCallback)(0), 1));
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeSocket;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe(): void {}
    disconnect(): void {}
    unobserve(): void {}
  };
});

afterEach(() => jest.restoreAllMocks());

function renderPair(a: Record<string, unknown>, b: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContainerPrimitiveHost containerId="left" settings={a} />
      <ContainerPrimitiveHost containerId="right" settings={b} />
    </QueryClientProvider>,
  );
}

function renderOpenedTerminalPair(a: Record<string, unknown>, b: Record<string, unknown>) {
  const rendered = renderPair(a, b);
  act(() => jest.runOnlyPendingTimers());
  return rendered;
}

/**
 * Every `id` attribute in the *document*, so duplicates are countable — a
 * primitive that portals its menu or its dialog to `document.body` collides
 * just as hard, and scoping this to the render container would miss it.
 */
function domIds(): string[] {
  return Array.from(document.querySelectorAll("[id]")).map((el) => el.id);
}

/**
 * `id` uniqueness is vacuously true when nothing has an id, so it cannot be the
 * only check. Every `for` / `aria-labelledby` / `aria-controls` reference must
 * also resolve inside the container that wrote it — the actual harm of a
 * duplicate id is a label in the right pane driving the widget in the left one.
 */
function danglingOrCrossPaneReferences(): string[] {
  const problems: string[] = [];
  const REFERENCING = ["for", "aria-labelledby", "aria-controls", "aria-describedby"];
  for (const el of Array.from(document.querySelectorAll("*"))) {
    const owner = el.closest("[data-container-id]");
    for (const attr of REFERENCING) {
      const value = el.getAttribute(attr);
      if (!value) continue;
      for (const id of value.split(/\s+/).filter(Boolean)) {
        // Matched by property rather than by selector: an id is arbitrary text
        // and `CSS.escape` is not something to rely on inside a test.
        const targets = Array.from(document.querySelectorAll("[id]")).filter((el) => el.id === id);
        if (targets.length !== 1) {
          problems.push(`${attr}="${id}" resolves to ${targets.length} elements`);
          continue;
        }
        const targetOwner = targets[0].closest("[data-container-id]");
        if (owner && targetOwner && owner !== targetOwner) {
          problems.push(`${attr}="${id}" crosses from one container to another`);
        }
      }
    }
  }
  return problems;
}

describe("AC7 — two web embeds side by side", () => {
  it("each renders its own URL", () => {
    const { container } = renderPair(
      { primitive_id: "web_embed", url: "https://one.example/" },
      { primitive_id: "web_embed", url: "https://two.example/" },
    );
    const srcs = Array.from(container.querySelectorAll("iframe")).map((f) => f.getAttribute("src"));
    expect(srcs).toEqual(["https://one.example/", "https://two.example/"]);
  });

  it("a refused URL in one does not blank the other", () => {
    // The failure this catches is shared state: a single module-level "current
    // url" or error flag makes one bad container poison its neighbour.
    const { container } = renderPair(
      { primitive_id: "web_embed", url: "javascript:alert(1)" },
      { primitive_id: "web_embed", url: "https://two.example/" },
    );
    const frames = container.querySelectorAll("iframe");
    expect(frames).toHaveLength(1);
    expect(frames[0]).toHaveAttribute("src", "https://two.example/");
  });

  it("collides on no DOM id", () => {
    renderPair(
      { primitive_id: "web_embed", url: "https://one.example/" },
      { primitive_id: "web_embed", url: "https://two.example/" },
    );
    const ids = domIds();
    expect(new Set(ids).size).toBe(ids.length);
    expect(danglingOrCrossPaneReferences()).toEqual([]);
  });
});

describe("AC7 — two run-ledger panels side by side", () => {
  function ledger(stageKey: string): TicketLedger {
    return {
      visits: [
        {
          stage_key: stageKey,
          visit_number: 1,
          status: "succeeded",
          is_parallel: false,
          attempts: [
            {
              run_id: `r-${stageKey}`,
              run_code: `run_${stageKey}`,
              agent_id: "backend_implementer",
              skill_name: "",
              status: "succeeded",
              started_at: "2026-08-14T09:00:00",
              finished_at: "2026-08-14T09:00:30",
              duration_seconds: 30,
            },
          ],
        },
      ],
      total_runs: 1,
      reworked_stages: [],
      total_seconds: 30,
    };
  }

  it("each shows the ledger of its own ticket", async () => {
    mockApi.ticketLedger.mockImplementation(async (ticketId: string) =>
      ticketId === "t-left" ? ledger("implement") : ledger("verify"),
    );

    renderPair(
      { primitive_id: "run_ledger", ticket_id: "t-left" },
      { primitive_id: "run_ledger", ticket_id: "t-right" },
    );

    expect(await screen.findByText("implement")).toBeInTheDocument();
    expect(await screen.findByText("verify")).toBeInTheDocument();
    expect(mockApi.ticketLedger).toHaveBeenCalledWith("t-left");
    expect(mockApi.ticketLedger).toHaveBeenCalledWith("t-right");

    const ids = domIds();
    expect(new Set(ids).size).toBe(ids.length);
    expect(danglingOrCrossPaneReferences()).toEqual([]);
  });

  it("one panel's failed fetch does not take its neighbour's data down", async () => {
    // Shared state is at its most plausible here: a react-query key that
    // forgets the ticket, or a module-level "last error", makes the left pane's
    // 500 blank the right pane's perfectly good ledger.
    mockApi.ticketLedger.mockImplementation(async (ticketId: string) => {
      if (ticketId === "t-left") throw new Error("boom");
      return ledger("verify");
    });

    const { container } = renderPair(
      { primitive_id: "run_ledger", ticket_id: "t-left" },
      { primitive_id: "run_ledger", ticket_id: "t-right" },
    );

    expect(await screen.findByText("verify")).toBeInTheDocument();
    // Both panes are still standing, and each still says which one it is.
    const hosts = Array.from(container.querySelectorAll("[data-container-id]"));
    expect(hosts.map((host) => host.getAttribute("data-container-id"))).toEqual(["left", "right"]);
    for (const host of hosts) {
      expect(host.textContent?.trim().length).toBeGreaterThan(0);
    }
  });
});

describe("AC7 — two terminals side by side", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("each opens and reaps its own shell", () => {
    const { unmount } = renderOpenedTerminalPair(
      { primitive_id: "terminal", workspace_slug: "loregarden" },
      { primitive_id: "terminal", workspace_slug: "blobert" },
    );

    expect(opened).toHaveLength(2);
    expect(opened.map((s) => s.url.split("/").pop())).toEqual(["loregarden", "blobert"]);

    unmount();
    expect(opened.every((s) => s.closed)).toBe(true);
  });

  it("two shells for the same workspace are still two shells", () => {
    // The collision an over-eager cache would introduce: keying a shell on the
    // workspace slug rather than on the container gives both panes one shell,
    // and closing one pane kills the other.
    renderOpenedTerminalPair(
      { primitive_id: "terminal", workspace_slug: "loregarden" },
      { primitive_id: "terminal", workspace_slug: "loregarden" },
    );
    expect(opened).toHaveLength(2);
  });
});
