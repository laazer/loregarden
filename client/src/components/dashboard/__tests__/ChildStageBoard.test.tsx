import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";

import * as apiClient from "../../../api/client";
import type { TicketSummary, TicketTreeNode, WorkflowStageView } from "../../../api/types";
import { ChildStageBoard } from "../ChildStageBoard";

jest.mock("../../../api/client", () => jest.requireActual("../../../test/apiClientMock"));

const KEYS = ["triage", "spec", "implement", "done"] as const;

function stages(at: number, status: WorkflowStageView["status"]): WorkflowStageView[] {
  return KEYS.map((key, i) => ({
    key,
    name: key[0].toUpperCase() + key.slice(1),
    status: i < at ? "done" : i === at ? status : "pending",
    agent_id: "",
    skill_name: "",
    optional: false,
    note: "",
    stage_type: "agent",
    agents: [],
  }));
}

function child(id: string, over: Partial<TicketSummary>): TicketSummary {
  return {
    id,
    external_id: `lg-${id}`,
    title: `Child ${id}`,
    state: "in_progress",
    priority: 1,
    workspace_slug: "loregarden",
    workflow_stage_key: "spec",
    workflow_stage_status: "pending",
    workflow_stage_name: "Spec",
    run_code: "",
    work_item_type: "task",
    parent_ticket_id: "parent",
    milestone: "",
    branch: "",
    child_count: 0,
    current_stage_agent: "",
    activity: "idle",
    ...over,
  } as TicketSummary;
}

const CHILDREN = [
  child("run", { activity: "running", workflow_stage_key: "implement", workflow_stage_name: "Implement", stages: stages(2, "running") }),
  child("stuck", { state: "blocked", workflow_stage_key: "triage", workflow_stage_name: "Triage", stages: stages(0, "blocked") }),
  child("landed", { state: "done", workflow_stage_key: "done", stages: stages(4, "done") }),
  child("split", { child_count: 2, stages: stages(1, "done") }),
];

const NODES = [
  {
    id: "split",
    children: [{ id: "p1", state: "done", workflow_stage_status: "done" }, { id: "p2", state: "in_progress", workflow_stage_status: "running" }],
  },
] as unknown as TicketTreeNode[];

function renderBoard(onOpenRunLog = jest.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ChildStageBoard parentId="parent" parentStages={[]} childNodes={NODES} onOpenTicket={jest.fn()} onOpenRunLog={onOpenRunLog} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(apiClient.api.tickets).mockResolvedValue(CHILDREN);
});

it("puts each child on its row with its stages named in words, blocked first and done last", async () => {
  renderBoard();

  const board = await screen.findByRole("table", { name: "Stages of each child" });
  const titles = within(board).getAllByRole("rowheader").map((cell) => within(cell).getByText(/^Child /).textContent);
  expect(titles).toEqual(["Child stuck", "Child run", "Child split", "Child landed"]);
  expect(within(board).getAllByRole("cell", { name: "Triage: blocked" })).toHaveLength(1);
  expect(within(board).getAllByRole("cell", { name: "Implement: running" })).toHaveLength(1);
  expect(screen.getByText(/1 of 2 under it done · 1 running/)).toBeInTheDocument();
});

it("counts how many children sit at each stage now", async () => {
  renderBoard();

  await screen.findByRole("table", { name: "Stages of each child" });
  expect(screen.getByRole("cell", { name: "1 at Triage" })).toBeInTheDocument();
  // The split child shows its parts, so its stale cursor counts nowhere.
  expect(screen.getByRole("cell", { name: "0 at Spec" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "1 at Implement" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "1 at Done" })).toBeInTheDocument();
});

it("opens a running row on its current run's live log, and nothing else gets the toggle", async () => {
  jest.mocked(apiClient.api.runs).mockResolvedValue([
    { id: "run-1", run_code: "run_abc", status: "running", command: "", agent_id: "backend_implementer", stage_key: "implement" },
  ]);
  jest.mocked(apiClient.api.runLog).mockResolvedValue({ status: "running", lines: [{ time: "10:00:01", tag: "OUT", text: "Reading the ticket" }], live: null, transport: "" } as never);
  const onOpenRunLog = jest.fn();
  renderBoard(onOpenRunLog);

  const toggles = await screen.findAllByRole("button", { name: /live log/ });
  expect(toggles.map((t) => t.getAttribute("aria-label"))).toEqual(["Show the live log of lg-run"]);
  fireEvent.click(toggles[0]);

  expect(await screen.findByText("Reading the ticket")).toBeInTheDocument();
  expect(toggles[0]).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(screen.getByRole("button", { name: "Open full log" }));
  expect(onOpenRunLog).toHaveBeenCalledWith("run-1");
});

it("says what failed and offers a retry when the children cannot be read", async () => {
  jest.mocked(apiClient.api.tickets).mockRejectedValue(new Error("board offline"));
  renderBoard();

  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t load the tickets under this one");
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
});
