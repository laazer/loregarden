import type { BlockKind, StageStatus, TicketState, WorkItemType } from "./types";

/** One stage visit of one child ticket: a segment of its bar on a parent's timeline. */
export interface ChildTimelineSegment {
  stage_key: string;
  stage_name: string;
  visit_number: number;
  status: "queued" | "running" | "awaiting_permission" | "succeeded" | "failed" | "cancelled";
  attempts: number;
  started_at: string | null;
  /** Null while the visit is still running. */
  finished_at: string | null;
}

export interface ChildTimelineDependency {
  id: string;
  external_id: string;
  title: string;
  state: TicketState;
}

export interface ChildTimelineRow {
  id: string;
  external_id: string;
  title: string;
  state: TicketState;
  work_item_type: WorkItemType;
  workflow_stage_key: string;
  workflow_stage_name: string;
  workflow_stage_status: StageStatus;
  block_kind: BlockKind | null;
  /** This child's own children; above zero means the row can expand. */
  child_count: number;
  total_runs: number;
  segments: ChildTimelineSegment[];
  waiting_on: ChildTimelineDependency[];
}

/** A parent ticket's direct children, laid on one time axis. */
export interface ChildrenTimeline {
  parent_id: string;
  children: ChildTimelineRow[];
  window_start: string | null;
  window_end: string | null;
}
