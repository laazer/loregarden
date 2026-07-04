import type { TicketDetail, WorkflowStageView } from "../api/client";

interface ConfirmRunStageModalProps {
  open: boolean;
  ticket: TicketDetail | null;
  stage: WorkflowStageView | null;
  isRunning: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export function ConfirmRunStageModal({
  open,
  ticket,
  stage,
  isRunning,
  onClose,
  onConfirm,
}: ConfirmRunStageModalProps) {
  if (!open || !ticket || !stage) return null;

  const isRerun = stage.status === "done";

  return (
    <>
      <div className="modal-overlay" onClick={isRunning ? undefined : onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby="confirm-run-stage-title">
        <div className="modal-header">
          <div>
            <div className="state-label">Stage execution</div>
            <h2 id="confirm-run-stage-title" className="modal-title">
              {isRerun ? "Re-run stage?" : "Run stage?"}
            </h2>
            <p className="modal-subtitle">{ticket.title}</p>
          </div>
          <button type="button" className="btn-secondary" disabled={isRunning} onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="modal-body">
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.55, color: "var(--txm)" }}>
            {isRerun ? (
              <>
                Are you sure you want to re-run <strong style={{ color: "var(--tx)" }}>{stage.name}</strong>?
                This stage is already marked done and will invoke its sub-agent again.
              </>
            ) : (
              <>
                Are you sure you want to run <strong style={{ color: "var(--tx)" }}>{stage.name}</strong>?
                This will invoke the stage sub-agent and may update ticket workflow state.
              </>
            )}
          </p>
          {(stage.agent_id || stage.skill_name) && (
            <div className="state-card" style={{ marginTop: 4 }}>
              <div className="state-label">Agent</div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                {stage.agent_id}
                {stage.skill_name ? ` · ${stage.skill_name}` : ""}
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={isRunning} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={isRunning} onClick={onConfirm}>
            {isRunning ? "Running…" : isRerun ? "Re-run stage" : "Run stage"}
          </button>
        </div>
      </div>
    </>
  );
}

export function stageRunButtonLabel(stage: WorkflowStageView, isRunning: boolean): string {
  if (isRunning) return "Running…";
  if (stage.status === "done") return "Re-Run";
  return "Run";
}

export function currentStageRunLabel(stage: WorkflowStageView | undefined, isRunning: boolean): string {
  if (!stage) return "Run current stage";
  if (isRunning) return "Running…";
  if (stage.status === "done") return "Re-run current stage";
  return "Run current stage";
}
