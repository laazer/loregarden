/**
 * The confirmation a view tab's delete goes through.
 *
 * Deleting a view is not recoverable: there is no undo endpoint, the layout goes
 * with it, and the sidebar entry goes with the layout. Same shape as
 * `DeleteTicketConfirmModal`, for the same reason.
 */

import type { ViewSummary } from "../lib/viewsApi";
import { IconCloseButton } from "./IconCloseButton";
import { Button } from "./ui/Button";
import { ModalShell } from "./ui/ModalShell";

export function DeleteViewConfirmModal({
  view,
  isDeleting,
  onClose,
  onConfirm,
}: {
  /** The view awaiting confirmation, or null when nothing is. */
  view: ViewSummary | null;
  isDeleting: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  if (!view) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="delete-view-confirm-title">{null}</ModalShell>;
  }

  return (
    <ModalShell open onDismiss={isDeleting ? undefined : onClose} labelledBy="delete-view-confirm-title">
      <div className="modal-header">
        <div>
          <div className="state-label">Tab</div>
          <h2 id="delete-view-confirm-title" className="modal-title">
            Delete view?
          </h2>
          <p className="modal-subtitle">{view.title}</p>
        </div>
        <IconCloseButton disabled={isDeleting} onClick={onClose} />
      </div>

      <div className="modal-body">
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.55, color: "var(--txm)" }}>
          This deletes the view, its layout and its tab. It cannot be undone.
        </p>
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
          {isDeleting ? "Deleting…" : "Delete view"}
        </Button>
      </div>
    </ModalShell>
  );
}
