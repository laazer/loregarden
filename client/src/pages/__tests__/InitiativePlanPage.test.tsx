import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { api } from "../../api/client";
import { ApiError } from "../../api/http";
import type {
  InitiativePlan,
  MilestoneSchedule,
  NodeStatus,
  PlanNode,
  PlannerSnapshot,
} from "../../api/initiativeApi";
import type { TicketState, TicketSummary } from "../../api/types";
import { DEFAULT_RUNTIME } from "../../lib/runtimeSettings";
import { findUsabilityProblems } from "../../lib/usabilityCheck";
import { useTicketRefStore } from "../../state/ticketRefStore";
import { InitiativePlanPage, InitiativePlanRoute } from "../InitiativePlanPage";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

/**
 * The live initiative's shape, measured 2026-10-02 (read-only): 7 milestones
 * (phases) over 4 workspaces, 84 work items joined by 91 dependency edges, and
 * a critical path that starts with loremaker work outside the initiative.
 */
const LIVE_SHAPE: { slug: string; title: string; items: number; forecast: string | null }[] = [
  { slug: "tinkercg", title: "Card slice", items: 21, forecast: "2026-12-25" },
  { slug: "loregarden", title: "Carry the product", items: 14, forecast: "2026-10-08" },
  { slug: "tinkercg", title: "Private alpha", items: 21, forecast: "2027-01-22" },
  { slug: "tinkercg", title: "Public beta", items: 8, forecast: "2027-01-08" },
  { slug: "tinkercg", title: "Commercial launch", items: 6, forecast: "2027-01-08" },
  { slug: "lore-eden", title: "What's new", items: 6, forecast: "2026-10-03" },
  { slug: "loremaker", title: "Deploy the product", items: 8, forecast: "2027-01-22" },
];

const NODE_STATUSES: NodeStatus[] = ["waiting", "waiting", "ready", "waiting", "running", "waiting"];
const TICKET_STATES: Record<NodeStatus, TicketState> = {
  waiting: "backlog",
  ready: "backlog",
  running: "in_progress",
  needs_person: "backlog",
  blocked: "blocked",
  done: "done",
};

function nodes(): PlanNode[] {
  const out: PlanNode[] = [];
  LIVE_SHAPE.forEach((m, i) => {
    for (let n = 0; n < m.items; n += 1) {
      const status: NodeStatus = i === 0 && n === 0 ? "needs_person" : NODE_STATUSES[n % NODE_STATUSES.length];
      out.push({
        id: `t${i}-${n}`,
        external_id: `${m.slug.slice(0, 3)}-t-${i}-${n}`,
        title: `${m.title} task ${n}`,
        workspace_slug: m.slug,
        state: TICKET_STATES[status],
        status,
        lane: `lane-${n % 5}`,
        milestone_id: `m${i}`,
        step: 1 + (n % 4),
        deps: [],
        waiting_on: status === "waiting" ? ["x"] : [],
        start: null,
        finish: "2026-11-01T00:00:00Z",
        duration_days: 1,
        basis: "agent_time",
        assumed: false,
        critical: n === 1,
        external: false,
      });
    }
  });
  out.push({
    ...out[1],
    id: "outside-1",
    external_id: "lmkr-deployment-primitives-23",
    title: "Deployment primitives",
    milestone_id: null,
    external: true,
    critical: true,
  });
  return out;
}

function milestones(): MilestoneSchedule[] {
  return LIVE_SHAPE.map((m, i) => ({
    id: `m${i}`,
    external_id: `${m.slug.slice(0, 3)}-m-${i}`,
    title: m.title,
    state: "backlog",
    workspace_slug: m.slug,
    work_item_type: "milestone",
    member: false,
    plan_order: i,
    target_date: i === 1 ? "2026-10-05" : null,
    forecast_date: m.forecast,
    planned_date: i === 1 ? "2026-10-05" : null,
    drift_days: i === 1 ? 3 : null,
    status: i === 1 ? "behind" : "unscheduled",
    basis: "agent_time",
    remaining: m.items,
    total: m.items,
    counts: { waiting: m.items - 2, ready: 2 },
    assumed: i === 0 ? 4 : 0,
  }));
}

