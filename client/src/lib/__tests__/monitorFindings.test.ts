import { conditionLabel, groupFindings } from "../monitorFindings";
import type { MonitorFinding } from "../../api/types";

/**
 * Grouping by (condition, stage) is the whole point of the workspace-wide view:
 * recurrence is the signal, and a flat list of five `stage_thrash` rows reads as
 * five unrelated problems rather than one pipeline fault.
 *
 * The shape under test is blobert's real finding set on 2026-09-11 —
 * `stage_thrash:script_review` x5, `stalled_run:plan` x2, two
 * `unbudgeted_repeat` on different stages, `stalled_run:implement`,
 * `stage_thrash:implement`.
 */

const finding = (over: Partial<MonitorFinding> = {}): MonitorFinding => ({
  condition: "stage_thrash",
  ticket_id: "t1",
  stage_key: "script_review",
  summary: "script_review attempted 12x against a baseline of 2",
  evidence: {},
  occurrences: 1,
  first_seen: null,
  last_seen: null,
  ...over,
});

it("collapses the same condition and stage across tickets into one group", () => {
  const groups = groupFindings([
    finding({ ticket_id: "t1" }),
    finding({ ticket_id: "t2" }),
    finding({ ticket_id: "t3" }),
  ]);

  expect(groups).toHaveLength(1);
  expect(groups[0].tickets).toBe(3);
  expect(groups[0].findings).toHaveLength(3);
});

it("keeps the same condition on different stages apart", () => {
  const groups = groupFindings([
    finding({ condition: "unbudgeted_repeat", stage_key: "test-break" }),
    finding({ condition: "unbudgeted_repeat", stage_key: "implement" }),
  ]);

  expect(groups).toHaveLength(2);
  expect(groups.map((g) => g.stageKey).sort()).toEqual(["implement", "test-break"]);
});

it("orders the most-recurring group first, because the ordering is the recommendation", () => {
  const groups = groupFindings([
    finding({ condition: "stalled_run", stage_key: "plan", ticket_id: "t9" }),
    finding({ ticket_id: "t1" }),
    finding({ ticket_id: "t2" }),
    finding({ ticket_id: "t3" }),
  ]);

  expect(groups[0].condition).toBe("stage_thrash");
  expect(groups[0].tickets).toBe(3);
  expect(groups[1].condition).toBe("stalled_run");
});

it("ignores the occurrences field entirely, because it counts sweep ticks", () => {
  // Against the live database every finding read 5989 — two days of the
  // reconcile sweep re-observing the same condition. Surfacing that as a count
  // states, in the most emphatic unit available, something that never happened.
  // The old fixtures used 1 and 4, which is why nothing caught it.
  const groups = groupFindings([
    finding({ ticket_id: "t1", occurrences: 5989 }),
    finding({ ticket_id: "t2", occurrences: 5989 }),
  ]);

  expect(groups[0].tickets).toBe(2);
  expect(groups[0]).not.toHaveProperty("occurrences");
});

it("keeps the earliest first_seen, which is how long it has been true", () => {
  const groups = groupFindings([
    finding({ ticket_id: "t1", first_seen: "2026-09-10T00:00:00Z" }),
    finding({ ticket_id: "t2", first_seen: "2026-09-09T00:00:00Z" }),
    finding({ ticket_id: "t3", first_seen: null }),
  ]);

  expect(groups[0].firstSeen).toBe("2026-09-09T00:00:00Z");
});

it("counts distinct tickets, not rows, when one ticket reports twice", () => {
  const groups = groupFindings([finding({ ticket_id: "t1" }), finding({ ticket_id: "t1" })]);

  expect(groups[0].tickets).toBe(1);
  expect(groups[0].findings).toHaveLength(2);
});

it("contributes no ticket count for a workspace-scoped finding", () => {
  // `list_findings` appends workspace-scoped conditions with no ticket id —
  // those are already about the workspace, not about a ticket that recurred.
  const groups = groupFindings([
    finding({ condition: "draft_drift", stage_key: "", ticket_id: "" }),
  ]);

  expect(groups[0].tickets).toBe(0);
  expect(groups[0].findings).toHaveLength(1);
});

it("keeps the newest last_seen for a group", () => {
  const groups = groupFindings([
    finding({ ticket_id: "t1", last_seen: "2026-09-01T00:00:00Z" }),
    finding({ ticket_id: "t2", last_seen: "2026-09-11T00:00:00Z" }),
    finding({ ticket_id: "t3", last_seen: null }),
  ]);

  expect(groups[0].lastSeen).toBe("2026-09-11T00:00:00Z");
});

it("returns nothing for nothing", () => {
  expect(groupFindings([])).toEqual([]);
});

it("renders a condition slug as prose", () => {
  expect(conditionLabel("unbudgeted_repeat")).toBe("Unbudgeted repeat");
  expect(conditionLabel("stage_thrash")).toBe("Stage thrash");
});
