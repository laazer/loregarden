import type { WorkflowReassignmentPreview } from "../api/client";

export interface WorkflowReassignWarningProps {
  preview: WorkflowReassignmentPreview | null;
  isPending?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Confirms a workflow change that would discard stage progress.
 *
 * Changing a ticket's workflow rewrites its stage map and resets it to the
 * first stage. This warns rather than blocks — an operator moving a ticket onto
 * a different pipeline usually means it — but names what is lost, because the
 * old behaviour discarded it silently.
 *
 * Renders nothing unless the change is actually destructive, so assigning a
 * workflow to a ticket that has not started never prompts.
 */
export function WorkflowReassignWarning({
  preview,
  isPending = false,
  onConfirm,
  onCancel,
}: WorkflowReassignWarningProps) {
  if (!preview?.destructive) return null;

  const target = preview.target_template_name || preview.target_template_slug;
  const lost = preview.completed_stages;

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="modal-panel"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="workflow-reassign-title"
        style={{ maxWidth: 480 }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="workflow-reassign-title" className="modal-title">
          Restart this ticket on {target}?
        </h2>

        <p className="modal-subtitle">
          Changing the workflow resets the ticket to the first stage. Progress on the
          current workflow is discarded and cannot be restored.
        </p>

        <div className="modal-field" style={{ marginTop: 14 }}>
          <div className="modal-field-label">
            {lost.length === 1 ? "Stage that will be reset" : "Stages that will be reset"}
          </div>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 12.5 }}>
            {lost.map((stage) => (
              <li key={stage} className="mono">
                {stage}
              </li>
            ))}
          </ul>
        </div>

        {preview.resets_to_stage_key && (
          <p className="modal-subtitle" style={{ marginTop: 10 }}>
            It will restart at <span className="mono">{preview.resets_to_stage_key}</span>.
          </p>
        )}

        <div className="modal-footer">
          <button type="button" className="btn-secondary" onClick={onCancel} disabled={isPending}>
            Keep current workflow
          </button>
          <button type="button" className="btn-primary" onClick={onConfirm} disabled={isPending}>
            {isPending ? "Switching…" : "Switch and reset"}
          </button>
        </div>
      </div>
    </div>
  );
}
