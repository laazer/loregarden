import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";

import { api } from "../../../../api/client";
import type { Approval, TicketDetail } from "../../../../api/types";
import { RouterBridgeSync } from "../../../RouterBridgeSync";
import { StudioChatMessages } from "../../../studio/StudioChat";
import { CalendarPrimitive } from "../CalendarPrimitive";
import { GatePrimitive } from "../GatePrimitive";
import { GiphyPrimitive } from "../GiphyPrimitive";
import { BranchHistoryPrimitive, CommitPrimitive } from "../GitPrimitive";
import { ParentTicketPrimitive } from "../ParentTicketPrimitive";
import { PrimitiveCard } from "../PrimitiveCard";
import { PrimitiveParts } from "../PrimitiveParts";
import { primitiveSize, widestPrimitiveSize } from "../primitiveFrame";
import { QAPrimitive } from "../QAPrimitive";
import { UnknownPrimitiveCard } from "../registry";
import {
  OpenAgentStudioButton,
  OpenGateStudioButton,
  OpenIdeButton,
  OpenTicketButton,
  OpenWorkflowStudioButton,
} from "../ResourceActionButton";
import { PlayButton, StopButton } from "../RunControlButton";
import { TerminalPrimitive } from "../TerminalPrimitive";
import { TicketPrimitive } from "../TicketPrimitive";
import { ThinkingPrimitive } from "../ThinkingPrimitive";
import { TodoListPrimitive } from "../TodoListPrimitive";
import { WorkflowPrimitive } from "../WorkflowPrimitive";
import { WorkspacePrimitive } from "../WorkspacePrimitive";
import {
  childProgressPercent,
  stageProgressPercent,
} from "../ticketProgress";
import type { ChatPart, QAItem, TodoItem } from "../types";
import {
  fetchBranchActivity,
  fetchCommitSnapshot,
} from "../../../../lib/branchTriageApi";

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
const mockedBranchActivity = fetchBranchActivity as jest.MockedFunction<
  typeof fetchBranchActivity
>;
const mockedCommitSnapshot = fetchCommitSnapshot as jest.MockedFunction<
  typeof fetchCommitSnapshot
