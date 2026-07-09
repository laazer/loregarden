import { IconCloseButton } from "./IconCloseButton";

import type { TicketDetail } from "../api/client";
import { workItemTypeLabel } from "../lib/workItemHierarchy";

interface DeleteTicketConfirmModalProps {
  open: boolean;
  ticket: TicketDetail | null;
  isDeleting: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: () => void;
}

export function DeleteTicketConfirmModal({
  open,
  ticket,
  isDeleting,
  error,
  onClose,
  onConfirm,
}: DeleteTicketConfirmModalProps) {
  if (!open || !ticket) return null;

  return (
    <>
      <div
        className="modal-overlay"
        onClick={isDeleting ? undefined : onClose}
        role="presentation"
      />
      <div
        className="modal-panel"
        role="dialog"
        aria-labelledby="delete-ticket-confirm-title"
        aria-modal="true"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">{workItemTypeLabel(ticket.work_item_type)}</div>
            <h2 id="delete-ticket-confirm-title" className="modal-title">
              Delete work item?
            </h2>
            <p className="modal-subtitle">{ticket.title}</p>
          </div>
          <IconCloseButton disabled={isDeleting} onClick={onClose} />
        </div>

        <div className="modal-body">
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.55, color: "var(--txm)" }}>
            This permanently deletes the ticket along with its run history, artifacts, and
            approvals. This action cannot be undone.
          </p>

          {error ? (
            <div
              style={{
                marginTop: 12,
                padding: "10px 12px",
                borderRadius: 10,
                border: "1px solid rgba(255, 106, 84, 0.35)",
                background: "rgba(255, 106, 84, 0.08)",
                color: "var(--rdl)",
                fontSize: 12,
                lineHeight: 1.45,
              }}
            >
              {error}
            </div>
          ) : null}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={isDeleting} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            style={{ background: "var(--rdl, #ff6a54)", borderColor: "transparent" }}
            disabled={isDeleting}
            onClick={onConfirm}
          >
            {isDeleting ? "Deleting…" : "Delete work item"}
          </button>
        </div>
      </div>
    </>
  );
}
