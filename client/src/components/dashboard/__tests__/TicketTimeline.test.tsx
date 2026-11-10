import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../api/client";
import type { TicketDetail } from "../../../api/client";
import type { CapacityWait, LedgerAttempt, LedgerVisit, TicketArtifactItem, TicketLedger, TicketTreeNode } from "../../../api/types";
import { stageFanoutApi } from "../../../lib/stageFanoutApi";
import { navigateToTicketTab } from "../../../lib/useAppNavigation";
import { useReaderStore } from "../../../state/readerStore";
import { TicketTimeline } from "../TicketTimeline";

jest.mock("../../../api/client");
jest.mock("../../../lib/stageFanoutApi");
jest.mock("../../../lib/useAppNavigation", () => ({
  ...jest.requireActual("../../../lib/useAppNavigation"),
  navigateToTicketTab: jest.fn(),
}));

const mockApi = api as jest.Mocked<typeof api>;

function attempt(overrides: Partial<LedgerAttempt> = {}): LedgerAttempt {
  return {
    run_id: "r1",
    run_code: "run_a",
    agent_id: "backend_implementer",
    skill_name: "",
    status: "succeeded",
    started_at: "2026-09-20T10:00:00+00:00",
    finished_at: "2026-09-20T10:10:00+00:00",
    duration_seconds: 600,
    error_tail: "",
    ...overrides,
  };
}

function visit(stage_key: string, attempts: LedgerAttempt[], overrides: Partial<LedgerVisit> = {}): LedgerVisit {
  return { stage_key, visit_number: 1, status: attempts[attempts.length - 1].status, is_parallel: false, attempts, ...overrides };
}

function ledger(visits: LedgerVisit[], capacity_wait: CapacityWait | null = null): TicketLedger {
  return {
    visits,
    total_runs: visits.reduce((n, v) => n + v.attempts.length, 0),
    reworked_stages: [...new Set(visits.filter((v) => v.visit_number > 1).map((v) => v.stage_key))],
    total_seconds: 1800,
    // Null by default, not 0: a ticket with no lease rows waited an unknown
    // amount of time, and the summary must then omit the clause entirely.
    capacity_wait,
  };
}

function item(overrides: Partial<TicketArtifactItem>): TicketArtifactItem {
  return {
    id: overrides.title ?? "x",
    kind: "handoff",
    title: "",
    run_id: null,
    stage_key: null,
    system: false,
    evidence_kind: "",
    commit_sha: "",
    created_at: "2026-09-20T10:05:00",
    content_bytes: 10,
    content: {},
    ...overrides,
  };
}

function ticket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "t1",
    stages: [
      { key: "plan", name: "Planning" },
      { key: "implement", name: "Implementation" },
      { key: "gate", name: "Quality Gate" },
    ],
    blocking_issues: "",
    artifacts: {},
    ...overrides,
  } as unknown as TicketDetail;
}

const PLAN = visit("plan", [attempt({ run_id: "p1", agent_id: "planner" })]);
const IMPLEMENT = visit("implement", [
  attempt({ run_id: "i1", status: "failed", started_at: "2026-09-20T10:20:00+00:00", finished_at: "2026-09-20T10:25:00+00:00" }),
  attempt({ run_id: "i2", started_at: "2026-09-20T10:26:00+00:00", finished_at: "2026-09-20T10:40:00+00:00" }),
]);
const GATE = visit("gate", [
  attempt({ run_id: "g1", agent_id: "gatekeeper", started_at: "2026-09-20T10:50:00+00:00", finished_at: "2026-09-20T10:52:00+00:00" }),
]);

function renderTimeline({
  visits = [PLAN, IMPLEMENT, GATE],
  items = [] as TicketArtifactItem[],
  detail = ticket(),
  pendingApprovals = 0,
  childTickets = [] as TicketTreeNode[],
  onOpenRunLog = jest.fn(),
  onOpenTicket = jest.fn(),
  capacityWait = null as CapacityWait | null,
} = {}) {
  mockApi.ticketLedger.mockResolvedValue(ledger(visits, capacityWait));
  mockApi.ticketArtifacts.mockResolvedValue({ items, total: items.length });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    // The summary's capacity figure is a Link to Machine -> Capacity, so the
    // pane needs a router: a reader who sees the wait must be able to see who
    // is holding the line.
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TicketTimeline
          ticket={detail}
          isActive={false}
          pendingApprovals={pendingApprovals}
          childTickets={childTickets}
          onOpenRunLog={onOpenRunLog}
          onOpenTicket={onOpenTicket}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { onOpenRunLog, onOpenTicket };
}

