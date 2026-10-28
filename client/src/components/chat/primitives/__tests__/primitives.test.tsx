import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";

import { ApiError, api } from "../../../../api/client";
import type { Approval, TicketDetail } from "../../../../api/types";
import { RouterBridgeSync } from "../../../RouterBridgeSync";
import { StudioChatMessages } from "../../../studio/StudioChat";
import { CalendarPrimitive } from "../CalendarPrimitive";
import { EditPrimitive } from "../EditPrimitive";
import { GatePrimitive } from "../GatePrimitive";
import { KanbanColumns, KanbanPrimitive } from "../KanbanPrimitive";
import { ParentTicketPrimitive } from "../ParentTicketPrimitive";
import { PrimitiveCard } from "../PrimitiveCard";
import { PrimitiveParts } from "../PrimitiveParts";
import { primitiveSize, widestPrimitiveSize } from "../primitiveFrame";
import { UnknownPrimitiveCard } from "../registry";
import {
  OpenAgentStudioButton,
  OpenEditorFileButton,
  OpenGateStudioButton,
  OpenIdeButton,
  OpenTicketButton,
  OpenWorkflowStudioButton,
} from "../ResourceActionButton";
import { PlayButton, StopButton } from "../RunControlButton";
import { TerminalPrimitive } from "../TerminalPrimitive";
import { TicketListPrimitive } from "../TicketListPrimitive";
import { TicketPrimitive } from "../TicketPrimitive";
import { TicketWorkflowPrimitive } from "../TicketWorkflowPrimitive";
import { ThinkingPrimitive } from "../ThinkingPrimitive";
import { WorkflowPrimitive } from "../WorkflowPrimitive";
import {
  childProgressPercent,
  stageProgressPercent,
} from "../ticketProgress";
import type { ChatPart } from "../types";
import { useUiStore } from "../../../../state/uiStore";

jest.mock("../../../../lib/branchTriageApi", () => ({
  fetchBranchActivity: jest.fn(),
  fetchCommitSnapshot: jest.fn(),
}));

jest.mock("../../../../api/client", () => {
  const actual = jest.requireActual("../../../../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      ticket: jest.fn(),
      ticketTree: jest.fn().mockResolvedValue([]),
      tickets: jest.fn().mockResolvedValue([]),
      workspaces: jest.fn(),
      approvals: jest.fn(),
      advance: jest.fn(),
      resolveApproval: jest.fn(),
      ciStatus: jest.fn().mockResolvedValue({ ci_status: null, auto_fix_history: [] }),
    },
  };
});

const mockedApi = api as jest.Mocked<typeof api>;

