/** The initiative board's data shaping, apart from its component. */

import type { TicketState, TicketSummary } from "../api/types";
import type { KanbanColumn } from "../components/chat/primitives/KanbanPrimitive";

/** Always drawn, in flow order. Parked and won't-do appear only when they hold something. */
const BASE_COLUMNS: TicketState[] = ["backlog", "in_progress", "blocked", "done"];
const OPTIONAL_COLUMNS: TicketState[] = ["parked", "wont_do"];

/** Done piles up for the life of an initiative. Draw a handful; the header counts them all. */
const DONE_SHOWN = 12;

export function boardQueryKey(initiativeId: string) {
  return ["tickets", "initiative-board", initiativeId] as const;
}

/** Each ticket's milestone, found by walking parents within the subtree. */
export function milestoneOf(tickets: TicketSummary[], milestoneIds: Set<string>): Map<string, string> {
  const parent = new Map(tickets.map((t) => [t.id, t.parent_ticket_id]));
  const owner = new Map<string, string>();
  for (const ticket of tickets) {
    let cursor: string | null | undefined = ticket.parent_ticket_id;
    const seen = new Set<string>();
    while (cursor && !milestoneIds.has(cursor) && !seen.has(cursor)) {
      seen.add(cursor);
      cursor = parent.get(cursor);
    }
    if (cursor && milestoneIds.has(cursor)) owner.set(ticket.id, cursor);
  }
  return owner;
}

export function buildColumns(tickets: TicketSummary[]): KanbanColumn[] {
  const byState = new Map<TicketState, TicketSummary[]>();
  for (const ticket of tickets) {
    const list = byState.get(ticket.state) ?? [];
    list.push(ticket);
    byState.set(ticket.state, list);
  }
  const states = [...BASE_COLUMNS, ...OPTIONAL_COLUMNS.filter((s) => byState.get(s)?.length)];
  return states.map((status) => {
    const all = byState.get(status) ?? [];
    if (status !== "done" || all.length <= DONE_SHOWN) return { status, tickets: all };
    return { status, tickets: all.slice(0, DONE_SHOWN), total: all.length };
  });
}
