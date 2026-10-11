import type { ChildTimelineRow, ChildTimelineSegment, ChildrenTimeline } from "../../api/client";
import { groupLanes, idleTail, sectionChildren, segmentSpan, timelineAxis } from "../parentTimeline";

const H = 3_600_000;
const T0 = Date.parse("2026-10-01T00:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();

function seg(fromH: number, toH: number | null, overrides: Partial<ChildTimelineSegment> = {}): ChildTimelineSegment {
  return {
    stage_key: "implement",
    stage_name: "Implement",
    visit_number: 1,
    status: toH === null ? "running" : "succeeded",
    attempts: 1,
    started_at: iso(T0 + fromH * H),
    finished_at: toH === null ? null : iso(T0 + toH * H),
    ...overrides,
  };
}

function row(id: string, overrides: Partial<ChildTimelineRow> = {}): ChildTimelineRow {
  return {
    id,
    external_id: id,
    title: id,
    state: "in_progress",
    work_item_type: "feature",
    workflow_stage_key: "implement",
    workflow_stage_name: "Implement",
    workflow_stage_status: "pending",
    block_kind: null,
    child_count: 0,
    total_runs: 0,
    segments: [],
    waiting_on: [],
    ...overrides,
  };
}

function timeline(children: ChildTimelineRow[], startH: number, endH: number): ChildrenTimeline {
  return { parent_id: "p", children, window_start: iso(T0 + startH * H), window_end: iso(T0 + endH * H) };
}

it("does not stretch the axis to now for children that are only waiting", () => {
  const waiting = row("a", { workflow_stage_status: "awaiting", segments: [seg(0, 48)] });
  const axis = timelineAxis(timeline([waiting], 0, 48), T0 + 240 * H)!;
  expect(axis.end).toBe(T0 + 48 * H);
  // The tail runs to the edge instead, and no further.
  expect(idleTail(axis, waiting, T0 + 240 * H)).toEqual({ left: 100, width: 0 });
});

it("stretches the axis to now while a visit is running, and the running segment reaches it", () => {
  const running = row("a", { segments: [seg(0, 10), seg(10, null)] });
  const now = T0 + 20 * H;
  const axis = timelineAxis(timeline([running], 0, 10), now)!;
  expect(axis.end).toBe(now);
  expect(segmentSpan(axis, running.segments[1], now)).toEqual({ left: 50, width: 50 });
});

it("draws a waiting tail from the last visit to the edge", () => {
  const waiting = row("a", { workflow_stage_status: "awaiting", segments: [seg(0, 5)] });
  const axis = { start: T0, end: T0 + 10 * H };
  expect(idleTail(axis, waiting, T0 + 100 * H)).toEqual({ left: 50, width: 50 });
  expect(idleTail(axis, row("b", { segments: [seg(0, 5)] }), T0 + 100 * H)).toBeNull();
});

it("has no axis when nothing has run", () => {
  expect(timelineAxis({ parent_id: "p", children: [row("a")], window_start: null, window_end: null }, T0)).toBeNull();
});

it("sections children by whether they ran, and why not", () => {
  const sections = sectionChildren([
    row("ran", { segments: [seg(0, 1)] }),
    row("ready", { state: "backlog" }),
    row("waits", { state: "backlog", waiting_on: [{ id: "x", external_id: "x", title: "x", state: "in_progress" }] }),
    row("closed", { state: "done" }),
    row("parent", { state: "done", child_count: 3 }),
  ]);
  expect(sections.lanes.map((r) => r.id)).toEqual(["ran", "parent"]);
  expect(sections.ready.map((r) => r.id)).toEqual(["ready"]);
  expect(sections.waiting.map((r) => r.id)).toEqual(["waits"]);
  expect(sections.closedWithoutRuns.map((r) => r.id)).toEqual(["closed"]);
});

it("groups four or more consecutive rows at one place, and leaves three alone", () => {
  const at = (id: string, stage: string) => row(id, { workflow_stage_key: stage });
  const items = groupLanes([at("a", "x"), at("b", "x"), at("c", "x"), at("d", "y"), at("e", "y"), at("f", "y"), at("g", "y")]);
  expect(items.map((i) => (i.kind === "row" ? i.row.id : `${i.first.id}+${i.rest.length}`))).toEqual([
    "a",
    "b",
    "c",
    "d+3",
  ]);
});
