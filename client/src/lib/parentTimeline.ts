import type { ChildTimelineRow, ChildTimelineSegment, ChildrenTimeline } from "../api/childrenTimeline";
import type { TicketState } from "../api/types";

/**
 * Layout for a parent ticket's timeline: which children go in the lanes, which
 * in the not-started lists, and where each segment sits on the shared axis.
 * Pure, so the view stays a rendering of it.
 */

const RESOLVED: ReadonlySet<TicketState> = new Set(["done", "wont_do"]);

/** A run of this many consecutive lane rows at the same place collapses behind a count. */
export const GROUP_AT = 4;
/** Over this many children, a text filter appears beside the list. */
export const FILTER_AT = 50;

export type ChildFilter = "all" | "needs_you" | "blocked";

export function needsPerson(row: ChildTimelineRow): boolean {
  return row.workflow_stage_status === "awaiting";
}

export function isBlocked(row: ChildTimelineRow): boolean {
  return row.state === "blocked" || row.workflow_stage_status === "blocked";
}

export function matchesFilter(row: ChildTimelineRow, filter: ChildFilter, text: string): boolean {
  if (filter === "needs_you" && !needsPerson(row)) return false;
  if (filter === "blocked" && !isBlocked(row)) return false;
  const needle = text.trim().toLowerCase();
  if (!needle) return true;
  return row.external_id.toLowerCase().includes(needle) || row.title.toLowerCase().includes(needle);
}

/** Where a child stands now, in a few words: what the row's tag says. */
export function nowLabel(row: ChildTimelineRow): string {
  if (RESOLVED.has(row.state)) return row.state === "done" ? "Done" : "Won't do";
  if (row.state === "parked") return `${row.workflow_stage_name || "Parked"} · parked`;
  const stage = row.workflow_stage_name || row.workflow_stage_key;
  if (isBlocked(row)) return `${stage} · blocked`;
  if (needsPerson(row)) return `${stage} · needs you`;
  if (row.workflow_stage_status === "running") return `${stage} · running`;
  return stage;
}

export type NowTone = "done" | "bad" | "warn" | "live" | "quiet";

export function nowTone(row: ChildTimelineRow): NowTone {
  if (row.state === "done") return "done";
  if (isBlocked(row)) return "bad";
  if (needsPerson(row) || row.state === "parked") return "warn";
  if (row.workflow_stage_status === "running") return "live";
  return "quiet";
}

/** The key rows are grouped on: same stage, same status, same state. */
function placeKey(row: ChildTimelineRow): string {
  return `${row.state}|${row.workflow_stage_key}|${row.workflow_stage_status}`;
}

export type LaneItem =
  | { kind: "row"; row: ChildTimelineRow }
  | { kind: "group"; key: string; first: ChildTimelineRow; rest: ChildTimelineRow[] };

/** Collapse runs of GROUP_AT or more consecutive rows at the same place. */
export function groupLanes(rows: readonly ChildTimelineRow[]): LaneItem[] {
  const items: LaneItem[] = [];
  let i = 0;
  while (i < rows.length) {
    let j = i + 1;
    while (j < rows.length && placeKey(rows[j]) === placeKey(rows[i])) j += 1;
    if (j - i >= GROUP_AT) {
      items.push({ kind: "group", key: `${placeKey(rows[i])}@${rows[i].id}`, first: rows[i], rest: rows.slice(i + 1, j) });
    } else {
      for (let k = i; k < j; k += 1) items.push({ kind: "row", row: rows[k] });
    }
    i = j;
  }
  return items;
}

export interface ChildSections {
  /**
   * Children with at least one stage visit, in the order their work began, then
   * children with children of their own: their work is often all below them,
   * and the lanes are where a row expands.
   */
  lanes: ChildTimelineRow[];
  /** Unresolved, nothing run, waiting on no open ticket: can start now. */
  ready: ChildTimelineRow[];
  /** Unresolved, nothing run, waiting on open tickets. */
  waiting: ChildTimelineRow[];
  /** Resolved without a single run here (closed by hand, or done elsewhere). */
  closedWithoutRuns: ChildTimelineRow[];
}