function plan(overrides: Partial<InitiativePlan> = {}): InitiativePlan {
  return {
    id: "init1",
    external_id: "init-tinkercg-build-1",
    title: "TinkerCG: build and launch",
    description: "A physical card-game platform.",
    state: "backlog",
    mode: "fixed",
    notes: "",
    target_date: null,
    forecast_date: "2027-01-22",
    planned_date: null,
    drift_days: null,
    status: "unscheduled",
    unforecast_milestones: 0,
    milestones: milestones(),
    paces: [
      { workspace_slug: "lore-eden", per_day: null, completed: 0, basis: "none" },
      { workspace_slug: "loregarden", per_day: 2.33, completed: 49, basis: "workspace_throughput" },
      { workspace_slug: "loremaker", per_day: 0.14, completed: 3, basis: "workspace_throughput" },
      { workspace_slug: "tinkercg", per_day: null, completed: 0, basis: "none" },
    ],
    window_days: 21,
    pending_proposal: null,
    nodes: nodes(),
    critical_path: ["outside-1", "t0-1", "t2-1"],
    cyclic: [],
    lanes: ["lane-0", "lane-1", "lane-2", "lane-3", "lane-4"],
    autopilot: {
      enabled: false,
      max_parallel: 3,
      paused_reason: "",
      in_flight: 2,
      next_up: ["t1-2", "t5-2"],
      available: true,
      recent: [],
    },
    generated_at: "2026-10-01T12:00:00Z",
    ...overrides,
  };
}

function boardTickets(): TicketSummary[] {
  return nodes()
    .filter((n) => !n.external)
    .map(
      (n) =>
        ({
          id: n.id,
          external_id: n.external_id,
          title: n.title,
          state: n.state,
          priority: 3,
          workspace_slug: n.workspace_slug,
          work_item_type: "feature",
          parent_ticket_id: n.milestone_id,
          stages: [],
        }) as unknown as TicketSummary,
    );
}

const emptyChat: PlannerSnapshot = {
  initiative_id: "init1",
  messages: [],
  active_turn_id: null,
  runtime: DEFAULT_RUNTIME,
  workspace_slug: "loregarden",
};

