import type { StageStatus, TicketState } from "../api/types";

export const TICKET_STATE_COLORS: Record<TicketState, string> = {
  backlog: "var(--txm)",
  in_progress: "var(--blue)",
  blocked: "var(--red)",
  done: "var(--grn)",
  wont_do: "var(--amb)",
};

export const TICKET_STATE_LABELS: Record<TicketState, string> = {
  backlog: "Backlog",
  in_progress: "In Progress",
  blocked: "Blocked",
  done: "Done",
  wont_do: "Won't do",
};

/** Segment / stage dots in the v6 ticket card — done stages go quiet, the live one glows. */
const STAGE_STATUS_COLORS: Record<StageStatus, string> = {
  pending: "var(--bd2)",
  running: "var(--blue)",
  awaiting: "var(--amb)",
  blocked: "var(--red)",
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

export function ticketStateColor(state: string | undefined): string {
  return fromMap(TICKET_STATE_COLORS, state, "var(--txm)");
}

export function ticketStateLabel(state: string | undefined): string {
  return fromMap(TICKET_STATE_LABELS, state, "Unknown");
}

export function stageStatusColor(status: string | undefined): string {
  return fromMap(STAGE_STATUS_COLORS, status, "var(--bd)");
}

export function stageStatusLabel(status: string | undefined): string {
  return fromMap(STAGE_STATUS_LABELS, status, "Idle");
}

export function priorityStyle(priority: number | undefined): PriorityStyle {
  return PRIORITY_STYLES[priority ?? 3] ?? PRIORITY_STYLES[3];
}