function stageButton(name: RegExp) {
  return screen.getByRole("button", { name });
}

beforeEach(() => {
  jest.clearAllMocks();
  (stageFanoutApi.list as jest.Mock).mockResolvedValue({ groups: [], open_group_id: null });
  useReaderStore.getState().close();
});

it("lists every stage visit by its workflow name, most recent first", async () => {
  renderTimeline();

  await screen.findByRole("button", { name: /Planning/ });
  const heads = screen.getAllByRole("button").filter((button) => button.hasAttribute("aria-controls"));
  expect(heads.map((head) => head.querySelector(".tl-stage")?.textContent)).toEqual([
    "Quality Gate",
    "Implementation",
    "Planning",
  ]);
});

function visitRow(name: RegExp) {
  return stageButton(name).closest("li.tl-visit");
}

it("opens a stage whose latest run failed and the latest stage, and leaves green history closed", async () => {
  const failing = visit("implement", [
    attempt({ run_id: "i1", status: "succeeded", started_at: "2026-09-20T10:20:00+00:00" }),
    attempt({ run_id: "i2", status: "failed", started_at: "2026-09-20T10:26:00+00:00" }),
  ]);
  renderTimeline({ visits: [PLAN, failing, GATE] });

  await screen.findByRole("button", { name: /Planning/ });
  expect(stageButton(/Planning/)).toHaveAttribute("aria-expanded", "false");
  expect(stageButton(/Implementation/)).toHaveAttribute("aria-expanded", "true");
  expect(stageButton(/Quality Gate/)).toHaveAttribute("aria-expanded", "true");
  expect(visitRow(/Implementation/)).toHaveClass("tl-visit--bad");
});

it("paints a stage green once its latest run succeeds, keeping the failure as a tag", async () => {
  renderTimeline();

  await screen.findByRole("button", { name: /Planning/ });
  expect(visitRow(/Implementation/)).toHaveClass("tl-visit--good");
  expect(stageButton(/Implementation/)).toHaveTextContent("1 failed");
  expect(stageButton(/Implementation/)).toHaveAttribute("aria-expanded", "false");
});

it("paints a second pass green when its latest run succeeds", async () => {
  renderTimeline({ visits: [PLAN, visit("implement", [attempt({ run_id: "i3" })], { visit_number: 2 }), GATE] });

  await screen.findByRole("button", { name: /Planning/ });
  expect(visitRow(/Implementation/)).toHaveClass("tl-visit--good");
  expect(stageButton(/Implementation/)).toHaveTextContent("2nd pass");
});

it("toggles a stage open and closed", async () => {
  renderTimeline();
  const plan = await screen.findByRole("button", { name: /Planning/ });

  fireEvent.click(plan);
  expect(plan).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("button", { name: /Open the log of planner run run_a/ })).toBeInTheDocument();

  fireEvent.click(plan);
  expect(plan).toHaveAttribute("aria-expanded", "false");
});

it("shows a failed run's error under it and opens its log", async () => {
  const failing = visit("implement", [
    attempt({ run_id: "i1", status: "failed", error_tail: "line one\nAssertionError: boom" }),
    attempt({ run_id: "i2" }),
  ]);
  const { onOpenRunLog } = renderTimeline({ visits: [PLAN, failing, GATE] });

  fireEvent.click(await screen.findByRole("button", { name: /Implementation/ }));
  expect(await screen.findByText(/AssertionError: boom/)).toBeInTheDocument();
  const implementBody = document.getElementById(stageButton(/Implementation/).getAttribute("aria-controls")!)!;
  fireEvent.click(within(implementBody).getAllByRole("button", { name: /Open the log/ })[0]);
  expect(onOpenRunLog).toHaveBeenCalledWith("i1");
});

