/**
 * The confirm step for discrediting or restoring a learning.
 *
 * Either direction changes what every future agent run is briefed with, so it
 * asks, and it asks why: the reason is stored with the change in the node's
 * version history, where the next person to wonder can read it.
 */

import { useState } from "react";

import type { MemoryNode } from "../../api/memoryApi";
import { useDialogDismiss } from "../../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../../hooks/useDialogFocusTrap";
import { IconCloseButton } from "../IconCloseButton";

export function DiscreditConfirmModal({
  node,
  isSaving,
  onClose,
  onConfirm,
}: {
  /** The learning awaiting confirmation, or null when nothing is. */
  node: MemoryNode | null;
  isSaving: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  useDialogDismiss(!node ? null : isSaving ? undefined : onClose);
  if (!node) return null;

  const restoring = node.discredited;
  const trimmed = reason.trim();
  const action = restoring ? "Restore learning" : "Discredit learning";

  return (
    <>
      <div className="modal-overlay" onClick={isSaving ? undefined : onClose} role="presentation" />
      <div
        ref={dialogRef}
        className="modal-panel"
        role="dialog"
        aria-labelledby="discredit-confirm-title"
        aria-modal="true"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">Learning</div>
            <h2 id="discredit-confirm-title" className="modal-title">
              {restoring ? "Restore this learning?" : "Discredit this learning?"}
            </h2>
            <p className="modal-subtitle">{node.title}</p>
          </div>
          <IconCloseButton disabled={isSaving} onClick={onClose} />
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (trimmed && !isSaving) onConfirm(trimmed);
          }}
        >
          <div className="modal-body">
            <p className="memory-muted">
              {restoring
                ? "Agents will be briefed with this learning again on their next run."
                : "Agents stop being briefed with this learning on their next run. It stays in the record and can be restored."}
            </p>
            <label className="memory-field">
              <span>Reason (stored with the change)</span>
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
                required
                disabled={isSaving}
              />
            </label>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn-secondary" disabled={isSaving} onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={isSaving || !trimmed}>
              {isSaving ? "Saving…" : action}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
