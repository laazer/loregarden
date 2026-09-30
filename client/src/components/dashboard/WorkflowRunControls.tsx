import type { TicketDetail, WorkflowTemplateSummary } from "../../api/client";
import { API_BASE } from "../../api/client";
import { canRunStage } from "../../lib/stageRunPolicy";
import { buildOrchestrateTerminalCommand } from "../../lib/terminalCommands";
import { agentsAssembleLabel } from "../../lib/workflowHelpers";
import { WorkflowRunOverflowMenu } from "../WorkflowRunOverflowMenu";

interface WorkflowRunControlsProps {
  ticket: TicketDetail;
  workflowTemplates: WorkflowTemplateSummary[] | undefined;
  hasActiveRun: boolean;
  workflowBusy: boolean;
  startRunPending: boolean;
  /** Agents-assemble is in flight for this ticket. */
  assemblePending: boolean;
  pausePending: boolean;
  isStageRunning: (stageKey: string) => boolean;
  onAssemble: () => void;
  onPause: () => void;
  onRerun: (stageKey: string) => void;
  onDelete: () => void;
}

/**
 * The bar under the Console's workflow pane: assemble agents, pause, and the
 * overflow (re-run the cursor stage, copy the orchestrate command, delete). A
 * parent ticket runs through its children, so it gets assemble only.
 */
export function WorkflowRunControls({
  ticket,
  workflowTemplates,
  hasActiveRun,
  workflowBusy,
  startRunPending,
  assemblePending,
  pausePending,
  isStageRunning,
  onAssemble,
  onPause,
  onRerun,
  onDelete,
}: WorkflowRunControlsProps) {
  return (
    <div className="run-controls">
      {ticket.child_count === 0 ? (
        (() => {
          const cursorStage = ticket.stages.find((s) => s.key === ticket.workflow_stage_key);
          const cursorRun = cursorStage
            ? canRunStage(ticket, cursorStage)
            : { allowed: false, reason: "No cursor stage" };
          const runningCursor = isStageRunning(ticket.workflow_stage_key);
          const canPause =
            hasActiveRun ||
            ticket.workflow_stage_status === "running" ||
            ticket.workflow_stage_status === "awaiting";
          const templateLabel =
            workflowTemplates?.find((t) => t.slug === ticket.workflow_template_slug)
              ?.name ??
            ticket.workflow_template_slug ??
            "";
          const rerunDisabled =
            workflowBusy || startRunPending || !cursorRun.allowed || runningCursor;
          return (
            <>
              <button
                type="button"
                className="btn-primary"
                disabled={assemblePending}
                onClick={() => onAssemble()}
              >
                {agentsAssembleLabel(ticket, assemblePending)}
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.6"
                  aria-hidden
                >
                  <path d="m9 18 6-6-6-6" />
                </svg>
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={!canPause || pausePending}
                title={canPause ? "Pause the running stage" : "Nothing to pause"}
                onClick={() => onPause()}
              >
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  aria-hidden
                >
                  <rect x="6" y="4" width="4" height="16" />
                  <rect x="14" y="4" width="4" height="16" />
                </svg>
                {pausePending ? "Pausing…" : "Pause"}
              </button>
              <div style={{ flex: 1 }} />
              {templateLabel ? (
                <span className="run-controls-template">{templateLabel}</span>
              ) : null}
              <WorkflowRunOverflowMenu
                ticket={ticket}
                orchestrateCommand={buildOrchestrateTerminalCommand(ticket, API_BASE)}
                rerunDisabled={rerunDisabled}
                rerunTitle={cursorRun.reason}
                onRerun={() => onRerun(ticket.workflow_stage_key)}
                onDelete={() => onDelete()}
              />
            </>
          );
        })()
      ) : (
        <>
          <button
            type="button"
            className="btn-primary"
            disabled={assemblePending}
            onClick={() => onAssemble()}
          >
            {agentsAssembleLabel(
              ticket,
              assemblePending,
            )}
          </button>
          <div style={{ flex: 1 }} />
        </>
      )}
    </div>
  );
}
