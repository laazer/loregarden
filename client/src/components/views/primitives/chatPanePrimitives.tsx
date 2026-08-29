/**
 * The thirteen chat primitives this ticket found adaptable, as view containers.
 *
 * Each one is a `defineChatPanePrimitive` call and nothing else: display
 * metadata, the settings schema 554's editor generates its inputs from, a
 * `parseSettings`, the `toPart` that rebuilds the chat payload, and the sentence
 * the pane shows before it has been given an identifier. All the machinery —
 * the fill-and-scroll box, the navigation suppression, the erasure of the
 * settings generic — is in `chatPanePrimitive`, once.
 *
 * The verdict behind every entry, and behind the ten that are absent, is in
 * `chatPrimitiveVerdicts`. A `Record<PrimitiveKind, …>` there and a test here
 * keep the two in step, so an entry cannot be added without a recorded reason
 * and a reason cannot be recorded without the entry it promises.
 *
 * ## `toPart` is a literal, deliberately
 *
 * It could have been an assertion. Writing the part out instead means the
 * compiler checks it against `chat/primitives/types.ts` — `kind` fixes which
 * member of `ChatPart` is required — so a field renamed on the chat side breaks
 * the build here rather than rendering a card with a blank title, and no entry
 * needs an escape hatch. That is AC8.
 *
 * ## Lists
 *
 * Four primitives take an array (`ticket_ids`, `statuses`, `filters`).
 * `SettingsField` has three kinds and none of them is a list, and inventing a
 * fourth would ripple through 554's editor for four fields. A comma-separated
 * string, split here, is the whole of the wrapper the audit calls for; the
 * fields say so in their help text.
 */

import {
  BranchHistoryPrimitive,
  CommitPrimitive,
} from "../../chat/primitives/GitPrimitive";
import { AgentPrimitive } from "../../chat/primitives/AgentPrimitive";
import { GatePrimitive } from "../../chat/primitives/GatePrimitive";
import {
  KanbanPrimitive,
  StatusColumnPrimitive,
} from "../../chat/primitives/KanbanPrimitive";
import { ParentTicketPrimitive } from "../../chat/primitives/ParentTicketPrimitive";
import { TicketListPrimitive } from "../../chat/primitives/TicketListPrimitive";
import { TicketPrimitive } from "../../chat/primitives/TicketPrimitive";
import { TicketWorkflowPrimitive } from "../../chat/primitives/TicketWorkflowPrimitive";
import { WorkflowPrimitive } from "../../chat/primitives/WorkflowPrimitive";
import { WorkspacePrimitive } from "../../chat/primitives/WorkspacePrimitive";
import {
  blank,
  defineChatPanePrimitive,
  settingCount,
  settingString,
} from "./chatPanePrimitive";
import type { RegisteredPrimitive } from "./types";

/**
 * A comma-separated settings string as the list the part wants.
 *
 * Empty entries are dropped rather than passed through: `"a,,b"` is a typo, and
 * an empty id sent to `api.ticket("")` is a request for a ticket that cannot
 * exist — the same failure `missing` exists to prevent for the whole pane.
 */
function settingList(raw: Record<string, unknown>, key: string): string[] {
  return settingString(raw, key)
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item !== "");
}

/** The help line every comma-list field carries, so the format is said once. */
const LIST_HELP = "Comma-separated. Leave empty for the default.";

/**
 * The stored value of `key`, or `fallback` when it is blank.
 *
 * For a string setting whose empty value is not "wait for the operator" but "we
 * have to pick something": a status column filtering on `""` matches no ticket
 * and renders permanently empty, which reads as a broken fetch rather than as a
 * cleared field.
 */
function settingOr(raw: Record<string, unknown>, key: string, fallback: string): string {
  const value = settingString(raw, key);
  return blank(value) ? fallback : value;
}

// ── Tickets ────────────────────────────────────────────────────────────────

const ticketPanePrimitive = defineChatPanePrimitive({
  kind: "ticket",
  displayName: "Ticket",
  icon: "◆",
  category: "Tickets",
  settingsFields: [
    {
      key: "ticket_id",
      kind: "string",
      label: "Ticket",
      default: "",
      help: "The ticket this card shows, by id or external id.",
    },
  ],
  parseSettings: (raw) => ({ ticketId: settingString(raw, "ticket_id") }),
  Chat: TicketPrimitive,
  toPart: (settings) => ({ primitive: "ticket", ticket_id: settings.ticketId }),
  missing: (settings) => (blank(settings.ticketId) ? "This card has no ticket yet." : null),
});

