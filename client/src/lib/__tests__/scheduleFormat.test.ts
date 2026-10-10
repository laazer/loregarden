import type { InitiativePlan, MilestoneSchedule } from "../../api/initiativeApi";
import {
  formatDay,
  formatDrift,
  groupByWorkspace,
  proposalRows,
  proposedInitiativeTarget,
  proposedSequence,
  timelinePercent,
  timelineRange,
} from "../scheduleFormat";

function row(overrides: Partial<MilestoneSchedule> = {}): MilestoneSchedule {
  return {
    id: "m1",
    external_id: "lg-m-1",
    title: "First",
    state: "backlog",
    workspace_slug: "loregarden",
    work_item_type: "milestone",
    member: false,
    plan_order: 0,
    target_date: null,
    forecast_date: null,
    planned_date: null,
    drift_days: null,
    status: "unscheduled",
    basis: "none",
    remaining: 3,
    total: 3,
    counts: { waiting: 3 },
    assumed: 0,
    ...overrides,
  };
}

function plan(milestones: MilestoneSchedule[]): InitiativePlan {
  return {
    id: "i1",
    external_id: "init-x-1",
    title: "X",
    description: "",
    state: "backlog",
    mode: "fixed",
    notes: "",
    target_date: "2026-12-01",
    forecast_date: null,
    planned_date: null,
    drift_days: null,
    status: "unscheduled",
    unforecast_milestones: 0,
    milestones,
    paces: [],
    window_days: 21,
    pending_proposal: null,
    nodes: [],
    critical_path: [],
    cyclic: [],
    lanes: [],
    autopilot: {
      enabled: false,
      max_parallel: 3,
      paused_reason: "",
      in_flight: 0,
      next_up: [],
      available: true,
      recent: [],
    },
    generated_at: "2026-10-01T12:00:00Z",
  };
}

test("drift reads as late, early or on target", () => {
  expect(formatDrift(3)).toBe("3d late");
  expect(formatDrift(-2)).toBe("2d early");
  expect(formatDrift(0)).toBe("on target");
  expect(formatDrift(null)).toBeNull();
});

test("the timeline spans today to the latest date, and clamps the past to its edge", () => {
  const range = timelineRange([row({ target_date: "2026-11-01", forecast_date: "2026-12-01" })], "2026-10-01");
  expect(timelinePercent("2026-10-01", range)).toBe(0);
  expect(timelinePercent("2026-09-01", range)).toBe(0);
  expect(timelinePercent("2026-12-01", range)).toBeGreaterThan(90);
  expect(timelinePercent("2026-12-01", range)).toBeLessThan(100);
});

test("groups keep plan order within each workspace", () => {
  const groups = groupByWorkspace([
    row({ id: "a", workspace_slug: "one" }),
    row({ id: "b", workspace_slug: "two" }),
    row({ id: "c", workspace_slug: "one" }),
  ]);
  expect(groups.map(([ws, rows]) => [ws, rows.map((r) => r.id)])).toEqual([
    ["one", ["a", "c"]],
    ["two", ["b"]],
  ]);
});

describe("proposalRows", () => {
  const m = row({ id: "m1", target_date: "2026-11-01", plan_order: 0 });

  test("an omitted date is not a change; null clears; the initiative is labelled as itself", () => {
    const rows = proposalRows(plan([m]), {
      id: "p",
      source: "chat",
      mode: "rolling",
      rationale: "",
      created_at: "",
      items: [
        { ticket_id: "m1", plan_order: 2 },
        { ticket_id: "i1", target_date: null },
      ],
    });
    expect(rows.map((r) => r.key)).toEqual(["mode", "m1:order", "i1:date"]);
    expect(rows[2]).toMatchObject({ label: "init-x-1 (whole initiative)", to: "—" });
  });

  test("a proposal matching the plan changes nothing", () => {
    const rows = proposalRows(plan([m]), {
      id: "p",
      source: "draft",
      mode: "fixed",
      rationale: "",
      created_at: "",
      items: [{ ticket_id: "m1", target_date: "2026-11-01", plan_order: 0 }],
    });
    expect(rows).toEqual([]);
  });
});

describe("proposedSequence", () => {
  const milestones = ["a", "b", "c", "d"].map((id, index) =>
    row({ id, external_id: id, plan_order: index, target_date: "2026-11-01" }),
  );

  test("orders as the server's _reorder does, duplicate positions included, and keeps unproposed dates", () => {
    // Oracle: loregarden.services.initiative_plan_service._reorder on the same items gives c, b, d, a.
    const proposal = {
      id: "p",
      source: "draft" as const,
      mode: null,
      rationale: "",
      created_at: "",
      items: [
        { ticket_id: "d", plan_order: 0, target_date: "2026-11-20" },
        { ticket_id: "c", plan_order: 0, target_date: null },
        { ticket_id: "b", plan_order: 1 },
        { ticket_id: "i1", target_date: "2027-01-05" },
      ],
    };
    const result = proposedSequence(plan(milestones), proposal);
    expect(result.map((r) => [r.milestone.id, r.position, r.target])).toEqual([
      ["c", 0, null],
      ["b", 1, "2026-11-01"],
      ["d", 2, "2026-11-20"],
      ["a", 3, "2026-11-01"],
    ]);
    expect(proposedInitiativeTarget(plan(milestones), proposal)).toBe("2027-01-05");
    expect(proposedInitiativeTarget(plan(milestones), { ...proposal, items: [] })).toBe("2026-12-01");
  });
});

describe("formatDay", () => {
  const now = new Date("2026-10-05T12:00:00Z");

  test("names the year only when it is not this year", () => {
    expect(formatDay("2026-11-26", now)).not.toMatch(/2026/);
    expect(formatDay("2027-07-07", now)).toMatch(/2027/);
    expect(formatDay(null, now)).toBe("—");
  });
});
