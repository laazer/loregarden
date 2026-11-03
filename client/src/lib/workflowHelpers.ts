import type { TicketDetail } from "../api/client";
import { isDoneStage } from "./stageDisplay";

export function workflowHasStarted(ticket: TicketDetail): boolean {
  if (ticket.state !== "backlog") return true;
  return ticket.stages.some((stage) => stage.status !== "pending");
}

/** A live orchestration on the ticket's terminal stage: no agent runs there, its work is landing. */
export function isLanding(
  ticket: Pick<TicketDetail, "active_orchestration" | "workflow_stage_key" | "stages">,
): boolean {
  const run = ticket.active_orchestration;
  if (!run || run.current_stage_key !== ticket.workflow_stage_key) return false;
  return ticket.stages.some((stage) => stage.key === run.current_stage_key && isDoneStage(stage));
}

export function agentsAssembleLabel(ticket: TicketDetail | undefined, isPending: boolean): string {
  if (isPending) return "Running…";
  if (!ticket) return "Run Agents Assemble";
  // Starting another would be refused ("Orchestration already running"); say what this one is doing.
  if (ticket.active_orchestration) return isLanding(ticket) ? "Landing…" : "Running…";
  return workflowHasStarted(ticket) ? "Continue Run" : "Run Agents Assemble";
}
