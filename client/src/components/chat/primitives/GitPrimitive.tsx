import { useQuery } from "@tanstack/react-query";

import {
  fetchBranchActivity,
  fetchCommitSnapshot,
} from "../../../lib/branchTriageApi";
import { PrimitiveCard } from "./PrimitiveCard";
import { VineNode } from "../../vine/VineNode";
import {
  OpenBranchTriageButton,
  OpenIdeButton,
} from "./ResourceActionButton";
import type { BranchHistoryPart, CommitPart } from "./types";

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.max(0, Math.floor((Date.now() - then) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function BranchHistoryPrimitive({ part }: { part: BranchHistoryPart }) {
  const limit = part.limit ?? 8;
  const query = useQuery({
    queryKey: ["branch-activity", part.workspace_slug, part.branch, limit],
    queryFn: () => fetchBranchActivity(part.workspace_slug, part.branch, limit),
  });
  const activity = query.data;
  const commits = activity?.commits ?? [];

  return (
    <PrimitiveCard
      kind="branch_history"
      title={part.title ?? part.branch}
      copyRef={part.branch}
      refLine={
        activity ? (activity.upstream ? `tracking ${activity.upstream}` : "No upstream") : part.branch
      }
      loading={query.isLoading}
      error={
        query.error
          ? query.error instanceof Error
            ? query.error.message
            : "Failed to load branch history"
          : null
      }
      footerHint={activity ? `${commits.length} recent commit${commits.length === 1 ? "" : "s"} · ${part.workspace_slug}` : null}
      actions={<OpenBranchTriageButton workspaceSlug={part.workspace_slug} branch={part.branch} />}
    >
      {commits.length ? (
        // Newest first, on the vine: order matters here. A pushed commit is a
        // leaf; one only on this machine is a held bud.
        <ol className="lg-primitive-commit-list">
          {commits.map((commit, index) => (
            <li key={commit.sha}>
              <VineNode index={index} mark={commit.pushed ? "leaf" : "bud-held"} last={index === commits.length - 1} />
              <code className="lg-primitive-commit-sha">{commit.short_sha}</code>
              <span className="lg-primitive-commit-subject" title={`${commit.message} — ${commit.author}`}>
                {commit.message}
                {commit.pushed ? null : <span className="lg-primitive-commit-local"> · local only</span>}
              </span>
              <time dateTime={commit.date}>{relativeTime(commit.date)}</time>
            </li>
          ))}
        </ol>
      ) : (
        <p className="lg-primitive-card-sub">No commits on this branch yet.</p>
      )}
    </PrimitiveCard>
  );
}

export function CommitPrimitive({ part }: { part: CommitPart }) {
  const query = useQuery({
    queryKey: ["commit", part.workspace_slug, part.sha],
    queryFn: () => fetchCommitSnapshot(part.workspace_slug, part.sha),
  });
  const commit = query.data;

  return (
    <PrimitiveCard
      kind="commit"
      title={part.title ?? commit?.message ?? "Commit"}
      refLine={
        commit ? [commit.short_sha, commit.author, part.branch].filter(Boolean).join(" · ") : part.sha
      }
      copyRef={commit?.sha ?? part.sha}
      loading={query.isLoading}
      error={
        query.error
          ? query.error instanceof Error
            ? query.error.message
            : "Failed to load commit"
          : null
      }
      status={
        commit
          ? commit.pushed
            ? { tone: "done", label: `Pushed ${relativeTime(commit.date)}` }
            : { tone: "idle", label: "Local only" }
          : null
      }
      actions={
        part.branch ? (
          <OpenBranchTriageButton
            workspaceSlug={part.workspace_slug}
            branch={part.branch}
          />
        ) : (
          <OpenIdeButton workspaceSlug={part.workspace_slug} />
        )
      }
    >
      {commit ? (
        <div className="lg-primitive-commit-detail">
          {commit.body ? <p>{commit.body}</p> : null}
          <div className="lg-primitive-commit-stats">
            <span>{commit.files_changed} files</span>
            <span className="is-add">+{commit.insertions}</span>
            <span className="is-delete">−{commit.deletions}</span>
          </div>
        </div>
      ) : null}
    </PrimitiveCard>
  );
}