function setNarrow(narrow: boolean) {
  window.matchMedia = jest.fn().mockImplementation((query: string) => ({
    matches: narrow && query.includes("max-width"),
    media: query,
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
  }));
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/initiatives/init1"]}>
        <Routes>
          <Route path="/initiatives/:initiativeId" element={<InitiativePlanPage initiativeId="init1" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function CurrentPath() {
  const location = useLocation();
  return <span data-testid="path">{location.pathname}</span>;
}

function renderRoute(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/initiatives/:initiativeId" element={<InitiativePlanRoute />} />
        </Routes>
        <CurrentPath />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const INIT_UUID = "6402cb6c-1d41-459f-af5e-8ad1c4dab5fb";
const INIT_REF = "init-tinkercg-build-1";

beforeEach(() => {
  jest.clearAllMocks();
  useTicketRefStore.setState({ uuidByRef: {}, refByUuid: {} });
  setNarrow(false);
  mockApi.initiativePlan.mockResolvedValue(plan());
  mockApi.initiative.mockResolvedValue({
    id: "init1",
    external_id: "init-tinkercg-build-1",
    title: "TinkerCG build",
    description: "",
    state: "in_progress",
    priority: 3,
    milestones: [],
    progress: { resolved: 0, total: 0 },
    workspaces: [],
  });
  mockApi.plannerChat.mockResolvedValue(emptyChat);
  mockApi.tickets.mockResolvedValue(boardTickets());
  mockApi.updateInitiativePlan.mockResolvedValue(plan());
  mockApi.chatTurnThinking.mockResolvedValue({ turn_id: "turn1", content: "", answer: "", activity: "", seq: 0 });
});

test("the schedule is one table in phase order, and the page passes the usability check", async () => {
  const { container } = renderPage();

  const table = await screen.findByRole("table", { name: "Milestones in phase order" });
  const rows = within(table).getAllByRole("rowheader");
  expect(rows.map((r) => within(r).getAllByRole("button")[0].textContent)).toEqual(
    milestones().map((m) => `${m.external_id} ${m.title}`),
  );
  expect(within(table).getByLabelText("4 items estimated without a measurement")).toBeInTheDocument();
  expect(findUsabilityProblems(container)).toEqual([]);
});

test("a target date says it is not saved until Enter, then that it is", async () => {
  const user = userEvent.setup();
  renderPage();

  const field = await screen.findByLabelText("Target date for Public beta");
  await user.type(field, "2026-12-15");
  expect(screen.getAllByText("Press Enter to save").length).toBeGreaterThan(0);
  await user.keyboard("{Enter}");

  await waitFor(() =>
    expect(mockApi.updateInitiativePlan).toHaveBeenCalledWith("init1", {
      targets: [{ ticket_id: "m3", target_date: "2026-12-15" }],
    }),
  );
  expect(await screen.findByText("Saved")).toBeInTheDocument();
});

test("reordering sends the whole phase order and no dates", async () => {
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Move Card slice later" }));

  expect(mockApi.updateInitiativePlan).toHaveBeenCalledWith("init1", {
    targets: [
      { ticket_id: "m1", plan_order: 0 },
      { ticket_id: "m0", plan_order: 1 },
      { ticket_id: "m2", plan_order: 2 },
      { ticket_id: "m3", plan_order: 3 },
      { ticket_id: "m4", plan_order: 4 },
      { ticket_id: "m5", plan_order: 5 },
      { ticket_id: "m6", plan_order: 6 },
    ],
  });
});

test("a pending proposal shows what moves and is accepted in one click", async () => {
  mockApi.initiativePlan.mockResolvedValue(
    plan({
      pending_proposal: {
        id: "p1",
        source: "draft",
        mode: "rolling",
        rationale: "Beta follows alpha by three weeks.",
        items: [{ ticket_id: "m3", target_date: "2026-12-15" }],
        created_at: "2026-10-01T12:00:00Z",
      },
    }),
  );
  mockApi.resolveScheduleProposal.mockResolvedValue(plan());
  const user = userEvent.setup();
  renderPage();

  const proposal = await screen.findByRole("region", { name: /Drafted schedule/ });
  expect(within(proposal).getByText("Beta follows alpha by three weeks.")).toBeInTheDocument();
  expect(within(proposal).getByRole("rowheader", { name: /Public beta/ })).toBeInTheDocument();
  await user.click(within(proposal).getByRole("button", { name: "Accept" }));

  await waitFor(() => expect(mockApi.resolveScheduleProposal).toHaveBeenCalledWith("init1", "p1", "accept"));
});

test("a drafted schedule opens on its timeline; the changed cells are one click away", async () => {
  mockApi.initiativePlan.mockResolvedValue(
    plan({
      pending_proposal: {
        id: "p1",
        source: "draft",
        mode: null,
        rationale: "",
        items: [{ ticket_id: "m3", target_date: "2026-12-15" }],
        created_at: "2026-10-01T12:00:00Z",
      },
    }),
  );
  const user = userEvent.setup();
  const { container } = renderPage();

  const proposal = await screen.findByRole("region", { name: /Drafted schedule/ });
  expect(within(proposal).getByRole("button", { name: "Timeline" })).toHaveAttribute("aria-pressed", "true");
  const timeline = within(proposal).getByRole("table", { name: "Proposed schedule in phase order" });
  // Every phase is drawn, not only the one that changed.
  expect(within(timeline).getAllByRole("rowheader")).toHaveLength(plan().milestones.length + 1);
  expect(findUsabilityProblems(container)).toEqual([]);

  await user.click(within(proposal).getByRole("button", { name: "Changes only" }));
  expect(within(proposal).queryByRole("table", { name: "Proposed schedule in phase order" })).not.toBeInTheDocument();
  expect(within(proposal).getByRole("columnheader", { name: "Proposed" })).toBeInTheDocument();
});

test("the board is reachable above a pending proposal, and the schedule tab says one is waiting", async () => {
  mockApi.initiativePlan.mockResolvedValue(
    plan({
      pending_proposal: {
        id: "p1",
        source: "draft",
        mode: null,
        rationale: "",
        items: [{ ticket_id: "m3", target_date: "2026-12-15" }],
        created_at: "2026-10-01T12:00:00Z",
      },
    }),
  );
  const user = userEvent.setup();
  renderPage();

  const proposal = await screen.findByRole("region", { name: /Drafted schedule/ });
  const boardTab = screen.getByRole("tab", { name: "Board" });
  expect(boardTab.compareDocumentPosition(proposal) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByRole("tab", { name: /Schedule.*proposal waiting/ })).toBeInTheDocument();

  await user.click(boardTab);
  expect(screen.queryByRole("region", { name: /Drafted schedule/ })).not.toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /proposal waiting/ })).toBeInTheDocument();
});

