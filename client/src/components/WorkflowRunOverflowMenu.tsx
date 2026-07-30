import type { TicketDetail } from "../api/client";
import { isAgentWorkflowTicket } from "../lib/terminalCommands";
import { OverflowMenu, OverflowMenuItem, OverflowMenuSection } from "./OverflowMenu";

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    document.body.removeChild(textarea);
  }
}

interface WorkflowRunOverflowMenuProps {
  ticket: TicketDetail;
  orchestrateCommand: string;
  rerunDisabled: boolean;
  rerunTitle?: string;
  onRerun: () => void;
  onDelete: () => void;
}

export function WorkflowRunOverflowMenu({
  ticket,
  orchestrateCommand,
  rerunDisabled,
  rerunTitle,
  onRerun,
  onDelete,
}: WorkflowRunOverflowMenuProps) {
  return (
    <OverflowMenu label="More workflow actions" align="right">
      <OverflowMenuSection title="Pipeline" />
      <OverflowMenuItem disabled={rerunDisabled} title={rerunTitle} onSelect={onRerun}>
        Re-run current stage
      </OverflowMenuItem>
      {isAgentWorkflowTicket(ticket) ? (
        <>
          <OverflowMenuSection title="Terminal" />
          <OverflowMenuItem
            disabled={!orchestrateCommand.trim()}
            title="Copy terminal command to orchestrate this ticket"
            onSelect={() => void copyText(orchestrateCommand)}
          >
            Copy orchestrate command
          </OverflowMenuItem>
        </>
      ) : null}
      {ticket.run_code ? (
        <>
          <OverflowMenuSection title="Run code" />
          <div className="overflow-menu-meta">{ticket.run_code}</div>
        </>
      ) : null}
      <OverflowMenuSection title="Danger zone" />
      <OverflowMenuItem danger onSelect={onDelete}>
        Delete ticket
      </OverflowMenuItem>
    </OverflowMenu>
  );
}
