import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  CLEANUP_CATEGORIES,
  candidateReason,
  cleanupCandidates,
  defaultCleanupSelection,
  type CleanupCandidate,
} from "../lib/branchCleanup";
import { deleteBranchTriage, type BranchTriageEntry } from "../lib/branchTriageApi";
import { describeError } from "../state/toastStore";
import { useDialogDismiss } from "../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../hooks/useDialogFocusTrap";
import { IconCloseButton } from "./IconCloseButton";

interface BranchCleanupFailure {
  branch: string;
  message: string;
}

interface BranchCleanupResult {
  deleted: string[];
  failures: BranchCleanupFailure[];
}

/**
 * A checkbox whose `indeterminate` is driven from props.
 *
 * `indeterminate` is a DOM property with no attribute, so React cannot set it
 * from JSX — the ref callback is the only way to say "some, but not all".
 */
function TriStateCheckbox({
  checked,
  indeterminate,
  disabled,
  onChange,
  id,
}: {
  checked: boolean;
  indeterminate: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  id?: string;
}) {
  return (
    <input
      id={id}
      type="checkbox"
      checked={checked}
      disabled={disabled}
      ref={(node) => {
        if (node) node.indeterminate = indeterminate && !checked;
      }}
      onChange={(event) => onChange(event.target.checked)}
    />
  );
}

/**
 * Bulk cleanup of branches whose work has already landed.
 *
 * Opens pre-selecting the branches that are provably merged — a merged PR, or
 * nothing left that the base branch does not already have — and leaves every
 * other branch in the list for the operator to tick by hand. Deleting a branch
 * removes the worktrees checked out on it, which is the other half of the mess
 * this screen exists to clear.
 *
 * Deletion runs one branch at a time rather than as a single request: a failure
 * on one branch must not decide the fate of the eleven behind it, and the
 * operator needs to be told which ones survived and why.
 */
