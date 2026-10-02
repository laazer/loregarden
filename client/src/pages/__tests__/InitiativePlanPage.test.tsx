import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../../api/client";
import { ApiError } from "../../api/http";
import type { InitiativePlan, MilestoneSchedule, PlannerSnapshot } from "../../api/initiativeApi";
import type { TicketState, TicketSummary } from "../../api/types";
import { findUsabilityProblems } from "../../lib/usabilityCheck";
import { InitiativePlanPage } from "../InitiativePlanPage";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

/**
 * The live initiative's shape, measured 2026-10-01 (read-only): 7 milestones over
 * 4 workspaces, 84 work items, and two workspaces with no completions in the
 * window — so no forecast.
 */
const LIVE_SHAPE: { slug: string; title: string; items: number; forecast: string | null }[] = [
  { slug: "tinkercg", title: "Card slice", items: 21, forecast: null },
  { slug: "loregarden", title: "Carry the product", items: 14, forecast: "2026-10-07" },
  { slug: "tinkercg", title: "Private alpha", items: 21, forecast: null },
  { slug: "tinkercg", title: "Public beta", items: 8, forecast: null },
  { slug: "tinkercg", title: "Commercial launch", items: 6, forecast: null },
  { slug: "lore-eden", title: "What's new", items: 6, forecast: null },
  { slug: "loremaker", title: "Deploy the product", items: 8, forecast: "2026-11-27" },
];

function milestones(): MilestoneSchedule[] {
  const order = new Map<string, number>();
  return LIVE_SHAPE.map((m, i) => {
    const position = order.get(m.slug) ?? 0;
    order.set(m.slug, position + 1);
    return {
      id: `m${i}`,
      external_id: `${m.slug.slice(0, 3)}-m-${i}`,
      title: m.title,
      state: "backlog",
      workspace_slug: m.slug,
      plan_order: position,
      target_date: i === 1 ? "2026-10-05" : null,
      forecast_date: m.forecast,
      earliest_date: "2026-10-02",
      planned_date: i === 1 ? "2026-10-05" : null,
      drift_days: i === 1 ? 2 : null,
      status: i === 1 ? "behind" : m.forecast ? "unscheduled" : "unscheduled",
      basis: m.forecast ? "workspace_throughput" : "none",
      remaining: m.items,
      total: m.items,
    };
  });
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
    forecast_date: null,
    planned_date: null,
    drift_days: null,
    status: "unscheduled",
    unforecast_milestones: 5,
    milestones: milestones(),
    paces: [
      { workspace_slug: "lore-eden", per_day: null, completed: 0, basis: "none" },
      { workspace_slug: "loregarden", per_day: 2.38, completed: 50, basis: "workspace_throughput" },
      { workspace_slug: "loremaker", per_day: 0.14, completed: 3, basis: "workspace_throughput" },
      { workspace_slug: "tinkercg", per_day: null, completed: 0, basis: "none" },
    ],
    window_days: 21,
    pending_proposal: null,
    generated_at: "2026-10-01T12:00:00Z",
    ...overrides,
  };
}

const STATES: TicketState[] = ["backlog", "backlog", "backlog", "in_progress", "done", "blocked"];

function boardTickets(): TicketSummary[] {
  const rows: TicketSummary[] = [];
  LIVE_SHAPE.forEach((m, i) => {
    for (let n = 0; n < m.items; n += 1) {
      rows.push({
        id: `t${i}-${n}`,
        external_id: `${m.slug.slice(0, 3)}-t-${i}-${n}`,
        title: `${m.title} task ${n}`,
        state: STATES[n % STATES.length],
        priority: 3,
        workspace_slug: m.slug,
        work_item_type: "task",
        parent_ticket_id: `m${i}`,
        stages: [],
      } as unknown as TicketSummary);
    }
  });
  return rows;
}

const emptyChat: PlannerSnapshot = { initiative_id: "init1", messages: [], active_turn_id: null };

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/initiatives/init1"]}>
        <Routes>
          <Route path="/initiatives/:initiativeId" element={<InitiativePlanPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.initiativePlan.mockResolvedValue(plan());
  mockApi.plannerChat.mockResolvedValue(emptyChat);
  mockApi.tickets.mockResolvedValue(boardTickets());
  mockApi.updateInitiativePlan.mockResolvedValue(plan());
  // A running turn streams its reasoning; nothing has arrived yet.
  mockApi.chatTurnThinking.mockResolvedValue({
    turn_id: "turn1",
    content: "",
    answer: "",
    activity: "",
    seq: 0,
  });
});

test("the schedule groups milestones by workspace and says why forecasts are missing", async () => {
  const { container } = renderPage();

  const tables = await screen.findAllByRole("table");
  expect(tables.map((t) => within(t).getByText(/./, { selector: "caption" }).textContent)).toEqual([
    "tinkercg",
    "loregarden",
    "lore-eden",
    "loremaker",
  ]);
  expect(screen.getByText(/5 open milestones have no forecast/)).toBeInTheDocument();
  expect(findUsabilityProblems(container)).toEqual([]);
});

test("a target date saves on blur, and only the date is sent", async () => {
  const user = userEvent.setup();
  renderPage();

  const field = await screen.findByLabelText("Target date for Public beta");
  await user.type(field, "2026-12-15");
  await user.tab();

  await waitFor(() =>
    expect(mockApi.updateInitiativePlan).toHaveBeenCalledWith("init1", {
      targets: [{ ticket_id: "m3", target_date: "2026-12-15" }],
    }),
  );
});

test("reordering sends the workspace's whole order and no dates", async () => {
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Move Card slice later" }));

  expect(mockApi.updateInitiativePlan).toHaveBeenCalledWith("init1", {
    targets: [
      { ticket_id: "m2", plan_order: 0 },
      { ticket_id: "m0", plan_order: 1 },
      { ticket_id: "m3", plan_order: 2 },
      { ticket_id: "m4", plan_order: 3 },
    ],
  });
  expect(screen.getByRole("button", { name: "Move Card slice earlier" })).toBeDisabled();
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

  await waitFor(() =>
    expect(mockApi.resolveScheduleProposal).toHaveBeenCalledWith("init1", "p1", "accept"),
  );
});

test("the board spans the subtree, filters by milestone, and moves a card by menu", async () => {
  mockApi.updateTicket.mockResolvedValue({} as never);
  const user = userEvent.setup();
  const { container } = renderPage();

  await user.click(await screen.findByRole("tab", { name: "Board" }));
  expect(mockApi.tickets).toHaveBeenCalledWith({ ancestor_ticket_id: "init1" });
  expect(await screen.findByText("84 work items")).toBeInTheDocument();
  expect(findUsabilityProblems(container)).toEqual([]);

  await user.selectOptions(screen.getByLabelText("Filter the board by milestone"), "m5");
  expect(screen.getByText("6 work items")).toBeInTheDocument();

  await user.selectOptions(screen.getByLabelText("Move What's new task 0 to another state"), "done");
  await waitFor(() => expect(mockApi.updateTicket).toHaveBeenCalledWith("t5-0", { state: "done" }));
});

test("Draft schedule asks the planner for a whole schedule", async () => {
  mockApi.sendPlannerMessage.mockResolvedValue({ ...emptyChat, active_turn_id: "turn1" });
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Draft schedule" }));

  await waitFor(() => expect(mockApi.sendPlannerMessage).toHaveBeenCalledWith("init1", "", "draft"));
});

test("with no milestones there is nothing to plan, and the page says how to get one", async () => {
  mockApi.initiativePlan.mockResolvedValue(plan({ milestones: [], unforecast_milestones: 0, paces: [] }));
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
