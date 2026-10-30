import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { removeBranchWorktree, type BranchTriageEntry } from "../lib/branchTriageApi";
import { useRestoreFocusOnSettle } from "../hooks/useRestoreFocusOnSettle";
import { describeError, pushToast } from "../state/toastStore";
import { removalNotice, type BranchRemovalNotice } from "../lib/branchRemovalNotices";
import { BranchNoticeRow, BranchRefusalBlock } from "./BranchRemovalNotices";
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
  // The gate can refuse this removal, and a refusal is not a failure: it means
  // nothing was lost. The two are carried apart so they can be styled apart.
  const [refusals, setRefusals] = useState<BranchRemovalNotice[]>([]);
  const [parked, setParked] = useState<BranchRemovalNotice | null>(null);

  const remove = useMutation({
    meta: { errorTitle: "Remove worktree" },
    mutationFn: (path: string) => removeBranchWorktree(workspaceSlug, branch!.name, path),
    onSuccess: (result, path) => {
      const ticket = branch?.linked_tickets[0] ?? null;
      const notice = removalNotice(path, result, ticket);
      setRefusals(result.outcome === "refused" ? [notice] : []);
      setParked(result.outcome === "parked" ? notice : null);
      if (result.outcome === "parked") {
        // Survives the operator closing this dialog, which the block does not.
        pushToast({
          tone: "error",
          title: "The checkout is parked, not deleted",
          message: result.detail || `Parked at ${result.parked_path}`,
        });
      }
      qc.invalidateQueries({ queryKey: ["branch-triage", workspaceSlug] });
    },
  });
  // Every row's Delete is disabled while any removal is pending, so the pressed
  // one is blurred; it gets focus back when the gate answers.
  const rememberActivated = useRestoreFocusOnSettle(remove.isPending);

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
          <p style={{ margin: 0, fontSize: "var(--fs-body)", color: "var(--txm)" }}>
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
                  variant="secondary" compact
                  // Every row, not just the row being removed: the release is a
                  // rename-then-delete, and a second one issued mid-flight has
                  // no row left to rename.
                  disabled={wt.is_primary || remove.isPending}
                  title={wt.is_primary ? "Can't remove the primary repository checkout" : undefined}
                  onClick={(event) => {
                    const message = wt.dirty
                      ? `"${wt.path}" has uncommitted changes that will be lost. Delete this worktree anyway?`
                      : `Delete worktree "${wt.path}"?`;
                    if (window.confirm(message)) {
                      rememberActivated(event.currentTarget);
                      setRefusals([]);
                      setParked(null);
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

        {remove.error || parked ? (
          <div className="branch-triage-delete-error branch-cleanup-failures" style={{ marginTop: 12 }}>
            {remove.error ? describeError(remove.error, "Failed to remove worktree") : null}
            {parked ? (
              <ul>
                <BranchNoticeRow notice={parked} />
              </ul>
            ) : null}
          </div>
        ) : null}

        <BranchRefusalBlock notices={refusals} pluralNoun="worktree(s)" />
      </div>
    </ModalShell>
  );
}
