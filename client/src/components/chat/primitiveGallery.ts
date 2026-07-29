import type { TicketSummary } from "../../api/types";
import type { ChatPart } from "./primitives/types";

/** Stable studio refs that exist in this workspace — prefer live fetch over drafts. */
const GALLERY_AGENT_SLUG = "planner";
const GALLERY_WORKFLOW_SLUG = "studio-loregarden-tdd-v3";
const GALLERY_GATE_STAGE = "gate";

/** Opening of the live Planner agent role file (trimmed for the edit card). */
const PLANNER_ROLE_EXCERPT = `---
description: Planner Agent – decomposes work into detailed, testable tickets without writing code.
model: claude-3.7-sonnet
globs: []
alwaysApply: false
---
You are Planner Agent. Your sole responsibility is to transform any project, task, or request into a fully detailed, actionable, and testable execution plan. You DO NOT write code, tests, or implementation. Your output must be a structured plan that another agent can execute directly without interpretation.

**Workflow compliance:** All execution must comply with the Workflow Enforcement Module (\`agent_context/agents/common_assets/workflow_enforcement_v1.md\`) in addition to this agent's role definition. Read that module before acting on any ticket.

**Loregarden MCP:** When Loregarden orchestrates this run, read and use \`agent_context/agents/common_assets/loregarden_mcp_v1.md\` — use MCP tools for ticket workflow state instead of inventing markdown deliverables.
`;

export type PrimitiveGalleryInput = {
  tickets?: TicketSummary[];
  /** @deprecated Prefer `tickets`. */
  ticketIds?: string[];
};

/** One exchange of the gallery: what the operator asked, and what Baxter rendered.
 *  The asks are what separate the cards visually — without them the whole gallery
 *  reads as one anonymous block and it is not obvious the cards are replies. */
export interface PrimitiveGallerySection {
  id: string;
  ask: string;
  /** Baxter's one-line answer. Leads `parts`, and doubles as the turn's text. */
  reply: string;
  parts: ChatPart[];
}

function section(
  id: string,
  ask: string,
  reply: string,
  ...cards: ChatPart[]
): PrimitiveGallerySection {
  return { id, ask, reply, parts: [{ primitive: "text", content: reply }, ...cards] };
}

type GalleryTicket = Pick<
  TicketSummary,
  | "id"
  | "title"
  | "state"
  | "parent_ticket_id"
  | "workflow_stage_key"
  | "workflow_stage_name"
  | "workspace_slug"
  | "branch"
>;

