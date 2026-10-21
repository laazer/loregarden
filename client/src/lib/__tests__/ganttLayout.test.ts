import type { InitiativePlan, MilestoneSchedule, PlanNode } from "../../api/initiativeApi";
import { PX_PER_DAY, ganttGroups, ganttRange, ganttTicks, linkPath, nodeSpan, xOf } from "../ganttLayout";

const DAY = 86_400_000;
// Wednesday 2026-10-21, noon UTC.
const NOW = Date.parse("2026-10-21T12:00:00Z");

function phase(overrides: Partial<MilestoneSchedule> = {}): MilestoneSchedule {
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
    status: "on_track",
    basis: "none",
    remaining: 1,
    total: 1,
    counts: {},
    assumed: 0,
    ...overrides,
  };
}

function node(overrides: Partial<PlanNode> = {}): PlanNode {
  return {
    id: "n1",
    external_id: "lg-n-1",
    title: "Work",
    workspace_slug: "loregarden",
    state: "backlog",
    status: "ready",
    lane: "loregarden",
    milestone_id: "m1",
    step: 1,
    deps: [],
    waiting_on: [],
    start: "2026-10-22T00:00:00Z",
    finish: "2026-10-24T00:00:00Z",
    duration_days: 2,
    basis: "agent_time",
    assumed: false,
    critical: false,
    external: false,
    ...overrides,
  };
}

function plan(milestones: MilestoneSchedule[], nodes: PlanNode[], overrides: Partial<InitiativePlan> = {}) {
  return { milestones, nodes, target_date: null, ...overrides } as InitiativePlan;
}

test("a phase that is one ticket is one bar; a phase with work lists it earliest first", () => {
  const groups = ganttGroups(
    plan(
      [phase({ id: "solo", external_id: "lg-solo-1" }), phase({ id: "m1" })],
      [
        node({ id: "solo", milestone_id: "solo" }),
        node({ id: "late", start: "2026-11-02T00:00:00Z", finish: "2026-11-04T00:00:00Z" }),
        node({ id: "early" }),
      ],
    ),
  );
  expect(groups.map((g) => [g.key, g.self?.id ?? null, g.children.map((n) => n.id)])).toEqual([
    ["solo", "solo", []],
    ["m1", null, ["early", "late"]],
  ]);
  expect(groups[1].span).toEqual({ start: Date.parse("2026-10-22T00:00:00Z"), finish: Date.parse("2026-11-04T00:00:00Z") });
});

test("a phase that is a ticket with work under it keeps its own ticket as a row", () => {
  const [group] = ganttGroups(plan([phase()], [node({ id: "m1" }), node({ id: "kid" })]));
  expect(group.self?.id).toBe("m1");
  expect(group.children.map((n) => n.id).sort()).toEqual(["kid", "m1"]);
});

test("prerequisites outside the initiative get a group of their own, last", () => {
  const groups = ganttGroups(plan([phase()], [node(), node({ id: "x", milestone_id: null, external: true })]));
  expect(groups.map((g) => g.key)).toEqual(["m1", "outside"]);
  expect(groups[1].phase).toBeNull();
});

test("an unscheduled node has no span, and does not stretch its phase", () => {
  expect(nodeSpan(node({ start: null }))).toBeNull();
  const [group] = ganttGroups(plan([phase()], [node(), node({ id: "n2", start: null, finish: null })]));
  expect(group.span).toEqual(nodeSpan(node()));
});

test("the range covers every span, every target and today, at whole days", () => {
  const range = ganttRange(
    plan([phase({ target_date: "2026-12-01" })], [node({ start: "2026-10-01T09:00:00Z" })], { target_date: "2026-12-10" }),
    NOW,
  );
  expect(range.start).toBe(Date.parse("2026-09-29T00:00:00Z"));
  expect(range.start + range.days * DAY).toBeGreaterThan(Date.parse("2026-12-11T00:00:00Z"));
  expect(xOf(range.start + 3 * DAY, range, "week")).toBe(3 * PX_PER_DAY.week);
});

test("week ticks start on Mondays and tile the range without gaps", () => {
  const range = ganttRange(plan([], []), NOW);
  const { upper, lower } = ganttTicks(range, "week");
  for (const tick of lower.slice(1)) expect(new Date(Number(tick.key)).getUTCDay()).toBe(1);
  for (const tiers of [upper, lower]) {
    expect(tiers.reduce((sum, t) => sum + t.width, 0)).toBeCloseTo(range.days * PX_PER_DAY.week);
  }
});

test("day ticks mark weekends", () => {
  const range = ganttRange(plan([phase()], [node({ finish: "2026-11-04T00:00:00Z" })]), NOW);
  const { lower } = ganttTicks(range, "day");
  expect(lower).toHaveLength(range.days);
  const weekends = lower.filter((t) => t.weekend).map((t) => new Date(Number(t.key)).getUTCDay());
  expect(weekends.length).toBeGreaterThan(0);
  expect(weekends.every((d) => d === 0 || d === 6)).toBe(true);
});

test("a link steps right when there is room, and doubles back between the rows when not", () => {
  expect(linkPath({ x: 10, y: 15 }, { x: 100, y: 75 }, 30)).toBe("M 10 15 H 18 V 75 H 100");
  expect(linkPath({ x: 100, y: 15 }, { x: 40, y: 75 }, 30)).toBe("M 100 15 H 108 V 30 H 32 V 75 H 40");
});
