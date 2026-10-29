import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";

import { api } from "../../../../api/client";
import type { TicketSummary, TicketTreeNode, WorkflowStageView } from "../../../../api/types";
import { useTicketPeekStore } from "../../../../state/ticketPeekStore";
import { KanbanPrimitive } from "../KanbanPrimitive";
import { TicketTable } from "../TicketTable";
import { TicketWorkflowPrimitive } from "../TicketWorkflowPrimitive";

jest.mock("../../../../api/client", () => {
  const actual = jest.requireActual("../../../../api/client");
  return { ...actual, api: { ...actual.api, tickets: jest.fn(), ticket: jest.fn() } };
});

const mockedApi = api as jest.Mocked<typeof api>;

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function node(i: number, overrides: Partial<TicketTreeNode> = {}): TicketTreeNode {
  return {
    id: `t${i}`,
    external_id: `lg-thing-${i}`,
    title: `Ticket ${i}`,
    state: "in_progress",
    priority: 1,
    work_item_type: "task",
    workflow_stage_name: "Plan",
    workflow_stage_status: "running",
    child_count: 0,
    children: [],
    ...overrides,
  };
}

function summary(i: number, state: TicketSummary["state"]): TicketSummary {
  return { ...node(i, { state }), stages: [] } as unknown as TicketSummary;
}

beforeEach(() => jest.clearAllMocks());

describe("TicketTable", () => {
  it("shows eight rows, then all of them behind a filter once there are many", () => {
    render(<TicketTable nodes={Array.from({ length: 20 }, (_, i) => node(i))} label="Open work" />);

    const table = screen.getByRole("table", { name: "Open work" });
    expect(within(table).getAllByRole("row")).toHaveLength(9);
    expect(screen.queryByRole("textbox")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show all 20" }));
    expect(within(table).getAllByRole("row")).toHaveLength(21);
    fireEvent.change(screen.getByRole("textbox", { name: "Filter Open work" }), { target: { value: "thing-17" } });
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  });

  it("opens a ticket's details from its title, and its children beneath it", () => {
    render(
      <TicketTable
        nodes={[node(1, { child_count: 1, children: [node(2, { title: "Child" })] })]}
        label="Open work"
      />,
    );

    expect(screen.queryByText("Child")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 1 under Ticket 1" }));
    expect(screen.getByText("Child")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Ticket 1\s*lg-thing-1/ }));
    expect(useTicketPeekStore.getState().peek?.ticketId).toBe("t1");
  });
});

describe("the board", () => {
  it("always offers the column filter: All shows three tiles a column, and a column alone shows more", async () => {
    mockedApi.tickets.mockResolvedValue([
      ...Array.from({ length: 5 }, (_, i) => summary(i, "in_progress")),
      summary(9, "blocked"),
    ]);
    wrap(<KanbanPrimitive part={{ primitive: "kanban", statuses: ["in_progress", "blocked"] }} />);

    expect(await screen.findByText("5 in progress · 1 blocked")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByText(/^Ticket \d$/)).toHaveLength(4);

    fireEvent.click(screen.getByRole("button", { name: "Show all 5" }));
    expect(screen.getByRole("button", { name: "In Progress" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByText(/^Ticket \d$/)).toHaveLength(5);
  });
});

describe("the workflow card", () => {
  it("shows five stages around the current one, and the rest on request", async () => {
    const stages: WorkflowStageView[] = Array.from({ length: 12 }, (_, i) => ({
      key: `s${i}`,
      name: `Stage ${i}`,
      status: i < 6 ? "done" : "pending",
      agent_id: "a",
      skill_name: "",
      optional: false,
      note: "",
      stage_type: "agent",
      agents: [],
    }));
    mockedApi.ticket.mockResolvedValue({
      ...node(1),
      stages,
      workflow_stage_key: "s6",
      workflow_stage_status: "pending",
    } as never);
    wrap(<TicketWorkflowPrimitive part={{ primitive: "ticket_workflow", ticket_id: "t1" }} />);

    expect(await screen.findByText("Stage 5")).toBeInTheDocument();
    expect(screen.queryByText("Stage 0")).toBeNull();
    expect(screen.getByText("6 of 12 stages done")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show all 12 stages" }));
    expect(screen.getByText("Stage 0")).toBeInTheDocument();
  });
});
