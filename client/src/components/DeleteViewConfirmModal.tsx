/**
 * The confirmation a view tab's delete goes through.
 *
 * Deleting a view is not recoverable: there is no undo endpoint, the layout goes
 * with it, and the sidebar entry goes with the layout. Same shape as
 * `DeleteTicketConfirmModal`, for the same reason.
 */

import type { ViewSummary } from "../lib/viewsApi";
import { IconCloseButton } from "./IconCloseButton";

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
  if (!view) return null;

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
        aria-labelledby="delete-view-confirm-title"
        aria-modal="true"
      >
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
            {isDeleting ? "Deleting…" : "Delete view"}
          </button>
        </div>
      </div>
    </>
  );
}
