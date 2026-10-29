/** The initiative Gantt chart's geometry, apart from its component. */

import type { InitiativePlan, MilestoneSchedule, NodeStatus, PlanNode } from "../api/initiativeApi";
import { isoDay } from "./scheduleFormat";

const DAY = 86_400_000;

export type GanttScale = "day" | "week" | "month";

/** Horizontal zoom: how wide one day is drawn. */
export const PX_PER_DAY: Record<GanttScale, number> = { day: 36, week: 14, month: 4 };

/** Below this a bar is too narrow to hold its own label, which is drawn after it. */
export const LABEL_INSIDE_MIN_PX = 140;

export const NODE_STATUS_COLOR: Record<NodeStatus, string> = {
  done: "var(--grn)",
  running: "var(--blue)",
  ready: "var(--ac)",
  waiting: "var(--txl)",
  needs_person: "var(--amb)",
  blocked: "var(--red)",
};

export const NODE_STATUS_LABEL: Record<NodeStatus, string> = {
  done: "done",
  running: "running",
  ready: "ready",
  waiting: "waiting",
  needs_person: "for a person",
  blocked: "blocked",
};

export interface GanttRange {
  /** UTC midnight of the first day drawn. */
  start: number;
  /** Whole days drawn. */
  days: number;
}

export interface Span {
  start: number;
  finish: number;
}

function midnight(ms: number): number {
  return Math.floor(ms / DAY) * DAY;
}

/** A node's span, or null when the graph could not schedule it. */
export function nodeSpan(node: PlanNode): Span | null {
  if (!node.start || !node.finish) return null;
  const start = Date.parse(node.start);
  const finish = Date.parse(node.finish);
  if (Number.isNaN(start) || Number.isNaN(finish)) return null;
  return { start, finish: Math.max(finish, start) };
}

/** Every scheduled span, every target and today, with two days of air each side. */
export function ganttRange(plan: InitiativePlan, now: number): GanttRange {
  let lo = now;
  let hi = now;
  for (const node of plan.nodes) {
    const span = nodeSpan(node);
    if (!span) continue;
    lo = Math.min(lo, span.start);
    hi = Math.max(hi, span.finish);
  }
  for (const iso of [plan.target_date, ...plan.milestones.map((m) => m.target_date)]) {
    if (!iso) continue;
    lo = Math.min(lo, isoDay(iso));
    hi = Math.max(hi, isoDay(iso) + DAY);
  }
  const start = midnight(lo) - 2 * DAY;
  return { start, days: Math.ceil((midnight(hi) + 3 * DAY - start) / DAY) };
}

export function xOf(ms: number, range: GanttRange, scale: GanttScale): number {
  return ((ms - range.start) / DAY) * PX_PER_DAY[scale];
}

export interface GanttTick {
  key: string;
  left: number;
  width: number;
  label: string;
  /** Saturday or Sunday, shaded on the day scale. */
  weekend?: boolean;
}

function fmt(ms: number, options: Intl.DateTimeFormatOptions): string {
  return new Date(ms).toLocaleDateString(undefined, { ...options, timeZone: "UTC" });
}

/** Cut the range into periods starting at each `next(t)` boundary. */
function periods(
  range: GanttRange,
  scale: GanttScale,
  next: (t: number) => number,
  label: (t: number, end: number) => string,
): GanttTick[] {
  const end = range.start + range.days * DAY;
  const out: GanttTick[] = [];
  let t = range.start;
  while (t < end) {
    const stop = Math.min(next(t), end);
    out.push({
      key: String(t),
      left: xOf(t, range, scale),
      width: xOf(stop, range, scale) - xOf(t, range, scale),
      label: label(t, stop),
    });
    t = stop;
  }
  return out;
}

function nextMonday(t: number): number {
  const weekday = new Date(t).getUTCDay(); // 0 Sunday
  return t + (((8 - weekday) % 7 || 7) * DAY);
}

function nextMonth(t: number): number {
  const d = new Date(t);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
}

function nextYear(t: number): number {
  return Date.UTC(new Date(t).getUTCFullYear() + 1, 0, 1);
}

