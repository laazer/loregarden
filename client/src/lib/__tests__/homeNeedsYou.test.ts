import type { Approval } from "../../api/chatTypes";
import type { TicketSummary } from "../../api/types";
import { failedToday, needsYouRows, pendingApprovals, type RecentRun } from "../homeNeedsYou";

const NOW = new Date(2026, 9, 26, 15, 0);
const at = (hours: number) => new Date(2026, 9, 26, hours, 0).toISOString();
const YESTERDAY = new Date(2026, 9, 25, 18, 0).toISOString();

function approval(overrides: Partial<Approval>): Approval {
  return {
    id: "a",
    title: "Merge PR",
    level: "ask",
    workspace_slug: "loregarden",
    stage_key: "",
    stage_name: "",
    impact: "",
    ticket_id: "t1",
    ticket_external_id: "lg-1",
    kind: "workflow_gate",
    status: "pending",
    run_id: "",
    tool_name: "",
    tool_input_json: "{}",
    cli_adapter: "",
    ...overrides,
  };
}

function run(overrides: Partial<RecentRun>): RecentRun {
  return {
    id: "r",
    status: "failed",
    agent_id: "test_designer",
    stage_key: "test-design",
    ticket_id: "t5",
    ticket_title: "Harness",
    finished_at: at(11),
    ...overrides,
  };
}

const ticket = (overrides: Partial<TicketSummary>) =>
  ({
    id: "t",
    external_id: "lg-t",
    title: "T",
    state: "in_progress",
    ...overrides,
  }) as TicketSummary;

test("only pending approvals count", () => {
  expect(
    pendingApprovals([approval({ id: "1" }), approval({ id: "2", status: "approved" })]).map(
      (a) => a.id,
    ),
  ).toEqual(["1"]);
});

test("approvals group by what they ask, biggest pile first, naming the ticket or how many", () => {
  const rows = needsYouRows(
    [
      approval({ id: "1", title: "Install hooks" }),
      approval({ id: "2", title: "Merge PR", ticket_id: "t1" }),
      approval({ id: "3", title: "Merge PR", ticket_id: "t2" }),
    ],
    [],
    [],
  );
  expect(
    rows.map((row) => [row.title, row.kind === "approval" ? row.count : 0, row.where]),
  ).toEqual([
    ["Merge PR", 2, "2 tickets"],
    ["Install hooks", 1, "lg-1"],
  ]);
  // A group across tickets opens the inbox itself; one on a single ticket opens there.
  expect(rows[0].kind === "approval" && rows[0].ticketId).toBeNull();
  expect(rows[1].kind === "approval" && rows[1].ticketId).toBe("t1");
});

test("today's failures group per ticket and stage", () => {
  const rows = needsYouRows(
    [],
    [run({ id: "1" }), run({ id: "2" }), run({ id: "3", stage_key: "spec" })],
    [],
  );
  expect(rows.map((row) => [row.kind, row.kind === "failed" ? row.count : 0, row.where])).toEqual([
    ["failed", 2, "test-design"],
    ["failed", 1, "spec"],
  ]);
});

test("a ticket waits on a person when parked or blocked on someone, not on other tickets", () => {
  const rows = needsYouRows(
    [],
    [],
    [
      ticket({ id: "p", state: "parked" }),
      ticket({ id: "h", state: "blocked", blocked_on: "person" }),
      ticket({ id: "x", state: "blocked", blocked_on: "tickets" }),
      ticket({ id: "i", state: "in_progress" }),
    ],
  );
  expect(rows.map((row) => row.key)).toEqual(["person-p", "person-h"]);
});

test("failed today keeps today's failures only", () => {
  const { runs, complete } = failedToday(
    [
      run({ id: "1" }),
      run({ id: "2", status: "succeeded" }),
      run({ id: "3", finished_at: YESTERDAY }),
    ],
    NOW,
    100,
  );
  expect(runs.map((r) => r.id)).toEqual(["1"]);
  expect(complete).toBe(true);
});

test("a full window that never reaches yesterday says its count is a floor", () => {
  const window = [run({ id: "1" }), run({ id: "2", status: "succeeded", finished_at: at(9) })];
  expect(failedToday(window, NOW, 2).complete).toBe(false);
  // The same window with room left, or reaching yesterday, is the whole day.
  expect(failedToday(window, NOW, 3).complete).toBe(true);
  expect(
    failedToday([run({ id: "1" }), run({ id: "y", finished_at: YESTERDAY })], NOW, 2).complete,
  ).toBe(true);
});