>;

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
  it("renders title and actions", () => {
    render(
      <PrimitiveCard title="Ticket" actions={<button type="button">Play</button>}>
        Body
      </PrimitiveCard>,
    );
    expect(screen.getByText("Ticket")).toBeInTheDocument();
    expect(screen.getByText("Body")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  });

  it("places the external-resource link last in the header, after the card controls", () => {
    const { container } = render(
      <PrimitiveCard
        title="Ticket"
        collapsible
        resourceAction={<OpenTicketButton ticketId="t1" />}
        actions={<PlayButton onClick={() => {}} />}
      >
        Body
      </PrimitiveCard>,
    );

    const header = container.querySelector(".lg-primitive-card-header-actions");
    const headerItems = Array.from(header?.children ?? []);
    expect(headerItems).toHaveLength(2);
    expect(headerItems[0]).toHaveClass("lg-primitive-card-collapse");
    expect(headerItems[1]).toHaveClass("lg-primitive-resource-btn");
    expect(
      container.querySelector(".lg-primitive-card-actions .lg-primitive-resource-btn"),
    ).toBeNull();
  });

  it("keeps the resource link visible while the body is collapsed", () => {
    const { container } = render(
      <PrimitiveCard
        title="Ticket"
        collapsible
        defaultCollapsed
        resourceAction={<OpenTicketButton ticketId="t1" />}
      >
        Body
      </PrimitiveCard>,
    );
    expect(screen.queryByText("Body")).not.toBeInTheDocument();
    expect(
      container.querySelector(".lg-primitive-card-header-actions .lg-primitive-resource-btn"),
    ).not.toBeNull();
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

    const play = screen.getByRole("button", { name: "Play" });
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
      "/tickets/ticket%2F42/diff",
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
      "Open triage",
      () => <OpenTicketButton ticketId="ticket/42" tab="triage" label="Open triage" />,
      "/tickets/ticket%2F42/triage",
    ],
  ])("navigates %s to its resource surface", (label, renderButton, path) => {
    renderResourceAction(renderButton());
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(screen.getByTestId("path")).toHaveTextContent(path);
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
    revision: 1,
    last_updated_by: "baxter",
    next_agent: "",
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
    expect(screen.getByRole("button", { name: "Open triage" })).toBeInTheDocument();
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
    expect(screen.getByText("P1")).toBeInTheDocument();
    expect(container.querySelector(".lg-primitive-ticket-v6-meta")?.textContent).toContain(
      "In Progress",
    );
    expect(container.querySelector(".lg-primitive-ticket-v6-meta")?.textContent).toContain(
      "loregarden",
    );
    expect(container.querySelectorAll(".lg-primitive-ticket-seg")).toHaveLength(3);
    expect(container.querySelector(".lg-primitive-ticket-segs-label")?.textContent).toBe("1/3");
    expect(container.querySelector(".lg-primitive-ticket-stage")?.textContent).toContain(
      "Quality Gate",
    );
    expect(container.querySelector(".lg-primitive-ticket-stage")?.textContent).toContain("Awaiting");
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
    expect(container.querySelector(".lg-primitive-ticket-segs-label")?.textContent).toBe("1/2");
    expect(container.querySelectorAll(".lg-primitive-ticket-seg")).toHaveLength(2);
    expect(container.querySelectorAll(".tree-row")).toHaveLength(2);
    expect(screen.getByText("Child one")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Child two" })).toBeInTheDocument();
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
    expect(container.querySelector(".lg-primitive-ticket-segs-label")?.textContent).toBe("0/1");
    expect(container.querySelector(".lg-primitive-ticket-children")).toBeNull();
  });

  it("links to the ticket from the header and runs it from the actions row", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket({ workflow_stage_status: "pending" }));

    const { container } = wrap(
      <TicketPrimitive part={{ primitive: "ticket", ticket_id: "gate-ticket-1" }} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Play" })).toBeEnabled();
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

  it("swaps the play button for stop while the stage runs", async () => {
    mockedApi.ticket.mockResolvedValue(gateTicket({ workflow_stage_status: "running" }));

    const { container } = wrap(
      <TicketPrimitive part={{ primitive: "ticket", ticket_id: "gate-ticket-1" }} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "Play" })).not.toBeInTheDocument();
    expect(
      container.querySelector(".lg-primitive-card-actions .lg-primitive-run-btn--stop"),
    ).not.toBeNull();
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

describe("Workflow primitive graph", () => {
  it("wraps stages, renders parallel agents, and labels gate edges", () => {
    const stage = {
      agent_id: "",
      skill_name: "",
      optional: false,
      gate_required: false,
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

describe("workspace, todo, git, Q&A, and Giphy primitives", () => {
  it("loads a live workspace summary", async () => {
    mockedApi.workspaces.mockResolvedValue([
      {
        id: "ws-1",
        slug: "loregarden",
        name: "Lore Garden",
        repo_path: "/workspace/loregarden",
        repo_root: "/workspace/loregarden",
        repo_exists: true,
        ticket_count: 12,
        blocked_count: 2,
        workflow_template_slug: "tdd",
        cli_adapter: "cursor",
        claude_model: "",
        cursor_model: "gpt-5",
        lmstudio_base_url: "",
        lmstudio_model: "",
      },
    ]);

    wrap(
      <WorkspacePrimitive
        part={{ primitive: "workspace", workspace_slug: "loregarden" }}
      />,
    );

    expect(await screen.findByText("Lore Garden")).toBeInTheDocument();
    expect(screen.getByText("12 tickets")).toBeInTheDocument();
    expect(screen.getByText("2 blocked")).toBeInTheDocument();
    expect(screen.getByText("tdd")).toBeInTheDocument();
  });

  it("keeps agent todos read-only and lets users toggle the same component", () => {
    const agent = render(
      <TodoListPrimitive
        part={{
          primitive: "todo_list",
          owner: "agent",
          items: [{ id: "test", text: "Run tests", checked: false }],
        }}
      />,
    );
    expect(screen.getByRole("checkbox", { name: "Run tests" })).toBeDisabled();
    agent.unmount();

    render(
      <TodoListPrimitive
        part={{
          primitive: "todo_list",
          owner: "user",
          items: [{ id: "review", text: "Review diff", checked: false }],
        }}
      />,
    );
    const checkbox = screen.getByRole("checkbox", { name: "Review diff" });
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    expect(screen.getByText("1/1 complete")).toBeInTheDocument();
  });

  it("keeps user ticks through a re-render but adopts a changed payload", () => {
    const userList = (items: TodoItem[]) => (
      <TodoListPrimitive part={{ primitive: "todo_list", owner: "user", items }} />
    );
    const original: TodoItem[] = [{ id: "review", text: "Review diff", checked: false }];
    const view = render(userList(original));

    fireEvent.click(screen.getByRole("checkbox", { name: "Review diff" }));
    // A fresh array with identical contents stands in for an unrelated
    // re-render of the thread; the tick must survive it.
    view.rerender(userList([{ ...original[0] }]));
    expect(screen.getByRole("checkbox", { name: "Review diff" })).toBeChecked();

    view.rerender(userList([{ id: "ship", text: "Approve release", checked: false }]));
    expect(screen.getByRole("checkbox", { name: "Approve release" })).not.toBeChecked();
    expect(screen.getByText("0/1 complete")).toBeInTheDocument();
  });

  it("keeps typed Q&A answers through a re-render", () => {
    const onSubmit = jest.fn();
    const card = (items: QAItem[]) => (
      <QAPrimitive part={{ primitive: "qa", items }} onSubmit={onSubmit} />
    );
    const items: QAItem[] = [{ id: "scope", question: "Who is this for?" }];
    const view = render(card(items));

    fireEvent.change(screen.getByLabelText("Who is this for?"), {
      target: { value: "Operators" },
    });
    view.rerender(card([{ ...items[0] }]));

    expect(screen.getByLabelText("Who is this for?")).toHaveValue("Operators");
    expect(screen.getByRole("button", { name: "Send answers" })).toBeEnabled();
  });

  it("renders live branch history and commit detail", async () => {
    mockedBranchActivity.mockResolvedValue({
      branch: "main",
      upstream: "origin/main",
      commits: [
        {
          sha: "a".repeat(40),
          short_sha: "aaaaaaa",
          date: new Date().toISOString(),
          author: "Baxter",
          message: "Add primitives",
          pushed: true,
        },
      ],
    });
    mockedCommitSnapshot.mockResolvedValue({
      sha: "a".repeat(40),
      short_sha: "aaaaaaa",
      date: new Date().toISOString(),
      author: "Baxter",
      message: "Add primitives",
      body: "Implement the new cards.",
      pushed: false,
      files_changed: 3,
      insertions: 42,
      deletions: 5,
    });

    const branch = wrap(
      <BranchHistoryPrimitive
        part={{
          primitive: "branch_history",
          workspace_slug: "loregarden",
          branch: "main",
        }}
      />,
    );
    expect(await screen.findByText("Add primitives")).toBeInTheDocument();
    expect(screen.getByText("tracking origin/main")).toBeInTheDocument();
    branch.unmount();

    wrap(
      <CommitPrimitive
        part={{
          primitive: "commit",
          workspace_slug: "loregarden",
          sha: "aaaaaaa",
          branch: "main",
        }}
      />,
    );
    expect(await screen.findByText("Implement the new cards.")).toBeInTheDocument();
    expect(screen.getByText("3 files")).toBeInTheDocument();
    expect(screen.getByText("+42")).toBeInTheDocument();
    expect(screen.getByText("−5")).toBeInTheDocument();
  });

  it("sends complete Q&A responses through the chat callback", () => {
    const onSubmit = jest.fn();
    render(
      <QAPrimitive
        part={{
          primitive: "qa",
          items: [{ id: "scope", question: "Who is this for?" }],
        }}
        onSubmit={onSubmit}
      />,
    );

    const send = screen.getByRole("button", { name: "Send answers" });
    expect(send).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Who is this for?"), {
      target: { value: "Operators" },
    });
    fireEvent.click(send);

    expect(onSubmit).toHaveBeenCalledWith(
      "1. Who is this for?\nAnswer: Operators",
    );
    expect(screen.getByText("Answers sent")).toBeInTheDocument();
  });

  it("renders only allowlisted Giphy media URLs", () => {
    const view = render(
      <GiphyPrimitive
        part={{
          primitive: "giphy",
          giphy_id: "ICOgUNjpvO0PC",
          alt: "Typing cat",
        }}
      />,
    );
    const image = screen.getByRole("img", { name: "Typing cat" });
    expect(image).toHaveAttribute(
      "src",
      "https://media.giphy.com/media/ICOgUNjpvO0PC/giphy.gif",
    );
    fireEvent.error(image);
    expect(screen.getByText("This Giphy image could not be loaded")).toBeInTheDocument();

    view.rerender(
      <GiphyPrimitive
        part={{
          primitive: "giphy",
          url: "https://example.com/tracker.gif",
        }}
      />,
    );
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText(/valid Giphy ID/)).toBeInTheDocument();
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

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Expand" })).toBeInTheDocument();
  });
});