const ticketWorkflowPanePrimitive = defineChatPanePrimitive({
  kind: "ticket_workflow",
  displayName: "Ticket Workflow",
  icon: "⇢",
  category: "Tickets",
  settingsFields: [
    {
      key: "ticket_id",
      kind: "string",
      label: "Ticket",
      default: "",
      help: "The ticket whose stage timeline this pane draws.",
    },
  ],
  parseSettings: (raw) => ({ ticketId: settingString(raw, "ticket_id") }),
  Chat: TicketWorkflowPrimitive,
  toPart: (settings) => ({
    primitive: "ticket_workflow",
    ticket_id: settings.ticketId,
  }),
  missing: (settings) => (blank(settings.ticketId) ? "This timeline has no ticket yet." : null),
});

const parentTicketPanePrimitive = defineChatPanePrimitive({
  kind: "parent_ticket",
  displayName: "Parent Ticket",
  icon: "⌸",
  category: "Tickets",
  settingsFields: [
    {
      key: "ticket_id",
      kind: "string",
      label: "Parent ticket",
      default: "",
      help: "The parent whose children this pane lists beneath it.",
    },
  ],
  parseSettings: (raw) => ({ ticketId: settingString(raw, "ticket_id") }),
  Chat: ParentTicketPrimitive,
  toPart: (settings) => ({
    primitive: "parent_ticket",
    ticket_id: settings.ticketId,
  }),
  missing: (settings) => (blank(settings.ticketId) ? "This card has no parent ticket yet." : null),
});

const ticketListPanePrimitive = defineChatPanePrimitive({
  kind: "ticket_list",
  displayName: "Ticket List",
  icon: "☰",
  category: "Tickets",
  settingsFields: [
    {
      key: "parent_ticket_id",
      kind: "string",
      label: "Parent ticket",
      default: "",
      help: "List this parent's children. Leave empty for the whole ticket tree.",
    },
    {
      key: "ticket_ids",
      kind: "string",
      label: "Ticket ids",
      default: "",
      help: `Narrow the tree to these tickets. ${LIST_HELP}`,
    },
  ],
  parseSettings: (raw) => ({
    parentTicketId: settingString(raw, "parent_ticket_id"),
    ticketIds: settingList(raw, "ticket_ids"),
  }),
  Chat: TicketListPrimitive,
  toPart: (settings) => ({
    primitive: "ticket_list",
    // `null` rather than `""`: the component branches on truthiness, and an
    // empty string read as a parent id would fetch that parent's children.
    parent_ticket_id: blank(settings.parentTicketId) ? null : settings.parentTicketId,
    ticket_ids: settings.ticketIds,
  }),
  // Both fields are optional here: with neither, the card lists the whole
  // ticket tree, which is a defensible thing for a pane to show.
  missing: () => null,
});

const gatePanePrimitive = defineChatPanePrimitive({
  kind: "gate",
  displayName: "Gate",
  icon: "⛊",
  category: "Tickets",
  settingsFields: [
    {
      key: "ticket_id",
      kind: "string",
      label: "Ticket",
      default: "",
      help: "The ticket whose gate this pane watches.",
    },
    {
      key: "stage_key",
      kind: "string",
      label: "Stage",
      default: "",
      help: "Which gate stage. Leave empty for the ticket's first one.",
    },
  ],
  parseSettings: (raw) => ({
    ticketId: settingString(raw, "ticket_id"),
    stageKey: settingString(raw, "stage_key"),
  }),
  Chat: GatePrimitive,
  toPart: (settings) => ({
    primitive: "gate",
    ticket_id: settings.ticketId,
    stage_key: blank(settings.stageKey) ? null : settings.stageKey,
  }),
  missing: (settings) => (blank(settings.ticketId) ? "This gate has no ticket yet." : null),
});

// ── Boards ─────────────────────────────────────────────────────────────────

