import { createPortal } from "react-dom";

import type { TicketDetail } from "../../api/client";
import type { TicketWork } from "../../hooks/useTicketWork";
import { DEFAULT_ORCHESTRATION_RUNTIME } from "../../hooks/useTicketWork";
import { WorkflowTicketSettings } from "../dashboard/WorkflowTicketSettings";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { ModalShell } from "../ui/ModalShell";
import { runtimeSummaryLabel } from "../WorkspaceRuntimeFields";

/**
 * The ticket's workflow settings behind the Workflow card's gear: template,
 * branch, compatibility posture, and the way into its model. Each field saves
 * as it changes, so the dialog has no submit; Done closes it.
 */
export function WorkflowSettingsModal({
  open,
  ticket,
  work,
  onClose,
  onOpenModel,
}: {
  open: boolean;
  ticket: TicketDetail;
  work: TicketWork;
  onClose: () => void;
  /** Closes this dialog and opens the model picker in its place. */
  onOpenModel: () => void;
}) {
  // Portalled whether open or not, so the shell stays put for its exit.
  return createPortal(
    <ModalShell open={open} onDismiss={onClose} labelledBy="workflow-settings-title">
      {open ? (
        <>
          <div className="modal-header">
            <div>
              <div className="state-label">{ticket.external_id}</div>
              <h2 id="workflow-settings-title" className="modal-title">
                Workflow settings
              </h2>
              <p className="modal-subtitle">Each change saves as you make it and applies to this ticket&apos;s next run.</p>
            </div>
            <IconCloseButton onClick={onClose} />
          </div>
          <div className="modal-body">
            <WorkflowTicketSettings
              ticket={ticket}
              workflowTemplates={work.workflowTemplates.data}
              workspaceWorkflow={work.workspaceWorkflow.data}
              workflowBusy={work.workflowBusy}
              templatePending={work.setTicketTemplate.isPending}
              postureSaving={work.setCompatibilityPosture.isPending}
              onWorkflowChange={(template) => void work.requestWorkflowChange(ticket.id, template)}
              onSaveBranch={(branch) => work.saveTicketBranch(ticket.id, branch)}
              onPostureChange={(posture) => work.setCompatibilityPosture.mutate(posture)}
            />
            <div>
              <div className="state-label" style={{ marginBottom: 6 }}>
                Model
              </div>
              <Button variant="secondary" compact onClick={onOpenModel} title="Model settings for this ticket's runs">
                {runtimeSummaryLabel(ticket.orchestration_runtime ?? DEFAULT_ORCHESTRATION_RUNTIME, work.runtimeOptions.data)}
                {" · Change…"}
              </Button>
            </div>
          </div>
          <div className="modal-footer">
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        </>
      ) : null}
    </ModalShell>,
    document.body,
  );
}
