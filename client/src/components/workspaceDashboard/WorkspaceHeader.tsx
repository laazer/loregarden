import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { Approval } from "../../api/chatTypes";
import type { TicketStatusSummary } from "../../api/types";
import type { WorkspacePullRequests, WorkspaceRemote } from "../../api/workspaceDashboardApi";
import { pathForPage } from "../../lib/appNavigation";
import {
  fetchBranchPullRequests,
  fetchBranchTriage,
  pullRequestStatusNote,
  withPullRequests,
} from "../../lib/branchTriageApi";
import { standingSentence } from "../../lib/workspaceDashboard";
import { describeError } from "../../state/toastStore";
import { useUiStore } from "../../state/uiStore";
import { BranchCleanupModal } from "../BranchCleanupModal";
import { Button } from "../ui/Button";

interface WorkspaceHeaderProps {
  workspaceSlug: string;
  name: string;
  remote: UseQueryResult<WorkspaceRemote>;
  summary: UseQueryResult<TicketStatusSummary>;
  approvals: UseQueryResult<Approval[]>;
  /** Blocked tickets a person or an agent has to clear; undefined while loading. */
  blockedNeedingAction: number | undefined;
  pullRequests: UseQueryResult<WorkspacePullRequests>;
}

interface Stat {
  key: string;
  label: string;
  value: number | undefined;
  /** Small line under the number, e.g. "1 running". */
  note?: string;
  /** Tinted when non-zero: these are the ones waiting on a person. */
  tone?: "attention" | "danger";
}

/**
 * The top of the dashboard: the workspace's name, one sentence saying where it
 * stands, the board in five numbers, and the jumps a person catching up makes
 * next. Branch cleanup reads a branch-triage snapshot, which walks every branch
 * in the repo, so it is fetched when the button is pressed rather than on load.
 */
export function WorkspaceHeader({
  workspaceSlug,
  name,
  remote,
  summary,
  approvals,
  blockedNeedingAction,
  pullRequests,
}: WorkspaceHeaderProps) {
  const navigate = useNavigate();
  const setWorkspace = useUiStore((s) => s.setWorkspace);
  const setBranchTriageWorkspaceSlug = useUiStore((s) => s.setBranchTriageWorkspaceSlug);
  const [cleanupRequested, setCleanupRequested] = useState(false);

  const triage = useQuery({
    queryKey: ["branch-triage", workspaceSlug],
    queryFn: () => fetchBranchTriage(workspaceSlug),
    enabled: cleanupRequested,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const branchPrs = useQuery({
    queryKey: ["branch-triage", workspaceSlug, "pull-requests"],
    queryFn: () => fetchBranchPullRequests(workspaceSlug),
    enabled: cleanupRequested,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const cleanupBranches = useMemo(
    () => withPullRequests(triage.data?.branches ?? [], branchPrs.data),
    [triage.data?.branches, branchPrs.data],
  );
  const cleanupPrNote = pullRequestStatusNote(branchPrs.data, {
    loading: branchPrs.isLoading,
    error: branchPrs.error ? describeError(branchPrs.error, "request failed") : null,
  });
  const cleanupOpen = cleanupRequested && triage.isSuccess;
  const scanning = cleanupRequested && triage.isFetching && !triage.isSuccess;

  const counts = summary.data;
  const prs = pullRequests.data?.pull_requests;
  const approved = prs?.filter((row) => row.tier === "ready_to_merge").length;
  const repo = remote.data;

  const stats: Stat[] = [
    {
      key: "approvals",
      label: "Approvals waiting",
      value: approvals.data?.length,
      tone: "attention",
    },
    {
      key: "blocked",
      label: "Blocked",
      value: counts?.blocked,
      tone: "danger",
    },
    {
      key: "prs",
      label: "Open pull requests",
      value: prs?.length,
      note: approved === undefined ? undefined : approved ? `${approved} approved` : "none approved",
    },
    {
      key: "in_progress",
      label: "In progress",
      value: counts?.in_progress,
      note: counts ? `${counts.running} running` : undefined,
    },
    {
      key: "done",
      label: "Done",
      value: counts?.done,
      note: counts ? `${counts.backlog} in backlog` : undefined,
    },
  ];

  return (
    <>
      <header className="wsd-header">
        <div className="wsd-header-text">
          <h1 className="wsd-title">{name}</h1>
          <p className="wsd-standing">
            {standingSentence({
              approvals: approvals.data?.length,
              blocked: blockedNeedingAction,
              pullRequests: prs,
            })}
          </p>
          <p className="wsd-repo">
            {repo?.web_url ? (
              <a href={repo.web_url} target="_blank" rel="noreferrer">
                {repo.web_url.replace(/^https:\/\//, "")}
              </a>
            ) : remote.isError ? (
              <span className="wsd-error-inline" role="alert">
                Repository link unavailable: {describeError(remote.error)}
              </span>
            ) : repo ? (
              <span className="wsd-error-inline">No browsable remote: {repo.error}</span>
            ) : null}
            {repo ? <span className="wsd-path">{repo.repo_root}</span> : null}
          </p>
        </div>
        <div className="wsd-header-actions">
          <Button
            variant="secondary"
            compact
            onClick={() => {
              setWorkspace(workspaceSlug);
              navigate(pathForPage("dashboard"));
            }}
          >
            Open in Console
          </Button>
          <Button
            variant="secondary"
            compact
            onClick={() => {
              setBranchTriageWorkspaceSlug(workspaceSlug);
              navigate(pathForPage("branch-triage"));
            }}
          >
            Branch triage
          </Button>
          <Button
            variant="secondary"
            compact
            disabled={scanning}
            aria-busy={scanning}
            onClick={() => {
              setCleanupRequested(true);
              if (triage.isError) void triage.refetch();
            }}
          >
            {scanning ? "Scanning branches…" : "Clean up branches"}
          </Button>
          {cleanupRequested && triage.isError ? (
            <span className="wsd-error-inline" role="alert">
              Could not scan branches: {describeError(triage.error)}
            </span>
          ) : null}
        </div>
      </header>

      {summary.isError ? (
        <p className="wsd-error-inline" role="alert">
          Ticket counts unavailable: {describeError(summary.error)}
        </p>
      ) : null}
      <dl className="wsd-stats">
        {stats.map((stat) => (
          <div key={stat.key} className="wsd-stat" data-tone={stat.tone && stat.value ? stat.tone : undefined}>
            <dt>{stat.label}</dt>
            <dd>
              <span className="wsd-stat-value">{stat.value ?? "…"}</span>
              {stat.note ? <span className="wsd-stat-note">{stat.note}</span> : null}
            </dd>
          </div>
        ))}
      </dl>

      <BranchCleanupModal
        open={cleanupOpen}
        workspaceSlug={workspaceSlug}
        baseBranch={triage.data?.base_branch ?? "main"}
        branches={cleanupBranches}
        prStatusNote={cleanupPrNote}
        onClose={() => setCleanupRequested(false)}
      />
    </>
  );
}
