import type { TicketSummary } from "../../api/types";
import { descendantCount, nestByParent, type KanbanNode } from "../kanbanTree";

function ticket(id: string, parent: string | null = null): TicketSummary {
  return { id, parent_ticket_id: parent } as TicketSummary;
}

/** The tree as nested ids, which is what the assertions care about. */
function shape(nodes: KanbanNode[]): unknown[] {
  return nodes.map((node) => (node.children.length ? { [node.ticket.id]: shape(node.children) } : node.ticket.id));
}

test("hangs a child under its parent in the same column, in the column's order", () => {
  const column = [ticket("m1"), ticket("a", "m1"), ticket("solo"), ticket("b", "m1")];
  expect(shape(nestByParent(column, column))).toEqual([{ m1: ["a", "b"] }, "solo"]);
});

test("nests a grandchild under its parent, not beside it", () => {
  const column = [ticket("task", "feature"), ticket("m1"), ticket("feature", "m1")];
  expect(shape(nestByParent(column, column))).toEqual([{ m1: [{ feature: ["task"] }] }]);
});

test("reaches past a parent in another column to the grandparent in this one", () => {
  const board = [ticket("m1"), ticket("feature", "m1"), ticket("task", "feature")];
  const column = [board[0], board[2]];
  expect(shape(nestByParent(column, board))).toEqual([{ m1: ["task"] }]);
});

test("leaves a ticket whose parent is in another column at the top", () => {
  const board = [ticket("m1"), ticket("a", "m1")];
  expect(shape(nestByParent([board[1]], board))).toEqual(["a"]);
});

test("keeps every ticket of a parent loop on the board", () => {
  const column = [ticket("a", "b"), ticket("b", "a"), ticket("c", "a")];
  const tree = nestByParent(column, column);
  const ids = (nodes: KanbanNode[]): string[] => nodes.flatMap((n) => [n.ticket.id, ...ids(n.children)]);
  expect(ids(tree).sort()).toEqual(["a", "b", "c"]);
  expect(tree).toHaveLength(1);
});

test("counts every ticket under a node, at any depth", () => {
  const column = [ticket("m1"), ticket("f", "m1"), ticket("t1", "f"), ticket("t2", "f")];
  expect(descendantCount(nestByParent(column, column)[0])).toBe(3);
});
