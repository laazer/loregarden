/** The initiative board's data shaping, apart from its component. */

import type { TicketSummary } from "../api/types";
import type { KanbanColumn } from "../components/chat/primitives/KanbanPrimitive";
import { flattenTree, nestByParent } from "./kanbanTree";
import type { BoardColumnKey } from "./ticketStates";

/**
 * Always drawn, in flow order. Blocked is split three ways by who has to act:
 * a person (`needs_you`), another ticket (`waiting`), or an agent (`blocked`).
 * Parked and won't-do appear only when they hold something.
 */
const BASE_COLUMNS: BoardColumnKey[] = ["backlog", "waiting", "in_progress", "needs_you", "blocked", "done"];
const OPTIONAL_COLUMNS: BoardColumnKey[] = ["parked", "wont_do"];

/** Which column a ticket sits in. A server that predates `blocked_on` leaves it in its state's. */
export function columnFor(ticket: TicketSummary): BoardColumnKey {
  if (ticket.state === "blocked" && ticket.blocked_on === "person") return "needs_you";
  if (ticket.blocked_on === "tickets" && (ticket.state === "blocked" || ticket.state === "backlog")) {
    return "waiting";
  }
  return ticket.state;
}

/** Ticket families (a ticket and everything under it in the column) drawn per column before "Show all". */
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
 *
 * The cap counts families, not tickets: a column draws its children under their
 * parent, and a flat cut could keep a child while dropping the parent it hangs
 * from. A family's children start folded when there are many of them.
 */
export function buildColumns(
  tickets: TicketSummary[],
  expanded: ReadonlySet<BoardColumnKey> = new Set(),
): KanbanColumn[] {
  const byState = new Map<BoardColumnKey, TicketSummary[]>();
  for (const ticket of tickets) {
    const key = columnFor(ticket);
    const list = byState.get(key) ?? [];
    list.push(ticket);
    byState.set(key, list);
  }
  const states = [...BASE_COLUMNS, ...OPTIONAL_COLUMNS.filter((s) => byState.get(s)?.length)];
  return states.map((status) => {
    const all = byState.get(status) ?? [];
    const families = nestByParent(all, tickets);
    if (expanded.has(status) || families.length <= COLUMN_SHOWN) return { status, tickets: flattenTree(families) };
    return { status, tickets: flattenTree(families.slice(0, COLUMN_SHOWN)), total: all.length };
  });
}
