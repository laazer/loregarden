import type { StageStatus, TicketState, WorkflowStageView } from "../../../api/types";

const DONE_STAGES: ReadonlySet<StageStatus> = new Set(["done", "wont_do"]);
const DONE_TICKET_STATES: ReadonlySet<TicketState> = new Set(["done", "wont_do"]);

export function stageProgressRatio(stages: WorkflowStageView[] | undefined): number {
  if (!stages || stages.length === 0) return 0;
  const done = stages.filter((s) => DONE_STAGES.has(s.status)).length;
  return done / stages.length;
}

export function stageProgressPercent(stages: WorkflowStageView[] | undefined): number {
  return Math.round(stageProgressRatio(stages) * 100);
}

export function childProgressRatio(
  children: { state: TicketState }[] | undefined,
): number {
  if (!children || children.length === 0) return 0;
  const done = children.filter((c) => DONE_TICKET_STATES.has(c.state)).length;
  return done / children.length;
}

export function childProgressPercent(children: { state: TicketState }[] | undefined): number {
  return Math.round(childProgressRatio(children) * 100);
}

export function isStageRunning(status: StageStatus | undefined): boolean {
  return status === "running" || status === "awaiting";
}

export function ticketStateColor(state: TicketState | string | undefined): string {
  switch (state) {
    case "in_progress":
      return "var(--blue)";
    case "blocked":
      return "var(--red)";
    case "done":
      return "var(--grn)";
    case "wont_do":
      return "var(--txl)";
    default:
      return "var(--amb)";
  }
}
