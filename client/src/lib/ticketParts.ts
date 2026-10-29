import type { TicketTreeNode, WorkItemType } from "../api/types";

/**
 * A ticket whose children are its parts: one the orchestrator split, or a bug
 * broken into sub-items by hand. Milestones, initiatives, features and
 * capabilities are containers: their children are the point, and their pages
 * already lead with them.
 */
export function hasParts(ticket: { work_item_type: WorkItemType; was_split?: boolean; split?: unknown }): boolean {
  return Boolean(ticket.was_split || ticket.split) || ticket.work_item_type === "bug";
}

/** Where one part stands, as the parts list, the bar and the list rows say it. */
export type PartStanding = "done" | "running" | "blocked" | "waiting";

export function partStanding(part: TicketTreeNode): PartStanding {
  if (part.state === "done" || part.state === "wont_do") return "done";
  if (part.state === "blocked") return "blocked";
  if (part.state === "in_progress" || part.workflow_stage_status === "running") return "running";
  return "waiting";
}

export interface PartsProgress {
  total: number;
  done: number;
  /** The part to open first: the one running, else the first not done. */
  current: TicketTreeNode | null;
  running: boolean;
}

export function partsProgress(parts: readonly TicketTreeNode[]): PartsProgress {
  const standings = parts.map(partStanding);
  const runningAt = standings.indexOf("running");
  const openAt = runningAt >= 0 ? runningAt : standings.findIndex((s) => s !== "done");
  return {
    total: parts.length,
    done: standings.filter((s) => s === "done").length,
    current: openAt >= 0 ? parts[openAt] : null,
    running: runningAt >= 0,
  };
}

/** "4 parts · 1 done", or "3 parts · done" once every part is. */
export function partsTag(parts: readonly TicketTreeNode[]): string {
  const { total, done } = partsProgress(parts);
  const noun = total === 1 ? "part" : "parts";
  return done === total ? `${total} ${noun} · done` : `${total} ${noun} · ${done} done`;
}
