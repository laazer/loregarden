import type { TicketState, TicketSummary } from "../../api/types";
import { buildColumns, milestoneOf } from "../initiativeBoard";

function ticket(id: string, parent: string | null, state: TicketState = "backlog"): TicketSummary {
  return { id, parent_ticket_id: parent, state } as TicketSummary;
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

test("done is capped with its real total; optional columns appear only when used", () => {
  const done = Array.from({ length: 30 }, (_, i) => ticket(`d${i}`, "m1", "done"));
  const columns = buildColumns([...done, ticket("p", "m1", "parked")]);
  expect(columns.map((c) => c.status)).toEqual(["backlog", "in_progress", "blocked", "done", "parked"]);
  const doneColumn = columns.find((c) => c.status === "done");
  expect(doneColumn?.tickets).toHaveLength(12);
  expect(doneColumn?.total).toBe(30);
});
