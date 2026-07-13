import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import type { TicketState } from "../api/client";
import {
  checkoutBranchTriage,
  deleteBranchTriage,
  type BranchTriageEntry,
  type BranchTriagePrStatus,
} from "../lib/branchTriageApi";
import { ticketPath } from "../lib/appNavigation";
import { useNavigate } from "react-router-dom";
import { BranchCheckoutConfirmModal } from "./BranchCheckoutConfirmModal";
import { BranchDeleteConfirmModal } from "./BranchDeleteConfirmModal";
import { BranchTriageCurrentTag } from "./BranchTriageCurrentTag";
import { BranchWorktreesModal } from "./BranchWorktreesModal";
import { STATE_COLORS, STATE_LABELS } from "./UpdateStateModal";
import "./BranchTriagePanel.css";

function branchNeedsWorktreeRemoval(branch: BranchTriageEntry): boolean {
  return branch.worktrees.length > 0;
}

function severityClass(severity: string) {
  if (severity === "high") return "high";
  if (severity === "medium") return "medium";
  return "low";
}

function ticketStateBadge(state: string) {
  const known = STATE_LABELS[state as TicketState];
  const color = STATE_COLORS[state as TicketState] ?? "var(--txm)";
  const label = known ?? state.replace(/_/g, " ");
  return (
    <span className="branch-triage-badge" style={{ color, borderColor: color }} title="Ticket state">
      {label}
    </span>
  );
}

function prBadgeLabelAndColor(pr: BranchTriagePrStatus): { label: string; color: string } {
  if (pr.state === "merged") return { label: "PR merged", color: "var(--grn)" };
  if (pr.state === "closed") return { label: "PR closed", color: "var(--rdl)" };
  if (pr.is_draft) return { label: "PR draft", color: "var(--txm)" };
  return { label: "PR open", color: "var(--blue)" };
}

function prStateBadge(pr: BranchTriagePrStatus) {
  const { label, color } = prBadgeLabelAndColor(pr);
  const title = pr.number ? `${pr.title} (#${pr.number})` : pr.title;
  return (
    <a
      className="branch-triage-badge"
      style={{ color, borderColor: color }}
      href={pr.url}
      target="_blank"
      rel="noreferrer"
      title={title}
      onClick={(event) => event.stopPropagation()}
    >
      {label}
    </a>
  );
}

