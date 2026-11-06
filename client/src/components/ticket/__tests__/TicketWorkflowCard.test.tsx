import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../api/client";
import type { TicketDetail } from "../../../api/client";
import type { LedgerAttempt, LedgerVisit, WorkflowStageView } from "../../../api/types";
import type { TicketWork } from "../../../hooks/useTicketWork";
import { TicketWorkflowCard } from "../TicketWorkflowCard";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function stage(key: string, name: string, overrides: Partial<WorkflowStageView> = {}): WorkflowStageView {
  return {
    key,
    name,
    status: "pending",
    agent_id: `${key}_agent`,
    skill_name: "",
    optional: false,
    note: "",
    stage_type: "agent",
    agents: [],
    ...overrides,
  };
}

function attempt(run_id: string, status: string, started: string, finished: string): LedgerAttempt {
  return {
    run_id,
    run_code: `run_${run_id}`,
    agent_id: "test_designer",
    skill_name: "",
    status,
    started_at: started,
    finished_at: finished,
    duration_seconds: null,
    error_tail: "",
  };
}

const STAGES = [
  stage("spec", "Specification", { status: "done" }),
  stage("test_design", "Design tests", { status: "blocked", agent_id: "test_designer", skill_name: "tdd" }),
  stage("review", "Review", {
    stage_type: "parallel",
    agent_id: "",
    agents: [
      { agent_id: "a", skill_name: "" },
      { agent_id: "b", skill_name: "" },
      { agent_id: "c", skill_name: "" },
      { agent_id: "d", skill_name: "" },
    ],
  }),
];

const VISITS: LedgerVisit[] = [
  {
    stage_key: "spec",
    visit_number: 1,
    status: "succeeded",
    is_parallel: false,
    attempts: [attempt("s1", "succeeded", "2026-10-24T11:00:00+00:00", "2026-10-24T11:04:00+00:00")],
  },
  {
    stage_key: "test_design",
    visit_number: 1,
    status: "failed",
    is_parallel: false,
    attempts: ["d1", "d2", "d3", "d4"].map((id) => attempt(id, "failed", "2026-10-24T11:35:00+00:00", "2026-10-24T11:35:02+00:00")),
  },
];

function ticket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "t1",
    external_id: "lg-thing-1",
    state: "blocked",
    stages: STAGES,
    workflow_stage_key: "test_design",
    workflow_stage_status: "blocked",
    workflow_transitions: [{ from: "test_design", to: "spec", when: "reject" }],
    resolved_compatibility_posture: "internal",
    compatibility_posture_source: "workspace",
    orchestration_runtime: { cli_adapter: "default" },
    ...overrides,
  } as unknown as TicketDetail;
}

function work(): TicketWork {
  const idle = { isPending: false, mutate: jest.fn() };
  return {
    hasActiveRun: false,
    isStageRunning: () => false,
    workflowBusy: false,
    startRun: idle,
    routeWorkflow: idle,
    patchStageWorkflow: idle,
    setTicketTemplate: idle,
    setCompatibilityPosture: idle,
    copyTerminalStageKey: null,
    setCopyTerminalStageKey: jest.fn(),
    setRunConfirmStageKey: jest.fn(),
    setStateModalOpen: jest.fn(),
    setTicketModelModalOpen: jest.fn(),
    runtimeOptions: { data: { claude_models: [] } },
    workflowTemplates: { data: [{ slug: "tdd", name: "TDD", stage_count: 3 }] },
    workspaceWorkflow: { data: undefined },
    requestWorkflowChange: jest.fn(),
    saveTicketBranch: jest.fn(),
  } as unknown as TicketWork;
}

