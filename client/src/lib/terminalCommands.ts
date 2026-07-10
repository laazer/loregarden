import { api, type TicketDetail, type WorkflowStageView } from "../api/client";

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

/**
 * Like buildStageRunTerminalCommand, but the copied text launches the actual configured
 * coding agent (Claude Code or Cursor) directly in the user's own terminal instead of
 * hitting POST /start — so the run survives the app's dev server restarting mid-run.
 */
export async function buildStageTerminalHandoffCommand(
  ticket: TicketDetail,
  stage: WorkflowStageView,
): Promise<string> {
  const title = ticket.title.replace(/"/g, '\\"');
  const { adapter, command } = await api.buildTerminalHandoffCommand(ticket.id, stage.key);
  return [
    `# Loregarden: run stage "${stage.name}" (${stage.key}) for ${ticket.external_id} — ${title} — via terminal ${adapter} agent`,
    command,
  ].join("\n");
}
