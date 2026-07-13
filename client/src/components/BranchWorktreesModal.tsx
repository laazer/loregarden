import { useMutation, useQueryClient } from "@tanstack/react-query";

import { removeBranchWorktree, type BranchTriageEntry } from "../lib/branchTriageApi";
import { IconCloseButton } from "./IconCloseButton";

export function BranchWorktreesModal({
  open,
  branch,
  workspaceSlug,
  onClose,
}: {
  open: boolean;
  branch: BranchTriageEntry | null;
  workspaceSlug: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();

  const remove = useMutation({
    mutationFn: (path: string) => removeBranchWorktree(workspaceSlug, branch!.name, path),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["branch-triage", workspaceSlug] });
    },
  });

  if (!open || !branch) return null;

  const worktrees = branch.worktrees;

  return (
    <>
      <div className="modal-overlay" onClick={onClose} role="presentation" />
      <div
        className="modal-panel branch-worktrees-modal"
        role="dialog"
        aria-labelledby="branch-worktrees-title"
        aria-modal="true"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">Branch triage</div>
            <h2 id="branch-worktrees-title" className="modal-title">
              Worktrees
            </h2>
            <p className="modal-subtitle" style={{ fontFamily: "var(--mono)" }}>
              {branch.name}
            </p>
          </div>
          <IconCloseButton onClick={onClose} />
        </div>

        <div className="modal-body">
          {worktrees.length === 0 ? (
            <p style={{ margin: 0, fontSize: 13, color: "var(--txm)" }}>
              No worktrees are checked out for this branch.
            </p>
          ) : (
            <ul className="branch-worktrees-list">
              {worktrees.map((wt) => (
                <li key={wt.path} className="branch-worktrees-row">
                  <div className="branch-worktrees-info">
                    <code>{wt.path}</code>
                    <span className={`branch-worktrees-status ${wt.dirty ? "dirty" : "clean"}`}>
                      {wt.dirty ? "dirty" : "clean"}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={wt.is_primary || remove.isPending}
                    title={wt.is_primary ? "Can't remove the primary repository checkout" : undefined}
                    onClick={() => {
                      const message = wt.dirty
                        ? `"${wt.path}" has uncommitted changes that will be lost. Delete this worktree anyway?`
                        : `Delete worktree "${wt.path}"?`;
                      if (window.confirm(message)) {
                        remove.mutate(wt.path);
                      }
                    }}
                  >
                    {remove.isPending && remove.variables === wt.path ? "Deleting…" : "Delete"}
                  </button>
                </li>
              ))}
            </ul>
          )}

          {remove.error ? (
            <div className="branch-triage-delete-error" style={{ marginTop: 12 }}>
              {remove.error instanceof Error ? remove.error.message : "Failed to remove worktree"}
            </div>
          ) : null}
        </div>
      </div>
    </>
  );
}
