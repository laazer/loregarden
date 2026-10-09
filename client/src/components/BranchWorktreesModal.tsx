import { useMutation, useQueryClient } from "@tanstack/react-query";

import { removeBranchWorktree, type BranchTriageEntry } from "../lib/branchTriageApi";
import { describeError } from "../state/toastStore";
import { IconCloseButton } from "./IconCloseButton";
import { Button } from "./ui/Button";
import { ModalShell } from "./ui/ModalShell";

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
    meta: { errorTitle: "Remove worktree" },
    mutationFn: (path: string) => removeBranchWorktree(workspaceSlug, branch!.name, path),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["branch-triage", workspaceSlug] });
    },
  });

  if (!open || !branch) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="branch-worktrees-title">{null}</ModalShell>;
  }

  const worktrees = branch.worktrees;

  return (
    <ModalShell open onDismiss={onClose} labelledBy="branch-worktrees-title">
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
                <Button
                  variant="secondary" className="btn-compact"
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
                </Button>
              </li>
            ))}
          </ul>
        )}

        {remove.error ? (
          <div className="branch-triage-delete-error" style={{ marginTop: 12 }}>
            {describeError(remove.error, "Failed to remove worktree")}
          </div>
        ) : null}
      </div>
    </ModalShell>
  );
}
