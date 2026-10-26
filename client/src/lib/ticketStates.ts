import type { StageStatus, TicketActivity, TicketState } from "../api/types";

import { STATUS_TONE_COLORS, type ChipStatus, type StatusTone } from "./statusTone";

// Every vocabulary below maps onto the six tones in statusTone.ts, and its
// colours are derived from them, so a hue means the same thing on every surface.
function toneColors<K extends string>(tones: Record<K, StatusTone>): Record<K, string> {
  const colors = {} as Record<K, string>;
  for (const key of Object.keys(tones) as K[]) colors[key] = STATUS_TONE_COLORS[tones[key]];
  return colors;
}

const TICKET_STATE_TONES: Record<TicketState, StatusTone> = {
  backlog: "idle",
  // Open, not executing: most of the in-progress pile is idle between stages,
  // and activity says when something runs. Blue is kept for that.
  in_progress: "idle",
  blocked: "bad",
  // Violet, not red: parked work is owed, not stuck, and colouring it like
  // blocked would undo the distinction (lg-workflow-integrity-449).
  parked: "you",
  done: "done",
  // Closed without doing it: quiet like done, but no tick, since nothing was done.
  wont_do: "idle",
};

export const TICKET_STATE_COLORS: Record<TicketState, string> = toneColors(TICKET_STATE_TONES);

export const TICKET_STATE_LABELS: Record<TicketState, string> = {
  backlog: "Backlog",
  in_progress: "In Progress",
  blocked: "Blocked",
  parked: "Parked",
  done: "Done",
  wont_do: "Won't do",
};

/**
 * Activity is the axis `state` does not cover: "in progress" says the work is
 * open, not that anything is executing. Idle is deliberately the quiet colour —
 * it is the majority of the board, and shouting about it helps nobody.
 * Awaiting is a run held for a person's approval, so it takes needs-you.
 */
const TICKET_ACTIVITY_TONES: Record<TicketActivity, StatusTone> = {
  running: "run",
  awaiting: "you",
  queued: "wait",
  idle: "idle",
};

export const TICKET_ACTIVITY_COLORS: Record<TicketActivity, string> = toneColors(TICKET_ACTIVITY_TONES);

export const TICKET_ACTIVITY_LABELS: Record<TicketActivity, string> = {
  running: "Running",
  awaiting: "Awaiting",
  queued: "Queued",
  idle: "Idle",
};

export function ticketActivityColor(activity: string | undefined): string {
  return fromMap(TICKET_ACTIVITY_COLORS, activity, "var(--txl)");
}

export function ticketActivityLabel(activity: string | undefined): string {
  return fromMap(TICKET_ACTIVITY_LABELS, activity, "Idle");
}

export function ticketActivityChip(activity: string | undefined): ChipStatus {
  return { tone: toneOf(TICKET_ACTIVITY_TONES, activity), label: ticketActivityLabel(activity) };
}

/**
 * Agent-run statuses, as they read on a card. The raw values are snake_case
 * enum members — a slot badge showing `awaiting_permission` is the server's
 * spelling leaking into the UI.
 */
const RUN_STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  awaiting_permission: "Awaiting approval",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
  blocked: "Blocked",
  // A lane keeping its slot while a block that may still resolve is repaired.
  // Distinct from "Blocked": the lane has not finished with the ticket.
  repairing: "Repairing",
};

const RUN_STATUS_TONES: Record<string, StatusTone> = {
  queued: "wait",
  running: "run",
  awaiting_permission: "you",
  succeeded: "done",
  failed: "bad",
  cancelled: "idle",
  blocked: "bad",
  repairing: "wait",
};

/**
 * Why a lane is being held through a block — the mechanism that makes the next
 * dispatch different from the one that blocked. Mirrors `RepairRoute` on the
 * server; an unrecognised value renders as itself rather than as a guess.
 */
const REPAIR_ROUTE_LABELS: Record<string, string> = {
  interruption: "interrupted run",
  scope_reroute: "scope handoff",
  stage_retry: "retry budget left",
  merge_turn: "merge turn left",
};

export function repairRouteLabel(route: string | undefined): string {
  return fromMap(REPAIR_ROUTE_LABELS, route, route || "unknown reason");
}

export function runStatusLabel(status: string | undefined): string {
  return fromMap(RUN_STATUS_LABELS, status, status || "Unknown");
}