function pickGalleryTickets(tickets: GalleryTicket[]): {
  primary: GalleryTicket | null;
  parent: GalleryTicket | null;
  gate: GalleryTicket | null;
  board: GalleryTicket[];
} {
  if (!tickets.length) {
    return { primary: null, parent: null, gate: null, board: [] };
  }

  const parentIds = new Set(
    tickets.map((t) => t.parent_ticket_id).filter((id): id is string => Boolean(id)),
  );
  const parent =
    tickets.find((t) => parentIds.has(t.id)) ??
    tickets.find((t) => !t.parent_ticket_id) ??
    tickets[0];

  const primary =
    tickets.find((t) => t.state === "in_progress") ??
    tickets.find((t) => t.state === "blocked") ??
    parent;

  const gate =
    tickets.find(
      (t) =>
        t.workflow_stage_key === GALLERY_GATE_STAGE ||
        /gate/i.test(t.workflow_stage_name ?? ""),
    ) ??
    tickets.find((t) => t.state === "blocked" && t.id !== primary?.id) ??
    tickets.find((t) => t.state === "done" && t.id !== primary?.id) ??
    tickets.find((t) => t.id !== primary?.id) ??
    primary;

  const byState = new Map<string, GalleryTicket[]>();
  for (const ticket of tickets) {
    const list = byState.get(ticket.state) ?? [];
    list.push(ticket);
    byState.set(ticket.state, list);
  }
  const board: GalleryTicket[] = [];
  for (const state of ["in_progress", "blocked", "backlog", "done"]) {
    for (const ticket of byState.get(state) ?? []) {
      if (board.length >= 8) break;
      if (!board.some((t) => t.id === ticket.id)) board.push(ticket);
    }
  }
  for (const ticket of tickets) {
    if (board.length >= 8) break;
    if (!board.some((t) => t.id === ticket.id)) board.push(ticket);
  }

  return { primary, parent, gate, board };
}

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function primitiveGallerySections(
  input: PrimitiveGalleryInput | string[] = {},
): PrimitiveGallerySection[] {
  const tickets = Array.isArray(input)
    ? []
    : (input.tickets ?? []);
  const legacyIds = Array.isArray(input) ? input : (input.ticketIds ?? []);
  const { primary, parent, gate, board } = pickGalleryTickets(
    tickets.length
      ? tickets
      : legacyIds.map((id) => ({
          id,
          title: id,
          state: "backlog" as const,
          parent_ticket_id: null,
          workflow_stage_key: "",
          workflow_stage_name: "",
          workspace_slug: "loregarden",
          branch: "main",
        })),
  );

  const primaryId = primary?.id ?? legacyIds[0] ?? "example-ticket";
  const parentId = parent?.id ?? primaryId;
  const gateId = gate?.id ?? primaryId;
  const workspaceSlug = primary?.workspace_slug ?? "loregarden";
  const branch = primary?.branch || "main";
  const gateStageKey =
    gate?.workflow_stage_key && /gate/i.test(gate.workflow_stage_key + gate.workflow_stage_name)
      ? gate.workflow_stage_key
      : GALLERY_GATE_STAGE;
  const boardIds = board.length ? board.map((t) => t.id) : legacyIds.slice(0, 8);
  const listIds = boardIds.length ? boardIds : [primaryId];
  const today = isoDate(new Date());
  const dayEvents = listIds.slice(0, 5).map((id, index) => {
    const ticket = board.find((t) => t.id === id);
    const start = new Date();
    start.setHours(9 + index * 2, index % 2 === 0 ? 0 : 30, 0, 0);
    const end = new Date(start);
    end.setMinutes(start.getMinutes() + 45);
    const kinds = ["run", "plan", "scheduled", "one_time", "run"] as const;
    return {
      id: `gallery-day-${id}`,
      title: ticket?.title ?? `Work item ${index + 1}`,
      starts_at: start.toISOString(),
      ends_at: end.toISOString(),
      kind: kinds[index] ?? "plan",
      ticket_id: id,
      description: ticket
        ? `${ticket.state.replaceAll("_", " ")} · ${ticket.workflow_stage_name || ticket.workflow_stage_key || "unscoped"}`
        : "Open tickets in this workspace to bind the schedule.",
    };
  });
  const weekEvents = listIds.slice(5, 8).map((id, index) => {
    const ticket = board.find((t) => t.id === id);
    const start = new Date();
    start.setDate(start.getDate() + index + 1);
    start.setHours(10 + index * 2, index === 1 ? 30 : 0, 0, 0);
    const end = new Date(start);
    end.setMinutes(start.getMinutes() + 45);
    return {
      id: `gallery-week-${id}`,
      title: ticket?.title ?? `Work item ${index + 6}`,
      starts_at: start.toISOString(),
      ends_at: end.toISOString(),
      kind: (["plan", "scheduled", "run"] as const)[index] ?? "plan",
      ticket_id: id,
      description: ticket
        ? `${ticket.state.replaceAll("_", " ")} · ${ticket.workflow_stage_name || ticket.workflow_stage_key || "unscoped"}`
        : "Open tickets in this workspace to bind the schedule.",
    };
  });

  return [
    section(
      "intro",
      "Show me examples of every chat UI primitive.",
      "Here is the **Chat UI Primitive gallery**, wired to live workspace refs where they exist (tickets, Planner agent, Loregarden TDD V3 workflow). Cards that take a ref fetch current state from the API. Ask for one at a time and I will render it.",
      {
        primitive: "thinking",
        content:
          "Prefer thin refs over drafts: ticket_id, agent slug, workflow_slug. Drafts are only for create/preview flows that do not have a saved record yet.",
        collapsed: false,
      },
    ),
    section(
      "ticket",
      "Start with the ticket you are working on.",
      "The ticket card. It reads live state from the `ticket_id` ref.",
      {
        primitive: "ticket",
        ticket_id: primaryId,
        title: primary?.title ?? null,
      },
    ),
    section(
      "ticket-workflow",
      "What do its workflow stages look like?",
      "Every stage on the ticket, with the live one highlighted.",
      {
        primitive: "ticket_workflow",
        ticket_id: primaryId,
        title: primary ? `${primary.title} · stages` : null,
      },
    ),
    section(
      "parent-ticket",
      "Now show me a parent with its children.",
      "The parent card rolls its children up into one progress line.",
      {
        primitive: "parent_ticket",
        ticket_id: parentId,
        title: parent?.title ?? null,
      },
    ),
    section(
      "ticket-list",
      "List the open work.",
      "A ticket list, expandable down the hierarchy.",
      {
        primitive: "ticket_list",
        ticket_ids: listIds,
        title: "Open work",
      },
    ),
    section(
      "status-column",
      "Just the in-progress column.",
      "One status column on its own.",
      {
        primitive: "status_column",
        status: "in_progress",
        ticket_ids: listIds,
        title: "In progress",
      },
    ),
    section("kanban", "Show me the whole board.", "The delivery board, one column per state.", {
      primitive: "kanban",
      ticket_ids: listIds,
      title: "Delivery board",
    }),
    section(
      "filterable-kanban",
      "Same board, but let me choose the columns.",
      "The filterable board — toggle a chip to drop a column.",
      {
        primitive: "filterable_kanban",
        ticket_ids: listIds,
        statuses: ["backlog", "in_progress", "blocked", "done"],
        filters: ["backlog", "in_progress", "blocked", "done"],
        title: "Filterable delivery board",
      },
    ),
    section("agent", "Who would plan this work?", "The agent card, resolved from its slug.", {
      primitive: "agent",
      slug: GALLERY_AGENT_SLUG,
      title: "Planner Agent",
    }),
    section(
      "workflow",
      "Show me the workflow template it runs.",
      "The full pipeline, as the state machine sees it.",
      {
        primitive: "workflow",
        workflow_slug: GALLERY_WORKFLOW_SLUG,
        title: "Loregarden TDD V3",
      },
    ),
    section(
      "gate",
      "What does an approval gate look like?",
      "The gate card, with its checks and anything waiting on you.",
      {
        primitive: "gate",
        ticket_id: gateId,
        stage_key: gateStageKey,
        title: gate?.workflow_stage_name || "Quality Gate",
        draft: {
          name: "Quality Gate",
          description: "Pause for operator sign-off once acceptance criteria and CI look good.",
          checklist: ["Diff reviewed", "Acceptance criteria met", "CI green"],
        },
      },
    ),
    section("terminal", "How did the last test run go?", "The transcript, exactly as it ran.", {
      primitive: "terminal",
      title: "client · npm test",
      cwd: "~/workspace/loregarden/client",
      lines: [
        { kind: "command", text: "npm test" },
        {
          kind: "stdout",
          text: "Test Suites: 1 skipped, 108 passed, 108 of 109 total",
        },
        { kind: "stdout", text: "Tests:       1 skipped, 25 todo, 1590 passed, 1616 total" },
        { kind: "stdout", text: "Time:        19.261 s" },
        { kind: "status", text: "Process exited with code 0" },
      ],
    }),
    section(
      "edit",
      "Let me review your proposed Planner role tweak.",
      "An active diff — hover a line, comment, and send it to chat.",
      {
        primitive: "edit",
        target: "agent",
        target_id: GALLERY_AGENT_SLUG,
        path: "agent_context/agents/1_planner/planner_v1.md",
        workspace_slug: workspaceSlug,
        language: "markdown",
        title: "Tighten Planner scope",
        original: PLANNER_ROLE_EXCERPT,
        content: `${PLANNER_ROLE_EXCERPT.trimEnd()}

**Hard rule:** Never write application code, tests, or config. If the request asks for implementation, stop and hand off to the implementer.
`,
      },
    ),
    section(
      "workspace",
      "Remind me which workspace this is.",
      "The workspace card: repo, runtime, and open work.",
      {
        primitive: "workspace",
        workspace_slug: workspaceSlug,
        title: "Current workspace",
      },
    ),
    section(
      "todo-lists",
      "Show me both kinds of checklist.",
      "Mine is read-only while I work it; yours you tick off.",
      {
        primitive: "todo_list",
        owner: "agent",
        title: "Agent execution plan",
        items: [
          { id: "inspect", text: "Inspect existing contracts", checked: true },
          { id: "implement", text: "Implement the requested change", checked: true },
          { id: "verify", text: "Run focused validation", checked: false },
        ],
      },
      {
        primitive: "todo_list",
        owner: "user",
        title: "Release checklist",
        items: [
          { id: "copy", text: "Review the final copy", checked: false },
          { id: "visual", text: "Confirm the visual treatment", checked: false },
          { id: "ship", text: "Approve release", checked: false },
        ],
      },
    ),
    section(
      "branch-history",
      "What has landed on this branch lately?",
      "Recent commits, and whether they are pushed.",
      {
        primitive: "branch_history",
        workspace_slug: workspaceSlug,
        branch,
        limit: 6,
        title: "Recent branch history",
      },
    ),
    section("commit", "Show me the latest commit on its own.", "One commit, with its diff stats.", {
      primitive: "commit",
      workspace_slug: workspaceSlug,
      sha: "HEAD",
      branch,
      title: "Latest commit",
    }),
    section(
      "qa",
      "Ask me whatever you still need to know.",
      "Two questions — answer them and I will pick the work back up.",
      {
        primitive: "qa",
        title: "Clarifying questions",
        prompt: "Answer these before Baxter continues",
        interactive: true,
        items: [
          {
            id: "audience",
            question: "Who is the primary operator for this workflow?",
            answer: "",
          },
          {
            id: "done",
            question: "What evidence proves the work is complete?",
            answer: "",
          },
        ],
      },
    ),
    section("giphy", "React with a gif.", "Purely for fun.", {
      primitive: "giphy",
      giphy_id: "JIX9t2j0ZTN9S",
      title: "Ship it",
      caption: "A lightweight Giphy reaction card.",
      alt: "A cat typing enthusiastically",
    }),
    section("calendar", "What is scheduled today?", "The day's agenda for this workspace.", {
      primitive: "calendar",
      title: "Workspace schedule",
      view: "day",
      focus_date: today,
      // Projected from open tickets so the day agenda has real titles to read;
      // agent-emitted calendars omit events and load live runs from the API.
      events: [...dayEvents, ...weekEvents],
    }),
    section("calendar-event", "Zoom in on a single event.", "One event, on its own card.", {
      primitive: "calendar_event",
      event: {
        id: `gallery-ticket-${primaryId}`,
        title: primary?.title ?? "Active ticket",
        starts_at: new Date().toISOString(),
        kind: "plan",
        ticket_id: primaryId,
        description: primary
          ? `Live ref to ${primary.state} ticket ${primaryId.slice(0, 8)}…`
          : "Open a workspace ticket to bind this card.",
      },
    }),
  ];
}
