import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api } from "../../../api/client";
import type { TicketDetail } from "../../../api/client";
import type { LedgerAttempt, LedgerVisit, TicketArtifactItem, TicketLedger, TicketTreeNode } from "../../../api/types";
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

function ledger(visits: LedgerVisit[]): TicketLedger {
  return {
    visits,
    total_runs: visits.reduce((n, v) => n + v.attempts.length, 0),
    reworked_stages: [...new Set(visits.filter((v) => v.visit_number > 1).map((v) => v.stage_key))],
    total_seconds: 1800,
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
} = {}) {
  mockApi.ticketLedger.mockResolvedValue(ledger(visits));
  mockApi.ticketArtifacts.mockResolvedValue({ items, total: items.length });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TicketTimeline
        ticket={detail}
        isActive={false}
        pendingApprovals={pendingApprovals}
        childTickets={childTickets}
        onOpenRunLog={onOpenRunLog}
        onOpenTicket={onOpenTicket}
      />
    </QueryClientProvider>,
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

