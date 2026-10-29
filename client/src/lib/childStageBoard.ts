import type { StageStatus, TicketSummary, WorkflowStageView } from "../api/types";

/** One column of the board: a stage some child's workflow has. */
export interface BoardColumn {
  key: string;
  name: string;
}

/**
 * The stages every child runs, in workflow order. Children on different
 * workflows contribute their own stages; a key appears once, where it first
 * appears in the child with the most stages, so the common path reads left to
 * right and extra stages slot in after the stage they follow.
 */
export function boardColumns(
  children: readonly TicketSummary[],
  fallback: readonly WorkflowStageView[] = [],
): BoardColumn[] {
  // A child gets its workflow instance when it first runs, so a parent whose
  // children have all yet to start has no child stages to read: the parent's
  // own workflow is the one they will run.
  if (!children.some((child) => child.stages?.length)) {
    return fallback.map((stage) => ({ key: stage.key, name: stage.name || stage.key }));
  }
  const ordered = [...children].sort((a, b) => (b.stages?.length ?? 0) - (a.stages?.length ?? 0));
  const columns: BoardColumn[] = [];
  const seen = new Set<string>();
  for (const child of ordered) {
    let after = -1;
    for (const stage of child.stages ?? []) {
      const at = columns.findIndex((column) => column.key === stage.key);
      if (at >= 0) {
        after = at;
        continue;
      }
      if (seen.has(stage.key)) continue;
      seen.add(stage.key);
      columns.splice(after + 1, 0, { key: stage.key, name: stage.name || stage.key });
      after += 1;
    }
  }
  return columns;
}

/** Where a child stands, as the board groups its rows. */
export type BoardGroup = "blocked" | "you" | "running" | "in_progress" | "not_started" | "done" | "wont_do";

export const BOARD_GROUPS: readonly { key: BoardGroup; label: string }[] = [
  { key: "blocked", label: "Blocked" },
  { key: "you", label: "Waiting on you" },
  { key: "running", label: "Running" },
  { key: "in_progress", label: "In progress" },
  { key: "not_started", label: "Not started" },
  { key: "done", label: "Done" },
  { key: "wont_do", label: "Won't do" },
];

export function boardGroup(child: TicketSummary): BoardGroup {
  if (child.state === "done") return "done";
  if (child.state === "wont_do") return "wont_do";
  if (child.state === "blocked") return "blocked";
  if (child.state === "parked" || child.activity === "awaiting") return "you";
  if (child.activity === "running" || child.activity === "queued") return "running";
  if (child.state === "backlog") return "not_started";
  return "in_progress";
}

/** A row whose current run can be opened on its live log. */
export function isActive(child: TicketSummary): boolean {
  return child.activity === "running" || child.activity === "awaiting";
}

/** How one cell draws: its stage's status, or nothing when the child's workflow lacks the stage. */
export type CellMark = StageStatus | "absent";

export function cellMark(child: TicketSummary, key: string): CellMark {
  // No workflow instance yet: it has reached none of them.
  if (!child.stages?.length) return "pending";
  return child.stages.find((stage: WorkflowStageView) => stage.key === key)?.status ?? "absent";
}

const MARK_WORDS: Record<CellMark, string> = {
  done: "done",
  running: "running",
  blocked: "blocked",
  awaiting: "waiting on you",
  pending: "not reached",
  wont_do: "skipped",
  absent: "not in this workflow",
};

export function cellLabel(column: BoardColumn, mark: CellMark): string {
  return `${column.name}: ${MARK_WORDS[mark]}`;
}

/**
 * How many children sit at each stage now: a child counts at its current stage
 * until it is done, then at the last column, so a pile-up reads without the rows.
 */
export function stageCounts(children: readonly TicketSummary[], columns: readonly BoardColumn[]): Map<string, number> {
  const counts = new Map<string, number>(columns.map((column) => [column.key, 0]));
  const last = columns.at(-1)?.key;
  for (const child of children) {
    // A child with children of its own shows their progress, not stages: its
    // own cursor stopped where it was split and would count at a stale stage.
    if (child.state === "wont_do" || child.child_count > 0) continue;
    const key = child.state === "done" ? last : child.workflow_stage_key;
    if (key && counts.has(key)) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}
