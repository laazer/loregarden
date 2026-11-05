/**
 * lg-milestone-that-2187 R1 (AC1, AC4, AC5, UX1(b), UX2, UX3) — a failed 30s
 * poll over a board already on screen.
 *
 * The board polls `api.tickets` every 30 seconds and renders its error branch
 * above `KanbanColumns`. query-core keeps `data` and sets `status: "error"`
 * when a *refetch* fails over data already present, so one transient failure
 * replaced 209 tickets with an error line and the next successful poll brought
 * them back: nothing was lost, and the only view of the initiative's work was
 * thrown away anyway. Because `queryClient.ts:28-31` suppresses the query toast
 * whenever `data !== undefined`, the inline notice this adds is the ONLY report
 * of the failed read.
 *
 * Every test here fails the SECOND read. That is the only way to reach the
 * data-present regime, and each asserts the notice AND the absence of the pane
 * (or the reverse), because either half alone passes under an implementation
 * that got the regime backwards.
 *
 * Two of these pin decisions no criterion originally named, and both are load
 * bearing:
 *  - R1-2/R1-3: `:186` and `:190` gate the work-item count and the WHOLE
 *    selection toolbar on `board.isSuccess`, which is FALSE on a failed refetch
 *    over good data. The `:243` predicate fix alone therefore keeps the tickets
 *    on screen and removes the only thing the operator can do with them, so
 *    AC1's promise that "the selection survives" is false without them
 *    (spec D6).
 *  - R1-8: the content test is `!board.data?.length`, what the READ produced,
 *    never `!workItems.length`, the filtered projection. Under the filtered
 *    reading a failed poll replaces a legitimate "Nothing matches these
 *    filters." view and its Clear-filters button with the full role="alert"
 *    first-load pane, destroying a view that had nothing wrong with it
 *    (spec D9; AC1 over UX2(a)).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../../api/client";
import { ApiError } from "../../../../api/http";
import type { MilestoneSchedule, PlanNode } from "../../../../api/initiativeApi";
import type { TicketState, TicketSummary } from "../../../../api/types";
import { boardQueryKey } from "../../../../lib/initiativeBoard";
import { InitiativeBoard } from "../InitiativeBoard";

jest.mock("../../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

const INITIATIVE = "init1";
/** Two milestones, so a filter can be pointed at one that holds nothing (R1-8). */
const FULL_MILESTONE = "m-full";
const EMPTY_MILESTONE = "m-empty";

function milestone(id: string, title: string): MilestoneSchedule {
  return {
    id,
    external_id: `lg-${id}`,
    title,
    state: "backlog",
    workspace_slug: "loregarden",
    work_item_type: "milestone",
    member: false,
    plan_order: 0,
    target_date: null,
    forecast_date: null,
    planned_date: null,
    drift_days: null,
    status: "unscheduled",
    basis: "agent_time",
    remaining: 0,
    total: 0,
    counts: {},
    assumed: 0,
  } as unknown as MilestoneSchedule;
}

const MILESTONES = [milestone(FULL_MILESTONE, "Card slice"), milestone(EMPTY_MILESTONE, "Public beta")];

function ticket(id: string, title: string, state: TicketState = "backlog"): TicketSummary {
  return {
    id,
    external_id: `lg-board-${id}`,
    title,
    state,
    priority: 3,
    workspace_slug: "loregarden",
    work_item_type: "feature",
    // Under the full milestone, so `milestoneOf` maps it and the filter bites.
    parent_ticket_id: FULL_MILESTONE,
    blocked_on: null,
    waiting_on: [],
    stages: [],
  } as unknown as TicketSummary;
}

/** The milestone rows come back on the board read too; they are filtered out of the work items. */
function milestoneRow(id: string): TicketSummary {
  return {
    ...ticket(id, "a milestone"),
    work_item_type: "milestone",
    parent_ticket_id: INITIATIVE,
  } as unknown as TicketSummary;
}

const WORK_ITEMS = [
  ticket("t1", "Reshape the card renderer"),
  ticket("t2", "Wire the deck importer", "in_progress"),
];
const BOARD_READ = [milestoneRow(FULL_MILESTONE), ...WORK_ITEMS];