const statusColumnPanePrimitive = defineChatPanePrimitive({
  kind: "status_column",
  displayName: "Status Column",
  icon: "▮",
  category: "Boards",
  settingsFields: [
    {
      key: "status",
      kind: "string",
      label: "Status",
      default: "in_progress",
      help: "The ticket state this column shows — backlog, in_progress, blocked, done, wont_do.",
    },
    {
      key: "ticket_ids",
      kind: "string",
      label: "Ticket ids",
      default: "",
      help: `Draw from these tickets. ${LIST_HELP}`,
    },
  ],
  parseSettings: (raw) => ({
    // The declared default is a real state rather than "", so a freshly dropped
    // column shows something, and a cleared field falls back to the same one.
    status: settingOr(raw, "status", "in_progress"),
    ticketIds: settingList(raw, "ticket_ids"),
  }),
  Chat: StatusColumnPrimitive,
  toPart: (settings) => ({
    primitive: "status_column",
    status: settings.status,
    ticket_ids: settings.ticketIds,
  }),
  missing: () => null,
});

const kanbanPanePrimitive = defineChatPanePrimitive({
  kind: "kanban",
  displayName: "Kanban",
  icon: "▤",
  category: "Boards",
  settingsFields: [
    {
      key: "statuses",
      kind: "string",
      label: "Columns",
      default: "",
      help: `Ticket states, in order. ${LIST_HELP}`,
    },
    {
      key: "ticket_ids",
      kind: "string",
      label: "Ticket ids",
      default: "",
      help: `Draw from these tickets. ${LIST_HELP}`,
    },
  ],
  parseSettings: (raw) => ({
    statuses: settingList(raw, "statuses"),
    ticketIds: settingList(raw, "ticket_ids"),
  }),
  Chat: KanbanPrimitive,
  toPart: (settings) => ({
    primitive: "kanban",
    statuses: settings.statuses,
    ticket_ids: settings.ticketIds,
  }),
  missing: () => null,
});

const filterableKanbanPanePrimitive = defineChatPanePrimitive({
  kind: "filterable_kanban",
  displayName: "Filterable Board",
  icon: "▦",
  category: "Boards",
  settingsFields: [
    {
      key: "statuses",
      kind: "string",
      label: "Columns",
      default: "",
      help: `Ticket states, in order. ${LIST_HELP}`,
    },
    {
      key: "filters",
      kind: "string",
      label: "Filter toggles",
      default: "",
      help: `Which states get a toggle. ${LIST_HELP}`,
    },
    {
      key: "ticket_ids",
      kind: "string",
      label: "Ticket ids",
      default: "",
      help: `Draw from these tickets. ${LIST_HELP}`,
    },
  ],
  parseSettings: (raw) => ({
    statuses: settingList(raw, "statuses"),
    filters: settingList(raw, "filters"),
    ticketIds: settingList(raw, "ticket_ids"),
  }),
  Chat: KanbanPrimitive,
  toPart: (settings) => ({
    primitive: "filterable_kanban",
    statuses: settings.statuses,
    filters: settings.filters,
    ticket_ids: settings.ticketIds,
  }),
  missing: () => null,
});

// ── Studio ─────────────────────────────────────────────────────────────────

const agentPanePrimitive = defineChatPanePrimitive({
  kind: "agent",
  displayName: "Agent",
  icon: "☉",
  category: "Studio",
  settingsFields: [
    {
      key: "slug",
      kind: "string",
      label: "Agent",
      default: "",
      help: "The agent slug this pane previews, e.g. frontend_implementer.",
    },
  ],
  parseSettings: (raw) => ({ slug: settingString(raw, "slug") }),
  Chat: AgentPrimitive,
  toPart: (settings) => ({ primitive: "agent", slug: settings.slug }),
  missing: (settings) => (blank(settings.slug) ? "This card has no agent yet." : null),
});

const workflowPanePrimitive = defineChatPanePrimitive({
  kind: "workflow",
  displayName: "Workflow",
  icon: "❖",
  category: "Studio",
  settingsFields: [
    {
      key: "workflow_slug",
      kind: "string",
      label: "Workflow",
      default: "",
      help: "The workflow template slug whose stage graph this pane draws.",
    },
  ],
  parseSettings: (raw) => ({ workflowSlug: settingString(raw, "workflow_slug") }),
  Chat: WorkflowPrimitive,
  toPart: (settings) => ({
    primitive: "workflow",
    workflow_slug: settings.workflowSlug,
  }),
  missing: (settings) => (blank(settings.workflowSlug) ? "This graph has no workflow yet." : null),
});