test("the autopilot panel says what waits on a person, what starts next, and what holds the date", async () => {
  mockApi.setAutopilot.mockResolvedValue(plan());
  mockApi.markNeedsPerson.mockResolvedValue(plan());
  const user = userEvent.setup();
  renderPage();

  const panel = await screen.findByRole("region", { name: "Autopilot" });
  expect(within(panel).getByText(/Waiting on a person \(1\)/)).toBeInTheDocument();
  expect(within(panel).getByText(/starts with work outside this initiative/)).toBeInTheDocument();

  await user.click(within(panel).getByRole("button", { name: "Agent can do it" }));
  await waitFor(() => expect(mockApi.markNeedsPerson).toHaveBeenCalledWith("init1", ["t0-0"], false));

  await user.click(within(panel).getByRole("button", { name: "Turn on" }));
  await waitFor(() => expect(mockApi.setAutopilot).toHaveBeenCalledWith("init1", { enabled: true }));
});

test("the parallel cap changes only on Update", async () => {
  mockApi.setAutopilot.mockResolvedValue(plan({ autopilot: { ...plan().autopilot, max_parallel: 8 } }));
  const user = userEvent.setup();
  renderPage();

  const panel = await screen.findByRole("region", { name: "Autopilot" });
  const update = within(panel).getByRole("button", { name: "Update" });
  expect(update).toBeDisabled();

  await user.selectOptions(within(panel).getByRole("combobox", { name: /Most tickets/ }), "8");
  expect(mockApi.setAutopilot).not.toHaveBeenCalled();

  await user.click(update);
  await waitFor(() => expect(mockApi.setAutopilot).toHaveBeenCalledWith("init1", { max_parallel: 8 }));
  await waitFor(() => expect(within(panel).getByRole("button", { name: "Update" })).toBeDisabled());
});

test("a paused autopilot says why", async () => {
  mockApi.initiativePlan.mockResolvedValue(
    plan({
      autopilot: {
        ...plan().autopilot,
        paused_reason: "3 tickets it started are blocked (tin-t-0-3)",
      },
    }),
  );
  renderPage();

  expect(await screen.findByText(/Stopped itself: 3 tickets it started are blocked/)).toBeInTheDocument();
});

test("the board acts on a selection from one toolbar, and caps long columns", async () => {
  mockApi.startInitiativeWork.mockResolvedValue({ "t0-2": "queued", "t0-1": "not ready: waiting" });
  const user = userEvent.setup();
  const { container } = renderPage();

  await user.click(await screen.findByRole("tab", { name: "Board" }));
  expect(await screen.findByText("84 work items")).toBeInTheDocument();
  // The toolbar is there before anything is ticked, so the checkboxes explain themselves.
  const idle = screen.getByRole("toolbar", { name: "Selected tickets" });
  expect(within(idle).getByText(/Tick tickets to move, start, or mark them/)).toBeInTheDocument();
  expect(within(idle).getByRole("button", { name: "Start now" })).toBeDisabled();
  expect(screen.getAllByRole("button", { name: /Show all/ }).length).toBeGreaterThan(0);
  expect(findUsabilityProblems(container)).toEqual([]);

  await user.click(screen.getByRole("checkbox", { name: "Select Card slice task 2" }));
  await user.click(screen.getByRole("checkbox", { name: "Select Card slice task 1" }));
  const toolbar = screen.getByRole("toolbar", { name: "Selected tickets" });
  expect(within(toolbar).getByText("2 selected")).toBeInTheDocument();

  await user.click(within(toolbar).getByRole("button", { name: "Start now" }));
  await waitFor(() => expect(mockApi.startInitiativeWork).toHaveBeenCalledWith("init1", ["t0-2", "t0-1"]));
});