function wrap(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function PathProbe() {
  const { pathname } = useLocation();
  return <output data-testid="path">{pathname}</output>;
}

function renderResourceAction(action: ReactNode) {
  return render(
    <MemoryRouter initialEntries={["/chat"]}>
      <RouterBridgeSync />
      {action}
      <PathProbe />
    </MemoryRouter>,
  );
}

describe("ticketProgress", () => {
  it("computes stage progress", () => {
    expect(
      stageProgressPercent([
        { key: "a", name: "A", status: "done", agent_id: "", skill_name: "", optional: false, note: "", stage_type: "agent", agents: [] },
        { key: "b", name: "B", status: "pending", agent_id: "", skill_name: "", optional: false, note: "", stage_type: "agent", agents: [] },
      ]),
    ).toBe(50);
  });

  it("computes child progress", () => {
    expect(
      childProgressPercent([
        { state: "done" },
        { state: "in_progress" },
        { state: "wont_do" },
        { state: "backlog" },
      ]),
    ).toBe(50);
  });
});

describe("PrimitiveCard", () => {
  it("heads every card the same way: kind icon, title, ref line, status chip, then Open and ⋯", () => {
    const { container } = render(
      <PrimitiveCard
        kind="ticket"
        title="Bootstrap vertical slice"
        refLine="lg-bootstrap-vertical-1 · loregarden"
        status={{ tone: "run", label: "In progress" }}
        copyRef="lg-bootstrap-vertical-1"
        resourceAction={<OpenTicketButton ticketId="t1" compact />}
      >
        Body
      </PrimitiveCard>,
    );

    expect(screen.getByRole("heading", { name: "Bootstrap vertical slice" })).toBeInTheDocument();
    expect(screen.getByText("lg-bootstrap-vertical-1 · loregarden")).toHaveClass("lg-primitive-card-ref");
    expect(container.querySelector(".lg-primitive-card-icon--work svg")).not.toBeNull();
    const head = container.querySelector(".lg-primitive-card-header-actions");
    const items = Array.from(head?.children ?? []);
    expect(items[0]).toHaveTextContent("In progress");
    expect(items[1]).toHaveClass("lg-primitive-resource-btn");
    expect(within(head as HTMLElement).getByRole("button", { name: "More for Bootstrap vertical slice" })).toBeInTheDocument();
  });

  it("puts one action in the footer with a line saying what it does, never in the head", () => {
    const { container } = render(
      <PrimitiveCard kind="ticket" title="Ticket" footerHint="Run starts the next stage" actions={<PlayButton onClick={() => {}} />}>
        Body
      </PrimitiveCard>,
    );

    const footer = container.querySelector(".lg-primitive-card-actions") as HTMLElement;
    expect(footer).toHaveTextContent("Run starts the next stage");
    // The action that moves work forward is the leaf-shaped primary.
    expect(within(footer).getByRole("button", { name: "Run" })).toHaveClass("btn-primary");
    expect(container.querySelector(".lg-primitive-card-header .btn-primary")).toBeNull();
  });

  it("has no footer when the card only shows something", () => {
    const { container } = render(
      <PrimitiveCard kind="terminal" title="client · npm test" refLine="~/workspace/loregarden/client">
        Output
      </PrimitiveCard>,
    );
    expect(container.querySelector(".lg-primitive-card-actions")).toBeNull();
  });

  it("keeps its head while loading, with a skeleton where the body will be", () => {
    const { container } = render(
      <PrimitiveCard kind="ticket" title="Ticket" loading>
        Body
      </PrimitiveCard>,
    );
    expect(screen.getByRole("heading", { name: "Ticket" })).toBeInTheDocument();
    expect(screen.queryByText("Body")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".lg-primitive-card-skeleton-line")).toHaveLength(2);
  });

  it("keeps its title when its ref no longer resolves, and says so in the body", () => {
    render(
      <PrimitiveCard kind="ticket" title="lg-gone-1" error="Ticket not found">
        Body
      </PrimitiveCard>,
    );
    expect(screen.getByRole("heading", { name: "lg-gone-1" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Ticket not found");
  });
});

describe("run control buttons", () => {
  it("renders a play triangle and a stop square on their toned buttons", () => {
    render(
      <>
        <PlayButton onClick={() => {}} />
        <StopButton onClick={() => {}} />
      </>,
    );

    const play = screen.getByRole("button", { name: "Run" });
    const stop = screen.getByRole("button", { name: "Stop" });
    expect(play).toHaveClass("lg-primitive-run-btn--play");
    expect(stop).toHaveClass("lg-primitive-run-btn--stop");
    expect(play.querySelector("svg path")).not.toBeNull();
    expect(stop.querySelector("svg rect")).not.toBeNull();
  });

  it("does not fire while disabled", () => {
    const onClick = jest.fn();
    render(<StopButton onClick={onClick} disabled />);
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe("resource action buttons", () => {
  it.each([
    [
      "Open ticket",
      () => <OpenTicketButton ticketId="ticket/42" />,
      "/tickets/ticket%2F42/timeline",
    ],
    [
      "Open in Agent Studio",
      () => <OpenAgentStudioButton slug="frontend agent" />,
      "/studio/agents/frontend%20agent",
    ],
    [
      "Open in Workflow Studio",
      () => <OpenWorkflowStudioButton slug="feature/tdd" />,
      "/studio/workflows/feature%2Ftdd",
    ],
    ["Open Gate Studio", () => <OpenGateStudioButton />, "/studio/gates"],
    ["Open IDE", () => <OpenIdeButton />, "/editor"],
    [
      "Open agent_context/agents/1_planner/planner_v1.md in editor",
      () => (
        <OpenEditorFileButton
          path="agent_context/agents/1_planner/planner_v1.md"
          workspaceSlug="loregarden"
        />
      ),
      "/editor",
    ],
    [
      "Open ticket",
      () => <OpenTicketButton ticketId="ticket/42" label="Open ticket" />,
      "/tickets/ticket%2F42/timeline",
    ],
  ])("navigates %s to its resource surface", (label, renderButton, path) => {
    renderResourceAction(renderButton());
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(screen.getByTestId("path")).toHaveTextContent(path);
  });

  it("points the editor at the requested file path", () => {
    useUiStore.setState({
      editorWorkspace: "",
      editorFilePath: null,
      editorContextRoot: ".",
    });
    renderResourceAction(
      <OpenEditorFileButton
        path="agent_context/agents/1_planner/planner_v1.md"
        workspaceSlug="loregarden"
      />,
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Open agent_context/agents/1_planner/planner_v1.md in editor",
      }),
    );
    expect(useUiStore.getState().editorWorkspace).toBe("loregarden");
    expect(useUiStore.getState().editorFilePath).toBe(
      "agent_context/agents/1_planner/planner_v1.md",
    );
  });

  it("uses the same blue arrow treatment for full and compact links", () => {
    render(
      <>
        <OpenTicketButton ticketId="full" />
        <OpenTicketButton ticketId="compact" compact label="Open compact ticket" />
      </>,
    );

    for (const button of [
      screen.getByRole("button", { name: "Open ticket" }),
      screen.getByRole("button", { name: "Open compact ticket" }),
    ]) {
      expect(button).toHaveClass("lg-primitive-resource-btn");
      expect(button.lastElementChild?.tagName).toBe("svg");
    }
    expect(screen.getByRole("button", { name: "Open compact ticket" })).toHaveClass(
      "lg-primitive-resource-btn--compact",
    );
  });
});

function gateTicket(overrides?: Partial<TicketDetail>): TicketDetail {
  return {
    id: "gate-ticket-1",
    external_id: "42-quality-gate",
    title: "Ship gate polish",
    state: "in_progress",
    priority: 2,
    workspace_slug: "loregarden",
    workflow_stage_key: "quality_gate",
    workflow_stage_status: "awaiting",
    workflow_stage_name: "Quality Gate",
    run_code: "",
    work_item_type: "task",
    parent_ticket_id: null,
    milestone: "",
    branch: "main",
    child_count: 0,
    description: "",
    acceptance_criteria: ["Diff reviewed", "CI green"],
    checked_acceptance_criteria: [],
    revision: 1,
    last_updated_by: "baxter",
    current_stage_agent: "",
    next_status: "",
    blocking_issues: "",
    state_locked: false,
    workflow_template_slug: "studio-loregarden-tdd-v3",
    workflow_template_name: "Loregarden TDD V3",
    stages: [
      {
        key: "quality_gate",
        name: "Quality Gate",
        status: "awaiting",
        agent_id: "human",
        skill_name: "",
        optional: false,
        note: "",
        stage_type: "gate",
        agents: [],
      },
    ],
    artifacts: {},
    ...overrides,
  };
}

function gateApproval(overrides?: Partial<Approval>): Approval {
  return {
    id: "approval-1",
    title: "Approve quality gate",
    level: "ask",
    workspace_slug: "loregarden",
    stage_key: "quality_gate",
    stage_name: "Quality Gate",
    impact: "Confirm the evidence before continuing.",
    ticket_id: "gate-ticket-1",
    ticket_external_id: "42-quality-gate",
    kind: "workflow_gate",
    status: "pending",
    run_id: "run-1",
    tool_name: "",
    tool_input_json: "{}",
    cli_adapter: "claude",
    ...overrides,
  };
}

describe("Gate primitive", () => {
  beforeEach(() => {
    mockedApi.ticket.mockReset();
    mockedApi.approvals.mockReset();
    mockedApi.advance.mockReset();
    mockedApi.resolveApproval.mockReset();
    mockedApi.ciStatus.mockResolvedValue({ ci_status: null, auto_fix_history: [] });
  });

  it("renders a useful draft preview with checks", () => {
    wrap(
      <GatePrimitive
        part={{
          primitive: "gate",
          title: "Acceptance gate",
          draft: {
            name: "Acceptance gate",
            description: "Pause until evidence is attached.",
            checklist: ["Diff reviewed", "Tests green"],
          },
        }}
      />,
    );
    expect(screen.getByText("Acceptance gate")).toBeInTheDocument();
    expect(screen.getByText("Draft gate preview")).toBeInTheDocument();
    expect(screen.getByText("Pause until evidence is attached.")).toBeInTheDocument();
    expect(screen.getByText("Diff reviewed")).toBeInTheDocument();
    expect(screen.getByText("Tests green")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Gate Studio" })).toBeInTheDocument();
  });

  it("loads live gate status, criteria, and pending approvals", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket());
    mockedApi.approvals.mockResolvedValue([gateApproval()]);

    wrap(
      <GatePrimitive
        part={{
          primitive: "gate",
          ticket_id: "gate-ticket-1",
          stage_key: "quality_gate",
          title: "Quality Gate",
        }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Waiting on operator sign-off")).toBeInTheDocument();
    });
    expect(screen.getByText("Diff reviewed")).toBeInTheDocument();
    expect(screen.getByText("CI green")).toBeInTheDocument();
    expect(screen.getByText("Approve quality gate")).toBeInTheDocument();
    expect(screen.getByText("Pending sign-off · 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open ticket" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("offers advance when the gate is awaiting with no pending approvals", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket());
    mockedApi.approvals.mockResolvedValue([]);
    mockedApi.advance.mockResolvedValue(gateTicket());

    wrap(
      <GatePrimitive
        part={{
          primitive: "gate",
          ticket_id: "gate-ticket-1",
          stage_key: "quality_gate",
        }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Advance past gate" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Advance past gate" }));
    await waitFor(() => {
      expect(mockedApi.advance).toHaveBeenCalledWith("gate-ticket-1");
    });
  });
});

describe("Ticket primitive chrome", () => {
  beforeEach(() => {
    mockedApi.ticket.mockReset();
    mockedApi.tickets.mockReset();
    mockedApi.tickets.mockResolvedValue([]);
  });

  it("wears the v6 ticket card: P-badge, state, stage segments, stage line", async () => {
    mockedApi.ticket.mockResolvedValue(
      gateTicket({
        priority: 1,
        stages: [
          {
            key: "plan",
            name: "Plan",
            status: "done",
            agent_id: "",
            skill_name: "",
            optional: false,
            note: "",
            stage_type: "agent",
            agents: [],
          },
          {
            key: "quality_gate",
            name: "Quality Gate",
            status: "awaiting",
            agent_id: "human",
            skill_name: "",
            optional: false,
            note: "",
            stage_type: "gate",
            agents: [],
          },
          {
            key: "done",
            name: "Done",
            status: "pending",
            agent_id: "",
            skill_name: "",
            optional: false,
            note: "",
            stage_type: "agent",
            agents: [],
          },
        ],
      }),
    );

    const { container } = wrap(
      <TicketPrimitive part={{ primitive: "ticket", ticket_id: "gate-ticket-1" }} />,
    );

    await waitFor(() => {
      expect(screen.getByText("Ship gate polish")).toBeInTheDocument();
    });
    // The head names the ticket, its state and where it lives; the body is progress.
    expect(screen.getByText("P1")).toBeInTheDocument();
    expect(container.querySelector(".lg-primitive-card-header-actions")?.textContent).toContain(
      "In Progress",
    );
    expect(container.querySelector(".lg-primitive-card-ref")?.textContent).toContain("loregarden");
    // Awaiting counts as running, so the footer offers Stop and says what it does.
    expect(container.querySelector(".lg-primitive-card-actions")?.textContent).toContain(
      "Stop pauses the running stage",
    );
    // One segment per stage, and the facts row: the stage and its state, and progress.
    expect(container.querySelectorAll(".lg-primitive-ticket-seg")).toHaveLength(3);
    const facts = container.querySelector(".lg-primitive-card-meta")?.textContent;
    expect(facts).toContain("Quality Gate");
    expect(facts).toContain("Awaiting");
    expect(facts).toContain("Progress 1/3");
  });

  it("labels parent progress as children done out of total", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket({ work_item_type: "milestone", child_count: 2 }));
    mockedApi.tickets.mockResolvedValue([
      gateTicket({ id: "child-1", external_id: "43-one", title: "Child one", state: "done" }),
      gateTicket({
        id: "child-2",
        external_id: "44-two",
        title: "Child two",
        state: "in_progress",
      }),
    ]);

    const { container } = wrap(
      <ParentTicketPrimitive part={{ primitive: "parent_ticket", ticket_id: "gate-ticket-1" }} />,
    );

    await waitFor(() => {
      expect(screen.getByText("Ship gate polish")).toBeInTheDocument();
    });
    expect(mockedApi.tickets).toHaveBeenCalledWith({ parent_ticket_id: "gate-ticket-1" });
    expect(container.querySelector(".lg-primitive-card-ref")?.textContent).toContain("1 of 2 children done");
    expect(container.querySelectorAll(".lg-primitive-ticket-seg")).toHaveLength(2);
    // The children are rows of a table, each opening its ticket.
    const table = screen.getByRole("table", { name: "Under Ship gate polish" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).getByRole("button", { name: /Child two/ })).toBeInTheDocument();
    // Its stage is awaiting, so the footer offers Stop; idle, it would offer Continue run.
    expect(container.querySelector(".lg-primitive-card-actions")?.textContent).toContain("Stop pauses");
  });

  it("falls back to stage progress when a parent has no children yet", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket({ work_item_type: "milestone" }));
    mockedApi.tickets.mockResolvedValue([]);

    const { container } = wrap(
      <ParentTicketPrimitive part={{ primitive: "parent_ticket", ticket_id: "gate-ticket-1" }} />,
    );

    await waitFor(() => {
      expect(screen.getByText("Ship gate polish")).toBeInTheDocument();
    });
    expect(container.querySelector(".lg-primitive-card-meta")).toBeNull();
    expect(container.querySelectorAll(".lg-primitive-ticket-seg")).toHaveLength(1);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("lays open work out as a table: priority, ticket, status, stage", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket({ title: "Open work parent" }));
    mockedApi.tickets.mockResolvedValue([
      gateTicket({
        id: "child-1",
        external_id: "43-hive-upgrade",
        title: "Hive Workplace Upgrade",
        priority: 1,
      }),
    ]);

    const { container } = wrap(
      <TicketListPrimitive
        part={{
          primitive: "ticket_list",
          parent_ticket_id: "gate-ticket-1",
          title: "Open work",
        }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Hive Workplace Upgrade")).toBeInTheDocument();
    });
    const table = screen.getByRole("table", { name: "Open work" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "P",
      "Ticket",
      "Status",
      "Stage",
    ]);
    const cells = within(table).getAllByRole("row")[1].querySelectorAll("td");
    expect(cells[0].textContent).toBe("P1");
    expect(cells[1].textContent).toContain("43-hive-upgrade");
    expect(cells[2].textContent).toContain("In Progress");
    expect(cells[3].textContent).toBe("Quality Gate");
    // A parent's list names the parent it lists.
    expect(container.querySelector(".lg-primitive-card-ref")?.textContent).toBe("42-quality-gate");
  });

  it("links to the ticket from the header and runs it from the actions row", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket({ workflow_stage_status: "pending" }));

    const { container } = wrap(
      <TicketPrimitive part={{ primitive: "ticket", ticket_id: "gate-ticket-1" }} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Run" })).toBeEnabled();
    });
    expect(
      container.querySelector(
        ".lg-primitive-card-header-actions .lg-primitive-resource-btn--compact",
      ),
    ).not.toBeNull();
    expect(
      container.querySelector(".lg-primitive-card-actions .lg-primitive-run-btn--play"),
    ).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
  });

  it("hides run/open controls when the ticket id does not resolve", async () => {
    mockedApi.ticket.mockRejectedValue(new ApiError(404, "Ticket not found"));

    const { container } = wrap(
      <TicketPrimitive
        part={{ primitive: "ticket", ticket_id: "queue-history-ticket-cards" }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Ticket not found")).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "Run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
    expect(container.querySelector(".lg-primitive-resource-btn--compact")).toBeNull();
    expect(mockedApi.ticket).toHaveBeenCalledTimes(1);
  });

  it("does not keep refetching a missing ticket id", async () => {
    mockedApi.ticket.mockRejectedValue(new ApiError(404, "Ticket not found"));

    wrap(
      <TicketPrimitive
        part={{ primitive: "ticket", ticket_id: "queue-history-ticket-cards" }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Ticket not found")).toBeInTheDocument();
    });
    const calls = mockedApi.ticket.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(mockedApi.ticket).toHaveBeenCalledTimes(calls);
  });

  it("swaps the play button for stop while the stage runs", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket({ workflow_stage_status: "running" }));

    const { container } = wrap(
      <TicketPrimitive part={{ primitive: "ticket", ticket_id: "gate-ticket-1" }} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "Run" })).not.toBeInTheDocument();
    expect(
      container.querySelector(".lg-primitive-card-actions .lg-primitive-run-btn--stop"),
    ).not.toBeNull();
  });

  it("gives the workflow card the v6 head above its stage timeline", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket({ priority: 1, workflow_stage_status: "pending" }));

    const { container } = wrap(
      <TicketWorkflowPrimitive
        part={{ primitive: "ticket_workflow", ticket_id: "gate-ticket-1" }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("P1")).toBeInTheDocument();
    });
    expect(container.querySelector(".lg-primitive-card-header-actions")?.textContent).toContain(
      "In Progress",
    );
    expect(container.querySelector(".lg-primitive-ticket-timeline")).not.toBeNull();
    expect(container.querySelector(".lg-primitive-card-actions")?.textContent).toContain("0 of 1 stages done");
  });
});