function renderCard(detail = ticket(), w = work()) {
  mockApi.ticketLedger.mockResolvedValue({ visits: VISITS, total_runs: 5, reworked_stages: [], total_seconds: 0 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TicketWorkflowCard ticket={detail} work={w} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return w;
}

beforeEach(() => jest.clearAllMocks());

it("gives each stage one row saying how long it took, how often it failed, or how many lanes it opens", async () => {
  renderCard();

  const card = screen.getByRole("region", { name: "Workflow" });
  expect(card).toHaveTextContent("1/3");
  const rows = within(within(card).getByRole("list", { name: "Stages" })).getAllByRole("listitem");
  expect(rows).toHaveLength(3);
  expect(await within(rows[1]).findByText("4×")).toBeInTheDocument();
  expect(rows[0]).toHaveTextContent(/Specification.*Done.*4m/);
  expect(rows[1]).toHaveTextContent(/Failing, current stage/);
  expect(rows[1]).toHaveAttribute("aria-current", "step");
  expect(rows[2]).toHaveTextContent("4 lanes");
  // Every stage keeps its own menu.
  expect(within(rows[2]).getByRole("button", { name: "Review actions" })).toBeInTheDocument();
});

it("says who runs the current stage, how, and where it routes, and retries it", async () => {
  const w = renderCard();

  const card = screen.getByRole("region", { name: "Workflow" });
  expect(card).toHaveTextContent(/Agent\s*test_designer · tdd/);
  expect(card).toHaveTextContent(/Posture\s*internal · workspace/);
  expect(card).toHaveTextContent(/Routes/);

  const retry = await within(card).findByRole("button", { name: "Retry stage" });
  expect(retry).toHaveClass("btn-primary");
  fireEvent.click(retry);
  expect(w.setRunConfirmStageKey).toHaveBeenCalledWith("test_design");
});

it("keeps the template, branch, posture and model behind the gear, in a dialog", () => {
  const w = renderCard();

  const gear = screen.getByRole("button", { name: "Workflow settings" });
  fireEvent.click(gear);
  const dialog = screen.getByRole("dialog", { name: "Workflow settings" });
  expect(within(dialog).getByRole("combobox", { name: "Workflow template" })).toBeInTheDocument();
  expect(within(dialog).getByRole("textbox", { name: "Branch" })).toBeInTheDocument();
  expect(within(dialog).getByRole("combobox", { name: /Compatibility posture/ })).toBeInTheDocument();

  fireEvent.click(within(dialog).getByRole("button", { name: /Workspace default · Change/ }));
  expect(w.setTicketModelModalOpen).toHaveBeenCalledWith(true);
});

it("points a ticket with no workflow at the settings that give it one", () => {
  renderCard(ticket({ stages: [], workflow_stage_key: "" } as Partial<TicketDetail>));

  const card = screen.getByRole("region", { name: "Workflow" });
  expect(card).toHaveTextContent(/No workflow yet/);
  expect(within(card).queryByRole("list", { name: "Stages" })).toBeNull();
  fireEvent.click(within(card).getAllByRole("button", { name: "Workflow settings" })[1]);
  expect(screen.getByRole("dialog", { name: "Workflow settings" })).toBeInTheDocument();
});

it("asks what a blocked landing is waiting on, though no orchestration is landing it", async () => {
  mockApi.ticketLanding.mockResolvedValue({
    phase: "verifying",
    target: "integration/lg-milestone-that-491",
    since: new Date().toISOString(),
    timeout_seconds: 1800,
    progress: "",
    progress_done: null,
    progress_total: null,
    ahead: [],
    ahead_count: 0,
    refused_attempts: 1,
    last_refusal: null,
  });
  renderCard(
    ticket({
      stages: [stage("spec", "Specification", { status: "done" }), stage("done", "Done", { status: "blocked" })],
      workflow_stage_key: "done",
      workflow_stage_status: "blocked",
      state: "blocked",
      active_orchestration: null,
    }),
  );

  expect(await screen.findByRole("region", { name: "What this landing is waiting on" })).toHaveTextContent(
    "Running the landing tests",
  );
  expect(mockApi.ticketLanding).toHaveBeenCalledWith("t1");
});

it("does not ask once the ticket is done", () => {
  renderCard(
    ticket({
      stages: [stage("spec", "Specification", { status: "done" }), stage("done", "Done", { status: "done" })],
      workflow_stage_key: "done",
      workflow_stage_status: "done",
      state: "done",
    }),
  );

  expect(mockApi.ticketLanding).not.toHaveBeenCalled();
});
