import type { TicketDetail, WorkflowStageView } from "../api/client";
import { currentStageRunLabel } from "../lib/stageDisplay";
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
  cursorStage: WorkflowStageView | undefined;
  cursorRun: { allowed: boolean; reason: string };
  runningCursor: boolean;
  workflowBusy: boolean;
  startRunPending: boolean;
  advancePending: boolean;
  onRunCurrentStage: () => void;
  onAdvance: () => void;
}

export function WorkflowRunOverflowMenu({
  ticket,
  orchestrateCommand,
  cursorStage,
  cursorRun,
  runningCursor,
  workflowBusy,
  startRunPending,
  advancePending,
  onRunCurrentStage,
  onAdvance,
}: WorkflowRunOverflowMenuProps) {
  const currentStageLabel = currentStageRunLabel(cursorStage, runningCursor);

  return (
    <OverflowMenu label="More workflow actions" align="right">
      <OverflowMenuSection title="Stage" />
      <OverflowMenuItem
        disabled={workflowBusy || startRunPending || !cursorRun.allowed}
        title={cursorRun.reason}
        onSelect={onRunCurrentStage}
      >
        {currentStageLabel}
      </OverflowMenuItem>
      <OverflowMenuItem disabled={advancePending} onSelect={onAdvance}>
        {advancePending ? "Advancing…" : "Advance stage"}
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
    </OverflowMenu>
  );
}
