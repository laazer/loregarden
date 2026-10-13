/** A board column's tickets arranged under their parents. */

import type { TicketSummary } from "../api/types";

export interface KanbanNode {
  ticket: TicketSummary;
  children: KanbanNode[];
}

/**
 * Nest a column's tickets under the nearest ancestor in the same column.
 *
 * Without this, a parent and its children sit side by side as equals, and a
 * backlog of a milestone with 27 sub-tickets reads as 28 unrelated cards. The
 * ancestor chain is walked through `board` (every ticket the board holds), so a
 * grandchild still lands under its grandparent when the parent between them is
 * in another column. A ticket with no ancestor in the column stays at the top.
 * Order within each level is the column's own order.
 */
export function nestByParent(column: readonly TicketSummary[], board: readonly TicketSummary[]): KanbanNode[] {
  const parentOf = new Map<string, string | null>();
  for (const ticket of board) parentOf.set(ticket.id, ticket.parent_ticket_id);
  for (const ticket of column) parentOf.set(ticket.id, ticket.parent_ticket_id);

  const inColumn = new Set(column.map((ticket) => ticket.id));
  const owner = new Map<string, string>();
  for (const ticket of column) {
    const found = nearestAncestorIn(ticket.id, parentOf, inColumn);
    if (found) owner.set(ticket.id, found);
  }
  // Parent links in bad data can loop. Inside one column a loop would make each
  // ticket the other's child and drop them all, so the first ticket of the loop
  // a walk reaches is lifted to the top instead.
  for (const ticket of column) {
    const seen = new Set<string>();
    let cursor: string | undefined = ticket.id;
    while (cursor && !seen.has(cursor)) {
      seen.add(cursor);
      cursor = owner.get(cursor);
    }
    if (cursor) owner.delete(cursor);
  }

  const nodes = new Map(column.map((ticket) => [ticket.id, { ticket, children: [] } as KanbanNode]));
  const roots: KanbanNode[] = [];
  for (const ticket of column) {
    const node = nodes.get(ticket.id)!;
    const ownerId = owner.get(ticket.id);
    if (ownerId) nodes.get(ownerId)!.children.push(node);
    else roots.push(node);
  }
  return roots;
}

function nearestAncestorIn(
  id: string,
  parentOf: ReadonlyMap<string, string | null>,
  inColumn: ReadonlySet<string>,
): string | null {
  const seen = new Set([id]);
  let cursor = parentOf.get(id) ?? null;
  while (cursor && !seen.has(cursor)) {
    if (inColumn.has(cursor)) return cursor;
    seen.add(cursor);
    cursor = parentOf.get(cursor) ?? null;
  }
  return null;
}

/** Every ticket under a node, at any depth. */
export function descendantCount(node: KanbanNode): number {
  return node.children.reduce((sum, child) => sum + 1 + descendantCount(child), 0);
}

/** The tickets of a tree, each parent before its children. */
export function flattenTree(nodes: readonly KanbanNode[]): TicketSummary[] {
  return nodes.flatMap((node) => [node.ticket, ...flattenTree(node.children)]);
}