export function sectionChildren(rows: readonly ChildTimelineRow[]): ChildSections {
  const sections: ChildSections = { lanes: [], ready: [], waiting: [], closedWithoutRuns: [] };
  for (const row of rows) {
    if (row.segments.length > 0 || row.child_count > 0) sections.lanes.push(row);
    else if (RESOLVED.has(row.state)) sections.closedWithoutRuns.push(row);
    else if (row.waiting_on.length > 0) sections.waiting.push(row);
    else sections.ready.push(row);
  }
  return sections;
}

export interface Axis {
  start: number;
  end: number;
}

/**
 * The shared time axis: the window of work, stretched to now only while a visit
 * is running. Waiting is not stretched to: a milestone that worked two days and
 * has waited eight would squeeze every bar into the first fifth. Waiting rows
 * run a tail to the edge and say how long instead (`lastActivity`).
 */
export function timelineAxis(timeline: ChildrenTimeline, now: number): Axis | null {
  if (!timeline.window_start || !timeline.window_end) return null;
  const start = Date.parse(timeline.window_start);
  const open = timeline.children.some((row) => row.segments.some((s) => s.finished_at === null));
  const end = Math.max(Date.parse(timeline.window_end), open ? now : 0);
  return { start, end: end > start ? end : start + 60_000 };
}

export interface Span {
  /** Percent of the axis from its start. */
  left: number;
  /** Percent of the axis wide; the view enforces a visible minimum. */
  width: number;
}

function clampPct(value: number): number {
  return Math.min(100, Math.max(0, value));
}

export function spanOn(axis: Axis, from: number, to: number): Span {
  const total = axis.end - axis.start;
  const left = clampPct(((from - axis.start) / total) * 100);
  const right = clampPct(((to - axis.start) / total) * 100);
  return { left, width: Math.max(0, right - left) };
}

export function segmentSpan(axis: Axis, segment: ChildTimelineSegment, now: number): Span | null {
  if (!segment.started_at) return null;
  const from = Date.parse(segment.started_at);
  const to = segment.finished_at ? Date.parse(segment.finished_at) : now;
  return spanOn(axis, from, to);
}

/**
 * The stretch after a child's last visit while it sits waiting on a person or
 * blocked: drawn as a trailing tail to now, so time lost waiting is visible.
 */
export function idleTail(axis: Axis, row: ChildTimelineRow, now: number): Span | null {
  if (!needsPerson(row) && !isBlocked(row)) return null;
  const last = lastActivity(row, now);
  return last !== null && last < now ? spanOn(axis, last, Math.max(now, axis.end)) : null;
}

/** When the child's last visit ended (now, while one runs); null if it never ran. */
export function lastActivity(row: ChildTimelineRow, now: number): number | null {
  const ends = row.segments.map((s) => (s.finished_at ? Date.parse(s.finished_at) : now));
  return ends.length > 0 ? Math.max(...ends) : null;
}

export type SegmentTone = "ok" | "bad" | "live" | "warn" | "quiet";

export function segmentTone(segment: ChildTimelineSegment): SegmentTone {
  if (segment.status === "running" || segment.status === "queued") return "live";
  if (segment.status === "failed") return "bad";
  if (segment.status === "awaiting_permission") return "warn";
  if (segment.status === "cancelled") return "quiet";
  return "ok";
}

/** What a screen reader hears for a row's bar, which is drawn and not read. */
export function rowSummary(row: ChildTimelineRow): string {
  const visits = row.segments.length;
  const back = row.segments.filter((s) => s.visit_number > 1).length;
  const parts = [`${visits} stage visit${visits === 1 ? "" : "s"}`, `${row.total_runs} run${row.total_runs === 1 ? "" : "s"}`];
  if (back > 0) parts.push(`went back ${back} time${back === 1 ? "" : "s"}`);
  parts.push(`now ${nowLabel(row)}`);
  return parts.join(", ");
}

/** Axis tick labels: start, two midpoints, end. */
export function axisTicks(axis: Axis): number[] {
  const step = (axis.end - axis.start) / 3;
  return [0, 1, 2, 3].map((i) => axis.start + step * i);
}
