import { IconCloseButton } from "./IconCloseButton";

import type { BranchTriageEntry } from "../lib/branchTriageApi";

interface BranchDeleteConfirmModalProps {
  open: boolean;
  branch: BranchTriageEntry | null;
  isDeleting: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: () => void;
}

export function BranchDeleteConfirmModal({
  open,
  branch,
  isDeleting,
  error,
  onClose,
  onConfirm,
}: BranchDeleteConfirmModalProps) {
  if (!open || !branch) return null;

  const worktreePaths = branch.worktrees.map((item) => item.path).filter(Boolean);
  const removeWorktrees = worktreePaths.length > 0;

  return (
    <>
      <div
        className="modal-overlay"
        onClick={isDeleting ? undefined : onClose}
        role="presentation"
      />
      <div
        className="modal-panel branch-delete-confirm-modal"
        role="dialog"
        aria-labelledby="branch-delete-confirm-title"
        aria-modal="true"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">Branch triage</div>
            <h2 id="branch-delete-confirm-title" className="modal-title">
              Delete branch?
            </h2>
            <p className="modal-subtitle" style={{ fontFamily: "var(--mono)" }}>
              {branch.name}
            </p>
          </div>
          <IconCloseButton disabled={isDeleting} onClick={onClose} />
        </div>

        <div className="modal-body">
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.55, color: "var(--txm)" }}>
            {branch.ahead > 0 ? (
              <>
                This branch has{" "}
                <strong style={{ color: "var(--tx)" }}>
                  {branch.ahead} commit{branch.ahead === 1 ? "" : "s"}
                </strong>{" "}
                not merged into main. Deleting it is permanent and cannot be undone.
              </>
            ) : (
              <>This will permanently delete the local branch. This action cannot be undone.</>
            )}
          </p>

          {removeWorktrees ? (
            <div className="branch-delete-confirm-worktrees">
              <div className="modal-section-title">
                {worktreePaths.length === 1 ? "Linked worktree" : "Linked worktrees"}
              </div>
              <p style={{ margin: "0 0 8px", fontSize: 12.5, lineHeight: 1.5, color: "var(--txm)" }}>
                This branch is checked out in {worktreePaths.length} worktree
                {worktreePaths.length === 1 ? "" : "s"}. They will be removed before the branch is
                deleted.
              </p>
              <ul className="branch-delete-confirm-path-list">
                {worktreePaths.map((path) => (
                  <li key={path}>
                    <code>{path}</code>
                  </li>
                ))}
              </ul>
              {branch.dirty ? (
                <p className="modal-hint" style={{ marginTop: 8 }}>
                  Uncommitted changes in those worktrees will be lost.
                </p>
              ) : null}
            </div>
          ) : null}

          {error ? (
            <div className="branch-triage-delete-error" style={{ marginTop: 12 }}>
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
            className="btn-primary branch-delete-confirm-submit"
            disabled={isDeleting}
            onClick={onConfirm}
          >
            {isDeleting
              ? "Deleting…"
              : removeWorktrees
                ? "Remove worktrees & delete"
                : "Delete branch"}
          </button>
        </div>
      </div>
    </>
  );
}