export function BranchTriageList({
  workspaceSlug,
  branches,
  selectedBranch,
  onSelectBranch,
  onReviewBranch,
  onBranchDeleted,
}: {
  workspaceSlug: string;
  branches: BranchTriageEntry[];
  selectedBranch: string | null;
  onSelectBranch: (branch: string) => void;
  onReviewBranch: (branch: string) => void;
  onBranchDeleted?: (branch: string) => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [deleteNotice, setDeleteNotice] = useState<string | null>(null);
  const [branchPendingDelete, setBranchPendingDelete] = useState<BranchTriageEntry | null>(null);
  const [branchPendingCheckout, setBranchPendingCheckout] = useState<BranchTriageEntry | null>(null);
  const [worktreesBranchName, setWorktreesBranchName] = useState<string | null>(null);

  const checkout = useMutation({
    mutationFn: (branch: string) => checkoutBranchTriage(workspaceSlug, branch),
    onSuccess: () => {
      setBranchPendingCheckout(null);
      qc.invalidateQueries({ queryKey: ["branch-triage", workspaceSlug] });
    },
  });

  const refreshBranches = (branch: string) => {
    onBranchDeleted?.(branch);
    qc.invalidateQueries({ queryKey: ["branch-triage", workspaceSlug] });
  };

  const remove = useMutation({
    mutationFn: ({ branch, removeWorktrees }: { branch: string; removeWorktrees: boolean }) =>
      deleteBranchTriage(workspaceSlug, branch, true, removeWorktrees),
    onSuccess: (result, { branch, removeWorktrees }) => {
      setBranchPendingDelete(null);
      refreshBranches(branch);
      if (result.already_gone) {
        setDeleteNotice(`${branch} was already removed — refreshing branch list.`);
        return;
      }
      if (removeWorktrees && result.removed_worktrees) {
        setDeleteNotice(`Removed worktree(s) and deleted ${branch}.`);
        return;
      }
      setDeleteNotice(`Deleted ${branch}.`);
    },
    onError: (error, { branch }) => {
      const message = error instanceof Error ? error.message : "Failed to delete branch";
      if (/not found/i.test(message)) {
        refreshBranches(branch);
        setDeleteNotice(`${branch} was already removed — refreshing branch list.`);
        return;
      }
      setDeleteNotice(null);
    },
  });

  const sorted = useMemo(
    () => [...branches].sort((a, b) => (b.issues.length - a.issues.length) || a.name.localeCompare(b.name)),
    [branches],
  );

  if (!sorted.length) {
    return <div className="branch-triage-empty">No branches found in this workspace repo.</div>;
  }

  return (
    <div className="branch-triage-list">
      <BranchCheckoutConfirmModal
        open={branchPendingCheckout !== null}
        branch={branchPendingCheckout}
        currentBranch={sorted.find((item) => item.is_current) ?? null}
        isCheckingOut={checkout.isPending}
        error={
          branchPendingCheckout && checkout.error instanceof Error ? checkout.error.message : null
        }
        onClose={() => {
          if (!checkout.isPending) setBranchPendingCheckout(null);
        }}
        onConfirm={() => {
          if (!branchPendingCheckout) return;
          checkout.mutate(branchPendingCheckout.name);
        }}
      />
      <BranchDeleteConfirmModal
        open={branchPendingDelete !== null}
        branch={branchPendingDelete}
        isDeleting={remove.isPending}
        error={
          branchPendingDelete && remove.error instanceof Error ? remove.error.message : null
        }
        onClose={() => {
          if (!remove.isPending) setBranchPendingDelete(null);
        }}
        onConfirm={() => {
          if (!branchPendingDelete) return;
          remove.mutate({
            branch: branchPendingDelete.name,
            removeWorktrees: branchNeedsWorktreeRemoval(branchPendingDelete),
          });
        }}
      />
      <BranchWorktreesModal
        open={worktreesBranchName !== null}
        branch={sorted.find((item) => item.name === worktreesBranchName) ?? null}
        workspaceSlug={workspaceSlug}
        onClose={() => setWorktreesBranchName(null)}
      />
      {deleteNotice ? (
        <div className="branch-triage-delete-notice">{deleteNotice}</div>
      ) : !branchPendingDelete && remove.error ? (
        <div className="branch-triage-delete-error">
          {remove.error instanceof Error ? remove.error.message : "Failed to delete branch"}
        </div>
      ) : null}
      {sorted.map((branch) => (
        <div
          key={branch.name}
          className={`branch-triage-card${selectedBranch === branch.name ? " selected" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => onSelectBranch(branch.name)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") onSelectBranch(branch.name);
          }}
        >
          <div className="branch-triage-card-header">
            <span className="branch-triage-card-name">{branch.name}</span>
            {branch.is_current ? <BranchTriageCurrentTag /> : null}
          </div>

          <div className="branch-triage-card-meta">
            {branch.ahead > 0 ? <span>{branch.ahead} ahead</span> : null}
            {branch.behind > 0 ? <span>{branch.behind} behind</span> : null}
            {branch.dirty ? <span>dirty</span> : null}
            {branch.worktrees.length ? (
              <span title={branch.worktrees.map((item) => item.path).join("\n")}>
                {branch.worktrees.length} worktree{branch.worktrees.length === 1 ? "" : "s"}
              </span>
            ) : null}
            {branch.linked_tickets[0] ? (
              <span>{branch.linked_tickets[0].external_id || branch.linked_tickets[0].title}</span>
            ) : null}
            {branch.linked_tickets[0] && !(branch.is_base && branch.linked_tickets[0].state === "done")
              ? ticketStateBadge(branch.linked_tickets[0].state)
              : null}
            {branch.pr ? prStateBadge(branch.pr) : null}
          </div>

          {branch.issues.length ? (
            <div className="branch-triage-actions">
              {branch.issues.map((issue) => (
                <span
                  key={`${branch.name}-${issue.code}`}
                  className={`branch-triage-issue ${severityClass(issue.severity)}`}
                  title={issue.message}
                >
                  {issue.code.replace(/_/g, " ")}
                </span>
              ))}
            </div>
          ) : (
            <div className="branch-triage-card-meta">Looks healthy</div>
          )}

          <div className="branch-triage-actions">
            <button
              type="button"
              className="btn-secondary btn-compact"
              disabled={checkout.isPending}
              onClick={(event) => {
                event.stopPropagation();
                checkout.reset();
                setBranchPendingCheckout(branch);
              }}
            >
              Checkout
            </button>
            <button
              type="button"
              className="btn-secondary btn-compact"
              onClick={(event) => {
                event.stopPropagation();
                onReviewBranch(branch.name);
              }}
            >
              Diff review
            </button>
            <button
              type="button"
              className="btn-secondary btn-compact"
              onClick={(event) => {
                event.stopPropagation();
                setWorktreesBranchName(branch.name);
              }}
            >
              Worktrees{branch.worktrees.length ? ` (${branch.worktrees.length})` : ""}
            </button>
            {branch.linked_tickets[0] ? (
              <button
                type="button"
                className="btn-secondary btn-compact"
                onClick={(event) => {
                  event.stopPropagation();
                  navigate(ticketPath(branch.linked_tickets[0].id, "diff"));
                }}
              >
                Open ticket
              </button>
            ) : null}
            {!branch.is_current && !branch.is_base ? (
              <button
                type="button"
                className="btn-secondary btn-compact"
                disabled={remove.isPending}
                onClick={(event) => {
                  event.stopPropagation();
                  setDeleteNotice(null);
                  remove.reset();
                  setBranchPendingDelete(branch);
                }}
              >
                Delete
              </button>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
