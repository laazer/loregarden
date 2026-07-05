import type { TicketDetail } from "../api/client";

export function workflowHasStarted(ticket: TicketDetail): boolean {
  if (ticket.state !== "backlog") return true;
  return ticket.stages.some((stage) => stage.status !== "pending");
}

export function agentsAssembleLabel(ticket: TicketDetail | undefined, isPending: boolean): string {
  if (isPending) return "Running…";
  if (!ticket) return "Run Agents Assemble";
  return workflowHasStarted(ticket) ? "Continue Run" : "Run Agents Assemble";
}