// ── Workspace and repository ───────────────────────────────────────────────

const workspacePanePrimitive = defineChatPanePrimitive({
  kind: "workspace",
  displayName: "Workspace",
  icon: "▣",
  category: "Workspace",
  settingsFields: [
    {
      key: "workspace_slug",
      kind: "string",
      label: "Workspace",
      default: "",
      help: "The workspace this card summarises.",
    },
  ],
  parseSettings: (raw) => ({ workspaceSlug: settingString(raw, "workspace_slug") }),
  Chat: WorkspacePrimitive,
  toPart: (settings) => ({
    primitive: "workspace",
    workspace_slug: settings.workspaceSlug,
  }),
  missing: (settings) =>
    blank(settings.workspaceSlug) ? "This card has no workspace yet." : null,
});

const branchHistoryPanePrimitive = defineChatPanePrimitive({
  kind: "branch_history",
  displayName: "Branch History",
  icon: "⑂",
  category: "Repository",
  settingsFields: [
    {
      key: "workspace_slug",
      kind: "string",
      label: "Workspace",
      default: "",
      help: "The workspace whose repository holds the branch.",
    },
    {
      key: "branch",
      kind: "string",
      label: "Branch",
      default: "",
      help: "The branch whose recent commits this pane lists.",
    },
    {
      key: "limit",
      kind: "number",
      label: "Commits",
      default: 8,
      help: "How many recent commits to show.",
    },
  ],
  parseSettings: (raw) => ({
    workspaceSlug: settingString(raw, "workspace_slug"),
    branch: settingString(raw, "branch"),
    limit: settingCount(raw, "limit", 8),
  }),
  Chat: BranchHistoryPrimitive,
  toPart: (settings) => ({
    primitive: "branch_history",
    workspace_slug: settings.workspaceSlug,
    branch: settings.branch,
    limit: settings.limit,
  }),
  // Both, not either: the fetch takes the pair, and a branch with no workspace
  // names no repository.
  missing: (settings) =>
    blank(settings.workspaceSlug) || blank(settings.branch)
      ? "This history has no workspace and branch yet."
      : null,
});

const commitPanePrimitive = defineChatPanePrimitive({
  kind: "commit",
  displayName: "Commit",
  icon: "◉",
  category: "Repository",
  settingsFields: [
    {
      key: "workspace_slug",
      kind: "string",
      label: "Workspace",
      default: "",
      help: "The workspace whose repository holds the commit.",
    },
    {
      key: "sha",
      kind: "string",
      label: "Commit",
      default: "",
      help: "The commit SHA this pane shows.",
    },
    {
      key: "branch",
      kind: "string",
      label: "Branch",
      default: "",
      help: "Optional — shown beside the commit.",
    },
  ],
  parseSettings: (raw) => ({
    workspaceSlug: settingString(raw, "workspace_slug"),
    sha: settingString(raw, "sha"),
    branch: settingString(raw, "branch"),
  }),
  Chat: CommitPrimitive,
  toPart: (settings) => ({
    primitive: "commit",
    workspace_slug: settings.workspaceSlug,
    sha: settings.sha,
    branch: blank(settings.branch) ? null : settings.branch,
  }),
  missing: (settings) =>
    blank(settings.workspaceSlug) || blank(settings.sha)
      ? "This card has no workspace and commit yet."
      : null,
});

/**
 * The thirteen, in the order the picker groups them.
 *
 * Exported as one list so `registry.tsx` spreads it rather than naming thirteen
 * imports — adding a fourteenth is an entry here and nothing else.
 */
export const CHAT_PANE_PRIMITIVES: RegisteredPrimitive[] = [
  ticketPanePrimitive,
  ticketWorkflowPanePrimitive,
  parentTicketPanePrimitive,
  ticketListPanePrimitive,
  gatePanePrimitive,
  statusColumnPanePrimitive,
  kanbanPanePrimitive,
  filterableKanbanPanePrimitive,
  agentPanePrimitive,
  workflowPanePrimitive,
  workspacePanePrimitive,
  branchHistoryPanePrimitive,
  commitPanePrimitive,
];