describe("a stage that failed more than once", () => {
  const LAUNCH = "Agent launch failed\ntmux: command too long";
  const failed = (run_id: string, error_tail = LAUNCH, duration_seconds = 2) =>
    attempt({ run_id, run_code: `run_${run_id}`, agent_id: "test_designer", status: "failed", error_tail, duration_seconds });
  const blockedOnDesign = ticket({
    state: "blocked",
    block_message: "Agent launch failed",
    stages: [{ key: "test_design", name: "Design tests" }],
  } as Partial<TicketDetail>);

  it("folds the earlier failures into one row and points the latest at the panel above", async () => {
    renderTimeline({
      visits: [visit("test_design", [failed("d1", LAUNCH, 1), failed("d2"), failed("d3"), failed("d4")])],
      detail: blockedOnDesign,
    });

    const stage = await screen.findByRole("button", { name: /Design tests/ });
    const body = document.getElementById(stage.getAttribute("aria-controls")!)!;
    const fold = within(body).getByRole("button", { name: /Attempts 1–3 · same error/ });
    expect(fold).toHaveTextContent("failed ×3");
    expect(fold).toHaveTextContent("1s–2s");
    expect(fold).toHaveAttribute("aria-expanded", "false");
    expect(within(body).getByText(/output shown above/)).toBeInTheDocument();
    // The latest attempt's output is in the panel, the folded ones are closed: no excerpt yet.
    expect(within(body).queryByText(/command too long/)).not.toBeInTheDocument();
    expect(within(body).getAllByRole("button", { name: /Open the log/ })).toHaveLength(1);
  });

  it("opens the folded attempts with their shared error once and a log for each", async () => {
    const onOpenRunLog = jest.fn();
    renderTimeline({
      visits: [visit("test_design", [failed("d1"), failed("d2"), failed("d3"), failed("d4")])],
      detail: blockedOnDesign,
      onOpenRunLog,
    });

    const stage = await screen.findByRole("button", { name: /Design tests/ });
    const body = document.getElementById(stage.getAttribute("aria-controls")!)!;
    fireEvent.click(within(body).getByRole("button", { name: /Attempts 1–3/ }));

    expect(within(body).getAllByText(/command too long/)).toHaveLength(1);
    const logs = within(body).getAllByRole("button", { name: /Open the log/ });
    expect(logs).toHaveLength(4);
    fireEvent.click(logs[1]);
    expect(onOpenRunLog).toHaveBeenCalledWith("d2");
  });

  it("does not claim the same error when the attempts failed differently", async () => {
    renderTimeline({
      visits: [visit("test_design", [failed("d1", "timeout"), failed("d2", "exit 1"), attempt({ run_id: "d3" })])],
    });

    // The latest stage, so it is open already.
    const stage = await screen.findByRole("button", { name: /test_design/ });
    const body = document.getElementById(stage.getAttribute("aria-controls")!)!;
    const fold = within(body).getByRole("button", { name: /Attempts 1–2 · each failed/ });
    fireEvent.click(fold);
    expect(within(body).getByText("timeout")).toBeInTheDocument();
    expect(within(body).getByText("exit 1")).toBeInTheDocument();
  });
});

it("puts each output under the stage that was running and opens it in the reader", async () => {
  renderTimeline({
    items: [
      item({ title: "Plan — rollout", kind: "plan", created_at: "2026-09-20T10:05:00" }),
      item({ title: "Stage report — gate", kind: "context", created_at: "2026-09-20T10:51:00", content: { stage_key: "gate", status: "pass", confidence: 0.91 } }),
      item({ title: "Run context", kind: "context", system: true, created_at: "2026-09-20T10:51:00" }),
    ],
  });

  const gate = await screen.findByRole("button", { name: /Quality Gate/ });
  const gateBody = document.getElementById(gate.getAttribute("aria-controls")!)!;
  expect(within(gateBody).getByText("Stage report — gate")).toBeInTheDocument();
  expect(within(gateBody).getByText("pass 0.91")).toBeInTheDocument();
  expect(screen.queryByText("Run context")).not.toBeInTheDocument();
  expect(gate).toHaveTextContent("1 output");

  fireEvent.click(within(gateBody).getByRole("button", { name: /Stage report — gate/ }));
  expect(useReaderStore.getState().document?.title).toBe("Stage report — gate");
});

