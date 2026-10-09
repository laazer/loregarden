import { IconCloseButton } from "./IconCloseButton";

import type { TicketDetail } from "../api/client";
import { workItemTypeLabel } from "../lib/workItemHierarchy";
import { Button } from "./ui/Button";
import { ModalShell } from "./ui/ModalShell";

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
  // Escape and the backdrop agree on purpose: whatever makes a click
  // dismiss this dialog is what makes the key dismiss it.
  return (
    <ModalShell
      open={open && ticket !== null}
      onDismiss={isDeleting ? undefined : onClose}
      labelledBy="delete-ticket-confirm-title"
    >
      {ticket ? (
        <>
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
              <div className="form-error" role="alert">
                {error}
              </div>
            ) : null}
          </div>

          <div className="modal-footer">
            <Button variant="secondary" disabled={isDeleting} onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="primary"
              style={{ background: "var(--rdl)", borderColor: "transparent" }}
              disabled={isDeleting}
              onClick={onConfirm}
            >
              {isDeleting ? "Deleting…" : "Delete work item"}
            </Button>
          </div>
        </>
      ) : null}
    </ModalShell>
  );
}
