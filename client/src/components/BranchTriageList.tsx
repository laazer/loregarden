import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Fragment, useMemo, useState } from "react";

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
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";
import { OverflowMenu, OverflowMenuItem } from "./OverflowMenu";
import { cleanupCandidates, defaultCleanupSelection } from "../lib/branchCleanup";
import { NEXT_MOVES, branchRows, matchesNextMove, nextMoveCounts, type NextMove } from "../lib/branchNextMove";
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
  onCleanUp,
}: {
  workspaceSlug: string;
  branches: BranchTriageEntry[];
  selectedBranch: string | null;
  onSelectBranch: (branch: string) => void;
  onReviewBranch: (branch: string) => void;
  onBranchDeleted?: (branch: string) => void;
  /** Opens the cleanup dialog, which lists what it deletes before anything goes. */
  onCleanUp?: () => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [deleteNotice, setDeleteNotice] = useState<string | null>(null);
  const [branchPendingDelete, setBranchPendingDelete] = useState<BranchTriageEntry | null>(null);
  const [branchPendingCheckout, setBranchPendingCheckout] = useState<BranchTriageEntry | null>(null);
  const [worktreesBranchName, setWorktreesBranchName] = useState<string | null>(null);
  // A repo carries well over a hundred branches; the list needs a way to narrow it.
  const [filter, setFilter] = useState("");
  // What to do next, narrowed before the text filter: merged, stale, no ticket.
  const [move, setMove] = useState<NextMove>("all");
  // Families folded past FAMILY_FOLD_AT branches; these are the ones opened.
  const [openFamilies, setOpenFamilies] = useState<Set<string>>(() => new Set());

  const checkout = useMutation({
    meta: { errorTitle: "Checkout branch" },
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
    meta: { errorTitle: "Delete branch" },
    mutationFn: ({
      branch,
      removeWorktrees,
      discardCommits,
    }: {
      branch: string;
      removeWorktrees: boolean;
      discardCommits: boolean;
    }) => deleteBranchTriage(workspaceSlug, branch, discardCommits, removeWorktrees),
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

  const counts = useMemo(() => nextMoveCounts(sorted), [sorted]);
  // What the cleanup dialog will tick when it opens: merged, nothing only here, nothing dirty.
  const cleanupCount = useMemo(() => defaultCleanupSelection(cleanupCandidates(sorted)).size, [sorted]);

  if (!sorted.length) {
    return <div className="branch-triage-empty">No branches found in this workspace repo.</div>;
  }

  // By branch name, or by a linked ticket's id: "which branch is lg-x-12 on?"
  const needle = filter.trim().toLowerCase();
  const inMove = sorted.filter((branch) => matchesNextMove(branch, move));
  const shown = needle
    ? inMove.filter(
        (branch) =>
          branch.name.toLowerCase().includes(needle) ||
          branch.linked_tickets.some((ticket) => ticket.external_id.toLowerCase().includes(needle)),
      )
    : inMove;

  const renderBranch = (branch: BranchTriageEntry, nested = false) => {
              const ticket = branch.linked_tickets[0];
              const deletable = !branch.is_current && !branch.is_base;
              return (
                <tr
                  key={branch.name}
                  className={[selectedBranch === branch.name ? "is-selected" : null, nested ? "is-nested" : null].filter(Boolean).join(" ") || undefined}
                >
                  <td className="branch-triage-cell-name">
                    <Button
                      variant="plain"
                      className="branch-triage-name"
                      aria-current={selectedBranch === branch.name ? "true" : undefined}
                      title={branch.name}
                      onClick={() => onSelectBranch(branch.name)}
                    >
                      {branch.name}
                    </Button>
                    <div className="branch-triage-row-meta">
                      {branch.is_current ? <BranchTriageCurrentTag /> : null}
                      {ticket ? (
                        <>
                          <span className="branch-triage-row-ticket">{ticket.external_id || ticket.title}</span>
                          {!(branch.is_base && ticket.state === "done") ? ticketStateBadge(ticket.state) : null}
                        </>
                      ) : null}
                      {branch.issues.slice(0, 3).map((issue) => (
                        <span
                          key={`${branch.name}-${issue.code}`}
                          className={`branch-triage-issue ${severityClass(issue.severity)}`}
                          title={issue.message}
                        >
                          {issue.code.replace(/_/g, " ")}
                        </span>
                      ))}
                      {branch.worktrees.length ? (
                        <span title={branch.worktrees.map((item) => item.path).join("\n")}>
                          {branch.worktrees.length} worktree{branch.worktrees.length === 1 ? "" : "s"}
                        </span>
                      ) : null}
                    </div>
                  </td>
                  <td className="num">
                    {branch.ahead} / {branch.behind}
                  </td>
                  <td>{branch.pr ? prStateBadge(branch.pr) : <span className="branch-triage-none">—</span>}</td>
                  <td className="branch-triage-cell-more">
                    <OverflowMenu label={`${branch.name} actions`} align="right">
                      <OverflowMenuItem onSelect={() => onReviewBranch(branch.name)}>Diff review</OverflowMenuItem>
                      <OverflowMenuItem
                        disabled={checkout.isPending || branch.is_current}
                        onSelect={() => {
                          checkout.reset();
                          setBranchPendingCheckout(branch);
                        }}
                      >
                        Check out…
                      </OverflowMenuItem>
                      <OverflowMenuItem onSelect={() => setWorktreesBranchName(branch.name)}>
                        {`Worktrees${branch.worktrees.length ? ` (${branch.worktrees.length})` : ""}…`}
                      </OverflowMenuItem>
                      {ticket ? (
                        <OverflowMenuItem onSelect={() => navigate(ticketPath(ticket.id, "diff"))}>
                          Open ticket
                        </OverflowMenuItem>
                      ) : null}
                      {deletable ? (
                        <OverflowMenuItem
                          danger
                          disabled={remove.isPending}
                          onSelect={() => {
                            setDeleteNotice(null);
                            remove.reset();
                            setBranchPendingDelete(branch);
                          }}
                        >
                          Delete…
                        </OverflowMenuItem>
                      ) : null}
                    </OverflowMenu>
                  </td>
                </tr>
              );
  };

  return (
    <div className="branch-triage-list">
      <div className="branch-triage-moves">
        <div className="branch-triage-seg" role="group" aria-label="Next move">
          {NEXT_MOVES.map((item) => (
            <Button
              key={item.id}
              variant="plain"
              aria-pressed={move === item.id}
              className={move === item.id ? "is-on" : undefined}
              title={item.hint}
              onClick={() => setMove(item.id)}
            >
              {item.label}
              <span className="branch-triage-seg-count">{counts[item.id]}</span>
            </Button>
          ))}
        </div>
        {onCleanUp ? (
          <Button
            variant={move === "merged" && cleanupCount ? "primary" : "secondary"}
            compact
            disabled={!cleanupCount}
            title={
              cleanupCount
                ? "Lists the merged branches and their worktrees before deleting any"
                : "No merged branch is safe to delete: each has commits only it holds, or uncommitted work"
            }
            onClick={onCleanUp}
          >
            {cleanupCount ? `Delete ${cleanupCount} merged…` : "Nothing merged to delete"}
          </Button>
        ) : null}
      </div>
      <Input
        type="search"
        className="branch-triage-filter"
        aria-label="Filter branches by name or linked ticket"
        placeholder={`Filter ${sorted.length} branches by name or ticket…`}
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && filter) {
            event.preventDefault();
            setFilter("");
          }
        }}
      />
      {shown.length === 0 ? (
        <div className="branch-triage-empty">
          <p>
            {needle
              ? `No ${move === "all" ? "" : `${NEXT_MOVES.find((m) => m.id === move)?.label.toLowerCase()} `}branch name or linked ticket contains “${filter.trim()}”.`
              : `No branch is ${NEXT_MOVES.find((m) => m.id === move)?.label.toLowerCase()}.`}
          </p>
          <Button
            variant="secondary"
            compact
            onClick={() => {
              setFilter("");
              setMove("all");
            }}
          >
            Show every branch
          </Button>
        </div>
      ) : null}
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
        onConfirm={(discardCommits) => {
          if (!branchPendingDelete) return;
          remove.mutate({
            branch: branchPendingDelete.name,
            removeWorktrees: branchNeedsWorktreeRemoval(branchPendingDelete),
            discardCommits,
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
      {shown.length ? (
        <table className="branch-triage-table" aria-label={`${shown.length} branches`}>
          <thead>
            <tr>
              <th scope="col">Branch</th>
              <th scope="col" className="num" title="Commits ahead of and behind the base branch">
                Ahead / behind
              </th>
              <th scope="col">PR</th>
              <th scope="col">
                <span className="branch-triage-sr">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {(needle ? shown.map((branch) => ({ kind: "branch" as const, branch })) : branchRows(shown)).map((row) => {
              if (row.kind === "branch") return renderBranch(row.branch);
              // A selected branch's family stays open, so the selection is never hidden.
              const open = openFamilies.has(row.key) || row.branches.some((b) => b.name === selectedBranch);
              const done = row.branches.filter((b) => b.linked_tickets[0]?.state === "done").length;
              return (
                <Fragment key={`family:${row.key}`}>
                  <tr className="branch-triage-family">
                    <td colSpan={4}>
                      <Button
                        variant="plain"
                        className="branch-triage-family-toggle"
                        aria-expanded={open}
                        onClick={() =>
                          setOpenFamilies((prev) => {
                            const next = new Set(prev);
                            if (next.has(row.key)) next.delete(row.key);
                            else next.add(row.key);
                            return next;
                          })
                        }
                      >
                        <span aria-hidden>{open ? "▾" : "▸"}</span>
                        <span className="branch-triage-family-name">{row.key}-…</span>
                        <span className="branch-triage-seg-count">{row.branches.length} branches</span>
                        {done ? <span className="branch-triage-family-note">{done} with the ticket done</span> : null}
                      </Button>
                    </td>
                  </tr>
                  {open ? row.branches.map((branch) => renderBranch(branch, true)) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
