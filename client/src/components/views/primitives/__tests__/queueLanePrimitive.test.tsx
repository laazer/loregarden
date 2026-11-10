/**
 * The queue-lane pane, and its keyboard path.
 *
 * lg-milestone-that-2215: `.queue-lane` is flex-bound on BOTH axes with
 * `overflow: auto` — one of the regions a vertical-only predicate could not
 * produce at all. Everything in it is text: the lane's state, what is running,
 * and the `<ol>` of what is waiting. Nothing took focus, so a pane the grid
 * made short could not be scrolled with the arrow keys.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import { queueLanePrimitive } from "../queueLanePrimitive";
import { useQueueLanes } from "../../../../hooks/useQueueLanes";
import type { LaneEntry, QueueLane } from "../../../../lib/queueSocket";

jest.mock("../../../../hooks/useQueueLanes", () => ({
  useQueueLanes: jest.fn(),
}));
const mockLanes = useQueueLanes as jest.MockedFunction<typeof useQueueLanes>;

function waiting(position: number, code: string): LaneEntry {
  return {
    entry_id: `e${position}`,
    ticket_id: `t${position}`,
    workspace_id: "w1",
    position,
    auto_approve: false,
    stop_at_stage_key: "",
    queued_at: "2026-11-07T10:00:00Z",
    ticket_code: code,
    ticket_title: `Work item ${position}`,
  } as unknown as LaneEntry;
}

function lane(overrides: Partial<QueueLane> = {}): QueueLane {
  return {
    slot_number: 2,
    running: null,
    waiting: [waiting(1, "lg-1"), waiting(2, "lg-2")],
    attention_total: 0,
    ...overrides,
  };
}

const REGION = "Lane 2";
const Pane = queueLanePrimitive.Component;

function renderPane(lanes: QueueLane[]) {
  mockLanes.mockReturnValue({ lanes, isLoading: false } as ReturnType<typeof useQueueLanes>);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  // No `PaneSizeContext` provider, so `usePaneSize` hands back its `regular`
  // stand-in — the tier the waiting list renders at.
  return render(<Pane containerId="pane-1" settings={{ slot: 2 }} />, { wrapper });
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("the lane pane's keyboard path", () => {
  it("is a named tab stop holding the waiting list — AC3", async () => {
    renderPane([lane()]);

    const pane = screen.getByRole("group", { name: REGION });
    expect(pane).toHaveAttribute("tabindex", "0");
    expect(pane).toHaveClass("scroll-focus-ring");

    // Nothing in the pane is interactive and the primitive is rendered alone,
    // so the pane itself is the only tab stop — which is the whole point: Tab
    // from the document now reaches it, and the arrow keys scroll it.
    expect(within(pane).getByRole("list", { name: "Waiting in this lane" })).toBeInTheDocument();
    await userEvent.tab();
    expect(pane).toHaveFocus();
  });

  it("keeps its head line, so the named region is never empty — AC8", () => {
    // A lane with nothing running and nobody waiting still says both of those
    // things, so there is no state in which the region announces a name with
    // nothing behind it. The waiting list itself is what disappears.
    renderPane([lane({ waiting: [], running: null })]);

    const pane = screen.getByRole("group", { name: REGION });
    expect(pane).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Waiting in this lane" })).toBeNull();
    expect(within(pane).getByText("Nothing is running in this lane.")).toBeInTheDocument();
    expect(within(pane).getByText("0 waiting")).toBeInTheDocument();
  });

  it("draws no pane at all when the lane is not in the pool — AC8", () => {
    renderPane([lane({ slot_number: 1 })]);

    // An unconfigured or missing lane is one line saying which, in the pane's
    // place, rather than a labelled box over nothing.
    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
    expect(screen.getByText("Lane 2 is not in the pool.")).toBeInTheDocument();
  });
});