it("groups automatic retries by stage and shows the latest cause", async () => {
  renderTimeline({
    detail: ticket({
      artifacts: {
        transient_retries: [
          { stage_key: "implement", message: "CLI login lost", reason: "infrastructure", at: "" },
          { stage_key: "implement", message: "Lease reaped", reason: "infrastructure", at: "" },
        ],
      },
    } as Partial<TicketDetail>),
  });

  const retries = await screen.findByRole("region", { name: "Automatic retries" });
  expect(retries).toHaveTextContent("implement · 2 retries");
  expect(retries).toHaveTextContent("Lease reaped");
  expect(retries).not.toHaveTextContent("CLI login lost");
});

it("sends the operator to approvals that are waiting", async () => {
  renderTimeline({ pendingApprovals: 2 });

  const waiting = await screen.findByRole("region", { name: "Approvals waiting" });
  expect(waiting).toHaveTextContent("2 approvals waiting on you");
  fireEvent.click(within(waiting).getByRole("button", { name: "Review" }));
  expect(navigateToTicketTab).toHaveBeenCalledWith("t1", "approvals");
});

it("says how to start when nothing has run", async () => {
  renderTimeline({ visits: [] });

  expect(await screen.findByText("Nothing has run for this ticket yet")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Blocking issue" })).not.toBeInTheDocument();
});

it("names what failed to load and retries it", async () => {
  mockApi.ticketLedger.mockRejectedValue(new Error("ledger down"));
  mockApi.ticketArtifacts.mockResolvedValue({ items: [], total: 0 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TicketTimeline
        ticket={ticket()}
        isActive={false}
        pendingApprovals={0}
        childTickets={[]}
        onOpenRunLog={jest.fn()}
        onOpenTicket={jest.fn()}
      />
    </QueryClientProvider>,
  );

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("run ledger");
  mockApi.ticketLedger.mockResolvedValue(ledger([PLAN]));
  fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("button", { name: /Planning/ })).toBeInTheDocument();
});

function renderWithFeed(feed: Promise<{ items: TicketArtifactItem[]; total: number }>) {
  mockApi.ticketLedger.mockResolvedValue(ledger([PLAN, IMPLEMENT]));
  mockApi.ticketArtifacts.mockReturnValue(feed);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TicketTimeline
        ticket={ticket()}
        isActive={false}
        pendingApprovals={0}
        childTickets={[]}
        onOpenRunLog={jest.fn()}
        onOpenTicket={jest.fn()}
      />
    </QueryClientProvider>,
  );
}

it("shows the stages from the ledger while the output feed is still loading", async () => {
  renderWithFeed(new Promise(() => {}));

  expect(await screen.findByRole("button", { name: /Implementation/ })).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Loading what each stage produced");
});

it("keeps the stages when the output feed fails, and retries the feed", async () => {
  renderWithFeed(Promise.reject(new Error("feed down")));

  expect(await screen.findByRole("button", { name: /Planning/ })).toBeInTheDocument();
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("feed down");

  mockApi.ticketArtifacts.mockResolvedValue({ items: [], total: 0 });
  fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

it("waits for the feed before saying nothing has run", async () => {
  mockApi.ticketLedger.mockResolvedValue(ledger([]));
  mockApi.ticketArtifacts.mockReturnValue(new Promise(() => {}));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TicketTimeline
        ticket={ticket()}
        isActive={false}
        pendingApprovals={0}
        childTickets={[]}
        onOpenRunLog={jest.fn()}
        onOpenTicket={jest.fn()}
      />
    </QueryClientProvider>,
  );

  // Let the ledger settle, so the skeleton is held by the feed and not by it.
  await act(async () => {});
  expect(mockApi.ticketLedger).toHaveBeenCalled();
  expect(screen.getByText("Loading timeline…")).toBeInTheDocument();
  expect(screen.queryByText("Nothing has run for this ticket yet")).not.toBeInTheDocument();
});

it("tails one live log for a running fan-out, not one per lane", async () => {
  mockApi.runLog.mockReturnValue(new Promise(() => {}));
  const running = visit(
    "plan",
    [
      attempt({ run_id: "l1", agent_id: "planner", skill_name: "plan-risk", status: "running", finished_at: null }),
      attempt({ run_id: "l2", agent_id: "planner", skill_name: "plan-seams", status: "running", finished_at: null }),
    ],
    { is_parallel: true, status: "running" },
  );
  renderTimeline({ visits: [running] });

  expect(await screen.findByRole("button", { name: /Planning/ })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getAllByText("Loading log…")).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: /Open the log of planner/ })).toHaveLength(2);
});

