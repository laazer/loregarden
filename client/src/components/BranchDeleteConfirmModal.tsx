import { useRestoreFocusOnSettle } from "../hooks/useRestoreFocusOnSettle";
import { IconCloseButton } from "./IconCloseButton";

import type { BranchRemovalOutcome, BranchTriageEntry } from "../lib/branchTriageApi";
import { removalNotice } from "../lib/branchRemovalNotices";
import { BranchNoticeRow, BranchRefusalBlock } from "./BranchRemovalNotices";
import { Button } from "./ui/Button";
import { ModalShell } from "./ui/ModalShell";

interface BranchDeleteConfirmModalProps {
  open: boolean;
  branch: BranchTriageEntry | null;
  isDeleting: boolean;
  /** A transport or unknown failure — red, as before. */
  error?: string | null;
  /**
   * What the receipt gate answered, when it has answered.
   *
   * A refusal used to arrive here as `error`, which told the operator it had
   * gone wrong. It had not: the gate worked and nothing was lost, so the two
   * are separate props and are styled apart.
   */
  outcome?: BranchRemovalOutcome;
  refusal?: { reason: string; detail: string; parked_path: string } | null;
  onClose: () => void;
  onConfirm: () => void;
}

export function BranchDeleteConfirmModal({
  open,
  branch,
  isDeleting,
  error,
  outcome,
  refusal,
  onClose,
  onConfirm,
}: BranchDeleteConfirmModalProps) {
  // The confirm is disabled for the duration of the delete, which blurs it;
  // focus returns to it when the gate answers, refusal or parked alike.
  const rememberActivated = useRestoreFocusOnSettle(isDeleting);

  if (!open || !branch) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="branch-delete-confirm-title">{null}</ModalShell>;
  }

  const worktreePaths = branch.worktrees.map((item) => item.path).filter(Boolean);
  const removeWorktrees = worktreePaths.length > 0;
  const notice = refusal
    ? removalNotice(branch.name, { outcome: outcome ?? "refused", ...refusal }, branch.linked_tickets[0] ?? null)
    : null;

  return (
    <ModalShell open onDismiss={isDeleting ? undefined : onClose} labelledBy="branch-delete-confirm-title" panelClassName="branch-delete-confirm-modal">
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

        {error || (notice && outcome === "parked") ? (
          <div className="branch-triage-delete-error branch-cleanup-failures" style={{ marginTop: 12 }}>
            {error}
            {notice && outcome === "parked" ? (
              <ul>
                <BranchNoticeRow notice={notice} />
              </ul>
            ) : null}
          </div>
        ) : null}

        <BranchRefusalBlock
          notices={notice && outcome === "refused" ? [notice] : []}
          pluralNoun="branch(es)"
        />
      </div>

      <div className="modal-footer">
        <Button variant="secondary" disabled={isDeleting} onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          className="branch-delete-confirm-submit"
          disabled={isDeleting}
          onClick={(event) => {
            rememberActivated(event.currentTarget);
            onConfirm();
          }}
        >
          {isDeleting
            ? "Deleting…"
            : removeWorktrees
              ? "Remove worktrees & delete"
              : "Delete branch"}
        </Button>
      </div>
    </ModalShell>
  );
}
