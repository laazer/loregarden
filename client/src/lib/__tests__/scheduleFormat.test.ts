import type { InitiativePlan, MilestoneSchedule } from "../../api/initiativeApi";
import {
  formatDrift,
  groupByWorkspace,
  proposalRows,
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
