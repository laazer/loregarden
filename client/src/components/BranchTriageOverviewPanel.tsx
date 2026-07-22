import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, type WorkspaceRuntimeSettings } from "../api/client";
import { ticketPath } from "../lib/appNavigation";
import { DEFAULT_RUNTIME } from "../lib/runtimeSettings";
import { TRIAGE_AGENT_NAME } from "../lib/triageAgent";
import {
  fetchBranchActivity,
  type BranchTriageChatSnapshot,
  type BranchTriageEntry,
} from "../lib/branchTriageApi";
import { useBranchChatSession } from "../hooks/useBranchChatSession";
import { useUiStore } from "../state/uiStore";
import { BranchTriageCurrentTag } from "./BranchTriageCurrentTag";
import { TriageModelModal } from "./TriageModelModal";
import { runtimeSummaryLabel } from "./WorkspaceRuntimeFields";

/** Coarse "2m ago" for a commit timestamp; precision past the day is noise here. */
function commitAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.floor((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/**
 * What the branch's numbers add up to, in a sentence.
 *
 * Built only from what the snapshot reports — no inference about whether the
 * branch is safe to merge or delete. That judgement is what the copilot is for.
 */
function healthSummary(entry: BranchTriageEntry | undefined, baseBranch: string) {
  if (!entry) {
    return { tone: "neutral" as const, title: "No branch data", detail: "", rest: [] };
  }

  const worst =
    entry.issues.find((issue) => issue.severity === "high") ??
    entry.issues.find((issue) => issue.severity === "medium") ??
    entry.issues[0];

  const facts: string[] = [];
  if (entry.ahead > 0) facts.push(`${entry.ahead} commit(s) ahead of ${baseBranch}`);
  if (entry.behind > 0) facts.push(`${entry.behind} behind`);
  if (!entry.ahead && !entry.behind) facts.push(`level with ${baseBranch}`);
  if (entry.upstream) facts.push(`tracking ${entry.upstream}`);
  else facts.push("no upstream");
  if (entry.worktrees.length > 1) facts.push(`${entry.worktrees.length} worktrees checked out`);
  if (entry.dirty) facts.push("uncommitted changes in a worktree");

  const detail = `${facts.join(" · ")}.`;

  if (!worst) {
    return {
      tone: "ok" as const,
      title: `Clean — up to date with ${baseBranch}`,
      detail,
      rest: [],
    };
  }
  return {
    tone: worst.severity === "high" ? ("bad" as const) : ("warn" as const),
    title: worst.message,
    detail,
    // Everything the headline did not say. Keyed off the chosen issue rather
    // than its position: the headline is picked by severity, so dropping
    // `issues[0]` would hide a different issue and repeat this one.
    rest: entry.issues.filter((issue) => issue !== worst),
  };
}

/**
 * The branch at a glance, with the conversation left to the copilot dock.
 *
 * This screen used to embed a second full chat for the same session the dock
 * binds to — two composers for one conversation. What the panel is uniquely
 * placed to show is the branch's state, so that is all it shows now.
 */
export function BranchTriageOverviewPanel({
  workspaceSlug,
  branch,
  baseBranch,
  branchEntry,
  onReviewDiff,
}: {
  workspaceSlug: string;
  branch: string;
  baseBranch: string;
  branchEntry?: BranchTriageEntry;
  onReviewDiff: () => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const setCopilotOpen = useUiStore((s) => s.setCopilotOpen);
  const [modelModalOpen, setModelModalOpen] = useState(false);

  const runtimeOptions = useQuery({ queryKey: ["runtime-options"], queryFn: api.runtimeOptions });

  const activity = useQuery({
    queryKey: ["branch-triage-activity", workspaceSlug, branch],
    queryFn: () => fetchBranchActivity(workspaceSlug, branch),
    enabled: Boolean(workspaceSlug && branch),
  });

  // Bound for the runtime it carries, not to render turns — the dock owns those.
  const session = useBranchChatSession(workspaceSlug, branch);
  const linkedTicketId = session.snapshot?.linked_ticket_id ?? null;
  const savedRuntime = session.snapshot?.runtime ?? DEFAULT_RUNTIME;
  const chatQueryKey = ["branch-triage-chat", workspaceSlug, branch] as const;

  const applyRuntime = (saved: WorkspaceRuntimeSettings) => {
    qc.setQueryData(chatQueryKey, (current: BranchTriageChatSnapshot | undefined) =>
      current ? { ...current, runtime: saved } : current,
    );
  };

  const saveTicketRuntime = useMutation({
    mutationFn: (runtime: WorkspaceRuntimeSettings) =>
      api.setTriageRuntime(linkedTicketId!, runtime),
    onSuccess: applyRuntime,
  });

  const saveWorkspaceRuntime = useMutation({
    mutationFn: (runtime: WorkspaceRuntimeSettings) =>
      api.setWorkspaceRuntime(workspaceSlug, runtime),
    onSuccess: applyRuntime,
  });

  const summary = healthSummary(branchEntry, baseBranch);
  const linkedTicket = branchEntry?.linked_tickets[0];
  const commits = activity.data?.commits ?? [];

  return (
    <div className="branch-triage-main branch-triage-overview">
      <div className="branch-triage-summary">
        <div className="branch-triage-summary-title">
          <h2>{branch}</h2>
          {branchEntry?.is_current ? <BranchTriageCurrentTag /> : null}
          {branchEntry?.dirty ? <span className="branch-triage-overview-dirty">dirty</span> : null}
        </div>
        {linkedTicket ? (
          <button
            type="button"
            className="btn-secondary btn-compact"
            onClick={() => navigate(ticketPath(linkedTicket.id, "triage"))}
          >
            Open ticket triage
          </button>
        ) : null}
      </div>

      <div className="branch-triage-overview-body">
        <div className="branch-triage-stats">
          <div className="branch-triage-stat">
            <span className="branch-triage-stat-label">Commits ahead</span>
            <span className="branch-triage-stat-value">{branchEntry?.ahead ?? 0}</span>
            <span className="branch-triage-stat-note">unmerged into {baseBranch}</span>
          </div>
          <div className="branch-triage-stat">
            <span className="branch-triage-stat-label">Commits behind</span>
            <span className="branch-triage-stat-value">{branchEntry?.behind ?? 0}</span>
            <span className="branch-triage-stat-note">
              {branchEntry?.behind ? `behind ${baseBranch}` : "up to date"}
            </span>
          </div>
          <div className="branch-triage-stat">
            <span className="branch-triage-stat-label">Worktrees</span>
            <span className="branch-triage-stat-value">{branchEntry?.worktrees.length ?? 0}</span>
            <span className="branch-triage-stat-note">
              {(branchEntry?.worktrees.length ?? 0) === 1 ? "active checkout" : "active checkouts"}
            </span>
          </div>
          <div className="branch-triage-stat">
            <span className="branch-triage-stat-label">Working tree</span>
            <span
              className={`branch-triage-stat-value${branchEntry?.dirty ? " is-dirty" : ""}`}
            >
              {branchEntry?.dirty ? "Dirty" : "Clean"}
            </span>
            <span className="branch-triage-stat-note">
              {branchEntry?.dirty ? "uncommitted changes" : "nothing uncommitted"}
            </span>
          </div>
        </div>

        <section className={`branch-triage-health branch-triage-health--${summary.tone}`}>
          <h3 className="branch-triage-health-title">{summary.title}</h3>
          {summary.detail ? <p className="branch-triage-health-detail">{summary.detail}</p> : null}
          {summary.rest.length > 0 ? (
            <ul className="branch-triage-health-issues">
              {summary.rest.map((issue) => (
                <li key={issue.code} className={`branch-triage-issue ${issue.severity}`}>
                  {issue.message}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="branch-triage-health-actions">
            <button type="button" className="btn-primary btn-compact" onClick={onReviewDiff}>
              Diff review
            </button>
          </div>
        </section>

        <section className="branch-triage-activity">
          <div className="branch-triage-activity-header">
            <h3>Recent commits</h3>
            {commits.length ? (
              <span className="branch-triage-activity-count">{commits.length}</span>
            ) : null}
          </div>
          {activity.isLoading ? (
            <div className="branch-triage-empty">Reading history…</div>
          ) : activity.error ? (
            <div className="branch-triage-empty">
              {activity.error instanceof Error
                ? activity.error.message
                : "Failed to load branch history"}
            </div>
          ) : commits.length === 0 ? (
            <div className="branch-triage-empty">No commits on this branch yet.</div>
          ) : (
            <ul className="branch-triage-activity-list">
              {commits.map((commit) => (
                <li key={commit.sha} className="branch-triage-activity-item">
                  <span
                    className={`branch-triage-activity-dot${commit.pushed ? " is-pushed" : ""}`}
                    aria-hidden
                  />
                  <div className="branch-triage-activity-copy">
                    <span className="branch-triage-activity-message">{commit.message}</span>
                    <span className="branch-triage-activity-meta">
                      <code>{commit.short_sha}</code> · {commit.author} ·{" "}
                      {commit.pushed ? "pushed" : "local only"}
                    </span>
                  </div>
                  <span className="branch-triage-activity-time">{commitAgo(commit.date)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="branch-triage-copilot-card">
          <div className="branch-triage-copilot-copy">
            <h3>Ask {TRIAGE_AGENT_NAME} about this branch</h3>
            <p>
              The chat lives in the bar below — it already has <strong>{branch}</strong> in
              context. Ask about risks, or tell it to commit, push, merge, or clean up.
            </p>
          </div>
          <div className="branch-triage-copilot-actions">
            <button
              type="button"
              className="btn-primary btn-compact"
              onClick={() => setCopilotOpen(true)}
            >
              Ask in chat
            </button>
            <button
              type="button"
              className="btn-secondary btn-compact"
              disabled={!runtimeOptions.data || session.isLoading}
              onClick={() => setModelModalOpen(true)}
            >
              Model · {runtimeSummaryLabel(savedRuntime, runtimeOptions.data)}
            </button>
          </div>
        </section>
      </div>

      <TriageModelModal
        open={modelModalOpen}
        runtime={savedRuntime}
        runtimeOptions={runtimeOptions.data}
        isSaving={saveTicketRuntime.isPending || saveWorkspaceRuntime.isPending}
        onClose={() => setModelModalOpen(false)}
        onSave={async (runtime) => {
          if (linkedTicketId) {
            await saveTicketRuntime.mutateAsync(runtime);
          } else {
            await saveWorkspaceRuntime.mutateAsync(runtime);
          }
        }}
      />
    </div>
  );
}