describe("Board tiles", () => {
  beforeEach(() => {
    mockedApi.ticket.mockReset();
    mockedApi.tickets.mockReset();
    mockedApi.tickets.mockResolvedValue([]);
  });

  it("renders board tickets as compact v6 tiles", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket({ priority: 1 }));

    const { container } = wrap(
      <KanbanPrimitive
        part={{ primitive: "kanban", ticket_ids: ["gate-ticket-1"], statuses: ["in_progress"] }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Ship gate polish")).toBeInTheDocument();
    });
    const tile = container.querySelector(".lg-primitive-ticket-tile");
    expect(tile).not.toBeNull();
    expect(tile?.querySelector(".lg-primitive-ticket-v6--compact")).not.toBeNull();
    expect(screen.getByText("P1")).toBeInTheDocument();
    expect(tile?.textContent).toContain("In Progress");
    expect(tile?.textContent).toContain("Quality Gate");
    expect(
      tile?.querySelector(".lg-primitive-ticket-tile-action .lg-primitive-resource-btn--compact"),
    ).not.toBeNull();
  });

  it("labels board columns with the ticket state and a count", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket());

    const { container } = wrap(
      <KanbanPrimitive
        part={{
          primitive: "kanban",
          ticket_ids: ["gate-ticket-1"],
          statuses: ["in_progress", "wont_do"],
        }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Ship gate polish")).toBeInTheDocument();
    });
    const titles = Array.from(
      container.querySelectorAll(".lg-primitive-kanban-col-title"),
    ).map((el) => el.textContent);
    expect(titles).toEqual(["In Progress1", "Won't do0"]);
  });
});

