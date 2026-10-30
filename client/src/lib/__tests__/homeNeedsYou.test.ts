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

type ShipViolation = {
  ticket_id: string;
  external_id: string;
  title: string;
  reason: "failing" | "pending" | "never_reached_remote";
  pr_number: number | null;
  pr_url: string | null;
  check_names: string[];
  carrying_branch: string;
};

function ship(overrides: Partial<ShipViolation> = {}): ShipViolation {
  return {
    ticket_id: "t-1624",
    external_id: "lg-living-map-1624",
    title: "Map the hive",
    reason: "failing",
    pr_number: 733,
    pr_url: "https://github.com/o/r/pull/733",
    check_names: ["Server (Python)", "Client (TypeScript)"],
    carrying_branch: "integration/lg-living-map-1618",
    ...overrides,
  };
}

test("one done ticket with a failing required check is a ship row naming the PR", () => {
  const rows = needsYouRows([], [], [], [ship()]);
  expect(rows).toHaveLength(1);
  const row = rows[0];
  expect(row.kind).toBe("ship");
  expect(row.chip).toEqual({ tone: "bad", label: "Ship" });
  expect(row.title).toBe("Map the hive");
  expect(row.where).toBe("PR #733 failing · Server (Python), Client (TypeScript)");
  expect(row.ticketId).toBe("t-1624");
});

test("one pending required check uses wait tone and pending where-text", () => {
  const rows = needsYouRows([], [], [], [ship({ reason: "pending", check_names: ["Server (Python)"] })]);
  expect(rows[0].chip).toEqual({ tone: "wait", label: "Ship" });
  expect(rows[0].where).toBe("PR #733 pending · Server (Python)");
});

test("never reached remote uses you tone and that where-text", () => {
  const rows = needsYouRows(
    [],
    [],
    [],
    [ship({ reason: "never_reached_remote", pr_number: null, pr_url: null, check_names: [] })],
  );
  expect(rows[0].chip).toEqual({ tone: "you", label: "Ship" });
  expect(rows[0].title).toBe("Map the hive");
  expect(rows[0].where).toBe("never reached remote");
});

test("two done tickets on PR #733 collapse to one ship row; click ticketId is the lower external_id", () => {
  const rows = needsYouRows(
    [],
    [],
    [],
    [
      ship({ ticket_id: "id-z", external_id: "lg-ship-z", title: "Later" }),
      ship({ ticket_id: "id-a", external_id: "lg-ship-a", title: "Earlier" }),
    ],
  );
  expect(rows).toHaveLength(1);
  expect(rows[0].kind).toBe("ship");
  expect(rows[0].title).toBe("PR #733 failing");
  expect(rows[0].where).toBe("2 tickets");
  expect("count" in rows[0] && rows[0].count).toBe(2);
  expect(rows[0].ticketId).toBe("id-a");
});

test("several never-reached-remote on one carrying branch collapse to one row", () => {
  const rows = needsYouRows(
    [],
    [],
    [],
    [
      ship({
        ticket_id: "n2",
        external_id: "lg-n-2",
        reason: "never_reached_remote",
        pr_number: null,
        check_names: [],
        carrying_branch: "integration/lg-root",
      }),
      ship({
        ticket_id: "n1",
        external_id: "lg-n-1",
        reason: "never_reached_remote",
        pr_number: null,
        check_names: [],
        carrying_branch: "integration/lg-root",
      }),
    ],
  );
  expect(rows).toHaveLength(1);
  expect(rows[0].title).toBe("never reached remote");
  expect(rows[0].where).toBe("2 tickets");
  expect(rows[0].chip).toEqual({ tone: "you", label: "Ship" });
  expect(rows[0].ticketId).toBe("n1");
});

test("ship rows sit after approvals and before failed-today", () => {
  const rows = needsYouRows(
    [approval({ id: "1", title: "Install hooks" })],
    [run({ id: "1" })],
    [ticket({ id: "p", state: "parked" })],
    [ship()],
  );
  expect(rows.map((row) => row.kind)).toEqual(["approval", "ship", "failed", "person"]);
});

test("among ships, failing then pending then never-reached-remote, then PR number", () => {
  const rows = needsYouRows(
    [],
    [],
    [],
    [
      ship({
        ticket_id: "n",
        external_id: "lg-n",
        reason: "never_reached_remote",
        pr_number: null,
        check_names: [],
      }),
      ship({
        ticket_id: "p",
        external_id: "lg-p",
        reason: "pending",
        pr_number: 10,
        check_names: [],
      }),
      ship({
        ticket_id: "f2",
        external_id: "lg-f2",
        reason: "failing",
        pr_number: 900,
        check_names: [],
      }),
      ship({
        ticket_id: "f1",
        external_id: "lg-f1",
        reason: "failing",
        pr_number: 12,
        check_names: [],
      }),
    ],
  );
  expect(rows.map((row) => row.where)).toEqual([
    "PR #12 failing",
    "PR #900 failing",
    "PR #10 pending",
    "never reached remote",
  ]);
});

test("never-reached-remote on different carrying branches stay separate rows", () => {
  const rows = needsYouRows(
    [],
    [],
    [],
    [
      ship({
        ticket_id: "n2",
        external_id: "lg-n-2",
        reason: "never_reached_remote",
        pr_number: null,
        check_names: [],
        carrying_branch: "integration/b",
      }),
      ship({
        ticket_id: "n1",
        external_id: "lg-n-1",
        reason: "never_reached_remote",
        pr_number: null,
        check_names: [],
        carrying_branch: "integration/a",
      }),
    ],
  );
  expect(rows).toHaveLength(2);
  expect(rows.map((row) => row.ticketId).sort()).toEqual(["n1", "n2"]);
  expect(rows.every((row) => row.title === "never reached remote" || row.where === "never reached remote")).toBe(
    true,
  );
});

test("check-names suffix keeps two names and then N more", () => {
  const two = needsYouRows([], [], [], [ship()]);
  expect(two[0].where).toBe("PR #733 failing · Server (Python), Client (TypeScript)");
  const three = needsYouRows(
    [],
    [],
    [],
    [ship({ check_names: ["Server (Python)", "Client (TypeScript)", "Lint"] })],
  );
  expect(three[0].where).toBe("PR #733 failing · Server (Python), Client (TypeScript) and 1 more");
  const none = needsYouRows([], [], [], [ship({ check_names: [] })]);
  expect(none[0].where).toBe("PR #733 failing");
});
