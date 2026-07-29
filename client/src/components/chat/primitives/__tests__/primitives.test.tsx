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
import { PrimitiveCard } from "../PrimitiveCard";
import { PrimitiveParts } from "../PrimitiveParts";
import { primitiveSize, widestPrimitiveSize } from "../primitiveFrame";
import { UnknownPrimitiveCard } from "../registry";
import {
  OpenAgentStudioButton,
  OpenGateStudioButton,
  OpenIdeButton,
  OpenTicketButton,
  OpenWorkflowStudioButton,
} from "../ResourceActionButton";
import { TerminalPrimitive } from "../TerminalPrimitive";
import { ThinkingPrimitive } from "../ThinkingPrimitive";
import { WorkflowPrimitive } from "../WorkflowPrimitive";
import {
  childProgressPercent,
  stageProgressPercent,
} from "../ticketProgress";
import type { ChatPart } from "../types";

jest.mock("../../../../api/client", () => {
  const actual = jest.requireActual("../../../../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      ticket: jest.fn(),
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