export function BranchCleanupModal({
  workspaceSlug,
  baseBranch,
  branches,
  onClose,
  onBranchesDeleted,
}: {
  workspaceSlug: string;
  baseBranch: string;
  branches: BranchTriageEntry[];
  onClose: () => void;
  onBranchesDeleted?: (branches: string[]) => void;
}) {
  const qc = useQueryClient();
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();

  const candidates = useMemo(() => cleanupCandidates(branches), [branches]);
  // Seeded once, at mount. The caller mounts this only while it is open, so a
  // fresh open is a fresh decision — and a snapshot refresh mid-session cannot
  // silently re-tick boxes the operator just cleared.
  const [selected, setSelected] = useState<Set<string>>(
    () => defaultCleanupSelection(cleanupCandidates(branches)),
  );
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [failures, setFailures] = useState<BranchCleanupFailure[]>([]);

  // A branch that disappeared from the snapshot (deleted here or elsewhere)
  // must not stay ticked, or the next run would try to delete it again.
  useEffect(() => {
    const live = new Set(candidates.map((item) => item.entry.name));
    setSelected((current) => {
      const next = new Set([...current].filter((name) => live.has(name)));
      return next.size === current.size ? current : next;
    });
  }, [candidates]);

  const remove = useMutation({
    meta: { errorTitle: "Clean up branches" },
    mutationFn: async (names: string[]): Promise<BranchCleanupResult> => {
      const byName = new Map(candidates.map((item) => [item.entry.name, item]));
      const deleted: string[] = [];
      const failed: BranchCleanupFailure[] = [];
      setProgress({ done: 0, total: names.length });
      for (const name of names) {
        try {
          await deleteBranchTriage(workspaceSlug, name, true, (byName.get(name)?.worktreeCount ?? 0) > 0);
          deleted.push(name);
        } catch (error) {
          // silent-ok: not swallowed — the failure is recorded per branch and
          // rendered in the dialog, so the remaining branches still get their
          // turn instead of one locked worktree ending the run.
          failed.push({ branch: name, message: describeError(error, "Failed to delete branch") });
        }
        setProgress((current) => ({ done: (current?.done ?? 0) + 1, total: names.length }));
      }
      return { deleted, failures: failed };
    },
    onSettled: () => {
      setProgress(null);
      qc.invalidateQueries({ queryKey: ["branch-triage", workspaceSlug] });
    },
    onSuccess: (result) => {
      setFailures(result.failures);
      if (result.deleted.length) onBranchesDeleted?.(result.deleted);
      if (!result.failures.length) onClose();
    },
  });

  const isDeleting = remove.isPending;
  // Escape and the backdrop agree on purpose: whatever makes a click dismiss
  // this dialog is what makes the key dismiss it — including mid-delete, when
  // neither may.
  useDialogDismiss(isDeleting ? undefined : onClose);

  const toggleBranch = (name: string, next: boolean) => {
    setSelected((current) => {
      const updated = new Set(current);
      if (next) updated.add(name);
      else updated.delete(name);
      return updated;
    });
  };

  const toggleCategory = (members: CleanupCandidate[], next: boolean) => {
    setSelected((current) => {
      const updated = new Set(current);
      for (const item of members) {
        if (next) updated.add(item.entry.name);
        else updated.delete(item.entry.name);
      }
      return updated;
    });
  };

  const selectedCandidates = candidates.filter((item) => selected.has(item.entry.name));
  const selectedDirty = selectedCandidates.filter((item) => item.dirty);
  const selectedWorktrees = selectedCandidates.reduce((sum, item) => sum + item.worktreeCount, 0);

  return (
    <>
      <div
        className="modal-overlay"
        onClick={isDeleting ? undefined : onClose}
        role="presentation"
      />
      <div
        ref={dialogRef}
        className="modal-panel branch-cleanup-modal"
        role="dialog"
        aria-labelledby="branch-cleanup-title"
        aria-modal="true"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">Branch triage</div>
            <h2 id="branch-cleanup-title" className="modal-title">
              Clean up branches
            </h2>
            <p className="modal-subtitle">
              Deleting a branch also removes the worktrees checked out on it.
            </p>
          </div>
          <IconCloseButton disabled={isDeleting} onClick={onClose} />
        </div>

        <div className="modal-body">
          {candidates.length === 0 ? (
            <p className="branch-cleanup-empty">
              Nothing to clean up. Every branch in this workspace is either {baseBranch} or the
              one you have checked out, and neither can be deleted from here.
            </p>
          ) : (
            <>
              <div className="branch-cleanup-section">
                <div className="modal-section-title">Select by category</div>
                <ul className="branch-cleanup-categories">
                  {CLEANUP_CATEGORIES.map((category) => {
                    const members = candidates.filter((item) =>
                      item.categories.includes(category.id),
                    );
                    const chosen = members.filter((item) => selected.has(item.entry.name)).length;
                    const inputId = `branch-cleanup-category-${category.id}`;
                    return (
                      <li key={category.id} className="branch-cleanup-category">
                        <TriStateCheckbox
                          id={inputId}
                          checked={members.length > 0 && chosen === members.length}
                          indeterminate={chosen > 0}
                          disabled={isDeleting || members.length === 0}
                          onChange={(next) => toggleCategory(members, next)}
                        />
                        <label htmlFor={inputId} className="branch-cleanup-category-copy">
                          <span className="branch-cleanup-category-label">
                            {category.label(baseBranch)}
                            <span className="branch-cleanup-count">{members.length}</span>
                          </span>
                          <span className="branch-cleanup-category-hint">{category.hint}</span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </div>

              <div className="branch-cleanup-section">
                <div className="modal-section-title">Branches</div>
                <ul className="branch-cleanup-branches">
                  {candidates.map((item) => {
                    const inputId = `branch-cleanup-branch-${item.entry.name}`;
                    return (
                      <li key={item.entry.name} className="branch-cleanup-branch">
                        <input
                          id={inputId}
                          type="checkbox"
                          checked={selected.has(item.entry.name)}
                          disabled={isDeleting}
                          onChange={(event) =>
                            toggleBranch(item.entry.name, event.target.checked)
                          }
                        />
                        <label htmlFor={inputId} className="branch-cleanup-branch-copy">
                          <span className="branch-cleanup-branch-name">
                            {item.entry.name}
                            {item.dirty ? (
                              <span className="branch-cleanup-flag">uncommitted work</span>
                            ) : null}
                          </span>
                          <span className="branch-cleanup-branch-reason">
                            {candidateReason(item, baseBranch)}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </>
          )}

          {selectedDirty.length ? (
            <p className="branch-cleanup-warning">
              {selectedDirty.length} selected branch{selectedDirty.length === 1 ? " has" : "es have"}{" "}
              uncommitted changes. Deleting {selectedDirty.length === 1 ? "it" : "them"} discards
              that work permanently.
            </p>
          ) : null}

          {failures.length ? (
            <div className="branch-triage-delete-error branch-cleanup-failures">
              <div className="modal-section-title">
                {failures.length} branch{failures.length === 1 ? "" : "es"} could not be deleted
              </div>
              <ul>
                {failures.map((failure) => (
                  <li key={failure.branch}>
                    <code>{failure.branch}</code> — {failure.message}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>

        <div className="modal-footer">
          <span className="branch-cleanup-status" aria-live="polite">
            {progress
              ? `Deleting ${progress.done} of ${progress.total}…`
              : `${selected.size} selected${
                  selectedWorktrees
                    ? ` · ${selectedWorktrees} worktree${selectedWorktrees === 1 ? "" : "s"}`
                    : ""
                }`}
          </span>
          <button type="button" className="btn-secondary" disabled={isDeleting} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary branch-delete-confirm-submit"
            disabled={isDeleting || selected.size === 0}
            onClick={() => {
              setFailures([]);
              remove.mutate(
                candidates
                  .filter((item) => selected.has(item.entry.name))
                  .map((item) => item.entry.name),
              );
            }}
          >
            {isDeleting ? "Deleting…" : `Delete selected (${selected.size})`}
          </button>
        </div>
      </div>
    </>
  );
}