function child(id: string, state: TicketTreeNode["state"], stage = "Triage"): TicketTreeNode {
  return {
    id,
    external_id: `lg-${id}`,
    title: `Child ${id}`,
    state,
    priority: 1,
    work_item_type: "feature",
    workflow_stage_name: stage,
    workflow_stage_status: "pending",
    child_count: 0,
    children: [],
  };
}

describe("a parent's timeline is where each child is", () => {
  const parent = ticket({ child_count: 4 } as Partial<TicketDetail>);
  const kids = [child("a", "done", "Done"), child("b", "in_progress", "Implement"), child("c", "backlog"), child("d", "blocked", "Verify")];
  const summaries = kids.map((kid) => ({ ...kid, workflow_stage_key: kid.workflow_stage_name.toLowerCase(), stages: [], activity: "idle" }));

  beforeEach(() => {
    mockApi.tickets.mockResolvedValue(summaries as never);
  });

  it("shows the stage board instead of saying nothing has run", async () => {
    renderTimeline({ visits: [], detail: parent, childTickets: kids });

    expect(await screen.findByRole("table", { name: "Stages of each child" })).toBeInTheDocument();
    expect(screen.getByText(/of 4/)).toHaveTextContent("1 of 4");
    expect(screen.queryByText("Nothing has run for this ticket yet")).not.toBeInTheDocument();
    expect(mockApi.tickets).toHaveBeenCalledWith({ parent_ticket_id: parent.id });
  });

  it("opens a child from its row", async () => {
    const { onOpenTicket } = renderTimeline({ visits: [], detail: parent, childTickets: kids });

    fireEvent.click(await screen.findByRole("button", { name: "Child b" }));
    expect(onOpenTicket).toHaveBeenCalledWith("b");
  });

  it("shows a skeleton while the children load, rather than claiming the parent is empty", async () => {
    mockApi.tickets.mockReturnValue(new Promise(() => {}));
    renderTimeline({ visits: [], detail: parent, childTickets: kids });

    expect(await screen.findByText("Loading the tickets under this one…")).toBeInTheDocument();
    expect(screen.queryByText("Nothing has run for this ticket yet")).not.toBeInTheDocument();
  });

  it("still shows an output from before any run beside the board", async () => {
    renderTimeline({ visits: [], detail: parent, childTickets: kids, items: [item({ title: "Integration diff", kind: "diff" })] });

    expect(await screen.findByRole("table", { name: "Stages of each child" })).toBeInTheDocument();
    expect(screen.getByText("Before the first run")).toBeInTheDocument();
  });

  it("shows the board first and then its own stages when it ran some before it got children", async () => {
    renderTimeline({ visits: [PLAN], detail: parent, childTickets: kids });

    expect(await screen.findByRole("table", { name: "Stages of each child" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "This ticket’s own stages" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Planning/ })).toBeInTheDocument();
  });
});


// ---- lg-machine-resource-1923 R12: how much of this was waiting for the machine ----
//
// Measured on lg-reference-sources-1912: of 57 minutes wall-clock, 8 was agent
// work and 29 was queueing. The summary already states the first half ("N runs ·
// Xm of agent time") and the second half had nowhere to land, so a reader could
// not make the comparison this ticket is about. It lands here rather than in a
// new panel: a new panel would need its own five states and its own reason to
// exist, and the operator already reads this line.

