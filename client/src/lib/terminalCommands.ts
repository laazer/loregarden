import type { TicketDetail, WorkflowStageView } from "../api/client";

export function isAgentWorkflowTicket(ticket: TicketDetail): boolean {
  return Boolean(ticket.workflow_template_slug?.trim());
}

const DEFAULT_API_BASE = "http://127.0.0.1:8000";

export function buildOrchestrateTerminalCommand(
  ticket: TicketDetail,
  apiBase: string = DEFAULT_API_BASE,
): string {
  const base = apiBase.replace(/\/$/, "");
  const title = ticket.title.replace(/"/g, '\\"');
  return [
    `# Loregarden: orchestrate ticket ${ticket.external_id} — ${title}`,
    `curl -sS -X POST '${base}/api/tickets/${ticket.id}/orchestrate' \\`,
    `  -H 'Content-Type: application/json' \\`,
    `  -d '{}'`,
  ].join("\n");
}

export function buildStageRunTerminalCommand(
  ticket: TicketDetail,
  stage: WorkflowStageView,
  apiBase: string = DEFAULT_API_BASE,
): string {
  const base = apiBase.replace(/\/$/, "");
  const title = ticket.title.replace(/"/g, '\\"');
  const body = JSON.stringify({ manual: true, stage_key: stage.key });
  return [
    `# Loregarden: run stage "${stage.name}" (${stage.key}) for ${ticket.external_id} — ${title}`,
    `curl -sS -X POST '${base}/api/tickets/${ticket.id}/start' \\`,
    `  -H 'Content-Type: application/json' \\`,
    `  -d '${body}'`,
  ].join("\n");
}
