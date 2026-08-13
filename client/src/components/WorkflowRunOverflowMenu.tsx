import type { TicketDetail } from "../api/client";
import { copyText } from "../lib/clipboard";
import {
  EXTERNAL_HARNESSES,
  EXTERNAL_HARNESS_LABELS,
  copyExternalHarnessPrompt,
} from "../lib/externalHarnessPrompt";
import { isAgentWorkflowTicket } from "../lib/terminalCommands";
import { OverflowMenu, OverflowMenuItem, OverflowMenuSection } from "./OverflowMenu";

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
          <OverflowMenuSection title="Copy prompt for" />
          {EXTERNAL_HARNESSES.map((harness) => (
            <OverflowMenuItem
              key={harness}
              title={`Copy a prompt that runs this ticket in ${EXTERNAL_HARNESS_LABELS[harness]}, reporting progress and timing back over MCP`}
              onSelect={() => void copyExternalHarnessPrompt(ticket, harness)}
            >
              {EXTERNAL_HARNESS_LABELS[harness]}
            </OverflowMenuItem>
          ))}
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