export function runStatusChip(status: string | undefined): ChipStatus {
  return { tone: toneOf(RUN_STATUS_TONES, status), label: runStatusLabel(status) };
}

const STAGE_STATUS_TONES: Record<StageStatus, StatusTone> = {
  pending: "idle",
  running: "run",
  awaiting: "you",
  blocked: "bad",
  done: "done",
  wont_do: "idle",
};

/**
 * Segment / stage dots in the v6 ticket card — done stages go quiet, the live
 * one glows. Quieter than the tones for the settled states: a strip of
 * segments is mostly done and pending, and only the live one should stand out.
 */
const STAGE_STATUS_COLORS: Record<StageStatus, string> = {
  pending: "var(--bd2)",
  running: STATUS_TONE_COLORS.run,
  awaiting: STATUS_TONE_COLORS.you,
  blocked: STATUS_TONE_COLORS.bad,
  done: "var(--bd2)",
  wont_do: "var(--bd2)",
};

const STAGE_STATUS_LABELS: Record<StageStatus, string> = {
  pending: "Idle",
  running: "Running",
  awaiting: "Awaiting",
  blocked: "Blocked",
  done: "Done",
  wont_do: "Won't do",
};

export type PriorityStyle = {
  code: string;
  color: string;
  background: string;
  border: string;
};

const PRIORITY_STYLES: Record<number, PriorityStyle> = {
  1: {
    code: "P1",
    color: "var(--rdl)",
    background: "rgba(255,106,84,.12)",
    border: "rgba(255,106,84,.3)",
  },
  2: {
    code: "P2",
    color: "var(--aml)",
    background: "rgba(229,167,44,.12)",
    border: "rgba(229,167,44,.3)",
  },
  3: {
    code: "P3",
    color: "var(--txm)",
    background: "var(--bg3)",
    border: "var(--bd2)",
  },
};

function fromMap(map: Record<string, string>, key: string | undefined, fallback: string): string {
  const hit = key === undefined ? undefined : map[key];
  return hit ?? fallback;
}

// An unrecognised value reads as idle: quiet, and its label still says what it is.
function toneOf(map: Record<string, StatusTone>, key: string | undefined): StatusTone {
  const hit = key === undefined ? undefined : map[key];
  return hit ?? "idle";
}

export function ticketStateColor(state: string | undefined): string {
  return fromMap(TICKET_STATE_COLORS, state, "var(--txm)");
}

export function ticketStateLabel(state: string | undefined): string {
  return fromMap(TICKET_STATE_LABELS, state, "Unknown");
}

export function ticketStateChip(state: string | undefined): ChipStatus {
  return { tone: toneOf(TICKET_STATE_TONES, state), label: ticketStateLabel(state) };
}

export function stageStatusColor(status: string | undefined): string {
  return fromMap(STAGE_STATUS_COLORS, status, "var(--bd)");
}

export function stageStatusLabel(status: string | undefined): string {
  return fromMap(STAGE_STATUS_LABELS, status, "Idle");
}

export function stageStatusChip(status: string | undefined): ChipStatus {
  return { tone: toneOf(STAGE_STATUS_TONES, status), label: stageStatusLabel(status) };
}

export function priorityStyle(priority: number | undefined): PriorityStyle {
  return PRIORITY_STYLES[priority ?? 3] ?? PRIORITY_STYLES[3];
}

/**
 * A board column: a state, or one of the two the Blocked column splits off.
 * `needs_you` is a block only a person can clear; `waiting` is a ticket held by
 * another one — a blocked child, or an unmet prerequisite while in backlog.
 */
export type BoardColumnKey = TicketState | "needs_you" | "waiting";

const BOARD_SPLIT_TONES: Record<"needs_you" | "waiting", StatusTone> = {
  needs_you: "you",
  waiting: "wait",
};

const BOARD_SPLIT_COLORS = toneColors(BOARD_SPLIT_TONES);

const BOARD_SPLIT_LABELS: Record<"needs_you" | "waiting", string> = {
  needs_you: "Needs you",
  waiting: "Waiting on tickets",
};

export function boardColumnColor(key: BoardColumnKey): string {
  return fromMap(BOARD_SPLIT_COLORS, key, ticketStateColor(key));
}

export function boardColumnLabel(key: BoardColumnKey): string {
  return fromMap(BOARD_SPLIT_LABELS, key, ticketStateLabel(key));
}