describe("what this ticket spent waiting for the machine", () => {
  const WAITED: CapacityWait = {
    total_seconds: 2944,
    lease_count: 2,
    reacquired_seconds: 0,
  };

  it("the summary states the capacity wait and links to the capacity board", async () => {
    // The figure IS the action: a wait a reader cannot act on ends at the text.
    // machinePath("capacity") is "/machine".
    renderTimeline({ capacityWait: WAITED });

    await screen.findByRole("button", { name: /Planning/ });
    const link = screen.getByRole("link", { name: /waiting for machine capacity/ });
    expect(link).toHaveAttribute("href", "/machine");
    expect(link).toHaveTextContent("49m 4s");
    // Formatted by the existing duration() helper, never raw seconds.
    expect(link).not.toHaveTextContent("2944");
  });

  it("a null capacity_wait omits the clause entirely", async () => {
    // Absent is not zero. The ledger is switched off, or this ticket predates
    // it; "0s waiting for machine capacity" would be a measurement nobody made.
    renderTimeline({ capacityWait: null });

    await screen.findByRole("button", { name: /Planning/ });
    expect(screen.queryByText(/waiting for machine capacity/)).not.toBeInTheDocument();
    expect(screen.queryByText(/no capacity wait/)).not.toBeInTheDocument();
    expect(screen.getByText(/of agent time/)).toBeInTheDocument();
  });

  it("a wait under five seconds reads as no capacity wait", async () => {
    // The floor is measured: one of 1912's five gate leases was granted in
    // 0.1s. Below it the honest statement is that there was no wait, which is
    // a different fact from not knowing.
    renderTimeline({ capacityWait: { total_seconds: 0.1, lease_count: 1, reacquired_seconds: 0 } });

    await screen.findByRole("button", { name: /Planning/ });
    expect(screen.getByText(/no capacity wait/)).toBeInTheDocument();
    expect(screen.queryByText(/0s waiting/)).not.toBeInTheDocument();
  });

  it("a visit preceded by a wait carries one quiet line", async () => {
    // One line under the stage name, in the existing quiet-visit styling — not
    // a panel, and not one line per lease: 30 leases sit on one live ticket.
    renderTimeline({
      visits: [
        visit("plan", [attempt({ run_id: "p1", agent_id: "planner" })], {
          capacity_wait_seconds: 384,
        }),
        visit("implement", [attempt({ run_id: "i1" })], { capacity_wait_seconds: null }),
      ],
      capacityWait: WAITED,
    });

    await screen.findByRole("button", { name: /Planning/ });
    expect(screen.getByText("waited 6m 24s for machine capacity")).toBeInTheDocument();
    // The visit whose wait was below the floor carries no line at all.
    expect(screen.getAllByText(/for machine capacity$/)).toHaveLength(1);
  });

  it("a wait that followed a park reads as re-acquired", async () => {
    // A re-acquire is the cost of a park a person had to resolve, and it is
    // the only place that cost is legible. "waited" and "re-acquired after"
    // are different events and must not read the same.
    renderTimeline({
      visits: [
        visit("plan", [attempt({ run_id: "p1", agent_id: "planner" })], {
          capacity_wait_seconds: 384,
          capacity_wait_after_park: true,
        }),
      ],
      capacityWait: { total_seconds: 384, lease_count: 2, reacquired_seconds: 384 },
    });

    await screen.findByRole("button", { name: /Planning/ });
    expect(screen.getByText("re-acquired after 6m 24s")).toBeInTheDocument();
    expect(screen.queryByText(/^waited 6m 24s/)).not.toBeInTheDocument();
  });

  it("shows the ledger's error instead of a stale figure, and nothing while it loads", async () => {
    // The wait arrives in the same payload as the stages, so it has no state of
    // its own: a failed read is the existing alert, and a pending one is the
    // existing skeleton with no placeholder number beside it.
    mockApi.ticketLedger.mockRejectedValue(new Error("database is locked"));
    mockApi.ticketArtifacts.mockResolvedValue({ items: [], total: 0 });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <TicketTimeline
            ticket={ticket()}
            isActive={false}
            pendingApprovals={0}
            childTickets={[]}
            onOpenRunLog={jest.fn()}
            onOpenTicket={jest.fn()}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load this ticket’s run ledger",
    );
    expect(screen.queryByText(/machine capacity/)).not.toBeInTheDocument();
  });
});

