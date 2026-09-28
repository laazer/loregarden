import type { ChatMessageView } from "./chatUtils";
import { agentPlanPartKey, supersededAgentPlanKeys } from "./primitives/agentPlan";
import type { ChatPart, UnknownPart } from "./primitives/types";

/** One card an assistant turn sent, as the side card lists it. */
export interface PrimitiveHistoryEntry {
  /** `${messageId}:${index}` — stable across polls, unique in a thread. */
  key: string;
  messageId: string;
  part: ChatPart | UnknownPart;
  kind: string;
  label: string;
}

/** Prose and reasoning are the turn itself, not a card it produced. */
const NOT_A_CARD = new Set(["text", "thinking"]);

const KIND_LABELS: Record<string, string> = {
  ticket: "Ticket",
  ticket_workflow: "Ticket workflow",
  parent_ticket: "Parent ticket",
  ticket_list: "Ticket list",
  status_column: "Status column",
  kanban: "Board",
  filterable_kanban: "Board",
  agent: "Agent",
  workflow: "Workflow",
  gate: "Gate",
  terminal: "Terminal",
  edit: "Edit",
  calendar: "Calendar",
  calendar_event: "Event",
  workspace: "Workspace",
  todo_list: "Plan",
  branch_history: "Branch history",
  commit: "Commit",
  qa: "Question",
  btw: "Aside",
  giphy: "GIF",
};

export function primitiveKindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind.replace(/_/g, " ");
}

function stringField(part: ChatPart | UnknownPart, field: string): string {
  const value = (part as unknown as Record<string, unknown>)[field];
  return typeof value === "string" ? value.trim() : "";
}

/** The most specific name a card carries, falling back to its kind. */
export function primitiveLabel(part: ChatPart | UnknownPart): string {
  return (
    stringField(part, "title") ||
    stringField(part, "ticket_id") ||
    stringField(part, "slug") ||
    stringField(part, "workflow_slug") ||
    stringField(part, "path") ||
    primitiveKindLabel(part.primitive)
  );
}

/**
 * Every card in the thread, newest first.
 *
 * A plan re-emitted later replaces its earlier card in the thread, so the
 * replaced copies are left out here too — the list names what the thread shows.
 */
export function primitiveHistory(messages: ChatMessageView[]): PrimitiveHistoryEntry[] {
  const superseded = supersededAgentPlanKeys(messages);
  const entries: PrimitiveHistoryEntry[] = [];
  for (const message of messages) {
    if (message.role === "user") continue;
    (message.parts ?? []).forEach((part, index) => {
      if (NOT_A_CARD.has(part.primitive)) return;
      if (superseded.has(agentPlanPartKey(message.id, index))) return;
      entries.push({
        key: `${message.id}:${index}`,
        messageId: message.id,
        part,
        kind: part.primitive,
        label: primitiveLabel(part),
      });
    });
  }
  return entries.reverse();
}