/** Two header tiers: the coarse period above, the unit the bars are read against below. */
export function ganttTicks(range: GanttRange, scale: GanttScale): { upper: GanttTick[]; lower: GanttTick[] } {
  if (scale === "day") {
    return {
      upper: periods(range, scale, nextMonday, (t, stop) =>
        `${fmt(t, { month: "short", day: "numeric" })} – ${fmt(stop - DAY, { month: "short", day: "numeric" })}`,
      ),
      lower: periods(range, scale, (t) => t + DAY, (t) => fmt(t, { weekday: "short", day: "numeric" })).map((tick) => {
        const weekday = new Date(Number(tick.key)).getUTCDay();
        return { ...tick, weekend: weekday === 0 || weekday === 6 };
      }),
    };
  }
  if (scale === "week") {
    return {
      upper: periods(range, scale, nextMonth, (t) => fmt(t, { month: "long", year: "numeric" })),
      lower: periods(range, scale, nextMonday, (t) => fmt(t, { month: "short", day: "numeric" })),
    };
  }
  return {
    upper: periods(range, scale, nextYear, (t) => fmt(t, { year: "numeric" })),
    lower: periods(range, scale, nextMonth, (t) => fmt(t, { month: "short" })),
  };
}

/** One phase of the plan, or the prerequisites outside it, with the work drawn under it. */
export interface GanttGroup {
  key: string;
  /** Null for the group of prerequisites outside the initiative. */
  phase: MilestoneSchedule | null;
  /** The phase's own ticket when the graph schedules it as work. */
  self: PlanNode | null;
  /** Work under the phase, earliest first; empty when the phase is one ticket. */
  children: PlanNode[];
  /** From its earliest start to its latest finish; null when nothing is scheduled. */
  span: Span | null;
}

function byStart(a: PlanNode, b: PlanNode): number {
  return (nodeSpan(a)?.start ?? Infinity) - (nodeSpan(b)?.start ?? Infinity);
}

function cover(nodes: PlanNode[]): Span | null {
  let span: Span | null = null;
  for (const node of nodes) {
    const s = nodeSpan(node);
    if (!s) continue;
    span = span ? { start: Math.min(span.start, s.start), finish: Math.max(span.finish, s.finish) } : s;
  }
  return span;
}

/** Phases in plan order, then whatever the plan waits on from outside. */
export function ganttGroups(plan: InitiativePlan): GanttGroup[] {
  const byPhase = new Map<string, PlanNode[]>();
  const outside: PlanNode[] = [];
  for (const node of plan.nodes) {
    if (node.external || !node.milestone_id) {
      outside.push(node);
      continue;
    }
    const list = byPhase.get(node.milestone_id);
    if (list) list.push(node);
    else byPhase.set(node.milestone_id, [node]);
  }
  const groups: GanttGroup[] = plan.milestones.map((phase) => {
    const nodes = byPhase.get(phase.id) ?? [];
    const self = nodes.find((n) => n.id === phase.id) ?? null;
    // A phase that is one ticket is one bar; otherwise its own ticket is one more row of work.
    const children = self && nodes.length === 1 ? [] : [...nodes].sort(byStart);
    return { key: phase.id, phase, self, children, span: cover(nodes) };
  });
  if (outside.length > 0) {
    groups.push({ key: "outside", phase: null, self: null, children: [...outside].sort(byStart), span: cover(outside) });
  }
  return groups;
}

export interface LinkEnd {
  /** Bar edge, in timeline pixels. */
  x: number;
  /** Row centre, in pixels from the top of the body. */
  y: number;
}

/**
 * An elbow from a prerequisite's finish to its dependent's start. When the
 * dependent starts before there is room to step right, the line drops between
 * the rows and comes back, as YouTrack draws it.
 */
export function linkPath(from: LinkEnd, to: LinkEnd, rowHeight: number): string {
  const stub = 8;
  const out = from.x + stub;
  if (to.x - stub >= out) {
    return `M ${from.x} ${from.y} H ${out} V ${to.y} H ${to.x}`;
  }
  const between = from.y + (to.y > from.y ? rowHeight / 2 : -rowHeight / 2);
  return `M ${from.x} ${from.y} H ${out} V ${between} H ${to.x - stub} V ${to.y} H ${to.x}`;
}
