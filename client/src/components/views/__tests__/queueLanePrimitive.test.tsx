/**
 * The Queue Lane pane: one lane, at whatever size the pane happens to be.
 *
 * The lane data is faked at the fetch, not at the hook, so the query key, the
 * shape it reads out of `/api/parallel/status`, and the dedupe across panes are
 * all part of what is under test. Two of those are the reasons this primitive
 * does not use the app-wide queue provider, and asserting them at the hook
 * would assert nothing.
 *
 * Nothing here measures a pane. What the tier changes is *which elements
 * render*, which is observable; whether the result fits in 120px is not a
 * question jsdom can answer.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";

import { COMPACT_WIDTH, WIDE_WIDTH } from "../paneSize";
import { elapsedLabel } from "../primitives/queueLanePrimitive";
import { ContainerPrimitiveHost } from "../primitives/registry";

const LANES = [
  {
    slot_number: 1,
    running: {
      run_id: "r-1",
      ticket_id: "t-1",
      slot_number: 1,
      elapsed_seconds: 137,
      status: "running",
      agent_id: "backend_implementer",
      ticket_code: "lg-flex-views-561",
      ticket_title: "A queue lane, as a pane",
    },
    waiting: [
      { entry_id: "e-1", ticket_id: "t-2", workspace_id: "w", position: 1, auto_approve: false, stop_at_stage_key: "", queued_at: null, ticket_code: "lg-a-1", ticket_title: "First in line" },
      { entry_id: "e-2", ticket_id: "t-3", workspace_id: "w", position: 2, auto_approve: false, stop_at_stage_key: "", queued_at: null, ticket_code: "lg-a-2", ticket_title: "Second in line" },
    ],
    attention_total: 1,
  },
  { slot_number: 2, running: null, waiting: [], attention_total: 0 },
];

let fetchMock: jest.Mock;

/** Observers created during a test, with the callback each would receive. */
const observed: { target: Element; fire: (width: number, height: number) => void }[] = [];

beforeEach(() => {
  observed.length = 0;
  fetchMock = jest.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ lanes: LANES }),
  });
  (globalThis as unknown as { fetch: unknown }).fetch = fetchMock;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    callback: ResizeObserverCallback;
    constructor(callback: ResizeObserverCallback) {
      this.callback = callback;
    }
    observe(target: Element): void {
      observed.push({
        target,
        fire: (width, height) =>
          this.callback(
            [
              {
                target,
                contentRect: { width, height, top: 0, left: 0, bottom: height, right: width, x: 0, y: 0 },
              } as unknown as ResizeObserverEntry,
            ],
            this as unknown as ResizeObserver,
          ),
      });
    }
    disconnect(): void {}
    unobserve(): void {}
  };
});

/**
 * Sized through the host's own observer, not by wrapping it in a
 * `PaneSizeContext`.
 *
 * The host provides that context itself, so a provider placed above it is
 * overridden and the pane reads `regular` no matter what the test asked for.
 * Both compact tests below passed against the full-size layout until this went
 * through the observer instead.
 */
function renderLane(
  settings: Record<string, unknown>,
  box: { width: number; height: number } = { width: WIDE_WIDTH - 100, height: 400 },
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <ContainerPrimitiveHost containerId="c1" settings={{ primitive_id: "queue_lane", ...settings }} />
    </QueryClientProvider>,
  );
  const host = rendered.container.querySelector("[data-container-id='c1']");
  const observer = observed.find((entry) => entry.target === host);
  act(() => observer?.fire(box.width, box.height));
  return rendered;
}

/** Narrow enough that `paneTierFor` calls it compact, by the vocabulary's own number. */
const COMPACT_BOX = { width: COMPACT_WIDTH - 20, height: 400 };

describe("elapsedLabel", () => {
  it("reads as a duration at every scale", () => {
    expect(elapsedLabel(0)).toBe("0s");
    expect(elapsedLabel(45)).toBe("45s");
    expect(elapsedLabel(137)).toBe("2m 17s");
    expect(elapsedLabel(3600)).toBe("1h 0m");
    expect(elapsedLabel(7860)).toBe("2h 11m");
  });

  it("refuses to render a negative or fractional lane age", () => {
    // `elapsed_seconds` is server-computed and has been negative before, when
    // a clock moved. "-1s" in a pane is worse than "0s".
    expect(elapsedLabel(-4)).toBe("0s");
    expect(elapsedLabel(2.9)).toBe("2s");
  });
});

describe("before a lane is chosen", () => {
  it("says so and asks the server for nothing", async () => {
    renderLane({});
    expect(await screen.findByText(/no lane yet/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("distinguishes a lane that is not in the pool from one not chosen", async () => {
    renderLane({ slot: "7" });
    // The pool is configurable; a view naming lane 7 after it shrank to two
    // should say which of the two things is wrong.
    expect(await screen.findByText(/Lane 7 is not in the pool/)).toBeInTheDocument();
  });
});

describe("a running lane", () => {
  it("shows what is running, for how long, and what is behind it", async () => {
    renderLane({ slot: "1" });

    expect(await screen.findByText(/lg-flex-views-561 · A queue lane, as a pane/)).toBeInTheDocument();
    expect(screen.getByText(/2m 17s/)).toBeInTheDocument();
    expect(screen.getByText(/2 waiting · 1 needing attention/)).toBeInTheDocument();

    const list = screen.getByRole("list", { name: "Waiting in this lane" });
    expect(list.querySelectorAll("li")).toHaveLength(2);
  });

  it("reads the lane by its slot number, not by its position in the payload", async () => {
    // Lane 2 is second in the array. A pane that indexed would show lane 1.
    renderLane({ slot: "2" });
    expect(await screen.findByText("Lane 2")).toBeInTheDocument();
    expect(screen.getByText(/Nothing is running in this lane/)).toBeInTheDocument();
    expect(screen.getByText("0 waiting")).toBeInTheDocument();
  });
});

describe("the pane's size changes what is rendered", () => {
  it("drops the waiting list to its count when the pane is compact", async () => {
    renderLane({ slot: "1" }, COMPACT_BOX);

    // The count is the fact and survives every tier; the list needs room.
    expect(await screen.findByText(/2 waiting/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Waiting in this lane" })).toBeNull();
  });

  it("shows the running ticket's code alone when the pane is compact", async () => {
    renderLane({ slot: "1" }, COMPACT_BOX);

    expect(await screen.findByText("lg-flex-views-561")).toBeInTheDocument();
    expect(screen.queryByText(/A queue lane, as a pane/)).toBeNull();
    // The status word goes with the title; the elapsed time is the one number
    // worth the space at any size.
    expect(screen.getByText("2m 17s")).toBeInTheDocument();
  });
});

describe("many panes, one request", () => {
  it("fetches the lanes once for three panes sharing a cache", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ContainerPrimitiveHost containerId="a" settings={{ primitive_id: "queue_lane", slot: "1" }} />
        <ContainerPrimitiveHost containerId="b" settings={{ primitive_id: "queue_lane", slot: "2" }} />
        <ContainerPrimitiveHost containerId="c" settings={{ primitive_id: "queue_lane", slot: "1" }} />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getAllByText(/Lane [12]/)).toHaveLength(3));
    // One key, one in-flight request — the reason this does not use
    // `useParallelExecutionWS`, which would have opened three sockets.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