function node(id: string): PlanNode {
  return {
    id,
    external_id: `lg-board-${id}`,
    title: "node",
    workspace_slug: "loregarden",
    state: "backlog",
    status: "ready",
    lane: "lane-0",
    milestone_id: FULL_MILESTONE,
    step: 1,
    deps: [],
    waiting_on: [],
    start: null,
    finish: null,
    duration_days: 1,
    basis: "agent_time",
    assumed: false,
    critical: false,
    external: false,
  } as unknown as PlanNode;
}

function renderBoard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <InitiativeBoard
          initiativeId={INITIATIVE}
          milestones={MILESTONES}
          nodes={WORK_ITEMS.map((t) => node(t.id))}
          onNeedsPerson={jest.fn().mockResolvedValue(undefined)}
          onStart={jest.fn().mockResolvedValue({})}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return {
    ...result,
    /**
     * The 30s background poll, with no timer. `refetchInterval: 30_000` fires
     * exactly this call when it elapses, so a test that needs a second read on
     * a surface whose only control is the first-load pane's Retry asks for one
     * directly rather than driving jest's clock through react-query and RTL.
     */
    poll: () =>
      act(async () => {
        await client.refetchQueries({ queryKey: boardQueryKey(INITIATIVE) });
        // query-core writes the error into the cache synchronously, but the
        // subscribed component's render lands a tick later, so a poll that
        // returned as soon as the refetch settled would let a SYNCHRONOUS
        // assertion read the pre-failure DOM and pass under an implementation
        // that never renders the notice at all. Flushing a macrotask inside
        // `act` makes the poll deterministic, which is what lets the negative
        // assertions below ("no alert", "no empty state") be trustworthy.
        await new Promise((resolve) => setTimeout(resolve, 0));
      }),
    /** The same poll, started but not awaited — for a read the test settles itself. */
    startPoll: () => {
      let started: Promise<unknown> | undefined;
      act(() => {
        started = client.refetchQueries({ queryKey: boardQueryKey(INITIATIVE) }).catch(() => {});
      });
      return started as Promise<unknown>;
    },
  };
}

/** A promise the test settles, so an in-flight state can be asserted on. */
function deferred<T>() {
  let settle!: { resolve: (value: T) => void; reject: (error: unknown) => void };
  const promise = new Promise<T>((resolve, reject) => {
    settle = { resolve, reject };
  });
  return { promise, ...settle };
}

/**
 * Make every read from here on reject.
 *
 * Written as a switch after the first read rather than as `mockResolvedValueOnce`
 * + `mockRejectedValue`, because react-query issues more than one read on mount
 * and a `…Once` queue is silently consumed by the extra one — which fails the
 * test at its `findBy` for the first read, not at the assertion it is about.
 */
function failNextRead() {
  mockApi.tickets.mockRejectedValue(new Error(REASON));
}

const REASON = "the tickets endpoint did not answer";
const LEAD = "These work items may be out of date — the last refresh failed.";

beforeEach(() => jest.clearAllMocks());

