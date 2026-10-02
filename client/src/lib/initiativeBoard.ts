/** The initiative board's data shaping, apart from its component. */

import type { TicketState, TicketSummary } from "../api/types";
import type { KanbanColumn } from "../components/chat/primitives/KanbanPrimitive";

/** Always drawn, in flow order. Parked and won't-do appear only when they hold something. */
const BASE_COLUMNS: TicketState[] = ["backlog", "in_progress", "blocked", "done"];
const OPTIONAL_COLUMNS: TicketState[] = ["parked", "wont_do"];

/** Tickets drawn per column before "Show all". */
export const COLUMN_SHOWN = 12;

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

/**
 * Columns for the board. Every column is capped, not only done: a phase can
 * hold eighty backlog tickets, and a column that long hides the others. The
 * header counts them all; `expanded` lifts the cap for the columns asked for.
 */
export function buildColumns(
  tickets: TicketSummary[],
  expanded: ReadonlySet<TicketState> = new Set(),
): KanbanColumn[] {
  const byState = new Map<TicketState, TicketSummary[]>();
  for (const ticket of tickets) {
    const list = byState.get(ticket.state) ?? [];
    list.push(ticket);
    byState.set(ticket.state, list);
  }
  const states = [...BASE_COLUMNS, ...OPTIONAL_COLUMNS.filter((s) => byState.get(s)?.length)];
  return states.map((status) => {
    const all = byState.get(status) ?? [];
    if (expanded.has(status) || all.length <= COLUMN_SHOWN) return { status, tickets: all };
    return { status, tickets: all.slice(0, COLUMN_SHOWN), total: all.length };
  });
}