describe("Board hierarchy", () => {
  const milestone = gateTicket({ id: "m1", title: "Milestone", state: "backlog", parent_ticket_id: null });
  const child = (id: string, parent = "m1") =>
    gateTicket({ id, title: `Child ${id}`, state: "backlog", parent_ticket_id: parent });

  it("hangs a child under its parent when both share a column", () => {
    wrap(<KanbanColumns columns={[{ status: "backlog", tickets: [milestone, child("c1"), child("c2")] }]} />);

    const under = screen.getByRole("list", { name: "Under Milestone" });
    expect(under).toHaveTextContent("Child c1");
    expect(under).toHaveTextContent("Child c2");
    expect(screen.getByRole("button", { name: /2 sub-tickets/ })).toHaveAttribute("aria-expanded", "true");
  });

  it("folds a parent with many children behind a count, and opens it on request", () => {
    const children = ["c1", "c2", "c3", "c4"].map((id) => child(id));
    wrap(<KanbanColumns columns={[{ status: "backlog", tickets: [milestone, ...children] }]} />);

    const toggle = screen.getByRole("button", { name: /4 sub-tickets/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Child c1")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("list", { name: "Under Milestone" })).toHaveTextContent("Child c4");
  });

  it("keeps a child at the top of its column when its parent is in another", () => {
    wrap(
      <KanbanColumns
        columns={[
          { status: "backlog", tickets: [milestone] },
          { status: "in_progress", tickets: [{ ...child("c1"), state: "in_progress" }] },
        ]}
      />,
    );
    expect(screen.queryByRole("list", { name: "Under Milestone" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sub-ticket/ })).not.toBeInTheDocument();
    expect(screen.getByText("Child c1")).toBeInTheDocument();
  });
});

describe("Thinking and Terminal primitives", () => {
  it("renders thinking content when expanded", () => {
    render(<ThinkingPrimitive part={{ primitive: "thinking", content: "Hmm", collapsed: false }} />);
    expect(screen.getByText("Hmm")).toBeInTheDocument();
  });

  it("renders terminal transcript lines", () => {
    render(
      <TerminalPrimitive
        part={{
          primitive: "terminal",
          title: "pytest",
          lines: [
            { kind: "command", text: "pytest -q" },
            { kind: "stdout", text: "ok" },
          ],
        }}
      />,
    );
    expect(screen.getByText(/pytest -q/)).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
  });
});

describe("Edit primitive", () => {
  it("shows an active diff with local comments that send to chat", async () => {
    const onSubmit = jest.fn();
    wrap(
      <EditPrimitive
        part={{
          primitive: "edit",
          title: "Tighten Planner scope",
          path: "agent_context/agents/1_planner/planner_v1.md",
          language: "markdown",
          original: "You write plans.\n",
          content: "You write plans.\nYou never write code.\n",
        }}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByText(/Proposed edit/)).toBeInTheDocument();
    expect(screen.getByText("You never write code.")).toBeInTheDocument();

    fireEvent.click(screen.getAllByTitle("Add inline review comment")[0]);
    fireEvent.change(screen.getByPlaceholderText("Leave an inline code review comment…"), {
      target: { value: "Make this a hard stop." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Comment" }));

    fireEvent.click(screen.getByRole("button", { name: "Send comments to chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Send to chat" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled();
    });
    const payload = onSubmit.mock.calls[0][0] as string;
    expect(payload).toContain("## Diff review: Tighten Planner scope");
    expect(payload).toContain("Make this a hard stop.");
  });

  it("falls back to a plain editor when no original is supplied", () => {
    wrap(
      <EditPrimitive
        part={{
          primitive: "edit",
          title: "Notes",
          content: "scratch pad",
        }}
      />,
    );
    expect(screen.getByTestId("monaco-editor")).toHaveValue("scratch pad");
    expect(screen.queryByText(/Proposed edit/)).not.toBeInTheDocument();
  });

  it("opens the file in the editor view, not Agent Studio", () => {
    useUiStore.setState({
      editorWorkspace: "",
      editorFilePath: null,
      editorContextRoot: ".",
    });
    render(
      <MemoryRouter initialEntries={["/chat"]}>
        <RouterBridgeSync />
        <QueryClientProvider
          client={
            new QueryClient({ defaultOptions: { queries: { retry: false } } })
          }
        >
          <EditPrimitive
            part={{
              primitive: "edit",
              title: "Tighten Planner scope",
              path: "agent_context/agents/1_planner/planner_v1.md",
              workspace_slug: "loregarden",
              target: "agent",
              target_id: "planner",
              original: "old\n",
              content: "new\n",
            }}
          />
        </QueryClientProvider>
        <PathProbe />
      </MemoryRouter>,
    );

    expect(screen.queryByRole("button", { name: "Open in Agent Studio" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Open .* in editor/ }));
    expect(screen.getByTestId("path")).toHaveTextContent("/editor");
    expect(useUiStore.getState().editorFilePath).toBe(
      "agent_context/agents/1_planner/planner_v1.md",
    );
    expect(useUiStore.getState().editorWorkspace).toBe("loregarden");
  });
});

describe("Workflow primitive graph", () => {
  it("wraps stages, renders parallel agents, and labels gate edges", () => {
    const stage = {
      agent_id: "",
      skill_name: "",
      optional: false,
      exit_actions_enabled: false,
      exit_actions: [],
      classify_routes: [],
      parallel_agents: [],
      model: "",
    };
    wrap(
      <WorkflowPrimitive
        part={{
          primitive: "workflow",
          title: "Delivery workflow",
          draft: {
            stages: [
              { ...stage, key: "scope", name: "Scope", stage_type: "agent", order: 0, agent_id: "planner" },
              {
                ...stage,
                key: "build",
                name: "Build",
                stage_type: "parallel",
                order: 1,
                parallel_agents: [
                  { agent_id: "frontend_implementer", skill_name: "frontend" },
                  { agent_id: "backend_implementer", skill_name: "backend" },
                ],
              },
              { ...stage, key: "verify", name: "Verify", stage_type: "agent", order: 2, agent_id: "verifier" },
              { ...stage, key: "review", name: "Review", stage_type: "agent", order: 3, agent_id: "reviewer" },
              { ...stage, key: "gate", name: "Quality Gate", stage_type: "gate", order: 4, agent_id: "gatekeeper" },
              { ...stage, key: "done", name: "Done", stage_type: "agent", order: 5 },
            ],
          },
        }}
      />,
    );

    expect(screen.getByText("frontend_implementer")).toBeInTheDocument();
    expect(screen.getByText("backend_implementer")).toBeInTheDocument();
    expect(screen.getByText("frontend")).toBeInTheDocument();
    expect(screen.getByText("backend")).toBeInTheDocument();
    expect(screen.getByText("Gate")).toBeInTheDocument();
    expect(screen.getByText("Pass")).toBeInTheDocument();

    const nodes = screen.getAllByTestId("react-flow-node");
    const xPositions = nodes.map((node) => Number(node.getAttribute("data-x")));
    const yPositions = nodes.map((node) => Number(node.getAttribute("data-y")));
    expect(Math.max(...xPositions)).toBe(850);
    expect(new Set(yPositions).size).toBeGreaterThan(1);
  });
});

describe("registry fallback", () => {
  it("renders unknown primitives without blanking", () => {
    render(<UnknownPrimitiveCard part={{ primitive: "spaceship", fuel: 3 }} />);
    expect(screen.getByText(/Unknown primitive: spaceship/)).toBeInTheDocument();
  });
});

describe("PrimitiveParts in StudioChatMessages", () => {
  it("stacks structured parts under an assistant turn", () => {
    const parts: ChatPart[] = [
      { primitive: "text", content: "Here is a thought." },
      { primitive: "thinking", content: "secret plan", collapsed: false },
    ];
    wrap(
      <StudioChatMessages
        messages={[{ id: "1", role: "assistant", content: "Here is a thought.", parts }]}
        showAssistantAvatar={false}
      />,
    );
    expect(screen.getByText("Here is a thought.")).toBeInTheDocument();
    expect(screen.getByText("secret plan")).toBeInTheDocument();
  });
});

describe("Calendar day agenda", () => {
  it("renders a readable agenda for the day view", () => {
    wrap(
      <CalendarPrimitive
        part={{
          primitive: "calendar",
          view: "day",
          focus_date: "2026-07-29T12:00:00-04:00",
          title: "Today",
          events: [
            {
              id: "1",
              title: "MCP Gateway completion",
              starts_at: "2026-07-29T09:00:00-04:00",
              ends_at: "2026-07-29T09:45:00-04:00",
              kind: "run",
              ticket_id: "f1c1a0cc-9641-4e88-9b3b-7e3b474958db",
              description: "in progress · implement",
            },
            {
              id: "2",
              title: "Planner review",
              starts_at: "2026-07-29T14:30:00-04:00",
              kind: "plan",
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("Wednesday")).toBeInTheDocument();
    expect(screen.getByText("July 29, 2026")).toBeInTheDocument();
    expect(screen.getByText("2 events")).toBeInTheDocument();
    expect(screen.getByText("MCP Gateway completion")).toBeInTheDocument();
    expect(screen.getByText("in progress · implement")).toBeInTheDocument();
    expect(screen.getByText("run")).toBeInTheDocument();
    expect(screen.getByText("45m")).toBeInTheDocument();
  });

  it("reads a bare focus_date as a local calendar day, not UTC midnight", () => {
    wrap(
      <CalendarPrimitive
        part={{
          primitive: "calendar",
          view: "day",
          focus_date: "2026-07-29",
          events: [],
        }}
      />,
    );
    expect(screen.getByText("July 29, 2026")).toBeInTheDocument();
  });
});

describe("Calendar week schedule", () => {
  it("groups timed events into readable day lanes", () => {
    wrap(
      <CalendarPrimitive
        part={{
          primitive: "calendar",
          view: "week",
          focus_date: "2026-07-29",
          title: "This week",
          events: [
            {
              id: "mon",
              title: "Implementation run",
              starts_at: "2026-07-27T09:00:00-04:00",
              kind: "run",
              description: "frontend · implement",
            },
            {
              id: "wed",
              title: "Quality gate",
              starts_at: "2026-07-29T14:30:00-04:00",
              kind: "scheduled",
            },
          ],
        }}
      />,
    );

    expect(screen.getByLabelText("Week schedule")).toBeInTheDocument();
    expect(screen.getByText("Implementation run")).toBeInTheDocument();
    expect(screen.getByText("Quality gate")).toBeInTheDocument();
    expect(screen.getByText("frontend · implement")).toBeInTheDocument();
    expect(screen.getAllByText("Open")).toHaveLength(5);
  });
});


describe("PrimitiveParts component", () => {
  it("skips empty text parts", () => {
    const { container } = render(
      <PrimitiveParts parts={[{ primitive: "text", content: "   " }]} />,
    );
    expect(container.querySelector(".lg-primitive-parts")).toBeNull();
  });
});

describe("primitive size tiers", () => {
  it("falls back to regular for unknown kinds", () => {
    expect(primitiveSize("spaceship")).toBe("regular");
    expect(primitiveSize("terminal")).toBe("wide");
    expect(primitiveSize("kanban")).toBe("full");
  });

  it("reports the widest tier in a turn", () => {
    expect(widestPrimitiveSize(undefined)).toBe("regular");
    expect(widestPrimitiveSize([{ primitive: "ticket" }])).toBe("regular");
    expect(
      widestPrimitiveSize([{ primitive: "ticket" }, { primitive: "terminal" }]),
    ).toBe("wide");
    expect(
      widestPrimitiveSize([{ primitive: "kanban" }, { primitive: "terminal" }]),
    ).toBe("full");
  });

  it("gives each part a slot sized to its tier", () => {
    const { container } = render(
      <PrimitiveParts
        parts={[
          { primitive: "thinking", content: "hmm", collapsed: false },
          { primitive: "terminal", title: "pytest", lines: [] },
        ]}
      />,
    );
    const slots = container.querySelectorAll(".lg-primitive-slot");
    expect(slots).toHaveLength(2);
    expect(slots[0]).toHaveClass("lg-primitive-slot--regular");
    expect(slots[1]).toHaveClass("lg-primitive-slot--wide");
  });

  it("widens the assistant turn to the widest tier it carries", () => {
    const { container } = wrap(
      <StudioChatMessages
        messages={[
          {
            id: "1",
            role: "assistant",
            content: "board",
            parts: [
              { primitive: "text", content: "board" },
              { primitive: "kanban", ticket_ids: [] },
            ],
          },
        ]}
        showAssistantAvatar={false}
      />,
    );
    expect(container.querySelector(".lg-chat-turn--full")).not.toBeNull();
  });

  it("leaves regular turns at the reading measure", () => {
    const { container } = wrap(
      <StudioChatMessages
        messages={[
          {
            id: "1",
            role: "assistant",
            content: "note",
            parts: [{ primitive: "thinking", content: "hmm", collapsed: false }],
          },
        ]}
        showAssistantAvatar={false}
      />,
    );
    expect(container.querySelector(".lg-chat-turn--wide")).toBeNull();
    expect(container.querySelector(".lg-chat-turn--full")).toBeNull();
  });
});

describe("primitive expansion", () => {
  it("offers no expand control for regular primitives", () => {
    render(<PrimitiveParts parts={[{ primitive: "thinking", content: "hmm", collapsed: false }]} />);
    expect(screen.queryByRole("button", { name: "Expand" })).toBeNull();
  });

  it("hoists a breakout primitive into an overlay and closes on Escape", () => {
    render(
      <PrimitiveParts
        parts={[
          { primitive: "terminal", title: "pytest", lines: [{ kind: "stdout", text: "ok" }] },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    const overlay = screen.getByRole("dialog");
    expect(overlay).toContainElement(screen.getByText("ok"));

    // At `document`, not `window`. A key press in a browser targets the focused
    // element and bubbles up through `document` to `window`, so a `document`
    // listener sees it — but an event fired *at* `window` is already past
    // `document` and never reaches it. This fired at `window` only because the
    // implementation happened to listen there; the shared dismiss stack listens
    // where the focus trap does.
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Expand" })).toBeInTheDocument();
  });
});