describe("a failed 30s poll over a board already on screen", () => {
  it("R1-1 — keeps the work items and reports the failed read as one polite notice", async () => {
    mockApi.tickets.mockResolvedValue(BOARD_READ);

    const { poll } = renderBoard();
    await screen.findByText("Reshape the card renderer");
    failNextRead();

    await poll();

    // All three together: the tickets survived, the operator was told, and they
    // were told without the alert that replaces everything.
    expect(screen.getByText("Reshape the card renderer")).toBeInTheDocument();
    expect(screen.getByText("Wire the deck importer")).toBeInTheDocument();
    const notice = screen.getByTestId("board-refresh-failed");
    expect(screen.getAllByTestId("board-refresh-failed")).toHaveLength(1);
    expect(notice).toHaveAttribute("role", "status");
    expect(notice).toHaveTextContent(LEAD);
    expect(notice).toHaveTextContent(`${REASON}.`);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/The board could not be loaded/)).toBeNull();
  });

  it("R1-2 — keeps the selection toolbar present and its actions enabled (UX1(b))", async () => {
    // `:190` gates the whole role="toolbar" on `board.isSuccess`, which a
    // failed refetch over good data makes FALSE. Without the content-based
    // condition this fix keeps the tickets and removes the only thing the
    // operator can do with them.
    mockApi.tickets.mockResolvedValue(BOARD_READ);

    const { poll } = renderBoard();
    await screen.findByText("Reshape the card renderer");
    await userEvent.click(screen.getByLabelText("Select Reshape the card renderer"));

    failNextRead();

    await poll();

    const toolbar = screen.getByRole("toolbar", { name: "Selected tickets" });
    expect(within(toolbar).getByRole("button", { name: "Start now" })).toBeEnabled();
    expect(within(toolbar).getByRole("button", { name: "Mark for a person" })).toBeEnabled();
    expect(within(toolbar).getByRole("combobox", { name: "Move the selected tickets to" })).toBeEnabled();
    // `:186` gates the count the same way: it must still show its number, not "".
    expect(screen.getByText("2 work items")).toBeInTheDocument();
  });

  it("R1-3 — keeps the milestone filter and the ticked ticket across the failed read", async () => {
    // `milestoneFilter` and `selected` are useState INSIDE this component, so
    // this assertion is really "the board was not unmounted". AC1 names both.
    mockApi.tickets.mockResolvedValue(BOARD_READ);

    const { poll } = renderBoard();
    await screen.findByText("Reshape the card renderer");
    const filter = screen.getByRole("combobox", { name: "Filter the board by milestone" });
    await userEvent.selectOptions(filter, FULL_MILESTONE);
    await userEvent.click(screen.getByLabelText("Select Reshape the card renderer"));
    expect(screen.getByText("1 selected")).toBeInTheDocument();

    failNextRead();

    await poll();

    expect(screen.getByRole("combobox", { name: "Filter the board by milestone" })).toHaveValue(
      FULL_MILESTONE,
    );
    expect(screen.getByLabelText("Select Reshape the card renderer")).toBeChecked();
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("R1-4 — leaves focus exactly where the operator left it (UX3(d))", async () => {
    // The notice appears on a timer. Any focus() or focus-restoring effect in
    // this path moves a keyboard operator's cursor every 30 seconds without
    // them touching anything — lg-milestone-that-1464's defect.
    mockApi.tickets.mockResolvedValue(BOARD_READ);

    const { poll } = renderBoard();
    await screen.findByText("Reshape the card renderer");
    const filter = screen.getByRole("combobox", { name: "Filter the board by milestone" });
    filter.focus();
    const before = document.activeElement;

    failNextRead();

    await poll();

    expect(screen.getByTestId("board-refresh-failed")).toBeInTheDocument();
    expect(document.activeElement).toBe(before);
  });

  it("R1-8 — keeps an empty-filter view rather than replacing it with the error pane (D9)", async () => {
    // The case AC1 and UX2(a) disagreed about, settled toward AC1. The read
    // produced two rows; the operator filtered to a milestone holding none.
    // Under `!workItems.length` the failed poll blows away a legitimate view
    // that had nothing wrong with it.
    mockApi.tickets.mockResolvedValue(BOARD_READ);

    const { poll } = renderBoard();
    await screen.findByText("Reshape the card renderer");
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Filter the board by milestone" }),
      EMPTY_MILESTONE,
    );
    expect(await screen.findByText("Nothing matches these filters.")).toBeInTheDocument();

    failNextRead();

    await poll();

    expect(screen.getByText("Nothing matches these filters.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
    expect(screen.getByTestId("board-refresh-failed")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("UX2(e) — the notice sits between the filters and the toolbar, displacing nothing", async () => {
    mockApi.tickets.mockResolvedValue(BOARD_READ);

    const { container, poll } = renderBoard();
    await screen.findByText("Reshape the card renderer");

    failNextRead();

    await poll();

    const notice = screen.getByTestId("board-refresh-failed");
    const filters = container.querySelector(".plan-board-filters");
    if (!filters) throw new Error("the board rendered no .plan-board-filters element");
    const toolbar = screen.getByRole("toolbar", { name: "Selected tickets" });
    expect(filters.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(notice.compareDocumentPosition(toolbar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("a read that had nothing to lose", () => {
  it("R1-7 — takes the full error pane when the first read succeeded with zero rows", async () => {
    // UX2(a)'s stated reason, honoured exactly by `!board.data?.length`: a read
    // that SUCCEEDED with zero rows and then failed also has nothing to
    // protect, and an empty state rendered over a failure asserts a claim the
    // surface cannot make.
    mockApi.tickets.mockResolvedValue([]);

    const { poll } = renderBoard();
    await screen.findByText(/No work items under this initiative's milestones yet/);

    failNextRead();

    await poll();

    const pane = screen.getByRole("alert");
    expect(pane).toHaveTextContent("The board could not be loaded");
    expect(pane).toHaveTextContent(REASON);
    expect(screen.queryByText(/No work items under this initiative's milestones yet/)).toBeNull();
    expect(screen.queryByTestId("board-refresh-failed")).toBeNull();
  });

  it("R1-5 — a first-load failure keeps today's pane, copy and Retry", async () => {
    mockApi.tickets.mockRejectedValue(new ApiError(502, REASON));

    renderBoard();

    const pane = await screen.findByRole("alert");
    expect(pane).toHaveTextContent("The board could not be loaded");
    expect(pane).toHaveTextContent(REASON);
    expect(within(pane).getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByTestId("board-refresh-failed")).toBeNull();
  });

  it("R1-5 — Retry does not unmount the pane into the skeleton, and the reason stays on screen", async () => {
    // AC4. query-core clears `error` and puts a failed-with-no-data query back
    // to `status: "pending"` the moment `refetch()` is called, so a branch
    // guarded on raw `isError` drops the operator into the loading skeleton
    // and takes the reason they were retrying with it
    // (lg-milestone-that-1435's defect).
    mockApi.tickets.mockRejectedValue(new Error(REASON));

    renderBoard();
    const pane = await screen.findByRole("alert");

    // The retry is held in flight, which is the only state the defect is
    // visible in: the hold has to outlive `refetch()`'s return to pending.
    const second = deferred<TicketSummary[]>();
    mockApi.tickets.mockReturnValue(second.promise);
    await userEvent.click(within(pane).getByRole("button", { name: "Retry" }));

    expect(screen.getByRole("alert")).toHaveTextContent(REASON);
    expect(screen.queryByLabelText("Loading the board")).toBeNull();

    await act(async () => {
      second.resolve(BOARD_READ);
    });
    expect(await screen.findByText("Reshape the card renderer")).toBeInTheDocument();
  });

  it("R1-6 — Retry's busy state is the operator's own click, never the background poll (UX3(c))", async () => {
    // Sibling lg-milestone-that-2196's defect, which must not be newly shipped
    // here. Guarding the pane on a held failure makes it survive its own
    // refetch, which makes this busy state reachable for the first time: keyed
    // to raw `isFetching` the one control on screen would go disabled and read
    // "Retrying…" every 30 seconds for the poll, dead at the moment it is
    // reached for.
    mockApi.tickets.mockRejectedValue(new Error(REASON));

    const { startPoll } = renderBoard();
    const pane = await screen.findByRole("alert");

    // The operator's own click: disabled, and saying so.
    const clicked = deferred<TicketSummary[]>();
    mockApi.tickets.mockReturnValue(clicked.promise);
    await userEvent.click(within(pane).getByRole("button", { name: "Retry" }));
    const busy = within(screen.getByRole("alert")).getByRole("button");
    expect(busy).toHaveTextContent("Retrying…");
    expect(busy).toBeDisabled();

    await act(async () => {
      clicked.reject(new Error(REASON));
    });
    await waitFor(() =>
      expect(within(screen.getByRole("alert")).getByRole("button", { name: "Retry" })).toBeEnabled(),
    );

    // A background refetch with no click: enabled, and still reading "Retry".
    const polled = deferred<TicketSummary[]>();
    mockApi.tickets.mockReturnValue(polled.promise);
    const inFlight = startPoll();
    const idle = within(screen.getByRole("alert")).getByRole("button");
    expect(idle).toHaveTextContent("Retry");
    expect(idle).toBeEnabled();

    await act(async () => {
      polled.reject(new Error(REASON));
    });
    await inFlight;
  });
});