/**
 * `.tl-stderr` is a bounded `overflow: auto` error pane, at two element sites
 * (lg-milestone-that-2215). Neither held a tab stop: the `Log` button that
 * serves the attempt sits in the row ABOVE the pane, and the shared-error pane
 * in a folded group is reached from the fold toggle above it.
 */
describe("the error panes' keyboard paths", () => {
  it("makes an attempt's error pane a tab stop stepping back to its Log button — AC3", async () => {
    // Two attempts, because the LATEST failure's output goes in the panel
    // above rather than in an excerpt — so the pane only renders for a
    // failure that something came after.
    const failing = visit("implement", [
      attempt({ run_id: "i1", status: "failed", error_tail: "line one\nAssertionError: boom" }),
      attempt({ run_id: "i2" }),
    ]);
    renderTimeline({ visits: [PLAN, failing, GATE] });
    fireEvent.click(await screen.findByRole("button", { name: /Implementation/ }));

    const pane = await screen.findByRole("group", { name: "Attempt error output" });
    expect(pane).toHaveAttribute("tabindex", "0");
    expect(pane).toHaveClass("scroll-focus-ring");

    // `Log` PRECEDES the pane in its row, so Shift+Tab is the step that
    // exists. There is no control after the pane to Tab forward on to.
    pane.focus();
    await userEvent.tab({ shift: true });
    // The `Log` of the failed row specifically, scoped to the stage body: the
    // other stages render their own, and both attempts in this fixture share
    // an agent and a run code, so neither the name nor a global index alone
    // picks it out.
    const implementBody = document.getElementById(
      stageButton(/Implementation/).getAttribute("aria-controls")!,
    )!;
    expect(within(implementBody).getAllByRole("button", { name: /Open the log/ })[0]).toHaveFocus();
  });

  it("makes a folded group's shared error pane a tab stop — AC3", async () => {
    const LAUNCH_TAIL = "Agent launch failed\ntmux: command too long";
    const failedRun = (run_id: string) =>
      attempt({
        run_id,
        run_code: `run_${run_id}`,
        agent_id: "test_designer",
        status: "failed",
        error_tail: LAUNCH_TAIL,
        duration_seconds: 2,
      });
    renderTimeline({
      visits: [visit("test_design", [failedRun("d1"), failedRun("d2"), failedRun("d3"), failedRun("d4")])],
      detail: ticket({
        state: "blocked",
        block_message: "Agent launch failed",
        stages: [{ key: "test_design", name: "Design tests" }],
      } as Partial<TicketDetail>),
    });

    const stage = await screen.findByRole("button", { name: /Design tests/ });
    const body = document.getElementById(stage.getAttribute("aria-controls")!)!;
    fireEvent.click(within(body).getByRole("button", { name: /Attempts 1–3/ }));

    const pane = within(body).getByRole("group", { name: "Stage error output" });
    expect(pane).toHaveAttribute("tabindex", "0");
    expect(pane).toHaveClass("scroll-focus-ring");
    pane.focus();
    await userEvent.tab({ shift: true });
    expect(within(body).getByRole("button", { name: /Attempts 1–3/ })).toHaveFocus();
  });

  it("renders no labelled pane for a failure that wrote nothing — AC8", async () => {
    const silent = visit("implement", [
      attempt({ run_id: "i1", status: "failed", error_tail: "" }),
      attempt({ run_id: "i2" }),
    ]);
    renderTimeline({ visits: [PLAN, silent, GATE] });
    fireEvent.click(await screen.findByRole("button", { name: /Implementation/ }));

    // An empty `error_tail` is a run that failed without saying anything, and
    // a labelled box over nothing is the dead tab stop AC8 forbids. The `Log`
    // button stays, because it is the route to the whole thing.
    expect((await screen.findAllByRole("button", { name: /Open the log/ })).length).toBeGreaterThan(0);
    expect(screen.queryByRole("group", { name: "Attempt error output" })).toBeNull();
  });
});