test("Draft schedule asks the planner for a whole schedule straight away when there is none", async () => {
  const unscheduled = plan();
  mockApi.initiativePlan.mockResolvedValue({
    ...unscheduled,
    target_date: null,
    milestones: unscheduled.milestones.map((m) => ({ ...m, target_date: null })),
  });
  mockApi.sendPlannerMessage.mockResolvedValue({ ...emptyChat, active_turn_id: "turn1" });
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Draft schedule" }));

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await waitFor(() => expect(mockApi.sendPlannerMessage).toHaveBeenCalledWith("init1", "", "draft"));
});

test("with a schedule set, Draft schedule warns what it replaces before asking", async () => {
  mockApi.sendPlannerMessage.mockResolvedValue({ ...emptyChat, active_turn_id: "turn1" });
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Draft schedule" }));
  let dialog = screen.getByRole("dialog", { name: "Replace the current schedule?" });
  await user.click(within(dialog).getByRole("button", { name: "Keep the current schedule" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(mockApi.sendPlannerMessage).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Draft schedule" }));
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Draft schedule" }));
  dialog = screen.getByRole("dialog", { name: "Replace the current schedule?" });
  await user.click(within(dialog).getByRole("button", { name: "Draft a new schedule" }));
  await waitFor(() => expect(mockApi.sendPlannerMessage).toHaveBeenCalledWith("init1", "", "draft"));
});

test("pace mode drops targets and shows where the work will likely land", async () => {
  mockApi.initiativePlan.mockResolvedValue(plan({ mode: "pace", status: "paced" }));
  renderPage();

  const table = await screen.findByRole("table", { name: "Milestones in phase order" });
  expect(within(table).queryByRole("columnheader", { name: "Target" })).not.toBeInTheDocument();
  expect(within(table).getByRole("columnheader", { name: "Likely done" })).toBeInTheDocument();
  expect(screen.queryByLabelText(/^Target date for/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Pace" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("Projected")).toBeInTheDocument();
});

test("on a narrow screen the planner is a tab, not a panel below everything", async () => {
  setNarrow(true);
  const user = userEvent.setup();
  renderPage();

  expect(await screen.findByRole("tab", { name: "Planner" })).toBeInTheDocument();
  expect(screen.queryByRole("complementary", { name: "Planning agent" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Planner" }));
  expect(screen.getByRole("complementary", { name: "Planning agent" })).toBeInTheDocument();
});

test("with no milestones there is nothing to plan, and the page says how to get one", async () => {
  mockApi.initiativePlan.mockResolvedValue(
    plan({ milestones: [], nodes: [], critical_path: [], unforecast_milestones: 0, paces: [] }),
  );
  renderPage();

  expect(await screen.findByText(/has no milestones yet/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Draft schedule" })).toBeDisabled();
});

test("a deleted initiative is said to be gone, with a way back", async () => {
  mockApi.initiativePlan.mockRejectedValue(new ApiError(404, "Initiative not found: init1"));
  renderPage();

  expect(await screen.findByRole("alert")).toHaveTextContent("no longer exists");
  expect(screen.getByRole("button", { name: "Back to initiatives" })).toBeInTheDocument();
});

test("a shareable initiative id stays in the address bar, and the plan is fetched by UUID", async () => {
  mockApi.ticket.mockResolvedValue({ id: INIT_UUID, external_id: INIT_REF } as Awaited<ReturnType<typeof api.ticket>>);

  renderRoute(`/initiatives/${INIT_REF}`);

  expect(await screen.findByRole("table", { name: "Milestones in phase order" })).toBeInTheDocument();
  expect(mockApi.ticket).toHaveBeenCalledWith(INIT_REF);
  expect(mockApi.initiativePlan).toHaveBeenCalledWith(INIT_UUID);
  expect(mockApi.initiativePlan).not.toHaveBeenCalledWith(INIT_REF);
  expect(screen.getByTestId("path")).toHaveTextContent(`/initiatives/${INIT_REF}`);
});

test("a UUID initiative address is swapped for the shareable id", async () => {
  mockApi.ticket.mockResolvedValue({ id: INIT_UUID, external_id: INIT_REF } as Awaited<ReturnType<typeof api.ticket>>);

  renderRoute(`/initiatives/${INIT_UUID}`);

  await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent(`/initiatives/${INIT_REF}`));
  expect(await screen.findByRole("table", { name: "Milestones in phase order" })).toBeInTheDocument();
  expect(mockApi.initiativePlan).toHaveBeenCalledWith(INIT_UUID);
});
