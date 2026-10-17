import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api } from "../../../api/client";
import type { TicketDetail } from "../../../api/client";
import type { LedgerAttempt, LedgerVisit, TicketArtifactItem, TicketLedger, TicketTreeNode } from "../../../api/types";
import { stageFanoutApi } from "../../../lib/stageFanoutApi";
import { navigateToTicketTab } from "../../../lib/useAppNavigation";
import { useReaderStore } from "../../../state/readerStore";
import { TicketTimeline, type TimelineRun } from "../TicketTimeline";

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
  runs = [] as TimelineRun[],
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
        runs={runs}
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

it("shows a failed run's stderr under it and opens its log", async () => {
  const { onOpenRunLog } = renderTimeline({
    runs: [{ id: "i1", run_code: "run_a", status: "failed", stderr: "line one\nAssertionError: boom" }],
  });

  fireEvent.click(await screen.findByRole("button", { name: /Implementation/ }));
  expect(await screen.findByText(/AssertionError: boom/)).toBeInTheDocument();
  const implementBody = document.getElementById(stageButton(/Implementation/).getAttribute("aria-controls")!)!;
  fireEvent.click(within(implementBody).getAllByRole("button", { name: /Open the log/ })[0]);
  expect(onOpenRunLog).toHaveBeenCalledWith("i1");
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

it("leads with the block and opens the failing run's log, resolving its run code", async () => {
  const { onOpenRunLog } = renderTimeline({
    detail: ticket({
      blocking_issues: "Gate failed: 2 commands",
      artifacts: { error: { message: "Gate failed: 2 commands", run_code: "run_zz", agent_id: "gatekeeper", stage_key: "gate", command: "" } },
    } as Partial<TicketDetail>),
    runs: [{ id: "g1", run_code: "run_zz", status: "failed" }],
  });

  const block = await screen.findByRole("region", { name: "Blocking issue" });
  expect(block).toHaveTextContent("Gate failed: 2 commands");
  fireEvent.click(within(block).getByRole("button", { name: "View log" }));
  expect(onOpenRunLog).toHaveBeenCalledWith("g1");
});

it("shows a long block's full text, not the pointer back to this tab", async () => {
  renderTimeline({
    detail: ticket({
      state: "blocked",
      blocking_issues: "Stage 'test-break' hit a blocking issue — see the Timeline tab for details.",
      block_message: "No failing test could be written: the module under test does not exist.",
    } as Partial<TicketDetail>),
  });

  const block = await screen.findByRole("region", { name: "Blocking issue" });
  expect(block).toHaveTextContent("the module under test does not exist");
  expect(block).not.toHaveTextContent("see the Timeline tab");
});

it("never calls a ticket blocked on the strength of an error artifact alone", async () => {
  renderTimeline({
    detail: ticket({
      state: "in_progress",
      artifacts: { error: { message: "Run exited 1", run_code: "run_zz", agent_id: "implementer", stage_key: "implement", command: "" } },
    } as Partial<TicketDetail>),
    runs: [{ id: "g1", run_code: "run_zz", status: "failed" }],
  });

  const failed = await screen.findByRole("region", { name: "Last run failed" });
  expect(failed).toHaveTextContent("Run exited 1");
  expect(screen.queryByRole("region", { name: "Blocking issue" })).toBeNull();
});

it("shows a blocked ticket as blocked even when no reason was recorded", async () => {
  renderTimeline({ detail: ticket({ state: "blocked" } as Partial<TicketDetail>) });

  const block = await screen.findByRole("region", { name: "Blocking issue" });
  expect(block).toHaveTextContent(/No reason was recorded/);
});

it("explains a parent blocked by its children rather than pointing at a run", async () => {
  renderTimeline({ detail: ticket({ state: "blocked", child_count: 4 } as Partial<TicketDetail>) });

  const block = await screen.findByRole("region", { name: "Blocking issue" });
  expect(block).toHaveTextContent(/work under it is blocked/);
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
        runs={[]}
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
        runs={[]}
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
        runs={[]}
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

describe("a parent that runs nothing itself", () => {
  const parent = ticket({ child_count: 4 } as Partial<TicketDetail>);
  const kids = [child("a", "done", "Done"), child("b", "in_progress", "Implement"), child("c", "backlog"), child("d", "blocked", "Verify")];

  it("lists the work under it by state instead of saying nothing has run", async () => {
    renderTimeline({ visits: [], detail: parent, childTickets: kids });

    expect(await screen.findByText(/1 of 4 done/)).toBeInTheDocument();
    expect(screen.queryByText("Nothing has run for this ticket yet")).not.toBeInTheDocument();
    const groups = within(screen.getByRole("region", { name: "Work under this ticket" })).getAllByRole("group");
    expect(groups.map((g) => g.querySelector("summary")?.textContent)).toEqual(["Blocked1", "In Progress1", "Done1", "Backlog1"]);
    // What needs the operator is open; the bulk (done, not started) waits behind its count.
    expect(groups.map((g) => g.hasAttribute("open"))).toEqual([true, true, false, false]);
  });

  it("opens a child from the list", async () => {
    const { onOpenTicket } = renderTimeline({ visits: [], detail: parent, childTickets: kids });

    fireEvent.click(await screen.findByRole("button", { name: /lg-b/ }));
    expect(onOpenTicket).toHaveBeenCalledWith("b");
  });

  it("waits for the board's tree rather than claiming the parent is empty", async () => {
    renderTimeline({ visits: [], detail: parent, childTickets: [] });

    expect(await screen.findByText("Loading the 4 tickets under this one…")).toBeInTheDocument();
    expect(screen.queryByText("Nothing has run for this ticket yet")).not.toBeInTheDocument();
  });

  it("still lists the work under it when it holds an output from before any run", async () => {
    renderTimeline({ visits: [], detail: parent, childTickets: kids, items: [item({ title: "Integration diff", kind: "diff" })] });

    expect(await screen.findByText(/1 of 4 done/)).toBeInTheDocument();
    expect(screen.getByText("Before the first run")).toBeInTheDocument();
    expect(screen.queryByText(/0 runs/)).not.toBeInTheDocument();
  });

  it("keeps its own visits when it has run something", async () => {
    renderTimeline({ visits: [PLAN], detail: parent, childTickets: kids });

    expect(await screen.findByRole("button", { name: /Planning/ })).toBeInTheDocument();
    expect(screen.queryByText(/of 4 done/)).not.toBeInTheDocument();
  });
});
