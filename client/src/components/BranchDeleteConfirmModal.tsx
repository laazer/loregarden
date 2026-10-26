import { useState } from "react";

import { IconCloseButton } from "./IconCloseButton";

import type { BranchTriageEntry } from "../lib/branchTriageApi";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";
import { ModalShell } from "./ui/ModalShell";

interface BranchDeleteConfirmModalProps {
  open: boolean;
  branch: BranchTriageEntry | null;
  isDeleting: boolean;
  error?: string | null;
  onClose: () => void;
  /** `discardCommits`: the person ticked "discard" for commits only this branch has. */
  onConfirm: (discardCommits: boolean) => void;
}

export function BranchDeleteConfirmModal({
  open,
  branch,
  isDeleting,
  error,
  onClose,
  onConfirm,
}: BranchDeleteConfirmModalProps) {
  if (!open || !branch) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="branch-delete-confirm-title">{null}</ModalShell>;
  }

  return (
    <BranchDeleteConfirmBody
      key={branch.name}
      branch={branch}
      isDeleting={isDeleting}
      error={error}
      onClose={onClose}
      onConfirm={onConfirm}
    />
  );
}

/**
 * Keyed by branch, so the "discard" tick never carries from one branch to the next.
 *
 * A branch whose commits exist nowhere else (not pushed, not on another branch,
 * not in the base) can be deleted here only after the person ticks a box that
 * names how many commits go with it. Without the tick the server refuses.
 */
function BranchDeleteConfirmBody({
  branch,
  isDeleting,
  error,
  onClose,
  onConfirm,
}: Omit<BranchDeleteConfirmModalProps, "open" | "branch"> & { branch: BranchTriageEntry }) {
  const [discard, setDiscard] = useState(false);
  const worktreePaths = branch.worktrees.map((item) => item.path).filter(Boolean);
  const removeWorktrees = worktreePaths.length > 0;
  const onlyHere = branch.commits_only_here;
  const losesCommits = onlyHere !== 0;
  const commitWord = onlyHere === 1 ? "commit" : "commits";

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
        <p style={{ margin: 0, fontSize: "var(--fs-body)", lineHeight: 1.55, color: "var(--txm)" }}>
          {losesCommits ? (
            <>
              {onlyHere === null ? (
                <>Git could not tell whether this branch holds commits that exist nowhere else.</>
              ) : (
                <>
                  <strong style={{ color: "var(--tx)" }}>
                    {onlyHere} {commitWord}
                  </strong>{" "}
                  on this branch {onlyHere === 1 ? "exists" : "exist"} nowhere else: not pushed, not
                  on another branch, and not in the base.
                </>
              )}{" "}
              Push the branch to keep {onlyHere === 1 ? "it" : "them"}.
            </>
          ) : (
            <>Every commit on this branch is also somewhere else, so deleting the branch loses none.</>
          )}
        </p>

        {losesCommits ? (
          <label className="branch-delete-confirm-discard">
            <Input
              type="checkbox"
              checked={discard}
              disabled={isDeleting}
              onChange={(event) => setDiscard(event.target.checked)}
            />
            <span>
              {onlyHere === null
                ? "Delete anyway, discarding any commits only this branch has"
                : `Discard ${onlyHere} ${commitWord} with the branch`}
            </span>
          </label>
        ) : null}

        {removeWorktrees ? (
          <div className="branch-delete-confirm-worktrees">
            <div className="modal-section-title">
              {worktreePaths.length === 1 ? "Linked worktree" : "Linked worktrees"}
            </div>
            <p style={{ margin: "0 0 8px", fontSize: "var(--fs-small)", lineHeight: 1.5, color: "var(--txm)" }}>
              This branch is checked out in {worktreePaths.length} worktree
              {worktreePaths.length === 1 ? "" : "s"}. They will be removed before the branch is
              deleted, unless one holds work that exists nowhere else.
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
                One has uncommitted changes, so the branch will be kept. Commit or discard them
                first.
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
        <Button variant="secondary" disabled={isDeleting} onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          className="branch-delete-confirm-submit"
          disabled={isDeleting || (losesCommits && !discard)}
          onClick={() => onConfirm(losesCommits && discard)}
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
