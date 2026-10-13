import type { BlockedOn, TicketState, TicketSummary } from "../../api/types";
import { buildColumns, columnFor, milestoneOf } from "../initiativeBoard";

function ticket(
  id: string,
  parent: string | null,
  state: TicketState = "backlog",
  blocked_on: BlockedOn | null = null,
): TicketSummary {
  return { id, parent_ticket_id: parent, state, blocked_on } as TicketSummary;
}

test("a ticket belongs to the milestone above it, however deep", () => {
  const tickets = [
    ticket("m1", "init"),
    ticket("feature", "m1"),
    ticket("capability", "feature"),
    ticket("task", "capability"),
    ticket("stray", "elsewhere"),
  ];
  const owner = milestoneOf(tickets, new Set(["m1"]));
  expect(owner.get("task")).toBe("m1");
  expect(owner.get("feature")).toBe("m1");
  expect(owner.has("stray")).toBe(false);
});

test("a parent cycle does not hang the walk", () => {
  const owner = milestoneOf([ticket("a", "b"), ticket("b", "a")], new Set(["m1"]));
  expect(owner.size).toBe(0);
});

test("every column is capped with its real total; optional columns appear only when used", () => {
  const backlog = Array.from({ length: 30 }, (_, i) => ticket(`b${i}`, "m1", "backlog"));
  const columns = buildColumns([...backlog, ticket("p", "m1", "parked")]);
  expect(columns.map((c) => c.status)).toEqual([
    "backlog",
    "waiting",
    "in_progress",
    "needs_you",
    "blocked",
    "done",
    "parked",
  ]);
  const first = columns.find((c) => c.status === "backlog");
  expect(first?.tickets).toHaveLength(12);
  expect(first?.total).toBe(30);
});

test("an expanded column draws everything", () => {
  const backlog = Array.from({ length: 30 }, (_, i) => ticket(`b${i}`, "m1", "backlog"));
  const column = buildColumns(backlog, new Set(["backlog"]))[0];
  expect(column.tickets).toHaveLength(30);
  expect(column.total).toBeUndefined();
});

test("blocked splits by who has to act; a held backlog ticket waits beside it", () => {
  expect(columnFor(ticket("a", null, "blocked", "person"))).toBe("needs_you");
  expect(columnFor(ticket("b", null, "blocked", "tickets"))).toBe("waiting");
  expect(columnFor(ticket("c", null, "backlog", "tickets"))).toBe("waiting");
  expect(columnFor(ticket("d", null, "blocked", "agent"))).toBe("blocked");
  // A server that predates `blocked_on` keeps every block where it was.
  expect(columnFor(ticket("e", null, "blocked"))).toBe("blocked");
  expect(columnFor(ticket("f", null, "backlog"))).toBe("backlog");
  // Started by name past its prerequisite: it is running, not waiting.
  expect(columnFor(ticket("g", null, "in_progress", "tickets"))).toBe("in_progress");
});
